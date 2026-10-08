"""PeCal Smart Forecast — new product, written from scratch.

Challenge 1: lab overload forecast — group DATUM_NAECHSTE_PRUEFUNG by
MESSRAUM × month against planned capacity (Soll-Kapa); devices without a
date are estimated with the median interval of their MESSMITTELGRUPPE.
As in TASK.md — no Prophet, no LLM.

Challenge 2: intelligent sales assistant — "Who to call today?"
(RFM, churn risk, order volume forecast, industry upsell opportunities).

Data: data/raw/*.csv (local database).
"""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent
RAW_DIR = BASE_DIR / "data" / "raw"

LABS = ["MR1", "MR2", "MR3", "MR4", "MR5", "MR6",
        "MR7", "MR8", "MR9", "MR11", "MR12", "MR14"]
HOURS_PER_DEVICE = 0.75        # estimated hours per device (TASK, SQL section)
AVG_CHECK_EUR = 250            # average deal size, constant (TASK §7)
UPSELL_BONUS_EUR = 500
WARN_PCT = 85.0
CRIT_PCT = 100.0
LOW_PCT = 60.0
TODAY = pd.Timestamp(datetime.now().date())
HORIZON_END = TODAY + pd.DateOffset(months=12)

app = FastAPI(title="PeCal Smart Forecast")


def _read(name: str, usecols: list[str] | None = None) -> pd.DataFrame:
    p = RAW_DIR / name
    if not p.exists():
        return pd.DataFrame()
    return pd.read_csv(p, sep=";", dtype=str, usecols=usecols, low_memory=False)


def _clean(v):
    if v is None:
        return None
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        f = float(v)
        return None if math.isnan(f) else f
    if isinstance(v, (pd.Timestamp, datetime)):
        try:
            if pd.isna(v):
                return None
        except Exception:
            pass
        return pd.Timestamp(v).strftime("%Y-%m-%d %H:%M")
    try:
        if pd.isna(v):
            return None
    except Exception:
        pass
    return v


def recs(df: pd.DataFrame | list[dict]) -> list[dict]:
    rows = df.to_dict("records") if isinstance(df, pd.DataFrame) else df
    return [{k: _clean(v) for k, v in r.items()} for r in rows]


# ---------------------------------------------------------------- load
mm = _read("MESSMITTEL.csv", usecols=[
    "MESSMITTEL_UUID", "DATUM_LETZTE_PRUEFUNG", "DATUM_NAECHSTE_PRUEFUNG",
    "PRUEFINTERVALL", "KUNDENNUMMER_SAP", "DATUM_ERSTNUTZUNG_1",
    "MESSMITTELGRUPPE", "MESSMITTELTYP", "MESSRAUM",
])
kalib = _read("KALIBRIERUNGEN.csv", usecols=["KUNDENNUMMER_SAP", "BEGINN"])
soll = _read("SollKapa.csv", usecols=["Kalendertag (Intervall)", "MESSRAUM",
                                      "SOLL Anwesend STD"])
kb = _read("KundenNrBranche.csv", usecols=["KundenNr", "Branche"])


# ------------------------------------- preparation: next inspection dates
for col in ("DATUM_LETZTE_PRUEFUNG", "DATUM_NAECHSTE_PRUEFUNG", "DATUM_ERSTNUTZUNG_1"):
    mm[col] = pd.to_datetime(mm[col], errors="coerce")
mm["PRUEFINTERVALL"] = pd.to_numeric(mm["PRUEFINTERVALL"], errors="coerce")
mm["KUNDENNUMMER_SAP"] = mm["KUNDENNUMMER_SAP"].replace({"NULL": None, "": None})

# Median interval per device group (for devices without a date / interval)
group_median = (
    mm.loc[mm["PRUEFINTERVALL"].notna() & (mm["PRUEFINTERVALL"] > 0)]
      .groupby("MESSMITTELGRUPPE")["PRUEFINTERVALL"].median()
)

# Devices without a date: base = last inspection, else first usage,
# + interval (personal, else group median, else 12 months).
iv = mm["PRUEFINTERVALL"].astype(float)
iv = iv.where(iv.notna() & (iv > 0), mm["MESSMITTELGRUPPE"].map(group_median)).fillna(12.0)
iv = iv.clip(upper=120)  # guard against data outliers (dates would hit 2600+)
base = mm["DATUM_LETZTE_PRUEFUNG"].fillna(mm["DATUM_ERSTNUTZUNG_1"])
est = (base.dt.to_period("M") + iv.round().astype(int)).dt.to_timestamp()
# EINHEIT=2 = months (98% of rows) — intervals are read as months.
mm["due"] = mm["DATUM_NAECHSTE_PRUEFUNG"].fillna(est)
mm["due_month"] = mm["due"].dt.strftime("%Y-%m")
mm["has_date"] = mm["DATUM_NAECHSTE_PRUEFUNG"].notna()

# Devices in labs with a valid date
mm_lab = mm[mm["MESSRAUM"].isin(LABS) & mm["due"].notna()].copy()

# --------------------------------------------------------------- capacity
soll["Kalendertag (Intervall)"] = pd.to_datetime(soll["Kalendertag (Intervall)"], errors="coerce")
soll["SOLL Anwesend STD"] = pd.to_numeric(soll["SOLL Anwesend STD"], errors="coerce")
soll["month"] = soll["Kalendertag (Intervall)"].dt.strftime("%Y-%m")
cap = (soll.dropna(subset=["month"])
           .groupby(["MESSRAUM", "month"])["SOLL Anwesend STD"].sum()
           .reset_index(name="soll_h"))
cap_map = {(r.MESSRAUM, r.month): float(r.soll_h) for r in cap.itertuples()}

# Last known monthly capacity per MESSRAUM — for months without data
last_cap: dict[str, float] = {}
for mr, g in cap.sort_values("month").groupby("MESSRAUM"):
    last_cap[mr] = float(g.tail(3)["soll_h"].mean())

def capacity(mr: str, month: str) -> float | None:
    v = cap_map.get((mr, month))
    if v is None:
        v = last_cap.get(mr)
    # < 40 h/month = incomplete data (SollKapa has months with a single day)
    if v is not None and v < 40:
        return None
    return v


# ------------------------------------------------------------ Challenge 1
def forecast(months: int = 6, messraum: str | None = None) -> pd.DataFrame:
    """Forecast per lab: devices, hours, load %."""
    end = TODAY + pd.DateOffset(months=months)
    d = mm_lab[(mm_lab["due"] >= TODAY) & (mm_lab["due"] < end)]
    if messraum:
        d = d[d["MESSRAUM"] == messraum]
    d = d.assign(est_month=d["due"].dt.strftime("%Y-%m"))
    grp = d.groupby(["MESSRAUM", "est_month"]).agg(
        mit_datum=("has_date", "sum"),
        geraete=("MESSMITTEL_UUID", "size"),
    ).reset_index()
    grp["geschaetzt"] = grp["geraete"] - grp["mit_datum"]
    # full grid labs × months (keep zeros)
    month_seq = pd.date_range(TODAY.replace(day=1), periods=months, freq="MS").strftime("%Y-%m")
    labs = [messraum] if messraum else LABS
    idx = pd.MultiIndex.from_product([labs, month_seq], names=["MESSRAUM", "est_month"])
    out = grp.set_index(["MESSRAUM", "est_month"]).reindex(idx, fill_value=0).reset_index()
    out["hours"] = (out["geraete"] * HOURS_PER_DEVICE).round(1)
    out["soll_h"] = [capacity(r.MESSRAUM, r.est_month) for r in out.itertuples()]
    out["util_pct"] = out.apply(
        lambda r: round(r.hours * 100.0 / r.soll_h, 1) if r.soll_h else None, axis=1)
    out["status"] = out["util_pct"].map(status_of)
    return out.rename(columns={"est_month": "month"})


def status_of(u) -> str:
    if u is None or (isinstance(u, float) and math.isnan(u)):
        return "NO_DATA"
    if u >= CRIT_PCT:
        return "OVERLOAD"
    if u >= WARN_PCT:
        return "WARNING"
    if u < LOW_PCT:
        return "LOW"
    return "NORMAL"


def bottlenecks(months: int = 6, limit: int = 10) -> list[dict]:
    """Bottlenecks with prevention advice: how many calibrations to move
    and to which lower-load month inside the horizon."""
    f = forecast(months)
    b = f[f["util_pct"].notna() & (f["util_pct"] >= WARN_PCT)]
    b = b.sort_values("util_pct", ascending=False).head(limit)
    rows = []
    for r in b.itertuples():
        overload_h = (r.hours - 0.85 * r.soll_h) if r.soll_h else 0.0
        shift = max(0, math.ceil(overload_h / HOURS_PER_DEVICE))
        free = f[(f["MESSRAUM"] == r.MESSRAUM) & f["util_pct"].notna() &
                 (f["util_pct"] < WARN_PCT) & (f["month"] != r.month)]
        free = free.sort_values("util_pct")
        shift_to = new_util = None
        if shift and len(free):
            t = free.iloc[0]
            shift_to = t.month
            if t.soll_h:
                new_util = round((t.hours + shift * HOURS_PER_DEVICE)
                                 * 100.0 / t.soll_h, 1)
        if r.util_pct >= CRIT_PCT:
            priority = "high"
            if shift_to:
                recommendation = (f"Prevent overload: move {shift} calibrations "
                                  f"from {r.month} to {shift_to} "
                                  f"(load {r.util_pct}% → ~{new_util}%)")
            else:
                recommendation = (f"No free month in horizon — add capacity "
                                  f"+{math.ceil(overload_h)} h or extend horizon")
        elif shift_to:
            priority = "medium"
            recommendation = (f"Move {shift} calibrations to {shift_to} "
                              f"(load {r.util_pct}% → ~{new_util}%)")
        else:
            priority = "medium"
            recommendation = f"Add staff / overtime — load {r.util_pct}% > 85%"
        rows.append({"MESSRAUM": r.MESSRAUM, "month": r.month,
                     "util_pct": r.util_pct, "hours": r.hours,
                     "soll_h": r.soll_h,
                     "shift_to": shift_to, "shift_devices": shift if shift_to else None,
                     "recommendation": recommendation, "priority": priority})
    return recs(rows)


# ------------------------------------------------------------ Challenge 2
kalib["BEGINN"] = pd.to_datetime(kalib["BEGINN"], errors="coerce")
kalib = kalib.dropna(subset=["BEGINN", "KUNDENNUMMER_SAP"])
kb["KundenNr"] = kb["KundenNr"].str.strip()
branche_of = dict(zip(kb["KundenNr"], kb["Branche"]))

def rfm_frame() -> pd.DataFrame:
    # RFM per spec (TASK): frequency over the last 2 years
    k2 = kalib[kalib["BEGINN"] >= TODAY - pd.DateOffset(months=24)]
    g_all = kalib.groupby("KUNDENNUMMER_SAP")["BEGINN"]
    g2 = k2.groupby("KUNDENNUMMER_SAP")["BEGINN"]
    df = pd.DataFrame({
        "last": g_all.max(),
        "freq": g2.size().reindex(g_all.size().index).fillna(0).astype(int),
    })
    df["recency"] = (TODAY - df["last"]).dt.days
    def seg(r):
        if r.recency < 180 and r.freq >= 5:
            return "Champion"
        if r.recency < 365 and r.freq >= 3:
            return "Loyal"
        if r.recency < 730:
            return "At Risk"
        return "Lost"
    df["segment"] = df.apply(seg, axis=1)
    df["churn_risk"] = df["recency"].map(
        lambda r: 95 if r > 730 else 60 if r > 365 else 30 if r > 180 else 10)
    df = df.reset_index()
    df["branche"] = df["KUNDENNUMMER_SAP"].map(branche_of).fillna("—")
    return df

RFM = rfm_frame()

def kpi_customers() -> dict:
    active = int((RFM["recency"] <= 180).sum())
    sleep = int(((RFM["recency"] > 180) & (RFM["recency"] <= 365)).sum())
    lost = int((RFM["recency"] > 365).sum())
    top = (RFM[RFM["recency"] <= 365]["branche"].value_counts().head(1))
    return {"active": active, "sleeping": sleep, "lost": lost,
            "top_branche": (top.index[0] if len(top) else "—"),
            "total": int(len(RFM))}

PERIOD_DAYS = {"day": 1, "week": 7, "month": 30}


def sales_today(period: str = "day", limit: int = 10) -> list[dict]:
    """Call list for the next day / week / month, ranked by commercial potential."""
    days = PERIOD_DAYS.get(period, 1)
    horizon = TODAY + pd.DateOffset(days=days)
    active = mm_lab[mm_lab["KUNDENNUMMER_SAP"].notna()]
    over = (active[active["due"] < TODAY].groupby("KUNDENNUMMER_SAP").size()
              .rename("overdue"))
    due = (active[(active["due"] >= TODAY) & (active["due"] < horizon)]
             .groupby("KUNDENNUMMER_SAP").size().rename("due_now"))
    fut = (active[(active["due"] >= TODAY) &
                  (active["due"] < TODAY + pd.DateOffset(months=6))]
             .groupby("KUNDENNUMMER_SAP").size().rename("zukunft"))
    df = (RFM.merge(over, on="KUNDENNUMMER_SAP", how="left")
             .merge(due, on="KUNDENNUMMER_SAP", how="left")
             .merge(fut, on="KUNDENNUMMER_SAP", how="left"))
    for c in ("overdue", "due_now", "zukunft"):
        df[c] = df[c].fillna(0)
    # audience: anything due by the horizon (incl. overdue) or churn risk
    min_risk = 95 if period == "day" else 60
    df = df[(df["overdue"] > 0) | (df["due_now"] > 0) |
            (df["churn_risk"] >= min_risk)].copy()
    df["potential_eur"] = (df["zukunft"] * AVG_CHECK_EUR +
                           (df["zukunft"] > 0) * 250).astype(int)
    freq_cap = df["freq"].clip(upper=50)
    df["score"] = (0.4 * df["potential_eur"] / 25.0 +
                   0.3 * (100 - df["churn_risk"]) +
                   0.2 * freq_cap +
                   0.1 * df["branche"].ne("—") * 100)
    df["score"] += 2.0 * df["overdue"] + 1.0 * df["due_now"]
    label = {"day": "today", "week": "this week",
             "month": "this month"}.get(period, "today")

    def reason(r):
        if r.overdue > 0:
            return "🔴 Overdue", f"{int(r.overdue)} calibrations overdue — fine risk"
        if r.due_now > 0:
            return "🟠 Due soon", f"{int(r.due_now)} calibrations due {label}"
        if r.churn_risk > 60:
            return "🟡 Churn risk", "Long inactive — win back"
        if r.recency > 365:
            return "🔵 Reactivation", "Win-back campaign"
        return "🟢 Upsell", "Offer related services"

    df[["reason", "action"]] = df.apply(lambda r: pd.Series(reason(r)), axis=1)
    top = df.sort_values("score", ascending=False).head(limit)
    out = top[["KUNDENNUMMER_SAP", "branche", "segment", "churn_risk",
               "recency", "freq", "overdue", "due_now", "zukunft",
               "potential_eur", "score", "reason", "action"]].copy()
    out["score"] = out["score"].round(1)
    out = out.rename(columns={"KUNDENNUMMER_SAP": "kunde",
                              "recency": "recency_tage",
                              "freq": "kalibrierungen",
                              "overdue": "overdue_devices",
                              "due_now": "due_devices",
                              "zukunft": "geplante_kalibrierungen"})
    return recs(out)

def churn_trend(months: int = 12) -> list[dict]:
    """Churn: customers whose last calibration was in month M and never after."""
    df = RFM.copy()
    df["m"] = df["last"].dt.strftime("%Y-%m")
    lim = (TODAY - pd.DateOffset(months=months)).strftime("%Y-%m")
    # 2-month judgment window: recent "last contacts" are not churn yet
    cut = (TODAY - pd.DateOffset(months=2)).strftime("%Y-%m")
    t = (df[(df["m"] >= lim) & (df["m"] <= cut)].groupby("m").size().reset_index(name="churned")
           .sort_values("m"))
    return recs(t.rename(columns={"m": "month"}))

def order_forecast(months: int = 6) -> list[dict]:
    """Order volume forecast: 12-month average × seasonal month index."""
    k = kalib.copy()
    k["m"] = k["BEGINN"].dt.strftime("%Y-%m")
    k["mon"] = k["BEGINN"].dt.month
    hist = k.groupby("m").size()
    # seasonal index per calendar month over the last 24 months
    k24 = k[k["BEGINN"] >= TODAY - pd.DateOffset(months=24)]
    base = k24.groupby("m").size().mean() if len(k24) else 1.0
    seas = k24.groupby("mon").size() / max(k24.groupby("mon").size().mean(), 1e-9)
    out = []
    last12 = hist.tail(12).mean() if len(hist) else 0
    for i in range(months):
        # forecast starts at the current month (inclusive) — no gap with history
        d = TODAY.replace(day=1) + pd.DateOffset(months=i)
        idx = float(seas.get(d.month, 1.0))
        out.append({"month": d.strftime("%Y-%m"),
                    "forecast": int(round(last12 * idx)),
                    "season_index": round(idx, 2)})
    hist_out = [{"month": m, "actual": int(v)} for m, v in hist.tail(months * 2).items()]
    return {"history": hist_out, "forecast": out}

def industry_gaps(limit: int = 15) -> list[dict]:
    """Device types popular in an industry and customers missing such a type."""
    m2 = mm[mm["KUNDENNUMMER_SAP"].notna() & mm["MESSMITTELTYP"].notna()].copy()
    m2["k"] = m2["KUNDENNUMMER_SAP"]
    j = m2.merge(kb, left_on="k", right_on="KundenNr", how="inner")
    tot = j.groupby("Branche")["k"].nunique()
    have = j.groupby(["Branche", "MESSMITTELTYP"])["k"].nunique().reset_index(name="mit")
    have["total"] = have["Branche"].map(tot)
    have["pct"] = (have["mit"] * 100.0 / have["total"]).round(1)
    top_types = (have[have["pct"] >= 50].sort_values(["pct"], ascending=False)
                   .groupby("Branche").head(3))
    rows = []
    for r in top_types.head(limit).itertuples():
        owners = set(j[(j["Branche"] == r.Branche) &
                       (j["MESSMITTELTYP"] == r.MESSMITTELTYP)]["k"])
        allc = set(j[j["Branche"] == r.Branche]["k"])
        missing = sorted(allc - owners)
        rows.append({"branche": r.Branche, "messmitteltyp": r.MESSMITTELTYP,
                     "verbreitung_pct": float(r.pct),
                     "kunden_ohne": len(missing),
                     "beispiel_kunden": missing[:5]})
    return rows

def kpi_forecast(months: int = 6) -> dict:
    f = forecast(months)
    m3 = forecast(min(months, 3))
    valid = f["util_pct"].dropna()
    overdue = int((mm_lab["due"] < TODAY).sum())
    return {
        "total_geraete": int(len(mm)),
        "mit_datum": int(mm["has_date"].sum()),
        "geschaetzt": int((~mm["has_date"]).sum()),
        "overdue": overdue,
        "awral_3m": int((m3["util_pct"].fillna(0) >= CRIT_PCT).sum()),
        "avg_util": round(float(valid.mean()), 1) if len(valid) else None,
        "labs": len(LABS),
    }


# ------------------------------------------------------------------- API
@app.get("/api/health")
def health():
    return {"status": "ok", "today": TODAY.strftime("%Y-%m-%d"),
            "geraete": int(len(mm)), "kunden": int(len(RFM)),
            "labs": LABS}

@app.get("/api/kpi/forecast")
def api_kpi_forecast(months: int = 6):
    return kpi_forecast(months)

@app.get("/api/forecast")
def api_forecast(months: int = Query(6, le=16), messraum: str | None = None):
    assert months in (3, 6, 16), "months must be 3/6/16"
    return recs(forecast(months, messraum))

@app.get("/api/bottlenecks")
def api_bottlenecks(months: int = 6, limit: int = 10):
    return {"items": bottlenecks(months, limit)}

@app.get("/api/heatmap")
def api_heatmap(months: int = 6):
    f = forecast(months)
    return {"months": sorted(f["month"].unique().tolist()),
            "items": recs(f[["MESSRAUM", "month", "util_pct", "status"]])}

@app.get("/api/kpi/customers")
def api_kpi_customers():
    return kpi_customers()

@app.get("/api/sales/today")
def api_sales_today(period: str = "day", limit: int = 10):
    if period not in PERIOD_DAYS:
        period = "day"
    return {"items": sales_today(period, limit), "period": period}

@app.get("/api/sales/rfm")
def api_sales_rfm(limit: int = 500):
    s = RFM.sample(n=min(limit, len(RFM)), random_state=42) if len(RFM) > limit else RFM
    cols = {"KUNDENNUMMER_SAP": "kunde", "recency": "recency_tage",
            "freq": "kalibrierungen", "segment": "segment",
            "churn_risk": "churn_risk", "branche": "branche"}
    return {"items": recs(s[list(cols)].rename(columns=cols))}

@app.get("/api/sales/churn-trend")
def api_churn_trend(months: int = 12):
    return {"items": churn_trend(months)}

@app.get("/api/sales/forecast-volumes")
def api_volumes(months: int = 6):
    return order_forecast(months)

@app.get("/api/sales/industry-gaps")
def api_gaps(limit: int = 15):
    return {"items": industry_gaps(limit)}

@app.get("/api/notifications/pending")
def api_pending():
    p = RAW_DIR.parent / "outbox.csv"
    if not p.exists():
        return {"items": []}
    try:
        df = pd.read_csv(p, sep=";")
        pend = df[df["STATUS"] == "PENDING"] if "STATUS" in df.columns else df
        return {"items": recs(pend.head(20)), "total": int(len(pend))}
    except Exception:
        return {"items": [], "total": 0}


# --------------------------------------------------------------- frontend
@app.get("/")
def index():
    return FileResponse(str(BASE_DIR / "static" / "index.html"))

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
