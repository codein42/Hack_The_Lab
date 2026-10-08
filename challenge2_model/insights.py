"""Print the key insights from the trained Challenge 2 model.

    .venv/bin/python challenge2_model/insights.py

Reads challenge2_model/output/scores_today.csv (written by train.py) and writes
challenge2_model/output/insights_by_industry.csv for slides / Excel.
"""
from pathlib import Path
import pandas as pd

OUT = Path(__file__).resolve().parent / "output"
s = pd.read_csv(OUT / "scores_today.csv", dtype={"customer": str})
big = s[s.cal_12m >= 10]                       # customers worth a call (>= 10 calibrations a year)
pct = lambda a, b: f"{a / b:.0%}"


def line(title):
    print(f"\n{title}\n" + "-" * len(title))


line("1. Revenue at risk")
risky = big[big.churn_prob >= 0.5]
print(f"Active customers: {len(s):,}  (of which {len(big):,} send 10+ instruments a year)")
print(f"Likely to stop (churn >= 50 %): {len(risky):,} customers = {risky.cal_12m.sum():,.0f} calibrations a year "
      f"({pct(risky.cal_12m.sum(), big.cal_12m.sum())} of volume)")
srt = big.sort_values("volume_at_risk", ascending=False)
cum = srt.volume_at_risk.cumsum() / srt.volume_at_risk.sum()
print(f"Half of all volume at risk sits with just {int((cum < 0.5).sum()) + 1} customers -> a small call list covers most of it")

line("2. What predicts churn")
for label, col, bins in [("Months since last calibration", "months_since_last_cal", [-1, 1, 3, 6, 99]),
                         ("Last 3 months vs normal", None, None)]:
    if col:
        g = big.groupby(pd.cut(big[col], bins), observed=True).churn_prob.agg(["count", "mean"])
    else:
        ratio = big.cal_3m / (big.cal_12m / 4)
        g = big.groupby(pd.cut(ratio, [-0.01, 0.0001, 0.5, 2, 99], labels=["none", "< half", "normal", "> double"]),
                        observed=True).churn_prob.agg(["count", "mean"])
    print(f"{label}:")
    for k, r in g.iterrows():
        print(f"   {str(k):<12} {int(r['count']):>5} customers   avg churn risk {r['mean']:.0%}")

line("3. Reminder potential")
print(f"Instruments overdue and not received: {s.overdue_open.sum():,.0f}")
print(f"Instruments due in the next 3 months: {s.due_next_3m.sum():,.0f}")
print(f"Median expected return rate (customers 10+/yr): {big.expected_return_rate.median():.0%} of due instruments come back on time")

line("4. Cross-sell: what customers calibrate elsewhere")
w = big[big.whitespace_score > 0.3]
print(f"{len(w):,} customers miss a group their industry usually sends. Most common gaps:")
for g, n in w.top_missing_group.value_counts().head(5).items():
    print(f"   {g:<25} {n:>4} customers")

line("5. By industry (customers 10+/yr)")
ind = big.groupby("branche").agg(customers=("customer", "count"), calibrations_12m=("cal_12m", "sum"),
                                 volume_at_risk=("volume_at_risk", "sum"),
                                 high_risk_customers=("churn_prob", lambda x: int((x >= 0.5).sum())),
                                 overdue=("overdue_open", "sum"))
ind["share_at_risk"] = (ind.volume_at_risk / ind.calibrations_12m).round(3)
ind = ind.sort_values("volume_at_risk", ascending=False).round(1)
print(ind.to_string())
ind.to_csv(OUT / "insights_by_industry.csv", sep=";", decimal=",")

line("6. Top 10 customers to call (by volume at risk)")
print(srt.head(10)[["customer", "branche", "churn_prob", "volume_at_risk", "cal_12m", "cal_3m", "overdue_open"]]
      .to_string(index=False))
print(f"\nSaved {OUT.name}/insights_by_industry.csv")
