# Data → vector pipeline (shared by Challenge 1 and 2)

```
data/*.parquet  ──clean.py──▶  build/ (clean tables)  ──profiles.py──▶  build/*_profile.csv + documents.parquet  ──embed.py──▶  vectordb/vectors.parquet  ──search.py──▶ LLM context
```

## Setup (once)
```
.venv/bin/pip install -r requirements.txt
```

## Run (about 1 minute, plus a one-time model download)
```
.venv/bin/python src/clean.py       # cleanup: due dates, article minutes, customer industry
.venv/bin/python src/profiles.py    # one row + one text per customer / industry / lab / lab-month
.venv/bin/python src/embed.py       # text -> vectors (multilingual-e5-small)
.venv/bin/python src/search.py "Warum ist MR7 überlastet?"
```
No internet for the model? `EMBED_BACKEND=tfidf .venv/bin/python src/embed.py` (keyword-based fallback).

## What to open and look at (CSV, `;`-separated, opens in Excel)
| File | One row per | For |
|---|---|---|
| build/kunden_profile.csv | customer (6.5k) | Challenge 2: risk, overdue, potential, gaps vs industry |
| build/branchen_profile.csv | industry (15) | Challenge 2: typical portfolio per industry |
| build/labor_monat.csv | lab × month | Challenge 1: demand hours vs present hours, due work ahead |
| build/labor_profile.csv | lab (12) | Challenge 1: utilisation 2025, peak month |

Raw and cleaned big tables stay parquet (faster, smaller, keeps dates).

## Vector collections (build/documents.parquet → vectordb/)
| collection | count | example question |
|---|---|---|
| kunde | ~6,500 | "Why should we call customer 11978?" |
| branche | 15 | "What does an aerospace customer usually calibrate?" |
| labor / labor_monat | 12 / ~600 | "Why is MR7 overloaded in March 2027?" |
| wissen | 11 | "What does ISTMASS mean?", planning rules (knowledge/*.md) |

Add knowledge by writing more `## sections` into `knowledge/*.md` and re-running profiles.py + embed.py.

## Important
- Rankings ("top 20 customers to call") come from the numbers in kunden_profile, not from vector search.
  Vector search is for finding context to *explain* a result.
- `risiko_score` is a simple rule for now - replace it with the trained churn model.
- build/ and vectordb/ contain customer data and are git-ignored. Keep them local.

## Models + accuracy (prototype)
```
.venv/bin/python build_features.py   # training tables -> features/ (about 1 min)
.venv/bin/python src/train.py        # trains both models, prints accuracy (about 30 s)
```
Accuracy is measured on months the model never saw (trained on older data, tested on 2026 / H2 2025),
and compared with a simple guess. Results land in build/:
| File | What |
|---|---|
| c1_forecast.csv | workload vs capacity per lab at +3/+6/+12 months, green/amber/red |
| c2_today.csv | today's 20 calls: win-back, reminders, cross-sell, with reason |
| c2_call_list.csv | all ~2,000 candidates, ranked within action type |
| metrics.json | accuracy numbers for the dashboard |
