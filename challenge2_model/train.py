"""Challenge 2 - Customer Activity Monitoring: train, compare data sources, score today.

Inputs
  features/c2_customer_snapshot.parquet     base: customer x month, leak-free (build_features.py)
  features/c2_instrument_interval.parquet   per calibration: days until the instrument comes back
  build/kunden_portfolio_vektor.parquet     extra: instrument-group mix per customer (src/profiles.py)
  build/branchen_portfolio_vektor.parquet   extra: average mix per industry
  vectordb/vectors.parquet                  extra: 384-dim text embedding per customer (src/embed.py)
  challenge2_model/extra/*.csv|xlsx|parquet extra: anything keyed by customer number (see extra/README.md)

Models (all trained on older snapshots, tested on newer ones)
  churn     target_inactive_next_6m     classifier  - customer sends nothing in the next 6 months
  volume    target_cal_next_6m          regressor   - calibrations expected in the next 6 months
  return    target_due_return_rate_6m   regressor   - share of due instruments that actually come back
  interval  target_gap_d                regressor   - days until a calibrated instrument returns

Usage
  .venv/bin/python challenge2_model/train.py                 # compare variants, save best leak-free one
  .venv/bin/python challenge2_model/train.py --variant all   # force a variant: base | portfolio | embedding | all

Outputs (challenge2_model/output/)
  metrics.json     every variant x model, test scores
  models.joblib    the fitted models + feature lists
  scores_today.csv every active customer with churn_prob, expected_cal_6m, expected_return_rate
  instruments_next_due.csv  open instruments with predicted return date
"""
from pathlib import Path
import argparse
import json
import warnings

import joblib
import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, roc_auc_score

warnings.filterwarnings("ignore")
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / "output"
OUT.mkdir(exist_ok=True)
SPLIT = pd.Timestamp("2025-07-01")   # train on snapshots before, test on snapshots from here
EMB_DIMS = 16
ID_COLS = ["kunde", "customer", "kundennr", "kundennummer", "kundennummer_sap"]

BASE = ["cal_1m", "cal_3m", "cal_6m", "cal_12m", "orders_12m", "hub_share_12m", "nio_rate_12m", "dakks_share_12m",
        "months_since_last_cal", "months_active_12m", "trend_3m_vs_12m", "due_next_3m", "due_next_6m",
        "overdue_open", "instruments_known", "month_of_year", "n_instruments", "n_groups",
        "industry_similarity", "whitespace_score", "branche"]


# ------------------------------------------------------------------ extra data sources
def portfolio_features():
    """Customer mix, and how far each group is from the industry average (negative = untapped)."""
    kv = pd.read_parquet(ROOT / "build/kunden_portfolio_vektor.parquet").rename(columns={"kunde": "customer"})
    bv = pd.read_parquet(ROOT / "build/branchen_portfolio_vektor.parquet").set_index("branche")
    br = pd.read_parquet(ROOT / "build/kunden.parquet").set_index("kunde").branche
    groups = [c for c in kv.columns if c != "customer"]
    ind = bv.reindex(kv.customer.map(br)).reset_index(drop=True)[groups]
    diff = kv[groups].values - ind.values
    out = pd.DataFrame(kv[groups].values, columns=[f"pf_{g}" for g in groups])
    out[[f"pfgap_{g}" for g in groups]] = diff
    out["pf_untapped_total"] = np.clip(-diff, 0, None).sum(axis=1)
    out.insert(0, "customer", kv.customer.astype(str))
    return out


def embedding_features():
    """Text embedding per customer, compressed with PCA. Built from today's profile text -> leaks into history."""
    v = pd.read_parquet(ROOT / "vectordb/vectors.parquet")
    v = v[v.collection == "kunde"]
    X = np.vstack(v.vector.values)
    Z = PCA(n_components=EMB_DIMS, random_state=0).fit_transform(X)
    out = pd.DataFrame(Z, columns=[f"emb_{i}" for i in range(EMB_DIMS)])
    out.insert(0, "customer", v.id.str.removeprefix("kunde:").values)
    return out


def industry_features():
    """extra/branchen.csv: the 15 industries from the poster, with any extra columns you fill in."""
    f = HERE / "extra/branchen.csv"
    if not f.exists():
        return None
    b = pd.read_csv(f, sep=";")
    num = [c for c in b.columns if c not in ("branche", "nr") and pd.api.types.is_numeric_dtype(b[c]) and b[c].notna().any()]
    return b[["branche"] + num].rename(columns={c: f"ind_{c}" for c in num}) if num else None


def user_extra_features():
    """Every csv/xlsx/parquet in extra/ (except branchen.csv) with a customer-number column gets joined."""
    frames = []
    for f in sorted((HERE / "extra").glob("*")):
        if f.name == "branchen.csv" or f.suffix.lower() not in {".csv", ".xlsx", ".xls", ".parquet"}:
            continue
        df = (pd.read_parquet(f) if f.suffix == ".parquet" else pd.read_excel(f) if f.suffix.startswith(".xls")
              else pd.read_csv(f, sep=None, engine="python"))
        key = next((c for c in df.columns if c.strip().lower() in ID_COLS), None)
        if key is None:
            print(f"  extra/{f.name}: skipped, no customer column (expected one of {ID_COLS})")
            continue
        df = df.rename(columns={key: "customer"})
        df["customer"] = df.customer.astype(str).str.strip().str.removesuffix(".0")
        df = df.drop_duplicates("customer")
        keep = {c: f"x_{f.stem}_{c}" for c in df.columns if c != "customer"}
        frames.append(df.rename(columns=keep))
        print(f"  extra/{f.name}: {len(df):,} customers, {len(keep)} columns")
    if not frames:
        return None
    out = frames[0]
    for f in frames[1:]:
        out = out.merge(f, on="customer", how="outer")
    for c in out.columns[1:]:
        if out[c].dtype == object:
            out[c] = out[c].astype("category")
    return out


# ------------------------------------------------------------------ helpers
def hgb(kind):
    cls = HistGradientBoostingClassifier if kind == "clf" else HistGradientBoostingRegressor
    return cls(max_iter=400, learning_rate=0.05, max_leaf_nodes=31, l2_regularization=1.0,
               categorical_features="from_dtype", random_state=0)


def wape_acc(y, p):
    y, p = np.asarray(y, float), np.asarray(p, float)
    return float(1 - np.abs(y - p).sum() / np.abs(y).sum())


def evaluate(df, feats):
    """Fit the three customer models on old snapshots, score on new ones."""
    res, tr, te = {}, df[df.snapshot < SPLIT], df[df.snapshot >= SPLIT]

    lab = lambda d, t: d.dropna(subset=[t])
    a, b = lab(tr[tr.cal_12m > 0], "target_inactive_next_6m"), lab(te[te.cal_12m > 0], "target_inactive_next_6m")
    p = hgb("clf").fit(a[feats], a.target_inactive_next_6m).predict_proba(b[feats])[:, 1]
    top = b.assign(p=p).sort_values("p", ascending=False).groupby("snapshot").head(100)
    res["churn_auc"] = roc_auc_score(b.target_inactive_next_6m, p)
    res["churn_top100_hit"] = top.target_inactive_next_6m.mean()
    # Tiny one-off customers make churn look easy; the sales-relevant score is on customers with >= 10 cal./year.
    big = b.assign(p=p)[b.cal_12m >= 10]
    res["churn_auc_big"] = roc_auc_score(big.target_inactive_next_6m, big.p)
    res["churn_top100_hit_big"] = big.sort_values("p", ascending=False).groupby("snapshot").head(100).target_inactive_next_6m.mean()

    a, b = lab(tr, "target_cal_next_6m"), lab(te, "target_cal_next_6m")
    p = hgb("reg").fit(a[feats], np.log1p(a.target_cal_next_6m)).predict(b[feats])
    res["volume_acc"] = wape_acc(b.target_cal_next_6m, np.expm1(p).clip(0))

    a, b = lab(tr, "target_due_return_rate_6m"), lab(te, "target_due_return_rate_6m")
    p = hgb("reg").fit(a[feats], a.target_due_return_rate_6m).predict(b[feats]).clip(0, 1)
    res["return_mae"] = mean_absolute_error(b.target_due_return_rate_6m, p)
    return {k: round(float(v), 4) for k, v in res.items()}


def naive_scores(df):
    te = df[df.snapshot >= SPLIT]
    c = te[te.cal_12m > 0].dropna(subset=["target_inactive_next_6m"])
    v = te.dropna(subset=["target_cal_next_6m"])
    r = te.dropna(subset=["target_due_return_rate_6m"])
    big = c[c.cal_12m >= 10]
    return {"churn_auc": round(roc_auc_score(c.target_inactive_next_6m, c.months_since_last_cal.fillna(99)), 4),
            "churn_auc_big": round(roc_auc_score(big.target_inactive_next_6m, big.months_since_last_cal.fillna(99)), 4),
            "churn_base_rate_big": round(big.target_inactive_next_6m.mean(), 4),
            "churn_base_rate": round(c.target_inactive_next_6m.mean(), 4),
            "volume_acc": round(wape_acc(v.target_cal_next_6m, v.cal_6m), 4),
            "return_mae": round(mean_absolute_error(r.target_due_return_rate_6m,
                                                    np.full(len(r), r.target_due_return_rate_6m.mean())), 4)}


# ------------------------------------------------------------------ instrument model
def train_interval():
    """Days until an instrument comes back after calibration -> future calibration demand."""
    ii = pd.read_parquet(ROOT / "features/c2_instrument_interval.parquet")
    feats = ["interval_d", "stated_interval_d", "reg_interval_d", "learned_interval_d", "interval_imputed", "cal_no",
             "prev_gap_d", "month_of_year", "cust_ratio_hist", "MESSMITTELGRUPPE", "MESSRAUM", "PRUEFUNGSART",
             "BEWERTUNG", "branche"]
    for c in ["MESSMITTELGRUPPE", "MESSRAUM", "PRUEFUNGSART", "BEWERTUNG", "branche"]:
        ii[c] = ii[c].astype("category")
    ii["interval_imputed"] = ii.interval_imputed.astype(int)
    # A gap can only be seen if the instrument came back before the data ends, so short gaps are over-represented
    # among late calibrations. Train only on calibrations done early enough that a 2-year gap was observable.
    lab = ii[ii.target_gap_d.notna() & (ii.ENDE < "2024-10-01")]  # NaN = same-week re-check, not a return
    tr, te = lab[lab.ENDE < "2024-07-01"], lab[lab.ENDE >= "2024-07-01"]
    m = hgb("reg").fit(tr[feats], tr.target_gap_d)
    mae = mean_absolute_error(te.target_gap_d, m.predict(te[feats]))
    naive = mean_absolute_error(te.target_gap_d, te.interval_d)
    m.fit(lab[feats], lab.target_gap_d)
    open_ = ii[ii.label_censored].copy()
    open_["pred_return"] = open_.ENDE + pd.to_timedelta(m.predict(open_[feats]).clip(7), unit="D")
    cols = ["MESSMITTEL_UUID", "KUNDENNUMMER_SAP", "branche", "MESSMITTELGRUPPE", "MESSRAUM", "ENDE", "due", "pred_return"]
    open_[cols].to_csv(OUT / "instruments_next_due.csv", index=False)
    return m, feats, {"interval_mae_days": round(mae, 1), "interval_naive_mae_days": round(naive, 1),
                      "interval_train_rows": len(tr), "interval_test_rows": len(te)}


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", choices=["auto", "base", "portfolio", "embedding", "all"], default="auto")
    args = ap.parse_args()

    print("Loading data ...")
    s = pd.read_parquet(ROOT / "features/c2_customer_snapshot.parquet")
    s["customer"] = s.customer.astype(str)
    pf, emb, ind, usr = portfolio_features(), embedding_features(), industry_features(), user_extra_features()
    s = s.merge(pf, on="customer", how="left").merge(emb, on="customer", how="left")
    extra_feats = []
    if ind is not None:
        s = s.merge(ind, on="branche", how="left")
        extra_feats += [c for c in ind.columns if c != "branche"]
    if usr is not None:
        s = s.merge(usr, on="customer", how="left")
        extra_feats += [c for c in usr.columns if c != "customer"]
    s["branche"] = s.branche.astype("category")

    pf_cols = [c for c in pf.columns if c != "customer"]
    emb_cols = [c for c in emb.columns if c != "customer"]
    variants = {"base": BASE + extra_feats, "portfolio": BASE + extra_feats + pf_cols,
                "embedding": BASE + extra_feats + emb_cols, "all": BASE + extra_feats + pf_cols + emb_cols}

    metrics = {"naive": naive_scores(s), "split": str(SPLIT.date()), "extra_feature_cols": extra_feats}
    print(f"\n{'variant':<11} {'churn AUC':>9} {'top100':>7} {'AUC >=10/yr':>11} {'top100 >=10':>11} {'volume acc':>10} {'return MAE':>10}")
    n = metrics["naive"]
    print(f"{'naive':<11} {n['churn_auc']:>9.3f} {n['churn_base_rate']:>7.1%} {n['churn_auc_big']:>11.3f} "
          f"{n['churn_base_rate_big']:>11.1%} {n['volume_acc']:>10.1%} {n['return_mae']:>10.3f}   (naive top100 = random pick)")
    for name, feats in variants.items():
        r = evaluate(s, feats)
        metrics[name] = r
        print(f"{name:<11} {r['churn_auc']:>9.3f} {r['churn_top100_hit']:>7.1%} {r['churn_auc_big']:>11.3f} "
              f"{r['churn_top100_hit_big']:>11.1%} {r['volume_acc']:>10.1%} {r['return_mae']:>10.3f}")

    # Embeddings are built from today's profile text, so their test score is optimistic: never auto-pick them.
    chosen = args.variant
    if chosen == "auto":
        chosen = max(["base", "portfolio"], key=lambda v: metrics[v]["churn_auc_big"] + metrics[v]["volume_acc"])
    feats = variants[chosen]
    metrics["chosen_variant"] = chosen
    print(f"\nUsing variant '{chosen}' ({len(feats)} features) for the final models.")

    print("Training instrument return model ...")
    im, ifeats, imet = train_interval()
    metrics.update(imet)
    print(f"  days until return: MAE {imet['interval_mae_days']} days (stated interval alone: {imet['interval_naive_mae_days']})")

    # Final fit on all labelled snapshots, then score today's snapshot.
    lab = lambda t, d=s: d.dropna(subset=[t])
    c = lab("target_inactive_next_6m", s[s.cal_12m > 0])
    churn = hgb("clf").fit(c[feats], c.target_inactive_next_6m)
    v = lab("target_cal_next_6m")
    volume = hgb("reg").fit(v[feats], np.log1p(v.target_cal_next_6m))
    r = lab("target_due_return_rate_6m")
    ret = hgb("reg").fit(r[feats], r.target_due_return_rate_6m)

    now = s[(s.snapshot == s.snapshot.max()) & (s.cal_12m > 0)].copy()
    now["churn_prob"] = churn.predict_proba(now[feats])[:, 1].round(3)
    now["expected_cal_6m"] = np.expm1(volume.predict(now[feats])).clip(0).round(0)
    now["expected_return_rate"] = ret.predict(now[feats]).clip(0, 1).round(2)
    now["volume_at_risk"] = (now.churn_prob * now.cal_12m).round(1)
    cols = ["customer", "branche", "churn_prob", "volume_at_risk", "expected_cal_6m", "expected_return_rate",
            "cal_12m", "cal_3m", "months_since_last_cal", "due_next_3m", "overdue_open", "whitespace_score",
            "top_missing_group"]
    now.sort_values("volume_at_risk", ascending=False)[cols].to_csv(OUT / "scores_today.csv", index=False)

    joblib.dump({"variant": chosen, "features": feats, "churn": churn, "volume_log1p": volume, "return_rate": ret,
                 "interval": im, "interval_features": ifeats, "snapshot": str(s.snapshot.max().date())},
                OUT / "models.joblib")
    (OUT / "metrics.json").write_text(json.dumps(metrics, indent=2, default=str))
    print(f"\nScored {len(now):,} active customers. Top 10 by volume at risk:")
    print(now.sort_values("volume_at_risk", ascending=False)[cols[:8]].head(10).to_string(index=False))
    print(f"\nSaved {OUT.relative_to(ROOT)}/: metrics.json, models.joblib, scores_today.csv, instruments_next_due.csv")


if __name__ == "__main__":
    main()
