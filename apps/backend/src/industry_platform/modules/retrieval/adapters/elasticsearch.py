"""Bounded Elasticsearch BM25 reader for authorized Knowledge chunks."""

import asyncio
import json
import math
from dataclasses import dataclass
from uuid import UUID

import httpx2

from industry_platform.modules.retrieval.domain import LexicalCandidate
from industry_platform.modules.retrieval.ports import LexicalSearchDependencyError

_MAX_RESPONSE_BYTES = 1_048_576


@dataclass(frozen=True, slots=True)
class ElasticsearchLexicalIndex:
    client: httpx2.AsyncClient
    endpoint: str | None
    api_key: str | None
    index: str
    timeout_seconds: float

    async def search(
        self,
        query: str,
        *,
        workspace_id: UUID,
        knowledge_base_ids: tuple[UUID, ...],
        document_version_ids: tuple[UUID, ...],
        limit: int,
    ) -> tuple[LexicalCandidate, ...]:
        endpoint = self.endpoint
        normalized_query = query.strip()
        if endpoint is None:
            raise LexicalSearchDependencyError("lexical_index_not_configured")
        if (
            not normalized_query
            or len(normalized_query) > 2_000
            or workspace_id.int == 0
            or not knowledge_base_ids
            or not document_version_ids
            or not 1 <= limit <= 100
        ):
            raise ValueError("Lexical search request is invalid")
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"ApiKey {self.api_key}"
        try:
            async with asyncio.timeout(self.timeout_seconds):
                response = await self.client.post(
                    f"{endpoint.rstrip('/')}/{self.index}/_search",
                    headers=headers,
                    json={
                        "_source": ["chunk_id", "document_version_id"],
                        "query": {
                            "bool": {
                                "filter": [
                                    {"term": {"workspace_id": str(workspace_id)}},
                                    {
                                        "terms": {
                                            "knowledge_base_id": [
                                                str(item) for item in knowledge_base_ids
                                            ]
                                        }
                                    },
                                    {
                                        "terms": {
                                            "document_version_id": [
                                                str(item) for item in document_version_ids
                                            ]
                                        }
                                    },
                                ],
                                "must": [{"match": {"text": {"query": normalized_query}}}],
                            }
                        },
                        "size": limit,
                        "track_total_hits": False,
                    },
                    follow_redirects=False,
                    timeout=self.timeout_seconds,
                )
        except (TimeoutError, httpx2.TimeoutException):
            raise LexicalSearchDependencyError("lexical_search_timeout") from None
        except (httpx2.RequestError, httpx2.InvalidURL):
            raise LexicalSearchDependencyError("lexical_search_unavailable") from None
        if response.status_code == 404:
            raise LexicalSearchDependencyError("lexical_index_missing")
        if response.status_code != 200:
            raise LexicalSearchDependencyError("lexical_search_failed")
        if len(response.content) > _MAX_RESPONSE_BYTES:
            raise LexicalSearchDependencyError("lexical_search_response_too_large")
        try:
            document: object = response.json()
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            raise LexicalSearchDependencyError("lexical_search_response_invalid") from None
        if not isinstance(document, dict):
            raise LexicalSearchDependencyError("lexical_search_response_invalid")
        hits_container = document.get("hits")
        hits = hits_container.get("hits") if isinstance(hits_container, dict) else None
        if not isinstance(hits, list) or len(hits) > limit:
            raise LexicalSearchDependencyError("lexical_search_response_invalid")
        candidates: list[LexicalCandidate] = []
        seen: set[tuple[UUID, UUID]] = set()
        try:
            for hit in hits:
                if not isinstance(hit, dict):
                    raise ValueError
                source = hit.get("_source")
                score = hit.get("_score")
                if (
                    not isinstance(source, dict)
                    or isinstance(score, bool)
                    or not isinstance(score, int | float)
                    or not math.isfinite(score)
                    or score < 0
                ):
                    raise ValueError
                chunk_id = UUID(str(source.get("chunk_id")))
                document_version_id = UUID(str(source.get("document_version_id")))
                key = (chunk_id, document_version_id)
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(
                    LexicalCandidate(
                        chunk_id=chunk_id,
                        document_version_id=document_version_id,
                        score=float(score),
                    )
                )
        except (TypeError, ValueError, AttributeError):
            raise LexicalSearchDependencyError("lexical_search_response_invalid") from None
        return tuple(candidates)
