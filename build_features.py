"""Build model-ready feature tables (one row = one training vector) for both challenges.

Reads data/*.parquet (from export_db.py) and writes features/*.parquet:

  shared_due_events.parquet       one row per calibration: stated/imputed interval, due date, next actual calibration
  c1_room_month.parquet           Challenge 1: lab (MESSRAUM) x month, targets at +3/+6/+12 months
  c1_future_due.parquet           Challenge 1: known due counts per lab for the coming 12 months (forecast input)
  c2_instrument_interval.parquet  Challenge 2: per calibration -> days until the next calibration (calibration requirement)
  c2_customer_snapshot.parquet    Challenge 2: customer x monthly snapshot, targets: volume / inactivity / due follow-through
  c2_customer_portfolio.parquet   Challenge 2: per customer instrument-group mix vs. industry average (whitespace)

Usage: .venv/bin/python build_features.py
"""
import os

import numpy as np
import pandas as pd

DATA = "data"
OUT = "features"
# Data runs to 2026-09-25; months from here on are incomplete and never used as targets.
DATA_END = pd.Timestamp("2026-09-01")
HORIZONS = [3, 6, 12]
UNIT_DAYS = {1: 365.25, 2: 30.4375, 3: 7.0, 4: 1.0}  # PRUEFINTERVALL_EINHEIT: years, months, weeks, days
MAX_INTERVAL_D = 3653
NIO = {"NICHT_EINSATZFAEHIG"}
TOP_GROUPS = 40


def load(name):
    return pd.read_parquet(os.path.join(DATA, f"{name}.parquet"))


def month(s):
    return s.dt.to_period("M").dt.to_timestamp()


def interval_days(value, unit):
    days = value * unit.map(UNIT_DAYS)
    return days.where((days > 0) & (days <= MAX_INTERVAL_D))  # > 10 years is a data-entry error


# ---------------------------------------------------------------- shared
def build_due_events(kal, mm, kb):
    """Every calibration with its due date; missing intervals are imputed from observed behaviour."""
    ev = kal[["MESSMITTEL_UUID", "KUNDENNUMMER_SAP", "ENDE", "BEWERTUNG", "PRUEFUNGSART",
              "PRUEFINTERVALL", "PRUEFINTERVALL_EINHEIT", "MESSMITTELGRUPPE", "MESSMITTELTYP", "MESSRAUM"]].copy()
    ev = ev.dropna(subset=["MESSMITTEL_UUID", "ENDE"]).sort_values(["MESSMITTEL_UUID", "ENDE"])
    ev["stated_interval_d"] = interval_days(ev.PRUEFINTERVALL, ev.PRUEFINTERVALL_EINHEIT)
    ev["next_cal"] = ev.groupby("MESSMITTEL_UUID").ENDE.shift(-1)
    ev["prev_cal"] = ev.groupby("MESSMITTEL_UUID").ENDE.shift(1)
    ev["gap_d"] = (ev.next_cal - ev.ENDE).dt.days
    ev.loc[ev.gap_d < 7, "gap_d"] = np.nan  # same-week re-checks are not a recurrence
    ev["cal_no"] = ev.groupby("MESSMITTEL_UUID").cumcount() + 1

    # The register's last test predates the 2024+ calibration history, so add it as an extra gap source.
    reg = mm[["MESSMITTEL_UUID", "DATUM_LETZTE_PRUEFUNG", "PRUEFINTERVALL", "EINHEIT_PRUEFINTERVALL"]].copy()
    reg["reg_interval_d"] = interval_days(reg.PRUEFINTERVALL, reg.EINHEIT_PRUEFINTERVALL)
    ev = ev.merge(reg[["MESSMITTEL_UUID", "reg_interval_d"]], on="MESSMITTEL_UUID", how="left")

    # Learned "real" interval, from most to least specific: same customer+type, type, group.
    g = ev.dropna(subset=["gap_d"])
    lvl = [["KUNDENNUMMER_SAP", "MESSMITTELTYP"], ["MESSMITTELTYP"], ["MESSMITTELGRUPPE"]]
    ev["learned_interval_d"] = np.nan
    for keys in lvl:
        med = g.groupby(keys).gap_d.agg(["median", "size"])
        med = med[med["size"] >= 5]["median"].rename("m").reset_index()
        ev = ev.merge(med, on=keys, how="left")
        ev["learned_interval_d"] = ev.learned_interval_d.fillna(ev.pop("m"))
    ev["learned_interval_d"] = ev.learned_interval_d.fillna(g.gap_d.median())

    known = ev.stated_interval_d.fillna(ev.reg_interval_d)
    ev["interval_imputed"] = known.isna()
    ev["interval_d"] = known.fillna(ev.learned_interval_d)
    ev["due"] = ev.ENDE + pd.to_timedelta(ev.interval_d, unit="D")
    ev["nio"] = ev.BEWERTUNG.isin(NIO)
    ev["dakks"] = ev.PRUEFUNGSART.eq("DAkkS")
    ev = ev.merge(kb.rename(columns={"KundenNr": "KUNDENNUMMER_SAP", "Branche": "branche"}),
                  on="KUNDENNUMMER_SAP", how="left")
    return ev


def register_future_due(mm, ev):
    """Next due date for every instrument in the register; imputes it when not recorded."""
    r = mm[["MESSMITTEL_UUID", "KUNDENNUMMER_SAP", "MESSRAUM", "MESSMITTELGRUPPE", "MESSMITTELTYP",
            "DATUM_LETZTE_PRUEFUNG", "DATUM_NAECHSTE_PRUEFUNG", "PRUEFINTERVALL", "EINHEIT_PRUEFINTERVALL",
            "FAELLIGKEIT_STOP", "LETZTE_BEWERTUNG"]].copy()
    learned = ev.drop_duplicates("MESSMITTEL_UUID", keep="last").set_index("MESSMITTEL_UUID").learned_interval_d
    stated = interval_days(r.PRUEFINTERVALL, r.EINHEIT_PRUEFINTERVALL)
    imputed_due = r.DATUM_LETZTE_PRUEFUNG + pd.to_timedelta(
        stated.fillna(r.MESSMITTEL_UUID.map(learned)).fillna(ev.learned_interval_d.median()), unit="D")
    r["due_imputed"] = r.DATUM_NAECHSTE_PRUEFUNG.isna()
    r["due"] = r.DATUM_NAECHSTE_PRUEFUNG.fillna(imputed_due)
    # Due dates centuries out are data-entry errors; instruments flagged stop or scrapped are not coming back.
    r = r[(r.due < "2035-01-01") & (r.FAELLIGKEIT_STOP != True) & (r.LETZTE_BEWERTUNG != "NICHT_EINSATZFAEHIG")]
    return r


# ---------------------------------------------------------------- challenge 1
IST_COLS = {"IST Sonstiges STD": "ist_sonstiges_h", "IST Krank STD": "ist_krank_h", "IST Urlaub STD": "ist_urlaub_h",
            "IST Anwesend STD": "ist_anwesend_h", "IST-Stunden Gesamtergebnis  STD": "ist_total_h"}
SOLL_COLS = {"SOLL Sonstiges STD": "soll_sonstiges_h", "SOLL Krank STD": "soll_krank_h", "SOLL Urlaub STD": "soll_urlaub_h",
             "SOLL Anwesend STD": "soll_anwesend_h", "Gesamtergebnis STD": "soll_total_h"}


def hours_by_room_month(df, date_col, cols):
    df = df.rename(columns=cols)
    df["month"] = month(pd.to_datetime(df[date_col]))
    return df.groupby(["MESSRAUM", "month"])[list(cols.values())].sum(min_count=1)


def build_c1(ev, mm, dl, az, ap, soll, ist, reg_due):
    rooms = sorted(r for r in ev.MESSRAUM.dropna().unique() if r.startswith("MR"))
    months = pd.date_range("2024-01-01", DATA_END - pd.offsets.MonthBegin(1), freq="MS")
    idx = pd.MultiIndex.from_product([rooms, months], names=["MESSRAUM", "month"])
    room_of = mm.set_index("MESSMITTEL_UUID").MESSRAUM

    # Demand: actual workload in hours (service minutes from ArtikelnummerZeit) and calibration counts.
    d = dl[dl.STATUS_DIENSTLESTUNG.eq("ERBRACHT")].merge(
        az[["Artikelnummer", "BearbeitungszeitMin"]], left_on="KATALOGNUMMER", right_on="Artikelnummer", how="left")
    d["MESSRAUM"] = d.UUID_KALIBRIERGEGENSTAND.map(room_of)
    d["month"] = month(d.QUITTIERT_AM)
    work = d.groupby(["MESSRAUM", "month"]).agg(workload_h=("BearbeitungszeitMin", lambda x: x.sum() / 60),
                                                n_services=("BearbeitungszeitMin", "size"),
                                                n_services_no_time=("BearbeitungszeitMin", lambda x: x.isna().sum()))
    e = ev.assign(month=month(ev.ENDE))
    cal = e.groupby(["MESSRAUM", "month"]).agg(n_cal=("nio", "size"), nio_rate=("nio", "mean"),
                                               dakks_share=("dakks", "mean"), first_cal_share=("cal_no", lambda x: (x == 1).mean()))
    a = ap.assign(MESSRAUM=ap.KALIBRIERGEGENSTANDUUID.map(room_of), month=month(ap.DATUM_ERSTELLT))
    orders = a.groupby(["MESSRAUM", "month"]).agg(n_orders_in=("UUID", "size"),
                                                  hub_share=("AUFTRAGSPOSITIONSQUELLE", lambda x: x.eq("HUB").mean()))
    # Expected demand: instruments falling due (from calibration history + register), incl. imputed due dates.
    due_ev = ev.assign(month=month(ev.due)).groupby(["MESSRAUM", "month"]).agg(
        n_due=("due", "size"), n_due_imputed=("interval_imputed", "sum"))

    # Supply: staff hours actual (Ist) and planned (Soll); company-wide Springer/Qualifizierung pools.
    ist_h = hours_by_room_month(ist, "Kalendertag", IST_COLS)
    soll_h = hours_by_room_month(soll, "Kalendertag (Intervall)", SOLL_COLS)
    pools = ist_h.reset_index()
    pools = pools[pools.MESSRAUM.isin(["Springer", "Qualifizierung"])].pivot(index="month", columns="MESSRAUM", values="ist_anwesend_h")
    pools.columns = [f"pool_{c.lower()}_anwesend_h" for c in pools.columns]

    df = pd.DataFrame(index=idx).join([work, cal, orders, due_ev, ist_h, soll_h]).reset_index()
    count_cols = ["workload_h", "n_services", "n_services_no_time", "n_cal", "n_orders_in", "n_due", "n_due_imputed"]
    df[count_cols] = df[count_cols].fillna(0)
    df = df.merge(pools, left_on="month", right_index=True, how="left")
    df["ist_capacity_h"] = df.ist_anwesend_h
    df["sick_rate"] = df.ist_krank_h / df.ist_total_h
    df["vacation_rate"] = df.ist_urlaub_h / df.ist_total_h
    df["fte"] = df.ist_total_h / (7.7 * np.busday_count(df.month.values.astype("M8[D]"),
                                                           (df.month + pd.offsets.MonthBegin(1)).values.astype("M8[D]")))
    df["utilisation"] = df.workload_h / df.ist_capacity_h.replace(0, np.nan)
    df["soll_utilisation"] = df.workload_h / df.soll_anwesend_h.replace(0, np.nan)
    df["year"] = df.month.dt.year
    df["month_of_year"] = df.month.dt.month
    df["month_sin"] = np.sin(2 * np.pi * df.month_of_year / 12)
    df["month_cos"] = np.cos(2 * np.pi * df.month_of_year / 12)
    df["workdays"] = np.busday_count(df.month.values.astype("M8[D]"), (df.month + pd.offsets.MonthBegin(1)).values.astype("M8[D]"))

    g = df.groupby("MESSRAUM")
    for c in ["workload_h", "n_cal", "n_orders_in", "utilisation", "sick_rate"]:
        for l in [1, 2, 3, 6, 12]:
            df[f"{c}_lag{l}"] = g[c].shift(l)
        df[f"{c}_roll3"] = g[c].transform(lambda s: s.shift(1).rolling(3).mean())
    # Due counts are known ahead of time, so the future values are legitimate features.
    for h in HORIZONS:
        df[f"n_due_lead{h}"] = g.n_due.shift(-h)
        df[f"soll_anwesend_h_lead{h}"] = g.soll_anwesend_h.shift(-h)
        df[f"target_workload_h_{h}m"] = g.workload_h.shift(-h)
        df[f"target_utilisation_{h}m"] = g.utilisation.shift(-h)

    # Coming 12 months of known due instruments per lab, for scoring the forecast.
    fut = reg_due[reg_due.due >= DATA_END].assign(month=month(reg_due.due))
    fut = fut[fut.month < DATA_END + pd.DateOffset(months=13)]
    fut = fut.groupby(["MESSRAUM", "month"]).agg(n_due=("due", "size"), n_due_imputed=("due_imputed", "sum")).reset_index()
    return df, fut


# ---------------------------------------------------------------- challenge 2
def build_c2_interval(ev):
    """Per calibration: predict days until the instrument comes back (target_gap_d). NaN target = still open."""
    x = ev[["MESSMITTEL_UUID", "KUNDENNUMMER_SAP", "branche", "ENDE", "MESSMITTELGRUPPE", "MESSMITTELTYP", "MESSRAUM",
            "PRUEFUNGSART", "BEWERTUNG", "stated_interval_d", "reg_interval_d", "interval_imputed", "interval_d",
            "learned_interval_d", "cal_no", "prev_cal", "due", "next_cal", "gap_d"]].copy()
    x["prev_gap_d"] = (x.ENDE - x.prev_cal).dt.days
    x["month_of_year"] = x.ENDE.dt.month
    # Customer punctuality so far (expanding, only past information): mean of actual gap / planned interval.
    x = x.sort_values("ENDE")
    ratio = (x.prev_gap_d / x.interval_d).where(x.prev_gap_d.notna())
    x["cust_ratio_hist"] = ratio.groupby(x.KUNDENNUMMER_SAP).transform(lambda s: s.expanding().mean().shift(1))
    x["target_gap_d"] = x.gap_d
    x["target_late_d"] = (x.next_cal - x.due).dt.days
    # Only gaps that could have been observed before the data ends are trustworthy labels.
    x["label_censored"] = x.next_cal.isna()
    return x.drop(columns=["prev_cal", "gap_d"])


def build_c2_portfolio(mm, kb):
    m = mm.merge(kb, left_on="KUNDENNUMMER_SAP", right_on="KundenNr", how="left")
    top = m.MESSMITTELGRUPPE.value_counts().index[:TOP_GROUPS]
    grp = m.MESSMITTELGRUPPE.where(m.MESSMITTELGRUPPE.isin(top), "OTHER")
    cnt = pd.crosstab(m.KUNDENNUMMER_SAP, grp)
    share = cnt.div(cnt.sum(axis=1), axis=0)
    branche = kb.set_index("KundenNr").Branche.reindex(share.index).fillna("UNBEKANNT")
    centroid = share.groupby(branche).transform("mean")
    # Groups the industry typically has (>= 5% share) but this customer does not calibrate here = upsell potential.
    gap = (centroid - share).clip(lower=0).where(centroid >= 0.05, 0)
    cos = (share * centroid).sum(axis=1) / (np.linalg.norm(share, axis=1) * np.linalg.norm(centroid, axis=1))
    out = share.add_prefix("share_")
    out["n_instruments"] = cnt.sum(axis=1)
    out["n_groups"] = (cnt > 0).sum(axis=1)
    out["branche"] = branche
    out["industry_similarity"] = cos
    out["whitespace_score"] = gap.sum(axis=1)
    out["top_missing_group"] = gap.idxmax(axis=1).where(gap.max(axis=1) > 0)
    return out.reset_index().rename(columns={"KUNDENNUMMER_SAP": "customer"})


def build_c2_snapshot(ev, ap, mm, portfolio):
    cust_of = mm.set_index("MESSMITTEL_UUID").KUNDENNUMMER_SAP
    e = ev.assign(m=month(ev.ENDE))
    monthly = e.groupby(["KUNDENNUMMER_SAP", "m"]).agg(cal=("nio", "size"), nio=("nio", "sum"), dakks=("dakks", "sum"),
                                                       instr=("MESSMITTEL_UUID", "nunique"))
    a = ap.assign(c=ap.KALIBRIERGEGENSTANDUUID.map(cust_of), m=month(ap.DATUM_ERSTELLT))
    monthly = monthly.join(a.groupby(["c", "m"]).agg(orders=("UUID", "size"),
                                                     hub=("AUFTRAGSPOSITIONSQUELLE", lambda x: x.eq("HUB").sum()))
                           .rename_axis(["KUNDENNUMMER_SAP", "m"]), how="outer").fillna(0)
    all_m = pd.date_range("2024-01-01", "2026-10-01", freq="MS")
    wide = {c: monthly[c].unstack("m").reindex(columns=all_m, fill_value=0).fillna(0) for c in monthly.columns}

    snaps = pd.date_range("2024-07-01", "2026-10-01", freq="MS")  # >= 6 months of history; last row = today
    rows = []
    for T in snaps:
        past = lambda c, n: wide[c].loc[:, (all_m < T) & (all_m >= T - pd.DateOffset(months=n))].sum(axis=1)
        fut = lambda c, n: wide[c].loc[:, (all_m >= T) & (all_m < T + pd.DateOffset(months=n))].sum(axis=1)
        f = pd.DataFrame({"snapshot": T, **{f"cal_{n}m": past("cal", n) for n in (1, 3, 6, 12)},
                          "orders_12m": past("orders", 12), "hub_share_12m": past("hub", 12) / past("orders", 12),
                          "nio_rate_12m": past("cal", 12).pipe(lambda d: past("nio", 12) / d),
                          "dakks_share_12m": past("dakks", 12) / past("cal", 12)})
        seen = wide["cal"].loc[:, all_m < T]
        last = seen.gt(0).iloc[:, ::-1].idxmax(axis=1).where(seen.gt(0).any(axis=1))
        f["months_since_last_cal"] = ((T - last).dt.days / 30.44)
        f["months_active_12m"] = (wide["cal"].loc[:, (all_m < T) & (all_m >= T - pd.DateOffset(months=12))] > 0).sum(axis=1)
        f["trend_3m_vs_12m"] = f.cal_3m / (f.cal_12m / 4).replace(0, np.nan)

        # Due-based features use only calibrations that happened before T.
        known = ev[ev.ENDE < T]
        open_ = known[known.next_cal.isna() | (known.next_cal >= T)]
        for n in (3, 6):
            win = open_[(open_.due >= T) & (open_.due < T + pd.DateOffset(months=n))]
            f[f"due_next_{n}m"] = win.groupby("KUNDENNUMMER_SAP").size()
        f["overdue_open"] = open_[(open_.due < T) & (open_.due >= T - pd.DateOffset(months=12))].groupby("KUNDENNUMMER_SAP").size()
        f["instruments_known"] = known.groupby("KUNDENNUMMER_SAP").MESSMITTEL_UUID.nunique()

        # Targets (only where the window is fully inside the data).
        for n in (3, 6):
            if T + pd.DateOffset(months=n) <= DATA_END:
                f[f"target_cal_next_{n}m"] = fut("cal", n)
                f[f"target_orders_next_{n}m"] = fut("orders", n)
        if T + pd.DateOffset(months=6) <= DATA_END:
            win = open_[(open_.due >= T) & (open_.due < T + pd.DateOffset(months=6))]
            back = win.next_cal < T + pd.DateOffset(months=8)  # 2 months grace
            f["target_due_return_rate_6m"] = back.groupby(win.KUNDENNUMMER_SAP).mean()
            f["target_inactive_next_6m"] = (fut("cal", 6) == 0).astype(float)
        rows.append(f.rename_axis("customer").reset_index())

    s = pd.concat(rows, ignore_index=True)
    for c in ["due_next_3m", "due_next_6m", "overdue_open", "instruments_known"]:
        s[c] = s[c].fillna(0)
    s = s[(s.cal_12m > 0) | (s.instruments_known > 0)]  # customers with any history before the snapshot
    s["month_of_year"] = s.snapshot.dt.month
    keep = ["customer", "branche", "n_instruments", "n_groups", "industry_similarity", "whitespace_score", "top_missing_group"]
    return s.merge(portfolio[keep], on="customer", how="left")


def main():
    os.makedirs(OUT, exist_ok=True)
    kal, mm, dl, ap = load("KALIBRIERUNGEN"), load("MESSMITTEL"), load("DIENSTLEISTUNGEN"), load("AUFTRAGSPOSITIONEN")
    az, kb, soll, ist = load("ArtikelnummerZeit"), load("Kunde_Branche"), load("Soll-Kapa"), load("Ist_Stunden_24-26")

    ev = build_due_events(kal, mm, kb)
    reg_due = register_future_due(mm, ev)
    c1, c1_fut = build_c1(ev, mm, dl, az, ap, soll, ist, reg_due)
    portfolio = build_c2_portfolio(mm, kb)
    outputs = {
        "shared_due_events": ev,
        "c1_room_month": c1,
        "c1_future_due": c1_fut,
        "c2_instrument_interval": build_c2_interval(ev),
        "c2_customer_portfolio": portfolio,
        "c2_customer_snapshot": build_c2_snapshot(ev, ap, mm, portfolio),
    }
    for name, df in outputs.items():
        df.to_parquet(os.path.join(OUT, f"{name}.parquet"), index=False)
        print(f"{name}: {len(df):,} rows x {df.shape[1]} cols")


if __name__ == "__main__":
    main()
