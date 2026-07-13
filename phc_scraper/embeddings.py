"""Embedding wrapper around a local sentence-transformers model.

Deliberately local rather than a paid embeddings API: ingestion volume
grows daily forever, and embedding is by far the highest-QPS part of this
pipeline (one call per chunk, potentially thousands as the archive grows)
- a local model means no per-token cost and no rate limit to design
around here. Swap EMBEDDING_MODEL_NAME in .env for a bigger model if
retrieval quality needs it; nothing else in the codebase depends on which
model produced the vectors, only on all of them being produced the same
way (a model swap requires re-embedding everything - see ingest.py).
"""
from sentence_transformers import SentenceTransformer

from . import config

_model = None


def _get_model():
    global _model
    if _model is None:
        _model = SentenceTransformer(config.EMBEDDING_MODEL_NAME)
    return _model


def embed_texts(texts):
    """texts: list[str] -> list[list[float]], batched for efficiency."""
    if not texts:
        return []
    model = _get_model()
    vectors = model.encode(texts, batch_size=32, show_progress_bar=False,
                           normalize_embeddings=True)
    #uses a list comprehension to convert those heavy native array objects back into standard Python lists of floats (list[list[float]]) so they can be easily serialized into JSON or sent across database clients.
    return [v.tolist() for v in vectors]

# takes a single string text.
# 2. It wraps it inside a single-element list: [text].
# 3. It passes that list into the batch embed_texts() function we explained above.
# 4. Since embed_texts always returns a nested list of results, this function immediately extracts the very first vector using index slicing [0] and returns it.
def embed_query(text):
    return embed_texts([text])[0]
