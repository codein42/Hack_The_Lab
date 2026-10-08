"""Step 4 - search the vectors. Used by the chatbot / dashboard to fetch context
for the LLM; also runnable from the terminal to try it out:

    .venv/bin/python src/search.py "Kunden aus der Luftfahrt mit sinkenden Aufträgen"
    .venv/bin/python src/search.py "Warum ist MR7 überlastet?" --collection labor_monat
    .venv/bin/python src/search.py "Was bedeutet ISTMASS?" --collection wissen

In code:
    from search import search
    hits = search("Kunden mit überfälligen Messmitteln", collection="kunde", k=10,
                  where=lambda m: m["branche"] == "Medical")
"""
from pathlib import Path
from functools import lru_cache
import argparse
import json
import pickle
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
VDB = ROOT / "vectordb"
MODEL = "intfloat/multilingual-e5-small"


@lru_cache
def _load():
    df = pd.read_parquet(VDB / "vectors.parquet")
    X = np.vstack(df.vector.to_numpy()).astype("float32")
    df["meta"] = df.meta.map(json.loads)
    return df.drop(columns="vector"), X, (VDB / "backend.txt").read_text().strip()


@lru_cache
def _encoder(backend: str):
    if backend == "tfidf":
        vec = pickle.load(open(VDB / "tfidf.pkl", "rb"))
        return lambda q: vec.transform([q])[0].astype("float32")
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(MODEL)
    return lambda q: model.encode("query: " + q, normalize_embeddings=True)


def search(query: str, collection: str | None = None, k: int = 5, where=None) -> pd.DataFrame:
    """Return the k most similar documents (columns: id, collection, score, text, meta)."""
    df, X, backend = _load()
    q = _encoder(backend)(query)
    q = q / (np.linalg.norm(q) + 1e-9)
    scores = X @ q
    mask = np.ones(len(df), bool)
    if collection:
        mask &= (df.collection == collection).to_numpy()
    if where:
        mask &= df.meta.map(where).to_numpy()
    idx = np.where(mask)[0]
    top = idx[np.argsort(-scores[idx])[:k]]
    out = df.iloc[top].copy()
    out.insert(2, "score", scores[top].round(3))
    return out


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("query")
    p.add_argument("--collection", choices=["kunde", "branche", "labor", "labor_monat", "wissen"])
    p.add_argument("-k", type=int, default=5)
    a = p.parse_args()
    for _, r in search(a.query, a.collection, a.k).iterrows():
        print(f"\n[{r.score:.3f}] {r.id}\n{r.text}")
