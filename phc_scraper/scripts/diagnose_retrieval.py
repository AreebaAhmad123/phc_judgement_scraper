"""Run this from the phc-scraper project root (same venv as everything else):
    python scripts/diagnose_retrieval.py
It isolates WHICH of the three likely causes is behind the empty chat response.
"""
from phc_scraper.weaviate_client import get_client
from phc_scraper.embeddings import embed_query
import weaviate.classes as wvc

COLLECTION_NAME = "PHCJudgmentChunk"

client = get_client()
collection = client.collections.get(COLLECTION_NAME)

# 1. Is there anything in the collection at all?
count = collection.aggregate.over_all(total_count=True).total_count
print(f"1. Total objects in '{COLLECTION_NAME}': {count}")
if count == 0:
    print("   -> Collection is EMPTY. Weaviate lost its data (container "
          "recreated / volume wiped / pointed at a different instance) even "
          "though ingestion_state.json thinks records were ingested.")
    print("   FIX: delete data/ingestion_state.json (forces a full "
          "re-embed/re-insert) and re-run `python -m phc_scraper.ingest`.")
    client.close()
    raise SystemExit

# 2. Sanity check one stored vector's dimensionality vs a fresh query vector
sample = collection.query.fetch_objects(limit=1, include_vector=True).objects[0]
stored_dim = len(sample.vector["default"]) if isinstance(sample.vector, dict) else len(sample.vector)
query_vec = embed_query("test query")
print(f"2. Stored vector dim: {stored_dim} | Fresh query vector dim: {len(query_vec)}")
if stored_dim != len(query_vec):
    print("   -> MISMATCH. EMBEDDING_MODEL_NAME changed after ingestion, or "
          "embed_texts/embed_query drifted apart. FIX: re-run ingestion with "
          "the current model (delete ingestion_state.json first).")
    client.close()
    raise SystemExit

# 3. near_vector with NO filter at all - does retrieval work in principle?
result_nofilter = collection.query.near_vector(
    near_vector=query_vec, limit=5,
    return_metadata=wvc.query.MetadataQuery(distance=True),
)
print(f"3. near_vector with no filter, limit=5 -> {len(result_nofilter.objects)} objects")
for obj in result_nofilter.objects:
    print(f"     distance={obj.metadata.distance:.4f}  chunk_type={obj.properties.get('chunk_type')}  "
          f"record_id={obj.properties.get('record_id')}")

if len(result_nofilter.objects) == 0:
    print("   -> Objects exist but near_vector STILL returns nothing. "
          "Check that objects were inserted WITH a vector (batch.add_object("
          "..., vector=...)) and not just properties with vector=None.")
else:
    print("   -> Retrieval works fine with no filter. If your chat endpoint "
          "still returns empty, the problem is the `chunk_type` filter your "
          "request is sending - check it's exactly 'metadata', "
          "'judgment_pdf', or 'sc_judgment_pdf' (or omitted/null entirely).")

client.close()