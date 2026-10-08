"""Step 1 - shared cleanup for both challenges.

Reads the raw exports in data/ and writes cleaned tables to build/:
  build/messmittel.parquet        one row per instrument, with clean due dates
  build/kalibrierungen.parquet    calibration history (slim)
  build/dienstleistungen.parquet  services with processing minutes + lab
  build/kunden.parquet            customer -> industry

Run from the project root:  .venv/bin/python src/clean.py
"""
from pathlib import Path
import duckdb

ROOT = Path(__file__).resolve().parents[1]
DATA, BUILD = ROOT / "data", ROOT / "build"
BUILD.mkdir(exist_ok=True)

# Last day covered by the export. Used as "today" for all relative measures.
REF_DATE = "2026-09-24"

con = duckdb.connect()
for name in ["MESSMITTEL", "KALIBRIERUNGEN", "AUFTRAGSPOSITIONEN",
             "DIENSTLEISTUNGEN", "Kunde_Branche", "ArtikelnummerZeit"]:
    con.execute(f"CREATE VIEW raw_{name.lower()} AS SELECT * FROM '{DATA / (name + '.parquet')}'")


def save(sql: str, name: str) -> None:
    path = BUILD / f"{name}.parquet"
    con.execute(f"COPY ({sql}) TO '{path}' (FORMAT parquet)")
    n = con.execute(f"SELECT count(*) FROM '{path}'").fetchone()[0]
    print(f"{name:<18} {n:>9,} rows -> {path.relative_to(ROOT)}")


# ---------------------------------------------------------------- customers
save("""
    SELECT trim(KundenNr) AS kunde, Branche AS branche
    FROM raw_kunde_branche
    QUALIFY row_number() OVER (PARTITION BY trim(KundenNr) ORDER BY Branche) = 1
""", "kunden")

# ---------------------------------------------------------- calibrations
save("""
    SELECT MESSMITTEL_UUID            AS messmittel_uuid,
           trim(KUNDENNUMMER_SAP)     AS kunde,
           BEGINN                     AS datum,
           date_trunc('month', BEGINN)::DATE AS monat,
           BEWERTUNG                  AS bewertung,
           BEWERTUNG = 'NICHT_EINSATZFAEHIG' AS nio,
           PRUEFUNGSART               AS pruefungsart,
           MESSMITTELGRUPPE           AS gruppe,
           MESSMITTELTYP              AS typ,
           MESSRAUM                   AS messraum
    FROM raw_kalibrierungen
    WHERE BEGINN IS NOT NULL
""", "kalibrierungen")

# ------------------------------------------------------------- instruments
# Interval unit codes (from the data): 1 = years, 2 = months, 3 = weeks, 4 = days.
# "Real" interval = median gap between this instrument's own calibrations.
save(f"""
    WITH hist AS (
        SELECT messmittel_uuid, datum,
               date_diff('day', lag(datum) OVER (PARTITION BY messmittel_uuid ORDER BY datum), datum) AS gap
        FROM '{BUILD / 'kalibrierungen.parquet'}'
    ),
    real AS (
        SELECT messmittel_uuid,
               count(*)                               AS n_kalibrierungen,
               max(datum)                             AS letzte_kalibrierung,
               median(gap) FILTER (WHERE gap BETWEEN 60 AND 1100) / 30.44 AS intervall_real_monate
        FROM hist GROUP BY 1
    ),
    m AS (
        SELECT *,
               CASE EINHEIT_PRUEFINTERVALL
                    WHEN 1 THEN PRUEFINTERVALL * 12
                    WHEN 2 THEN PRUEFINTERVALL
                    WHEN 3 THEN PRUEFINTERVALL / 4.345
                    WHEN 4 THEN PRUEFINTERVALL / 30.44 END AS intervall_monate
        FROM raw_messmittel
    )
    SELECT m.MESSMITTEL_UUID        AS messmittel_uuid,
           trim(m.KUNDENNUMMER_SAP) AS kunde,
           k.branche,
           m.MESSMITTELGRUPPE       AS gruppe,
           m.MESSMITTELTYP          AS typ,
           m.MESSRAUM               AS messraum,
           m.FAELLIGKEIT_TYP        AS faelligkeit_typ,
           coalesce(m.FAELLIGKEIT_STOP, false) AS faelligkeit_stop,
           m.LETZTE_BEWERTUNG       AS letzte_bewertung,
           m.DATUM_LETZTE_PRUEFUNG  AS letzte_pruefung,
           -- active = still in use and still expected back
           (coalesce(m.LETZTE_BEWERTUNG,'') <> 'NICHT_EINSATZFAEHIG'
             AND NOT coalesce(m.FAELLIGKEIT_STOP, false)) AS aktiv,
           round(m.intervall_monate, 1)       AS intervall_monate,
           round(r.intervall_real_monate, 1)  AS intervall_real_monate,
           r.n_kalibrierungen,
           r.letzte_kalibrierung,
           -- recorded due date, junk (before 2015 / after 2035) removed
           CASE WHEN m.DATUM_NAECHSTE_PRUEFUNG BETWEEN '2015-01-01' AND '2035-12-31'
                THEN m.DATUM_NAECHSTE_PRUEFUNG::DATE END AS faellig_erfasst,
           -- best estimate of next due date: recorded > history > stated interval
           coalesce(
             CASE WHEN m.DATUM_NAECHSTE_PRUEFUNG BETWEEN '2015-01-01' AND '2035-12-31'
                  THEN m.DATUM_NAECHSTE_PRUEFUNG::DATE END,
             (coalesce(r.letzte_kalibrierung, m.DATUM_LETZTE_PRUEFUNG)
               + to_days(CAST(round(coalesce(r.intervall_real_monate, m.intervall_monate, 12) * 30.44) AS INTEGER)))::DATE
           ) AS faellig,
           CASE WHEN m.DATUM_NAECHSTE_PRUEFUNG BETWEEN '2015-01-01' AND '2035-12-31' THEN 'erfasst'
                WHEN r.intervall_real_monate IS NOT NULL THEN 'historie'
                WHEN coalesce(r.letzte_kalibrierung, m.DATUM_LETZTE_PRUEFUNG) IS NOT NULL THEN 'intervall'
                END AS faellig_quelle
    FROM m
    LEFT JOIN real r ON r.messmittel_uuid = m.MESSMITTEL_UUID
    LEFT JOIN '{BUILD / 'kunden.parquet'}' k ON k.kunde = trim(m.KUNDENNUMMER_SAP)
""", "messmittel")

# ---------------------------------------------------------------- services
# Article numbers differ between tables:
#   DIENSTLEISTUNGEN  '10110350 000200'   ArtikelnummerZeit  '011030 200'
# key = digits 2-7 of the first part + ' ' + size without leading zeros.
# Exact match ~43 %, product family (first part) ~99 % -> use family average.
save(f"""
    WITH z AS (
        SELECT trim(Artikelnummer) AS art, split_part(trim(Artikelnummer), ' ', 1) AS fam,
               BearbeitungszeitMin AS minuten
        FROM raw_artikelnummerzeit
    ),
    fam AS (SELECT fam, avg(minuten) AS minuten FROM z GROUP BY 1),
    d AS (
        SELECT *,
               substr(ARTIKELNUMMER, 2, 6) AS fam,
               substr(ARTIKELNUMMER, 2, 6) || ' ' ||
                 coalesce(CAST(try_cast(split_part(ARTIKELNUMMER, ' ', 2) AS BIGINT) AS VARCHAR), '') AS art
        FROM raw_dienstleistungen
    )
    SELECT d.AUFTRAGSPOSITION_UUID   AS auftragsposition_uuid,
           d.UUID_KALIBRIERGEGENSTAND AS messmittel_uuid,
           m.kunde, m.messraum, m.gruppe,
           d.DIENSTLEISTUNGSTYP       AS dl_typ,
           d.DIENSTLEISTUNG           AS dl_text,
           d.STATUS_DIENSTLESTUNG     AS status,
           d.ARTIKELNUMMER            AS artikelnummer,
           d.HINZUGEFUEGT_AM          AS hinzugefuegt,
           d.QUITTIERT_AM             AS erbracht,
           date_trunc('month', coalesce(d.QUITTIERT_AM, d.HINZUGEFUEGT_AM))::DATE AS monat,
           coalesce(z.minuten, f.minuten) AS minuten,
           CASE WHEN z.minuten IS NOT NULL THEN 'exakt'
                WHEN f.minuten IS NOT NULL THEN 'familie' END AS minuten_quelle
    FROM d
    LEFT JOIN z   ON z.art = d.art
    LEFT JOIN fam f ON f.fam = d.fam
    LEFT JOIN '{BUILD / 'messmittel.parquet'}' m ON m.messmittel_uuid = d.UUID_KALIBRIERGEGENSTAND
""", "dienstleistungen")

# ------------------------------------------------------------ quick checks
print()
print(con.execute(f"""
    SELECT faellig_quelle, count(*) AS n, sum(aktiv::INT) AS aktiv
    FROM '{BUILD / 'messmittel.parquet'}' GROUP BY 1 ORDER BY 2 DESC
""").df().to_string(index=False))
print()
print(con.execute(f"""
    SELECT dl_typ, count(*) AS n,
           round(100 * avg((minuten IS NOT NULL)::INT), 1) AS pct_mit_minuten
    FROM '{BUILD / 'dienstleistungen.parquet'}' GROUP BY 1 ORDER BY 2 DESC
""").df().to_string(index=False))
