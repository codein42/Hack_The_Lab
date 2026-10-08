"""Step 3 - turn every profile text in build/documents.parquet into a vector.

Writes vectordb/vectors.parquet (id, collection, text, meta, vector).
7k documents x 384 numbers = a few MB, so a plain parquet file + numpy search
is enough; no database server needed. (Swap in ChromaDB/LanceDB later if wanted.)

Model: intfloat/multilingual-e5-small (German + English, runs on a laptop).
First run downloads it (~470 MB) from Hugging Face.

    .venv/bin/python src/embed.py
    EMBED_BACKEND=tfidf .venv/bin/python src/embed.py   # offline fallback, no download
"""
from pathlib import Path
import os
import pickle
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BUILD, VDB = ROOT / "build", ROOT / "vectordb"
VDB.mkdir(exist_ok=True)
MODEL = "intfloat/multilingual-e5-small"
BACKEND = os.environ.get("EMBED_BACKEND", "e5")


def embed_passages(texts: list[str]) -> np.ndarray:
    if BACKEND == "tfidf":
        # keyword-based fallback: TF-IDF compressed to 384 dims (no download needed)
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.decomposition import TruncatedSVD
        from sklearn.pipeline import make_pipeline
        vec = make_pipeline(TfidfVectorizer(sublinear_tf=True), TruncatedSVD(384, random_state=0))
        X = vec.fit_transform(texts).astype("float32")
        pickle.dump(vec, open(VDB / "tfidf.pkl", "wb"))
    else:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(MODEL)
        # e5 models expect the prefix "passage: " for documents, "query: " for questions
        X = model.encode(["passage: " + t for t in texts], batch_size=64,
                         show_progress_bar=True, normalize_embeddings=True)
    X /= np.linalg.norm(X, axis=1, keepdims=True) + 1e-9
    return X.astype("float32")


if __name__ == "__main__":
    docs = pd.read_parquet(BUILD / "documents.parquet")
    print(f"Embedding {len(docs):,} documents with backend '{BACKEND}' ...")
    X = embed_passages(docs.text.tolist())
    docs["vector"] = list(X)
    docs.to_parquet(VDB / "vectors.parquet", index=False)
    (VDB / "backend.txt").write_text(BACKEND)
    print(f"Saved {X.shape[0]:,} vectors of size {X.shape[1]} -> vectordb/vectors.parquet")
