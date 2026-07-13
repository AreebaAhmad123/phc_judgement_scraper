"""Weaviate v4 client setup and collection ("class") schema.

Vectors are supplied by us (embeddings.py), not by a Weaviate-side
vectorizer module - keeps embedding logic in one place, in Python, testable
without a running Weaviate instance, and swappable without touching the
schema.

Deterministic UUIDs (weaviate.util.generate_uuid5 over each chunk_id) are
what make ingestion an upsert instead of an append: inserting an object
with an ID that already exists REPLACES it. This is what "only embed
new/changed records" in ingest.py relies on - it doesn't need to delete
before inserting for a changed chunk, only for a chunk that no longer
exists at all (see ingest.py's shrinkage handling).
"""

#an open-source, cloud-native vector database purpose-built to scale artificial intelligence and semantic search applications
import weaviate

import weaviate.classes as wvc

#fetches a helper utility from the Weaviate client library used to generate deterministic UUIDs (Universally Unique Identifiers)
from weaviate.util import generate_uuid5

from . import config

_client = None


def get_client():
    #Opening a new network connection to a database is "expensive" (it takes time and memory). To avoid opening a brand-new connection every single time you want to search or insert data, the function checks a global variable _client.

    global _client

    # If _client is empty (None), it means this is the very first time your app is starting up, so it needs to build the connection. It looks at your configuration files to decide where your database live.
    if _client is not None:
        return _client
    #Path A: Weaviate Cloud (Production)
    if config.WEAVIATE_API_KEY:
        _client = weaviate.connect_to_weaviate_cloud(
            cluster_url=config.WEAVIATE_URL,
            auth_credentials=weaviate.auth.AuthApiKey(config.WEAVIATE_API_KEY),
        )
    else:
        # Local / self-hosted (e.g. docker-compose.weaviate.yml), no auth.
        _client = weaviate.connect_to_custom(
            http_host=config.WEAVIATE_HTTP_HOST, http_port=config.WEAVIATE_HTTP_PORT,
            http_secure=config.WEAVIATE_HTTP_SECURE,
            grpc_host=config.WEAVIATE_GRPC_HOST, grpc_port=config.WEAVIATE_GRPC_PORT,
            grpc_secure=config.WEAVIATE_HTTP_SECURE,
        )
    return _client


def close_client():
    global _client
    if _client is not None:
        _client.close()
        _client = None


def ensure_schema():
    client = get_client()
    if client.collections.exists(config.WEAVIATE_COLLECTION):
        return client.collections.get(config.WEAVIATE_COLLECTION)

    return client.collections.create(
        name=config.WEAVIATE_COLLECTION,
        #By default, Weaviate tries to be helpful and convert your text into math vectors using its own cloud modules. By setting this to .none(), you are telling Weaviate: "Hands off. I will handle turning the court text into vectors inside my own Python code and send you the raw numbers directly
        vectorizer_config=wvc.config.Configure.Vectorizer.none(),
        properties=[
            wvc.config.Property(name="record_id", data_type=wvc.config.DataType.TEXT),
            wvc.config.Property(name="chunk_id", data_type=wvc.config.DataType.TEXT),
            wvc.config.Property(name="chunk_type", data_type=wvc.config.DataType.TEXT),
            wvc.config.Property(name="chunk_index", data_type=wvc.config.DataType.INT),
            wvc.config.Property(name="text", data_type=wvc.config.DataType.TEXT),
            wvc.config.Property(name="case_info", data_type=wvc.config.DataType.TEXT),
            wvc.config.Property(name="year", data_type=wvc.config.DataType.INT),
            wvc.config.Property(name="category", data_type=wvc.config.DataType.TEXT),
            wvc.config.Property(name="decision_date", data_type=wvc.config.DataType.TEXT),
            wvc.config.Property(name="source_url", data_type=wvc.config.DataType.TEXT),
            wvc.config.Property(name="gdrive_view_url", data_type=wvc.config.DataType.TEXT),
            wvc.config.Property(name="chunk_hash", data_type=wvc.config.DataType.TEXT),
            wvc.config.Property(name="ingested_at", data_type=wvc.config.DataType.TEXT),
        ],
    )


def chunk_uuid(chunk_id):
    return generate_uuid5(chunk_id)


def delete_chunk_indices_from(record_id, chunk_type, from_index):
    """Deletes any chunks at index >= from_index for this record/type -
    needed when a re-ingested document has FEWER chunks than it used to
    (e.g. the PDF text extraction changed), so stale trailing chunks don't
    linger and get retrieved forever. Cheap: a filtered delete, not a scan."""
    collection = get_client().collections.get(config.WEAVIATE_COLLECTION)
    collection.data.delete_many(
        where=(
            #Look inside the database and find only the pages belonging to case
            wvc.query.Filter.by_property("record_id").equal(record_id)
            #AND only look at pages that are part of the judgment_body (not the citations or the headers)
            & wvc.query.Filter.by_property("chunk_type").equal(chunk_type)
            #AND only look at pages that are at or after the page number we want to delete from
            & wvc.query.Filter.by_property("chunk_index").greater_or_equal(from_index)
        )
    )
