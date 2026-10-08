# Challenge 2 model - how to build and run

Python has nothing to compile: "building" the model = running the scripts below in order.
Every command is run from the **project root** (`team_42_wolf/`) in the VS Code terminal.

## 1. One-time setup
```
.venv/bin/pip install -r requirements.txt
```
(`openpyxl` is only needed if you drop `.xlsx` files into `extra/`: `.venv/bin/pip install openpyxl`)

## 2. Build the inputs (in this order)
| Step | Command | Creates | Time |
|---|---|---|---|
| a | `.venv/bin/python src/clean.py` | `build/` cleaned tables, customer → industry | ~20 s |
| b | `.venv/bin/python src/profiles.py` | `build/kunden_portfolio_vektor.parquet`, `build/branchen_portfolio_vektor.parquet`, profiles | ~10 s |
| c | `.venv/bin/python src/embed.py` | `vectordb/vectors.parquet` (needed: train.py always loads it) | ~1-3 min, first run downloads the model |
| d | `.venv/bin/python build_features.py` | `features/c2_customer_snapshot.parquet`, `features/c2_instrument_interval.parquet` | ~1 min |

No internet for step c? Use `EMBED_BACKEND=tfidf .venv/bin/python src/embed.py`.
Steps a-d only need to be repeated when `data/` changes.

## 3. Train the model
```
.venv/bin/python challenge2_model/train.py
```
Takes about 1-2 minutes. It:
1. trains 4 variants (base / portfolio / embedding / all) on snapshots **before 2025-07-01**,
2. tests them on snapshots **from 2025-07-01** (data the model never saw) and prints a comparison table,
3. picks the best leak-free variant (base or portfolio) automatically,
4. re-trains on all data and scores every active customer for today.

Force one variant: `.venv/bin/python challenge2_model/train.py --variant portfolio`
(choices: `auto` (default), `base`, `portfolio`, `embedding`, `all`).
`embedding` is never auto-picked: the embeddings are built from today's profile text, so its test score is optimistic.

## 4. What comes out (`challenge2_model/output/`)
| File | Content |
|---|---|
| `metrics.json` | test scores of every variant + the naive baseline |
| `models.joblib` | the 4 fitted models + their feature lists |
| `scores_today.csv` | every active customer: `churn_prob`, `volume_at_risk`, `expected_cal_6m`, `expected_return_rate`, gaps |
| `instruments_next_due.csv` | every open instrument with its predicted return date (~80 MB) |

### The 4 models
| Model | Predicts | Type |
|---|---|---|
| churn | customer sends nothing in the next 6 months | classifier |
| volume | calibrations in the next 6 months | regressor (log scale) |
| return | share of due instruments that actually come back | regressor |
| interval | days until a calibrated instrument returns | regressor |

### Last run (08.10.2026, variant `base`)
| Metric | Model | Simple guess |
|---|---|---|
| Churn AUC, customers with ≥10 cal./year | **0.91** | 0.74 (months since last calibration) |
| Of the 100 flagged per month (≥10 cal./year), really went quiet | **97 %** | 27 % random |
| Order volume accuracy, next 6 months | **54 %** | 35 % (same as last 6 months) |
| Return-rate error (MAE) | **0.24** | 0.34 |
| Days until an instrument returns (MAE) | **53 days** | 59 days (stated interval) |

## 5. Use the model elsewhere (dashboard, notebook)
```python
import joblib, numpy as np, pandas as pd
m = joblib.load("challenge2_model/output/models.joblib")
X = df[m["features"]]                         # df with the same feature columns as features/c2_customer_snapshot
churn_prob = m["churn"].predict_proba(X)[:, 1]
expected_cal_6m = np.expm1(m["volume_log1p"].predict(X)).clip(0)
```
Or simply read `output/scores_today.csv` - the dashboard only needs that file.

## 6. Troubleshooting
| Error | Fix |
|---|---|
| `FileNotFoundError: features/...` | run `build_features.py` (step d) |
| `FileNotFoundError: build/kunden_portfolio_vektor.parquet` | run `src/clean.py` + `src/profiles.py` (steps a, b) |
| `FileNotFoundError: vectordb/vectors.parquet` | run `src/embed.py` (step c) |
| `ModuleNotFoundError` | step 1 again (`pip install -r requirements.txt`) |
| Results look identical after adding a file to `extra/` | check the printout: a file without a customer-number column is skipped |

## 7. Next ideas for Challenge 2 (not built yet)
- **Order priority:** score each order = customer value × churn risk × days waiting vs the 16-day median lead time × lab load.
- **Production status board:** Kanban over the order stages (received → confirmed → service done → delivered), replayable for any past date (the export has no open orders).
- **Lead-time link to Challenge 1:** median lead time 16 days, of which ~13 days waiting in the lab; overloaded labs → longer waits → churn.

---

# extra/ - additional data for Challenge 2

`train.py` picks up everything in this folder automatically.

## branchen.csv - the 15 industries (from the poster)
One row per industry, `;`-separated. `branche` must match the names in the data exactly.
Add any **numeric** column (e.g. `kalibrierbedarf_hoch` = 1/0, `marktgroesse`, `prioritaet`) and it becomes
a feature for every customer of that industry. Text columns (like `beschreibung`) are kept for reading only.

## Your own files (csv / xlsx / parquet)
Drop the file here. It needs one column with the customer number, named one of:
`kunde`, `customer`, `KundenNr`, `Kundennummer`, `KUNDENNUMMER_SAP` (case does not matter).
All other columns are joined to that customer as features (`x_<filename>_<column>`).
Then run `.venv/bin/python challenge2_model/train.py` and check the comparison table to see if it helped.
