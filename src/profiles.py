"""Step 2 - aggregate the cleaned tables into one row per entity and write
a short text profile for each (these texts are what gets embedded).

Outputs (parquet for code, CSV for looking at in Excel / VS Code):
  build/kunden_profile.{parquet,csv}    one row per customer   (Challenge 2)
  build/branchen_profile.{parquet,csv}  one row per industry   (Challenge 2)
  build/labor_monat.{parquet,csv}       one row per lab+month  (Challenge 1)
  build/labor_profile.{parquet,csv}     one row per lab        (Challenge 1)
  build/documents.parquet               all profile texts, ready to embed

Run after clean.py:  .venv/bin/python src/profiles.py
"""
from pathlib import Path
import json
import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA, BUILD = ROOT / "data", ROOT / "build"
REF = "2026-09-24"          # last day in the export = "today"
YTD_START_PREV, YTD_END_PREV = "2025-01-01", "2025-09-24"
YTD_START, YTD_END = "2026-01-01", REF

con = duckdb.connect()
con.execute(f"CREATE VIEW mm  AS SELECT * FROM '{BUILD / 'messmittel.parquet'}'")
con.execute(f"CREATE VIEW kal AS SELECT * FROM '{BUILD / 'kalibrierungen.parquet'}'")
con.execute(f"CREATE VIEW dl  AS SELECT * FROM '{BUILD / 'dienstleistungen.parquet'}'")
con.execute(f"CREATE VIEW kd  AS SELECT * FROM '{BUILD / 'kunden.parquet'}'")
con.execute(f"CREATE VIEW ist AS SELECT * FROM '{DATA / 'Ist_Stunden_24-26.parquet'}'")

LAB_NAMES = {"MR1": "Endmaße", "MR2": "Drehmomente", "MR3": "Messuhren", "MR4": "Lohnmessung",
             "MR5": "Bügelmessschrauben", "MR6": "Messschieber", "MR7": "Lehren", "MR8": "Temperatur",
             "MR9": "Elektro", "MR11": "KMG", "MR12": "Druck", "MR14": "Waage"}


def save(df: pd.DataFrame, name: str) -> None:
    df.to_parquet(BUILD / f"{name}.parquet", index=False)
    df.to_csv(BUILD / f"{name}.csv", index=False, sep=";", decimal=",", encoding="utf-8-sig")
    print(f"{name:<18} {len(df):>7,} rows -> build/{name}.parquet + .csv")


def pct(x) -> str:
    return "n/a" if pd.isna(x) else f"{x:.0%}"


# average processing minutes per instrument group (2025 calibrations)
con.execute("""
    CREATE TABLE min_gruppe AS
    SELECT gruppe, avg(minuten) AS minuten FROM dl
    WHERE dl_typ LIKE 'KALIBRIERUNG%' AND status = 'ERBRACHT' AND year(monat) = 2025 AND minuten IS NOT NULL
    GROUP BY 1
""")

# =========================================================== CUSTOMERS
kunden = con.execute(f"""
WITH inst AS (
    SELECT kunde,
           count(*) FILTER (WHERE aktiv)                                         AS messmittel_aktiv,
           count(*) FILTER (WHERE aktiv AND faellig BETWEEN DATE '{REF}' AND DATE '{REF}' + 90) AS faellig_90t,
           count(*) FILTER (WHERE aktiv AND faellig BETWEEN DATE '{REF}' - 730 AND DATE '{REF}' - 30
                            AND (letzte_kalibrierung IS NULL OR letzte_kalibrierung < faellig)) AS ueberfaellig,
           sum(coalesce(g.minuten, 10)) FILTER (WHERE aktiv AND (
                 faellig BETWEEN DATE '{REF}' AND DATE '{REF}' + 90
              OR faellig BETWEEN DATE '{REF}' - 730 AND DATE '{REF}' - 30)) / 60 AS potenzial_stunden
    FROM mm LEFT JOIN min_gruppe g USING (gruppe)
    WHERE kunde IS NOT NULL GROUP BY 1
),
k AS (
    SELECT kunde,
           count(*) FILTER (WHERE year(datum) = 2024)                                   AS kal_2024,
           count(*) FILTER (WHERE year(datum) = 2025)                                   AS kal_2025,
           count(*) FILTER (WHERE datum BETWEEN '{YTD_START_PREV}' AND '{YTD_END_PREV}') AS kal_ytd_2025,
           count(*) FILTER (WHERE datum BETWEEN '{YTD_START}' AND '{YTD_END}')           AS kal_ytd_2026,
           count(*) FILTER (WHERE datum > DATE '{REF}' - 365)                            AS kal_12m,
           max(datum)::DATE                                                             AS letzte_kalibrierung,
           avg((pruefungsart = 'DAkkS')::INT)                                            AS dakks_anteil,
           avg(nio::INT)                                                                 AS nio_quote
    FROM kal WHERE kunde IS NOT NULL GROUP BY 1
)
SELECT coalesce(i.kunde, k.kunde) AS kunde, kd.branche,
       coalesce(i.messmittel_aktiv, 0) AS messmittel_aktiv,
       coalesce(k.kal_2024, 0) AS kal_2024, coalesce(k.kal_2025, 0) AS kal_2025,
       coalesce(k.kal_ytd_2025, 0) AS kal_ytd_2025, coalesce(k.kal_ytd_2026, 0) AS kal_ytd_2026,
       k.letzte_kalibrierung,
       date_diff('day', k.letzte_kalibrierung, DATE '{REF}') AS tage_seit_letzter,
       coalesce(i.faellig_90t, 0) AS faellig_90t,
       coalesce(i.ueberfaellig, 0) AS ueberfaellig,
       round(coalesce(k.kal_12m, 0) / nullif(coalesce(k.kal_12m, 0) + coalesce(i.ueberfaellig, 0), 0), 3) AS ruecklaufquote,
       round(k.dakks_anteil, 3) AS dakks_anteil, round(k.nio_quote, 3) AS nio_quote,
       round(coalesce(i.potenzial_stunden, 0), 1) AS potenzial_stunden
FROM inst i FULL JOIN k USING (kunde)
LEFT JOIN kd ON kd.kunde = coalesce(i.kunde, k.kunde)
""").df()

kunden["trend_ytd"] = (kunden.kal_ytd_2026 / kunden.kal_ytd_2025.where(kunden.kal_ytd_2025 > 0) - 1).round(3)

# --- equipment mix per customer and per industry (the "portfolio vectors")
mix = con.execute("""
    SELECT kunde, branche, gruppe, count(*) AS n FROM mm
    WHERE aktiv AND kunde IS NOT NULL AND gruppe IS NOT NULL GROUP BY ALL
""").df()
mix["anteil"] = mix.n / mix.groupby("kunde").n.transform("sum")
cust_vec = mix.pivot_table(index="kunde", columns="gruppe", values="anteil", fill_value=0)

# industry centroid = average mix of its customers with >= 20 instruments
size = mix.groupby("kunde").n.sum()
branche_of = mix.drop_duplicates("kunde").set_index("kunde").branche
big = size[size >= 20].index
ind_vec = cust_vec.loc[big].groupby(branche_of.loc[big]).mean()

top_groups, gaps, sim = {}, {}, {}
for kunde, row in cust_vec.iterrows():
    top = row[row > 0].sort_values(ascending=False).head(3)
    top_groups[kunde] = ", ".join(f"{g} {v:.0%}" for g, v in top.items())
    b = branche_of.get(kunde)
    if size.get(kunde, 0) >= 20 and b in ind_vec.index:
        ref = ind_vec.loc[b]
        sim[kunde] = float((row @ ref) / ((row @ row) ** 0.5 * (ref @ ref) ** 0.5))
        # groups common in the industry (>=5 %) where this customer has < 1/4 of that share
        g = ref[(ref >= 0.05) & (row < ref * 0.25)].sort_values(ascending=False).head(3)
        gaps[kunde] = ", ".join(f"{x} (Branche {v:.0%})" for x, v in g.items())

kunden["top_gruppen"] = kunden.kunde.map(top_groups)
kunden["luecken_vs_branche"] = kunden.kunde.map(gaps).fillna("")
kunden["aehnlichkeit_branche"] = kunden.kunde.map(sim).round(3)

# --- transparent placeholder risk score (0-100). Replace with the trained churn model later.
t = kunden.trend_ytd.clip(-1, 1).fillna(0)
r = kunden.ruecklaufquote.fillna(1)
d = (kunden.tage_seit_letzter.fillna(999).clip(0, 365) / 365)
active_before = (kunden.kal_2025 >= 10)
kunden["risiko_score"] = (active_before * (40 * (-t).clip(0, 1) + 35 * (1 - r) + 25 * d) * 100 / 100).round(0)

kunden = kunden.sort_values("potenzial_stunden", ascending=False)

def kunde_text(k) -> str:
    parts = [f"Kunde {k.kunde} | Branche: {k.branche or 'unbekannt'}",
             f"{k.messmittel_aktiv} aktive Messmittel" + (f" (Schwerpunkt: {k.top_gruppen})" if k.top_gruppen else ""),
             f"Kalibrierungen 2024: {k.kal_2024}, 2025: {k.kal_2025}; Jan-Sep 2025: {k.kal_ytd_2025} vs Jan-Sep 2026: {k.kal_ytd_2026}"
             + (f" (Trend {k.trend_ytd:+.0%})" if pd.notna(k.trend_ytd) else ""),
             f"Letzte Kalibrierung: {pd.Timestamp(k.letzte_kalibrierung):%Y-%m-%d} ({k.tage_seit_letzter} Tage her)" if pd.notna(k.letzte_kalibrierung) else "Noch keine Kalibrierung seit 2024",
             f"In 90 Tagen fällig: {k.faellig_90t}; überfällig und nicht eingegangen: {k.ueberfaellig}; Rücklaufquote 12M: {pct(k.ruecklaufquote)}",
             f"DAkkS-Anteil: {pct(k.dakks_anteil)}; n.i.O.-Quote: {pct(k.nio_quote)}; Potenzial: {k.potenzial_stunden:.0f} Std.",
             f"Risiko-Score: {k.risiko_score:.0f}/100"]
    if k.luecken_vs_branche:
        parts.append(f"Lücken gegenüber Branchenportfolio: {k.luecken_vs_branche}")
    return "\n".join(parts)

kunden["text"] = kunden.apply(kunde_text, axis=1)
save(kunden, "kunden_profile")
cust_vec.reset_index().to_parquet(BUILD / "kunden_portfolio_vektor.parquet", index=False)
ind_vec.reset_index().to_parquet(BUILD / "branchen_portfolio_vektor.parquet", index=False)

# =========================================================== INDUSTRIES
branchen = (kunden.groupby("branche").agg(
    kunden=("kunde", "count"),
    messmittel_median=("messmittel_aktiv", "median"),
    kal_2025=("kal_2025", "sum"),
    dakks_anteil=("dakks_anteil", "mean"),
    nio_quote=("nio_quote", "mean"),
    trend_ytd_median=("trend_ytd", "median"),
    risiko_hoch=("risiko_score", lambda s: int((s >= 50).sum())),
).round(3).reset_index())
intervall = con.execute("SELECT branche, median(intervall_monate) AS intervall_median FROM mm WHERE aktiv GROUP BY 1").df()
branchen = branchen.merge(intervall, on="branche", how="left")
branchen["typisches_portfolio"] = branchen.branche.map(
    lambda b: ", ".join(f"{g} {v:.0%}" for g, v in ind_vec.loc[b].sort_values(ascending=False).head(6).items())
    if b in ind_vec.index else "")
branchen["text"] = branchen.apply(lambda b: (
    f"Branche: {b.branche}\n{b.kunden} Kunden, median {b.messmittel_median:.0f} aktive Messmittel je Kunde\n"
    f"Typisches Portfolio: {b.typisches_portfolio}\n"
    f"Median Prüfintervall: {b.intervall_median:.0f} Monate; DAkkS-Anteil: {pct(b.dakks_anteil)}; n.i.O.-Quote: {pct(b.nio_quote)}\n"
    f"Kalibrierungen 2025: {b.kal_2025:,}; Median-Trend Jan-Sep 2026 vs 2025: {pct(b.trend_ytd_median)}; Kunden mit hohem Risiko: {b.risiko_hoch}"), axis=1)
save(branchen, "branchen_profile")

# =========================================================== LABS x MONTH
labor_monat = con.execute(f"""
WITH bedarf AS (   -- delivered work, in hours
    SELECT messraum, monat, count(*) AS kalibrierungen, sum(minuten) / 60 AS bedarf_std
    FROM dl WHERE dl_typ LIKE 'KALIBRIERUNG%' AND status = 'ERBRACHT' AND monat < DATE '{REF}'
    GROUP BY ALL
),
kapa AS (
    SELECT MESSRAUM AS messraum, date_trunc('month', Kalendertag)::DATE AS monat,
           sum("IST Anwesend STD") AS anwesend_std, sum("IST Krank STD") AS krank_std, sum("IST Urlaub STD") AS urlaub_std
    FROM ist GROUP BY ALL
),
faellig AS (       -- work coming due (next 18 months), if every instrument comes back
    SELECT messraum, date_trunc('month', faellig)::DATE AS monat,
           count(*) AS faellige_messmittel, sum(coalesce(g.minuten, 10)) / 60 AS faellig_std
    FROM mm LEFT JOIN min_gruppe g USING (gruppe)
    WHERE aktiv AND faellig >= date_trunc('month', DATE '{REF}') AND faellig < DATE '{REF}' + INTERVAL 18 MONTH
    GROUP BY ALL
)
SELECT messraum, monat, b.kalibrierungen, round(b.bedarf_std, 1) AS bedarf_std,
       round(c.anwesend_std, 1) AS anwesend_std, round(c.krank_std, 1) AS krank_std, round(c.urlaub_std, 1) AS urlaub_std,
       round(b.bedarf_std / nullif(c.anwesend_std, 0), 3) AS auslastung,
       f.faellige_messmittel, round(f.faellig_std, 1) AS faellig_std
FROM bedarf b FULL JOIN kapa c USING (messraum, monat) FULL JOIN faellig f USING (messraum, monat)
WHERE messraum IN ({", ".join(repr(x) for x in LAB_NAMES)})
ORDER BY messraum, monat
""").df()
save(labor_monat, "labor_monat")

def lm_text(r) -> str:
    name = f"{r.messraum} {LAB_NAMES.get(r.messraum, '')}".strip()
    if pd.notna(r.bedarf_std) or pd.notna(r.anwesend_std):
        s = (f"Labor {name}, Monat {r.monat:%Y-%m} (Ist)\n"
             f"Erbrachte Kalibrierungen: {r.kalibrierungen if pd.notna(r.kalibrierungen) else 0:.0f}, Arbeitsbedarf {r.bedarf_std or 0:.0f} Std.\n"
             f"Anwesenheit {r.anwesend_std or 0:.0f} Std., Krank {r.krank_std or 0:.0f} Std., Urlaub {r.urlaub_std or 0:.0f} Std.\n"
             f"Auslastung (Bedarf/Anwesenheit): {pct(r.auslastung)}")
    else:
        s = f"Labor {name}, Monat {r.monat:%Y-%m} (Prognose)"
    if pd.notna(r.faellige_messmittel):
        s += f"\nFällig laut Messmitteldaten: {r.faellige_messmittel:.0f} Messmittel, ca. {r.faellig_std:.0f} Std. Arbeit (wenn alle eingehen)"
    return s
labor_monat["text"] = labor_monat.apply(lm_text, axis=1)

# =========================================================== LAB PROFILES
y25 = labor_monat[labor_monat.monat.astype(str).str.startswith("2025")].groupby("messraum").agg(
    bedarf_2025=("bedarf_std", "sum"), anwesend_2025=("anwesend_std", "sum"), krank_2025=("krank_std", "sum"))
y25["auslastung_2025"] = (y25.bedarf_2025 / y25.anwesend_2025).round(3)
ahead = labor_monat.dropna(subset=["faellig_std"])
peak = ahead.loc[ahead.groupby("messraum").faellig_std.idxmax()].set_index("messraum")
groups = con.execute("""
    SELECT messraum, string_agg(gruppe || ' ' || n, ', ' ORDER BY n DESC) AS gruppen FROM (
      SELECT messraum, gruppe, count(*) AS n, row_number() OVER (PARTITION BY messraum ORDER BY count(*) DESC) AS rk
      FROM mm WHERE aktiv GROUP BY 1, 2) WHERE rk <= 5 GROUP BY 1
""").df().set_index("messraum").gruppen
labore = y25.join(peak[["monat", "faellig_std"]].rename(columns={"monat": "spitzenmonat", "faellig_std": "spitze_std"}))
labore["name"] = labore.index.map(LAB_NAMES)
labore["haupt_gruppen"] = labore.index.map(groups)
labore = labore.reset_index().round({"bedarf_2025": 1, "anwesend_2025": 1, "krank_2025": 1, "spitze_std": 1})
labore["text"] = labore.apply(lambda l: (
    f"Labor {l.messraum} {l['name']}\nHauptgruppen (aktive Messmittel): {l.haupt_gruppen}\n"
    f"2025: Arbeitsbedarf {l.bedarf_2025:.0f} Std., Anwesenheit {l.anwesend_2025:.0f} Std., Auslastung {pct(l.auslastung_2025)}, Krank {l.krank_2025:.0f} Std.\n"
    f"Höchste kommende Fälligkeit: {l.spitzenmonat:%Y-%m} mit ca. {l.spitze_std:.0f} Std."), axis=1)
save(labore, "labor_profile")

# =========================================================== DOCUMENTS
def docs(df, collection, id_col, meta_cols):
    out = df[[id_col, "text"] + meta_cols].copy()
    out["id"] = collection + ":" + out[id_col].astype(str)
    out["collection"] = collection
    out["meta"] = out[meta_cols].astype(str).to_dict("records")
    out["meta"] = out.meta.map(json.dumps)
    return out[["id", "collection", "text", "meta"]]

labor_monat["lm_id"] = labor_monat.messraum + "_" + labor_monat.monat.astype(str).str[:7]
documents = pd.concat([
    docs(kunden[kunden.messmittel_aktiv + kunden.kal_2025 > 0], "kunde", "kunde",
         ["branche", "risiko_score", "potenzial_stunden", "ueberfaellig", "faellig_90t"]),
    docs(branchen, "branche", "branche", ["kunden"]),
    docs(labore, "labor", "messraum", ["auslastung_2025"]),
    docs(labor_monat, "labor_monat", "lm_id", ["messraum", "monat", "auslastung"]),
], ignore_index=True)

# hand-written knowledge docs (knowledge/*.md, one doc per "## " section)
for md in sorted((ROOT / "knowledge").glob("*.md")):
    title, *sections = md.read_text(encoding="utf-8").split("\n## ")
    title = title.strip().lstrip("# ").strip()
    for i, sec in enumerate(sections, 1):
        documents.loc[len(documents)] = [f"wissen:{md.stem}:{i}", "wissen", f"{title} - {sec.strip()}",
                                         json.dumps({"datei": md.name})]

documents.to_parquet(BUILD / "documents.parquet", index=False)
print(f"\ndocuments.parquet: {len(documents):,} texts")
print(documents.collection.value_counts().to_string())
