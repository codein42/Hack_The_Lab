"""Export every table in PeCalHackathon2026 to data/<table>.parquet.

Usage: SQLPWD='...' .venv/bin/python export_db.py
"""
import os
import pandas as pd
import pymssql

SERVER = "192.168.1.200"
USER = os.environ.get("SQLUSER", "PeCalHackathonParticipant")
DATABASE = "PeCalHackathon2026"
OUT_DIR = "data"
CHUNK = 100_000

conn = pymssql.connect(server=SERVER, user=USER, password=os.environ["SQLPWD"], database=DATABASE)
tables = pd.read_sql(
    "SELECT TABLE_SCHEMA, TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
    "WHERE TABLE_TYPE = 'BASE TABLE' AND TABLE_NAME <> 'sysdiagrams'",
    conn,
)
os.makedirs(OUT_DIR, exist_ok=True)
for schema, table in tables.itertuples(index=False):
    chunks = pd.read_sql(f"SELECT * FROM [{schema}].[{table}]", conn, chunksize=CHUNK)
    df = pd.concat(chunks, ignore_index=True)
    path = os.path.join(OUT_DIR, f"{table}.parquet")
    df.to_parquet(path, index=False)
    print(f"{table}: {len(df):,} rows, {df.shape[1]} cols -> {path}")
conn.close()
