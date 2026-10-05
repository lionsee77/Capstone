"""Local, lazily loaded sentence embeddings reusable by taxonomy metrics."""
from __future__ import annotations

import re
import threading
import unicodedata

import numpy as np

MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
MODEL_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
TEXT_PROFILE = "semantic_labels_v1"
_model = None
_model_lock = threading.Lock()


def semantic_text(label: str | None) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", label or "")).strip()


def _get_model():
    global _model
    with _model_lock:
        if _model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise RuntimeError(
                    "Semantic matching requires sentence-transformers; install the pipeline's dependencies."
                ) from exc
            _model = SentenceTransformer(
                MODEL_ID, revision=MODEL_REVISION, device="cpu", trust_remote_code=False,
            )
        return _model


def encode_labels(texts: list[str]) -> dict[str, np.ndarray]:
    """Encode each unique label once; cache vectors only for the caller's run."""
    unique = list(dict.fromkeys(text for text in texts if text))
    if not unique:
        return {}
    try:
        vectors = np.asarray(_get_model().encode(
            unique, batch_size=32, normalize_embeddings=True,
            convert_to_numpy=True, show_progress_bar=False,
        ))
        if vectors.ndim != 2 or vectors.shape[0] != len(unique) or not np.isfinite(vectors).all():
            raise ValueError("model returned invalid embeddings")
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        if np.any(norms == 0):
            raise ValueError("model returned a zero embedding")
        vectors = vectors / norms
        return dict(zip(unique, vectors, strict=True))
    except Exception as exc:
        raise RuntimeError(f"Semantic embedding failed: {type(exc).__name__}: {exc}") from exc
