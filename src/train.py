"""Train the two prototype models and print how accurate they are.

Needs the feature tables from build_features.py (features/*.parquet).

    .venv/bin/python build_features.py      # once (about 1 min)
    .venv/bin/python src/train.py           # trains, tests, writes predictions

How accuracy is measured: train on older months, test on later months the
model never saw (like using it for real), and compare with a simple
"same as last year / last 6 months" guess so you can see the model adds value.

Outputs:
    build/c1_forecast.csv     workload + utilisation forecast per lab, +3/+6/+12 months
    build/c2_call_list.csv    all customers worth a call, ranked within action type
    build/c2_today.csv        today's mixed call list (20 customers)
    build/metrics.json        the accuracy numbers (for the dashboard)
"""
from pathlib import Path
import json
import warnings
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.metrics import roc_auc_score

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
FEAT, BUILD = ROOT / "features", ROOT / "build"
BUILD.mkdir(exist_ok=True)
metrics = {}


def wape_accuracy(y, p) -> float:
    """1 - weighted absolute % error. 0.85 = forecast is off by 15 % in total."""
    y, p = np.asarray(y, float), np.asarray(p, float)
    return float(1 - np.abs(y - p).sum() / np.abs(y).sum())


# =================================================================== CHALLENGE 1
print("=" * 70 + "\nCHALLENGE 1 - workload forecast per lab\n" + "=" * 70)
c1 = pd.read_parquet(FEAT / "c1_room_month.parquet").sort_values(["MESSRAUM", "month"])
c1["lab"] = c1.MESSRAUM.astype("category")
TARGETS = [c for c in c1.columns if c.startswith("target_")]
LEADS = [c for c in c1.columns if "_lead" in c]
BASE = [c for c in c1.columns if c not in TARGETS + LEADS + ["MESSRAUM", "month", "lab", "year"]]

TEST_FROM = pd.Timestamp("2026-01-01")          # test on target months Jan 2026 onwards
forecast = []
for h in (3, 6, 12):
    feats = BASE + ["lab", f"n_due_lead{h}", f"soll_anwesend_h_lead{h}"]
    target = f"target_workload_h_{h}m"
    d = c1.dropna(subset=[target]).copy()
    d["target_month"] = d.month + pd.DateOffset(months=h)
    train, test = d[d.target_month < TEST_FROM], d[d.target_month >= TEST_FROM]
    feats = [f for f in feats if train[f].notna().any()]      # drop features empty in training data

    model = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, min_samples_leaf=5,
                                          categorical_features="from_dtype", random_state=0)
    model.fit(train[feats], train[target])

    if len(test):
        pred = model.predict(test[feats]).clip(0)
        # naive guess: same lab, same month one year earlier (else the last known month)
        naive = test.workload_h_lag12.fillna(test.workload_h) if h < 12 else test.workload_h
        acc, acc_naive = wape_accuracy(test[target], pred), wape_accuracy(test[target], naive)
        print(f"+{h:>2} months  accuracy {acc:6.1%}   (simple guess {acc_naive:6.1%})   "
              f"trained on {len(train)} rows, tested on {len(test)}")
        metrics[f"c1_accuracy_{h}m"] = round(acc, 3)
        metrics[f"c1_naive_{h}m"] = round(acc_naive, 3)
    else:
        print(f"+{h:>2} months  not enough data after {TEST_FROM:%Y-%m} to test; trained on all rows")

    # refit on everything, then forecast from the latest month of each lab
    model.fit(d[feats], d[target])
    latest = c1.loc[c1.groupby("MESSRAUM").month.idxmax()].copy()
    latest["horizon_months"] = h
    latest["forecast_month"] = latest.month + pd.DateOffset(months=h)
    latest["forecast_workload_h"] = model.predict(latest[feats]).clip(0).round(0)
    # capacity: planned hours if available, else same month last year (actual present hours)
    same_month_ly = c1.set_index(["MESSRAUM", "month"]).ist_anwesend_h
    latest["capacity_h"] = latest[f"soll_anwesend_h_lead{h}"].fillna(pd.Series(
        [same_month_ly.get((r.MESSRAUM, r.forecast_month - pd.DateOffset(years=1))) for r in latest.itertuples()],
        index=latest.index)).round(0)
    forecast.append(latest[["MESSRAUM", "horizon_months", "forecast_month", "forecast_workload_h", "capacity_h"]])

fc = pd.concat(forecast, ignore_index=True)
fc["forecast_utilisation"] = (fc.forecast_workload_h / fc.capacity_h.replace(0, np.nan)).round(3)
fc["status"] = pd.cut(fc.forecast_utilisation, [-np.inf, 0.85, 1.0, np.inf], labels=["green", "amber", "red"])
fc = fc.sort_values(["horizon_months", "forecast_utilisation"], ascending=[True, False])
fc.to_csv(BUILD / "c1_forecast.csv", index=False)
print("\nLabs at amber/red in the forecast:")
hot = fc[fc.status.isin(["amber", "red"])]
print(hot.to_string(index=False) if len(hot) else "  none")

# =================================================================== CHALLENGE 2
print("\n" + "=" * 70 + "\nCHALLENGE 2 - which customers stop sending (next 6 months)\n" + "=" * 70)
s = pd.read_parquet(FEAT / "c2_customer_snapshot.parquet")
s["branche"] = s.branche.astype("category")
s = s[s.cal_12m > 0]                              # only customers active in the last 12 months can churn
NUM = ["cal_1m", "cal_3m", "cal_6m", "cal_12m", "orders_12m", "hub_share_12m", "nio_rate_12m", "dakks_share_12m",
       "months_since_last_cal", "months_active_12m", "trend_3m_vs_12m", "due_next_3m", "due_next_6m",
       "overdue_open", "instruments_known", "month_of_year", "n_instruments", "n_groups",
       "industry_similarity", "whitespace_score"]
FEATS = NUM + ["branche"]

lab = s.dropna(subset=["target_inactive_next_6m"])
split = pd.Timestamp("2025-07-01")
train, test = lab[lab.snapshot < split], lab[lab.snapshot >= split]

clf = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, categorical_features="from_dtype",
                                     random_state=0)
clf.fit(train[FEATS], train.target_inactive_next_6m)
p = clf.predict_proba(test[FEATS])[:, 1]
auc = roc_auc_score(test.target_inactive_next_6m, p)

# the sales view: of the 100 customers flagged highest each month, how many really went quiet?
t = test.assign(p=p)
top = t.sort_values("p", ascending=False).groupby("snapshot").head(100)
hit_rate = top.target_inactive_next_6m.mean()
base_rate = t.target_inactive_next_6m.mean()
# only "real" customers (>= 10 calibrations a year) - the ones worth a call
big = t[t.cal_12m >= 10]
top_big = big.sort_values("p", ascending=False).groupby("snapshot").head(100)
print(f"Ranking quality (AUC)                     {auc:.2f}   (0.5 = coin flip, 1.0 = perfect)")
print(f"Top-100 flagged per month really churned  {hit_rate:.0%}   (vs {base_rate:.0%} if picked at random)")
print(f"  same, customers with >=10 cal./year     {top_big.target_inactive_next_6m.mean():.0%}   "
      f"(vs {big.target_inactive_next_6m.mean():.0%} random)")
metrics.update(c2_auc=round(auc, 3), c2_top100_hit=round(hit_rate, 3), c2_base_rate=round(base_rate, 3))

# volume forecast: calibrations per customer in the next 6 months
vol = s.dropna(subset=["target_cal_next_6m"])
vtrain, vtest = vol[vol.snapshot < split], vol[vol.snapshot >= split]
reg = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, categorical_features="from_dtype",
                                    random_state=0)
reg.fit(vtrain[FEATS], vtrain.target_cal_next_6m)
vacc = wape_accuracy(vtest.target_cal_next_6m, reg.predict(vtest[FEATS]).clip(0))
vnaive = wape_accuracy(vtest.target_cal_next_6m, vtest.cal_6m)
print(f"Order volume next 6 months, accuracy      {vacc:.0%}   (simple guess 'same as last 6 months' {vnaive:.0%})")
metrics.update(c2_volume_accuracy=round(vacc, 3), c2_volume_naive=round(vnaive, 3))

# ---- score today and build the call list
clf.fit(lab[FEATS], lab.target_inactive_next_6m)
reg.fit(vol[FEATS], vol.target_cal_next_6m)
now = s[s.snapshot == s.snapshot.max()].copy()
now["churn_prob"] = clf.predict_proba(now[FEATS])[:, 1].round(3)
now["expected_cal_6m"] = reg.predict(now[FEATS]).clip(0).round(0)


def reason(r) -> str:
    out = []
    if r.overdue_open >= 5:
        out.append(f"{r.overdue_open:.0f} overdue instruments not received")
    if r.due_next_3m >= 5:
        out.append(f"{r.due_next_3m:.0f} instruments due in 3 months")
    if pd.notna(r.trend_3m_vs_12m) and r.cal_12m >= 10 and r.trend_3m_vs_12m < 0.5:
        out.append("activity dropped sharply in the last 3 months")
    if r.months_since_last_cal >= 4:
        out.append(f"no calibration for {r.months_since_last_cal:.0f} months")
    if isinstance(r.top_missing_group, str) and r.top_missing_group and (r.whitespace_score or 0) > 0.3:
        out.append(f"industry peers also calibrate {r.top_missing_group}")
    return "; ".join(out)


now["action"] = np.select(
    [(now.churn_prob >= 0.5) & (now.cal_12m >= 10), now.overdue_open >= 5, now.due_next_3m >= 5,
     (now.whitespace_score.fillna(0) > 0.3) & (now.cal_12m >= 10)],
    ["win-back", "reminder: overdue", "reminder: due soon", "cross-sell"], "")
# what is at stake, in calibrations
now["priority"] = np.select(
    [now.action == "win-back", now.action.str.startswith("reminder"), now.action == "cross-sell"],
    [now.churn_prob * now.cal_12m,                       # yearly volume we expect to lose
     now.overdue_open + now.due_next_3m,                 # items we can bring in now
     now.whitespace_score.fillna(0) * now.cal_12m],      # rough size of the untapped share
    0).round(1)
now["reason"] = now.apply(reason, axis=1)
now.loc[now.action == "win-back", "reason"] = (
    "churn risk " + (now.churn_prob * 100).round(0).astype(int).astype(str) + "%; " + now.reason).str.rstrip("; ")
cols = ["customer", "branche", "action", "priority", "churn_prob", "cal_12m", "expected_cal_6m",
        "overdue_open", "due_next_3m", "months_since_last_cal", "top_missing_group", "reason"]
call = now[now.action != ""].sort_values("priority", ascending=False)[cols]
call["rank_in_action"] = call.groupby("action").cumcount() + 1
call.to_csv(BUILD / "c2_call_list.csv", index=False)

# today's list: a mix, so sales sees every kind of opportunity
PER_DAY = {"win-back": 8, "reminder: overdue": 6, "reminder: due soon": 3, "cross-sell": 3}
today = pd.concat([call[call.action == a].head(n) for a, n in PER_DAY.items()])
today.to_csv(BUILD / "c2_today.csv", index=False)
print(f"\nCandidates: {call.action.value_counts().to_dict()}")
print(f"Today's list ({len(today)} calls):")
print(today[["customer", "branche", "action", "cal_12m", "reason"]].to_string(index=False, max_colwidth=70))

(BUILD / "metrics.json").write_text(json.dumps(metrics, indent=2))
print(f"\nSaved build/c1_forecast.csv, build/c2_call_list.csv, build/c2_today.csv, build/metrics.json")
