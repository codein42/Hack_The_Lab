"""Challenge 2 - forecast for the coming 12 months: CSVs to look into + graphs to present.

Needs data/ (export_db.py), features/ (build_features.py) and challenge2_model/output/ (train.py).

    .venv/bin/python challenge2_model/forecast.py

Writes challenge2_model/forecast/:
  monthly_forecast.csv     total + per industry, calibrations per month: history, backtest, forecast with range
  industry_outlook.csv     per industry: last 12 months vs next 12 months, volume at risk
  due_pipeline.csv         instruments coming due per month: expected back vs. at risk (sales opportunity)
  customer_forecast.csv    per customer: expected calibrations per month for the next 6 months + action
  call_list_top50.csv      who to call first, and why
  charts/*.png             slide-ready graphs (1600x900)
"""
from pathlib import Path
import warnings

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / "forecast"
CH = OUT / "charts"
CH.mkdir(parents=True, exist_ok=True)

LAST_FULL = pd.Timestamp("2026-08-01")     # data ends 2026-09-24, so September is incomplete
START = pd.Timestamp("2026-10-01")         # forecast horizon: Oct 2026 - Sep 2027
HORIZON = pd.date_range(START, periods=12, freq="MS")
BACKTEST_CUT = pd.Timestamp("2025-09-01")  # pretend it is Sep 2025, forecast 12 months, compare with reality

# Chart palette (validated: CVD dE 24.7, normal-vision dE 33.6 on the light surface)
BLUE, ORANGE = "#2a78d6", "#eb6834"
BLUE_LIGHT = "#86b6ef"
INK, INK2, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#8a8984", "#e6e5e0", "#fcfcfb"
plt.rcParams.update({
    "figure.figsize": (16, 9), "figure.dpi": 100, "savefig.dpi": 100, "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE, "axes.edgecolor": GRID, "axes.labelcolor": INK2, "axes.grid": True,
    "grid.color": GRID, "grid.linewidth": 1, "axes.spines.top": False, "axes.spines.right": False,
    "axes.spines.left": False, "xtick.color": INK2, "ytick.color": INK2, "font.size": 15,
    "axes.titlesize": 22, "axes.titleweight": "bold", "axes.titlelocation": "left", "axes.titlecolor": INK,
    "axes.titlepad": 40, "axes.axisbelow": True,
    "legend.frameon": False, "font.family": ["Helvetica Neue", "Arial", "DejaVu Sans"],
})


def subtitle(ax, text):
    ax.text(0, 1.015, text, transform=ax.transAxes, color=INK2, fontsize=15, va="bottom")


def save(fig, name):
    fig.tight_layout()
    fig.savefig(CH / name, facecolor=SURFACE)
    plt.close(fig)
    print(f"  charts/{name}")


# ------------------------------------------------------------------ 1. monthly demand
def seasonal_forecast(y: pd.Series, cut: pd.Timestamp, months: pd.DatetimeIndex, damp: float = 0.5) -> pd.Series:
    """Same month last year x damped year-on-year trend. Uses only months before `cut`."""
    hist = y[y.index < cut]
    last12, prev12 = hist.iloc[-12:].sum(), hist.iloc[-24:-12].sum()
    growth = (last12 / prev12) ** damp if prev12 > 0 else 1.0
    # Same month last year; if that month is missing or incomplete (Sep 2026), the year before.
    base = [hist.get(m - pd.DateOffset(years=1), hist.get(m - pd.DateOffset(years=2), np.nan)) for m in months]
    return pd.Series(base, index=months) * growth


def monthly_forecast(kal, branche_of):
    k = kal.assign(month=kal.ENDE.dt.to_period("M").dt.to_timestamp(),
                   branche=kal.KUNDENNUMMER_SAP.map(branche_of).fillna("Unbekannt"))
    k = k[k.month <= LAST_FULL]
    table = k.groupby(["month", "branche"]).size().unstack(fill_value=0)
    table["TOTAL"] = table.sum(axis=1)

    # Backtest on the total to pick the trend damping and measure the error band.
    y = table.TOTAL
    bt_months = pd.date_range(BACKTEST_CUT, LAST_FULL, freq="MS")
    best = None
    for damp in (0.0, 0.5, 1.0):
        p = seasonal_forecast(y, BACKTEST_CUT, bt_months, damp)
        err = (p - y[bt_months]) / y[bt_months]
        acc = 1 - (p - y[bt_months]).abs().sum() / y[bt_months].sum()
        if best is None or acc > best[1]:
            best = (damp, acc, err)
    damp, acc, err = best
    lo_q, hi_q = np.quantile(err, [0.1, 0.9])  # 80 % of backtest months fell inside this band
    print(f"  backtest Sep 2025-Aug 2026: accuracy {acc:.1%} (trend damping {damp}), "
          f"80% of months within {lo_q:+.0%} .. {hi_q:+.0%}")

    rows = []
    for col in table.columns:
        s = table[col]
        bt = seasonal_forecast(s, BACKTEST_CUT, bt_months, damp)
        fc = seasonal_forecast(s, LAST_FULL + pd.DateOffset(months=1), HORIZON, damp)
        for m, v in s.items():
            rows.append((col, m, "actual", v, np.nan, np.nan))
        for m, v in bt.items():
            rows.append((col, m, "backtest", v, np.nan, np.nan))
        for m, v in fc.items():
            rows.append((col, m, "forecast", v, v / (1 - lo_q), v / (1 - hi_q)))
    df = pd.DataFrame(rows, columns=["segment", "month", "kind", "calibrations", "low", "high"])
    df[["calibrations", "low", "high"]] = df[["calibrations", "low", "high"]].round(0)
    df["low"], df["high"] = df[["low", "high"]].min(axis=1), df[["low", "high"]].max(axis=1)
    return df, acc, (lo_q, hi_q)


def chart_total(mf, acc):
    t = mf[mf.segment == "TOTAL"]
    act, bt, fc = (t[t.kind == k].set_index("month") for k in ("actual", "backtest", "forecast"))
    fig, ax = plt.subplots()
    ax.fill_between(fc.index, fc.low, fc.high, color=BLUE_LIGHT, alpha=0.35, linewidth=0, label="Likely range (80 %)")
    ax.plot(act.index, act.calibrations, color=INK2, lw=2, label="Actual")
    ax.plot(bt.index, bt.calibrations, color=MUTED, lw=2, ls=(0, (4, 3)), label=f"Backtest (accuracy {acc:.0%})")
    bridge = pd.concat([act.calibrations.iloc[-1:], fc.calibrations])
    ax.plot(bridge.index, bridge.values, color=BLUE, lw=2.5, label="Forecast")
    ax.scatter(fc.index, fc.calibrations, color=BLUE, s=60, zorder=3, edgecolor=SURFACE, linewidth=2)
    ax.axvline(START, color=MUTED, lw=1)
    ax.text(START, ax.get_ylim()[1], "  today", color=MUTED, va="top")
    peak = fc.calibrations.idxmax()
    ax.annotate(f"{fc.calibrations[peak]:,.0f}", (peak, fc.calibrations[peak]), xytext=(0, 12),
                textcoords="offset points", ha="center", color=INK, fontsize=14, fontweight="bold")
    ax.set_title("Calibrations per month: 12-month forecast")
    change = fc.calibrations.sum() / act.calibrations.iloc[-12:].sum() - 1
    trend = "same level as the last 12 months: no growth trend in the data" if abs(change) < 0.005 \
        else f"{change:+.0%} vs the last 12 months"
    subtitle(ax, f"Next 12 months: {fc.calibrations.sum():,.0f} calibrations expected ({trend})")
    ax.yaxis.set_major_formatter(mtick.StrMethodFormatter("{x:,.0f}"))
    ax.set_ylim(0)
    ax.legend(loc="lower left", ncol=4)
    save(fig, "1_total_forecast.png")


def tilt_industries(mf, scores, kal):
    """The seasonal total is the backtested number; the customer models only decide how it splits by industry.

    tilt = (model's next-6-month volume / same customers' volume a year earlier) per industry, relative to the
    overall ratio, square-rooted to damp it (not backtested), then rescaled so industries still add up to TOTAL.
    """
    ly = kal[(kal.ENDE >= START - pd.DateOffset(years=1)) & (kal.ENDE < START - pd.DateOffset(months=6))]
    s = scores.assign(ly6=scores.customer.map(ly.groupby("KUNDENNUMMER_SAP").size()).fillna(0))
    g = s.groupby("branche")[["expected_cal_6m", "ly6"]].sum()
    ratio = (g.expected_cal_6m / g.ly6) / (g.expected_cal_6m.sum() / g.ly6.sum())
    tilt = np.sqrt(ratio.clip(0.1, 3)).to_dict()

    fc = mf.kind.eq("forecast") & mf.segment.ne("TOTAL")
    for c in ["calibrations", "low", "high"]:
        mf.loc[fc, c] = mf.loc[fc, c] * mf.loc[fc, "segment"].map(tilt).fillna(1)
        month_sum = mf[fc].groupby("month")[c].transform("sum")
        total = mf[mf.kind.eq("forecast") & mf.segment.eq("TOTAL")].set_index("month")[c]
        mf.loc[fc, c] = (mf.loc[fc, c] / month_sum * mf.loc[fc, "month"].map(total)).round(0)
    return mf


# ------------------------------------------------------------------ 2. industries
def industry_outlook(mf, scores):
    m = mf[mf.segment != "TOTAL"]
    last12 = m[(m.kind == "actual") & (m.month > LAST_FULL - pd.DateOffset(months=12))].groupby("segment").calibrations.sum()
    next12 = m[m.kind == "forecast"].groupby("segment").calibrations.sum()
    risk = scores.groupby("branche").agg(customers_active=("customer", "size"),
                                         customers_high_risk=("churn_prob", lambda p: int((p >= 0.5).sum())),
                                         volume_at_risk=("volume_at_risk", "sum"))
    out = pd.DataFrame({"last_12m": last12, "next_12m_forecast": next12}).join(risk).fillna(0)
    out["change_pct"] = (out.next_12m_forecast / out.last_12m - 1).round(3)
    out["risk_share_pct"] = (out.volume_at_risk / out.last_12m).round(3)
    return out.sort_values("next_12m_forecast", ascending=False).rename_axis("branche").reset_index()


def chart_industries(io):
    d = io[io.branche != "Unbekannt"].head(10).iloc[::-1]
    fig, ax = plt.subplots()
    ax.barh(d.branche, d.last_12m, height=0.38, align="edge", color=BLUE_LIGHT, label="Last 12 months (actual)",
            edgecolor=SURFACE, linewidth=2)
    ax.barh(d.branche, d.next_12m_forecast, height=-0.38, align="edge", color=BLUE, label="Next 12 months (forecast)",
            edgecolor=SURFACE, linewidth=2)
    for y, (v, c) in enumerate(zip(d.next_12m_forecast, d.change_pct)):
        ax.text(v, y - 0.19, f"  {c:+.0%}", va="center", color=INK, fontsize=14)
    ax.grid(axis="y", visible=False)
    ax.xaxis.set_major_formatter(mtick.StrMethodFormatter("{x:,.0f}"))
    ax.set_title("Outlook by industry: calibrations, next 12 vs last 12 months")
    subtitle(ax, "Top 10 industries; label = expected change (total from seasonal forecast, split by customer churn model)")
    ax.legend(loc="lower right")
    save(fig, "2_industry_outlook.png")


# ------------------------------------------------------------------ 3. due pipeline
def due_pipeline(inst, scores):
    """Instruments predicted to come due, weighted by how likely the customer actually sends them."""
    rate = scores.set_index("customer").expected_return_rate
    i = inst[(inst.pred_return >= START) & (inst.pred_return < HORIZON[-1] + pd.offsets.MonthBegin(1))].copy()
    # Customers with no calibration in the last 12 months are not in scores -> treated as already lost.
    i["p_back"] = i.KUNDENNUMMER_SAP.map(rate).fillna(0)
    i["month"] = i.pred_return.dt.to_period("M").dt.to_timestamp()
    p = i.groupby("month").agg(instruments_due=("p_back", "size"), expected_back=("p_back", "sum"))
    p["at_risk"] = p.instruments_due - p.expected_back
    p = p.round(0)
    overdue = inst[(inst.pred_return < START) & inst.KUNDENNUMMER_SAP.isin(rate.index)]
    by_group = (i.groupby(["month", "MESSMITTELGRUPPE"]).size().rename("instruments_due").reset_index())
    return p.reset_index(), len(overdue), by_group


def chart_pipeline(p, n_overdue):
    fig, ax = plt.subplots()
    x = p.month.dt.strftime("%b %y")
    ax.bar(x, p.expected_back, width=0.7, color=BLUE, label="Expected to come back", edgecolor=SURFACE, linewidth=2)
    ax.bar(x, p.at_risk, width=0.7, bottom=p.expected_back, color=ORANGE, label="Due but at risk (call / remind)",
           edgecolor=SURFACE, linewidth=2)
    for xi, tot in zip(x, p.instruments_due):
        ax.text(xi, tot, f"{tot / 1000:.1f}k", ha="center", va="bottom", color=INK2, fontsize=13)
    ax.grid(axis="x", visible=False)
    ax.yaxis.set_major_formatter(mtick.StrMethodFormatter("{x:,.0f}"))
    share = p.at_risk.sum() / p.instruments_due.sum()
    ax.set_title("Instruments coming due: who will send them back?")
    subtitle(ax, f"{p.instruments_due.sum():,.0f} instruments due in the next 12 months; {share:.0%} at risk of not "
                 f"coming back. Past due and not returned yet: {n_overdue:,.0f}")
    ax.legend(loc="upper left")
    save(fig, "3_due_pipeline.png")


# ------------------------------------------------------------------ 4. customers
def customer_forecast(scores, inst, kal, total_6m):
    """Split each customer's expected 6-month volume across months by their own due dates (fallback: last year)."""
    months = HORIZON[:6]
    s = scores[scores.cal_12m > 0].copy()
    i = inst[inst.pred_return.between(months[0], months[-1] + pd.offsets.MonthEnd(1))]
    due = i.groupby([i.KUNDENNUMMER_SAP, i.pred_return.dt.to_period("M").dt.to_timestamp()]).size().unstack(fill_value=0)
    k = kal[kal.ENDE.between(months[0] - pd.DateOffset(years=1), months[-1] + pd.offsets.MonthEnd(1) - pd.DateOffset(years=1))]
    ly = k.groupby([k.KUNDENNUMMER_SAP, (k.ENDE + pd.DateOffset(years=1)).dt.to_period("M").dt.to_timestamp()]).size()
    ly = ly.unstack(fill_value=0)
    w = (due.reindex(columns=months, fill_value=0) + 0.5 * ly.reindex(columns=months, fill_value=0)).reindex(s.customer)
    w = w.fillna(0).values + 1e-9
    # The volume model is trained on log(volume), so its sum runs low; scale to the backtested 6-month total.
    s["expected_cal_6m_raw"] = s.expected_cal_6m
    s["expected_cal_6m"] = (s.expected_cal_6m * total_6m / s.expected_cal_6m.sum()).round(0)
    split = w / w.sum(axis=1, keepdims=True) * s.expected_cal_6m.values[:, None]
    for j, m in enumerate(months):
        s[m.strftime("%Y-%m")] = split[:, j].round(1)

    s["action"] = np.select(
        [(s.churn_prob >= 0.5) & (s.cal_12m >= 10), s.overdue_open >= 5, s.due_next_3m >= 5,
         (s.whitespace_score.fillna(0) > 0.3) & (s.cal_12m >= 10)],
        ["win-back", "remind: overdue", "remind: due soon", "cross-sell"], "monitor")
    s["why"] = s.apply(reason, axis=1)
    cols = ["customer", "branche", "action", "churn_prob", "volume_at_risk", "cal_12m", "expected_cal_6m", "expected_cal_6m_raw", "cal_3m"] + \
           [m.strftime("%Y-%m") for m in months] + ["due_next_3m", "overdue_open", "months_since_last_cal",
                                                    "top_missing_group", "why"]
    return s.sort_values("volume_at_risk", ascending=False)[cols]


def reason(r):
    out = []
    if r.cal_12m >= 10 and r.cal_3m >= 0.9 * r.cal_12m:
        out.append("one-off batch? (whole year's volume in the last 3 months)")
    if r.churn_prob >= 0.5 and r.cal_12m >= 10:
        out.append(f"{r.churn_prob:.0%} chance they stop sending")
    if r.cal_3m == 0 and r.cal_12m >= 10:
        out.append("nothing sent in the last 3 months")
    elif r.cal_12m >= 10 and r.cal_3m < r.cal_12m / 8:
        out.append("activity dropped sharply")
    if r.overdue_open >= 5:
        out.append(f"{r.overdue_open:.0f} instruments overdue")
    if r.due_next_3m >= 5:
        out.append(f"{r.due_next_3m:.0f} due within 3 months")
    if isinstance(r.top_missing_group, str) and (r.whitespace_score or 0) > 0.3:
        out.append(f"peers also calibrate {r.top_missing_group}")
    return "; ".join(out)


def chart_customers(cf):
    # Leave out one-off bulk senders: all of last year's volume arrived in the last 3 months.
    one_off = cf.cal_3m >= 0.9 * cf.cal_12m
    d = cf[(cf.cal_12m >= 10) & (cf.action == "win-back") & ~one_off].head(15).iloc[::-1]
    fig, ax = plt.subplots()
    labels = [f"{c}  ·  {b if isinstance(b, str) else 'unknown'}"[:60] for c, b in zip(d.customer, d.branche)]
    ax.barh(labels, d.volume_at_risk, height=0.6, color=ORANGE, edgecolor=SURFACE, linewidth=2)
    for y, (v, p, n) in enumerate(zip(d.volume_at_risk, d.churn_prob, d.cal_12m)):
        ax.text(v, y, f"  {p:.0%} risk · {n:,.0f}/yr", va="center", color=INK2, fontsize=13)
    ax.grid(axis="y", visible=False)
    ax.set_xlim(0, d.volume_at_risk.max() * 1.25)
    ax.set_title("Win-back list: biggest customers likely to stop sending")
    subtitle(ax, "Volume at risk = churn probability × calibrations in the last 12 months")
    ax.set_xlabel("Calibrations per year at risk")
    save(fig, "4_top_customers_at_risk.png")


def chart_risk_by_industry(io):
    d = io[(io.branche != "Unbekannt") & (io.last_12m > 1000)].sort_values("risk_share_pct").tail(12)
    fig, ax = plt.subplots()
    ax.barh(d.branche, d.risk_share_pct, height=0.6, color=ORANGE, edgecolor=SURFACE, linewidth=2)
    for y, (v, n) in enumerate(zip(d.risk_share_pct, d.customers_high_risk)):
        ax.text(v, y, f"  {v:.0%} · {n:,.0f} customers at high risk", va="center", color=INK2, fontsize=13)
    ax.grid(axis="y", visible=False)
    ax.xaxis.set_major_formatter(mtick.PercentFormatter(1.0, decimals=0))
    ax.set_xlim(0, d.risk_share_pct.max() * 1.45)
    ax.set_title("Where volume is at risk, by industry")
    subtitle(ax, "Share of last 12 months' calibrations held by customers likely to stop")
    save(fig, "5_risk_by_industry.png")


# ------------------------------------------------------------------ main
def main():
    kal = pd.read_parquet(ROOT / "data/KALIBRIERUNGEN.parquet", columns=["KUNDENNUMMER_SAP", "ENDE"])
    kb = pd.read_parquet(ROOT / "data/Kunde_Branche.parquet")
    branche_of = kb.drop_duplicates("KundenNr").set_index("KundenNr").Branche
    scores = pd.read_csv(HERE / "output/scores_today.csv", dtype={"customer": str})
    inst = pd.read_csv(HERE / "output/instruments_next_due.csv", dtype={"KUNDENNUMMER_SAP": str},
                       parse_dates=["ENDE", "due", "pred_return"])

    print("1. Monthly demand forecast")
    mf, acc, band = monthly_forecast(kal, branche_of)
    mf = tilt_industries(mf, scores, kal)
    mf.to_csv(OUT / "monthly_forecast.csv", index=False)
    chart_total(mf, acc)

    print("2. Industry outlook")
    io = industry_outlook(mf, scores)
    io.to_csv(OUT / "industry_outlook.csv", index=False)
    chart_industries(io)
    chart_risk_by_industry(io)

    print("3. Due pipeline")
    p, n_overdue, by_group = due_pipeline(inst, scores)
    p.to_csv(OUT / "due_pipeline.csv", index=False)
    by_group.to_csv(OUT / "due_pipeline_by_group.csv", index=False)
    chart_pipeline(p, n_overdue)

    print("4. Customer forecast")
    tot = mf[(mf.segment == "TOTAL") & (mf.kind == "forecast")]
    cf = customer_forecast(scores, inst, kal, tot.calibrations.iloc[:6].sum())
    cf.to_csv(OUT / "customer_forecast.csv", index=False)
    call = cf[(cf.action != "monitor") & (cf.cal_12m >= 10)].copy()
    call["priority"] = np.where(call.action == "win-back", call.volume_at_risk,
                                np.where(call.action.str.startswith("remind"), call.overdue_open + call.due_next_3m,
                                         call.cal_12m * 0.1))
    call.sort_values("priority", ascending=False).head(50).to_csv(OUT / "call_list_top50.csv", index=False)
    chart_customers(cf)

    print(f"\nNext 12 months: {tot.calibrations.sum():,.0f} calibrations "
          f"(range {tot.low.sum():,.0f} - {tot.high.sum():,.0f}); backtest accuracy {acc:.0%}")
    print(f"Actions: {cf.action.value_counts().to_dict()}")
    print(f"Saved to {OUT.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
