"""Versioned embedding and external-index contract for Knowledge ingestion."""

from typing import Final

EMBEDDING_PROVIDER_NAME: Final = "deterministic-hash"
EMBEDDING_MODEL: Final = "feature-hash-64"
EMBEDDING_VERSION: Final = "1.0.0"
EMBEDDING_DIMENSION: Final = 64
EMBEDDING_NORMALIZATION: Final = "l2"
EMBEDDING_BATCH_SIZE: Final = 32
EMBEDDING_TIMEOUT_SECONDS: Final = 30

INDEX_VERSION: Final = "knowledge-index-v1"
MILVUS_COLLECTION: Final = "knowledge_chunks_v1"
ELASTICSEARCH_INDEX: Final = "knowledge_chunks_v1"


def embedding_config_snapshot() -> dict[str, object]:
    return {
        "batch_size": EMBEDDING_BATCH_SIZE,
        "dimension": EMBEDDING_DIMENSION,
        "model": EMBEDDING_MODEL,
        "normalization": EMBEDDING_NORMALIZATION,
        "provider": EMBEDDING_PROVIDER_NAME,
        "timeout_seconds": EMBEDDING_TIMEOUT_SECONDS,
        "version": EMBEDDING_VERSION,
    }


def index_config_snapshot() -> dict[str, object]:
    return {
        "elasticsearch_index": ELASTICSEARCH_INDEX,
        "index_version": INDEX_VERSION,
        "milvus_collection": MILVUS_COLLECTION,
    }
