# Lab Planner website

Local dashboard combining Challenge 1 (capacity) and Challenge 2 (customers) for Admin and Sales.

## Run
```
.venv/bin/python website/build_data.py      # ~10 s, writes website/data/data.js
open website/index.html                      # or: python3 -m http.server 8642 --directory website
```
Re-run `build_data.py` after the forecast or the model changes. Charts load Chart.js from a CDN (needs internet).

## Tabs
| Tab | What |
|---|---|
| Dashboard | morning brief for Admin + Sales, forecast per industry, volume at risk, due pipeline, 2027 daily load |
| Admin | capacity calendar (lab × week, click → suggested fixes + Accept), vacation & skills planner with early warnings, qualifications expiring, maintenance windows, batching |
| Sales | seasonal/recurring demand radar (contact ~7 weeks ahead, status tracking), churn list with reason + next step + contract offer, quote tool (price from lab load), email drafts |
| What-if | demand ±, hire technicians, machine down, shift summer vacation, lose a top customer → live effect on load |

## Data sources
| Part | Source | Real / sample |
|---|---|---|
| Lab load 2027 | `forecast_data_2027/forecast_room_capacity_*.csv` | real forecast (MR12, MR14 have no capacity data) |
| Monthly forecast, industry risk, due pipeline | `challenge2_model/forecast/*.csv` | real |
| Churn, reasons, actions | `challenge2_model/forecast/customer_forecast.csv` | real model output |
| Recurring / seasonal orders | `data/KALIBRIERUNGEN.parquet` 2024-01 … 2026-09: same calendar month in every year seen, ≥10 cal. each year, ≥1.3× the customer's average month | real |
| Lab skills (instrument groups) + vacation pattern | KALIBRIERUNGEN + `Soll-Kapa` | real |
| Employees, personal skills, expiry dates, vacation weeks | generated (head count = planned FTE 2026) | **sample** – the DB has hours per lab, not per person |
| Prices | rule: express +15 % (+15 % if week >100 %), standard +5 % in peak, flexible −5/−8 % in quiet week | assumption, % of list price |

Accepted fixes, moved vacations and contact status are stored in the browser (localStorage) only.
