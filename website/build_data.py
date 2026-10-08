"""Collect model outputs + raw data into website/data/data.js for the local dashboard.

Run from the project root:  .venv/bin/python website/build_data.py
Needs: forecast_data_2027/, challenge2_model/forecast/, challenge2_model/output/, data/.
Writes window.DATA = {...} so index.html opens by double-click (no server needed).
"""
import json
import math
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "website" / "data" / "data.js"
TODAY = pd.Timestamp("2026-10-08")
RNG = np.random.default_rng(42)

LABS = {
    "MR1": "Endmaße", "MR2": "Drehmomente", "MR3": "Messuhren", "MR4": "Lohnmessung",
    "MR5": "Bügelmessschrauben", "MR6": "Messschieber", "MR7": "Lehren", "MR8": "Temperatur",
    "MR9": "Elektro", "MR11": "KMG", "MR12": "Druck", "MR14": "Waage",
}
KAL = f"'{ROOT / 'data/KALIBRIERUNGEN.parquet'}'"
SOLL = f"'{ROOT / 'data/Soll-Kapa.parquet'}'"


def records(df, digits=2):
    df = df.copy()
    for c in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            df[c] = df[c].dt.strftime("%Y-%m-%d")
        elif pd.api.types.is_float_dtype(df[c]):
            df[c] = df[c].round(digits)
    return json.loads(df.to_json(orient="records", force_ascii=False))


# ---------------------------------------------------------------- forecast (challenge 1 + 2)
fc_dir = ROOT / "forecast_data_2027"
daily = pd.read_csv(fc_dir / "forecast_capacity_20270104_20271231.csv")
room_week = pd.read_csv(fc_dir / "forecast_room_capacity_2027-01-04_2027-12-27.csv")
room_week = room_week[room_week.MESSRAUM.isin(LABS)][[
    "week_start", "kalenderwoche", "MESSRAUM", "room_forecast", "forecast_workload_hours",
    "available_hours", "hours_per_service", "load_pct", "status"]]
room_week.columns = ["week", "kw", "room", "services", "need_h", "avail_h", "h_per_service", "load", "status"]

c2 = ROOT / "challenge2_model"
monthly = pd.read_csv(c2 / "forecast/monthly_forecast.csv")
industry = pd.read_csv(c2 / "forecast/industry_outlook.csv")
due_pipe = pd.read_csv(c2 / "forecast/due_pipeline.csv")
metrics = json.loads((c2 / "output/metrics.json").read_text())

# ---------------------------------------------------------------- customers
cust = pd.read_csv(c2 / "forecast/customer_forecast.csv")
main_room = duckdb.sql(f"""
    select KUNDENNUMMER_SAP::varchar customer, MESSRAUM room, count(*) n
    from {KAL} where BEGINN >= '2025-10-01' and MESSRAUM in ({",".join(f"'{r}'" for r in LABS)})
    group by 1, 2 qualify row_number() over (partition by customer order by n desc) = 1
""").df()
cust["customer"] = cust.customer.astype(str)
cust = cust.merge(main_room[["customer", "room"]], on="customer", how="left")
month_cols = [c for c in cust.columns if c[:2] == "20" and len(c) == 7]
cust["next6"] = cust[month_cols].values.tolist()
cust = cust[["customer", "branche", "action", "churn_prob", "volume_at_risk", "cal_12m", "expected_cal_6m",
             "due_next_3m", "overdue_open", "months_since_last_cal", "top_missing_group", "why", "room", "next6"]]
cust["months_since_last_cal"] = cust.months_since_last_cal.round(1)

# ---------------------------------------------------------------- recurring / seasonal orders
# Per customer and calendar month: did they send in every year we can see, and is it a peak?
season = duckdb.sql(f"""
    with m as (
        select KUNDENNUMMER_SAP::varchar customer, year(BEGINN) y, month(BEGINN) mo, count(*) n,
               mode(MESSRAUM) room, mode(MESSMITTELGRUPPE) grp
        from {KAL} where BEGINN >= '2024-01-01' and BEGINN < '2026-10-01' group by all),
    tot as (select customer, sum(n) / 33.0 avg_month from m group by 1)
    select m.customer, mo, count(*) years_seen, round(avg(n)) avg_n, min(n) min_n,
           list(y order by y) yrs, list(n order by y) cnts, mode(room) room, mode(grp) grp,
           round(avg(n) / any_value(tot.avg_month), 2) peak_ratio
    from m join tot using (customer) group by m.customer, mo
""").df()
season = season.rename(columns={"yrs": "years", "cnts": "counts"})
season["years_possible"] = np.where(season.mo >= 10, 2, 3)
rec = season[(season.years_seen == season.years_possible) & (season.min_n >= 10) & (season.peak_ratio >= 1.3)].copy()
rec["next_date"] = [pd.Timestamp(2026 if m >= 10 else 2027, m, 1) for m in rec.mo]
rec = rec[rec.next_date > TODAY - pd.Timedelta(days=7)]
rec["contact_by"] = rec.next_date - pd.Timedelta(weeks=7)
rec["kind"] = np.where(rec.peak_ratio >= 2.5, "seasonal peak", "yearly recurring")
rec = rec.merge(cust[["customer", "branche", "churn_prob", "cal_12m"]], on="customer", how="left")
rec = rec.sort_values(["next_date", "avg_n"], ascending=[True, False])
rec = rec[["customer", "branche", "mo", "next_date", "contact_by", "avg_n", "years", "counts", "room", "grp",
           "peak_ratio", "kind", "churn_prob", "cal_12m"]]
rec["years"] = rec.years.map(list)
rec["counts"] = rec.counts.map(list)

# ---------------------------------------------------------------- labs: skills + real vacation pattern
groups = duckdb.sql(f"""
    select MESSRAUM room, MESSMITTELGRUPPE grp, count(*) n from {KAL}
    where MESSRAUM in ({",".join(f"'{r}'" for r in LABS)}) group by 1, 2
    qualify row_number() over (partition by room order by n desc) <= 4
""").df()
staff = duckdb.sql(f"""
    select MESSRAUM room,
           sum("SOLL Anwesend STD") / count(distinct "Kalendertag (Intervall)") / 7.7 fte
    from {SOLL} where year("Kalendertag (Intervall)") = 2026 group by 1
""").df().set_index("room").fte
vac = duckdb.sql(f"""
    select MESSRAUM room, month("Kalendertag (Intervall)") mo, sum("SOLL Urlaub STD") h
    from {SOLL} group by all
""").df()
labs = []
for r, name in LABS.items():
    g = groups[groups.room == r].sort_values("n", ascending=False).grp.tolist()
    v = vac[vac.room == r].groupby("mo").h.sum().reindex(range(1, 13), fill_value=0)
    labs.append({"id": r, "name": name, "fte": round(float(staff.get(r, 1)), 1), "groups": g,
                 "vacation_profile": (v / max(v.sum(), 1)).round(3).tolist()})

# ---------------------------------------------------------------- SAMPLE employee roster (no per-person data in the DB)
# Head count per lab = planned FTE 2026. Names, cross-skills, expiry and vacation weeks are generated,
# with vacation spread like each lab's real monthly vacation hours 2024-2026.
weeks = sorted(room_week.week.unique())
week_month = {w: int(w[5:7]) for w in weeks}
employees = []
first = ["Anna", "Ben", "Clara", "David", "Elif", "Felix", "Greta", "Hannes", "Ida", "Jonas", "Katrin", "Lukas",
         "Mia", "Nils", "Olga", "Paul", "Rita", "Sven", "Tara", "Uwe", "Vera", "Wim", "Yara", "Zoe"]
names = iter(RNG.permutation([f"{f} {l}." for f in first for l in "BKMRSTW"]).tolist())
for lab in labs:
    n = max(1, round(lab["fte"]))
    prof = np.array(lab["vacation_profile"]) + 0.02
    wprob = np.array([prof[week_month[w] - 1] for w in weeks])
    wprob = wprob / wprob.sum()
    for i in range(n):
        skills = lab["groups"][: max(1, len(lab["groups"]) - (i % 2))]
        cross = []
        if RNG.random() < 0.35:
            other = RNG.choice([l for l in labs if l["id"] != lab["id"]])
            cross = [other["groups"][0]] if other["groups"] else []
        off = set()
        while len(off) < 6:  # 30 vacation days = 6 weeks, in blocks of 1-3 weeks
            start = RNG.choice(len(weeks), p=wprob)
            for k in range(int(RNG.integers(1, 4))):
                if start + k < len(weeks) and len(off) < 6:
                    off.add(weeks[start + k])
        quals = []
        for s in skills + cross:
            exp = TODAY + pd.Timedelta(days=int(RNG.integers(20, 900)))
            quals.append({"group": s, "expires": exp.strftime("%Y-%m-%d"), "cross": s in cross})
        employees.append({"name": next(names), "room": lab["id"], "quals": quals, "vacation": sorted(off)})

# ---------------------------------------------------------------- write
data = {
    "meta": {"today": TODAY.strftime("%Y-%m-%d"), "built": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"),
             "churn_auc": metrics[metrics["chosen_variant"]]["churn_auc_big"],
             "churn_auc_naive": metrics["naive"]["churn_auc_big"],
             "top100_hit": metrics[metrics["chosen_variant"]]["churn_top100_hit_big"],
             "volume_acc": metrics[metrics["chosen_variant"]]["volume_acc"],
             "interval_mae_days": metrics.get("interval_mae_days"), "median_lead_days": 16,
             "sample": ["employees"]},
    "daily": records(daily),
    "roomWeek": records(room_week),
    "monthly": records(monthly),
    "industry": records(industry, 3),
    "duePipeline": records(due_pipe),
    "customers": records(cust, 3),
    "recurring": records(rec),
    "labs": labs,
    "employees": employees,
}
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text("window.DATA = " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n", encoding="utf-8")
print(f"wrote {OUT.relative_to(ROOT)}  {OUT.stat().st_size / 1e6:.1f} MB  "
      f"customers={len(cust)} recurring={len(rec)} employees={len(employees)}")
