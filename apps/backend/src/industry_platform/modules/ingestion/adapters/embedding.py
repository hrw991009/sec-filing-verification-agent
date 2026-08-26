"""Deterministic bounded embedding adapter for the Day 5 dense baseline."""

import hashlib
import math
import re

from industry_platform.modules.ingestion.domain import (
    ChunkEmbedding,
    EmbeddingInput,
)
from industry_platform.modules.ingestion.index_contract import EMBEDDING_DIMENSION

_TOKEN_PATTERN = re.compile(r"[\w]+", re.UNICODE)


class DeterministicHashEmbeddingProvider:
    """Produce stable normalized feature-hash vectors without hidden network access."""

    async def embed(self, inputs: tuple[EmbeddingInput, ...]) -> tuple[ChunkEmbedding, ...]:
        return tuple(
            ChunkEmbedding(
                chunk_id=item.chunk_id,
                content_hash=item.content_hash,
                vector=embed_query_text(item.text),
            )
            for item in inputs
        )


def embed_query_text(text: str) -> tuple[float, ...]:
    """Use the exact indexed feature-hash contract for Dense query vectors."""

    if not text.strip() or len(text) > 20_000 or "\x00" in text:
        raise ValueError("Embedding query text is invalid")
    vector = [0.0] * EMBEDDING_DIMENSION
    tokens = _TOKEN_PATTERN.findall(text.casefold())
    if not tokens:
        tokens = [text.casefold()]
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:4], "big") % EMBEDDING_DIMENSION
        sign = 1.0 if digest[4] & 1 else -1.0
        vector[bucket] += sign
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        vector[0] = 1.0
        norm = 1.0
    return tuple(value / norm for value in vector)
