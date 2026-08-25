"""Bounded Milvus REST query adapter for the unique Dense baseline."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from uuid import UUID

import httpx2

from industry_platform.modules.ingestion.index_contract import EMBEDDING_DIMENSION
from industry_platform.modules.retrieval.domain import DenseCandidate
from industry_platform.modules.retrieval.ports import DenseSearchDependencyError

_MAX_RESPONSE_BYTES = 1_048_576


@dataclass(frozen=True, slots=True)
class MilvusDenseIndex:
    client: httpx2.AsyncClient
    endpoint: str | None
    token: str | None
    collection: str
    timeout_seconds: float

    async def search(
        self,
        vector: tuple[float, ...],
        *,
        workspace_id: UUID,
        knowledge_base_ids: tuple[UUID, ...],
        document_version_ids: tuple[UUID, ...],
        limit: int,
    ) -> tuple[DenseCandidate, ...]:
        if self.endpoint is None:
            raise DenseSearchDependencyError("vector_index_not_configured")
        if (
            len(vector) != EMBEDDING_DIMENSION
            or not knowledge_base_ids
            or not document_version_ids
            or isinstance(limit, bool)
            or not 1 <= limit <= 20
        ):
            raise ValueError("Dense search request is invalid")
        filters = (
            f"workspace_id == {json.dumps(str(workspace_id))}",
            "knowledge_base_id in ["
            + ",".join(json.dumps(str(item)) for item in knowledge_base_ids)
            + "]",
            "document_version_id in ["
            + ",".join(json.dumps(str(item)) for item in document_version_ids)
            + "]",
        )
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            async with asyncio.timeout(self.timeout_seconds):
                response = await self.client.post(
                    f"{self.endpoint.rstrip('/')}/v2/vectordb/entities/search",
                    headers=headers,
                    json={
                        "collectionName": self.collection,
                        "annsField": "vector",
                        "data": [list(vector)],
                        "filter": " and ".join(filters),
                        "limit": limit,
                        "outputFields": ["chunk_id", "document_version_id"],
                    },
                    follow_redirects=False,
                    timeout=self.timeout_seconds,
                )
        except (TimeoutError, httpx2.TimeoutException):
            raise DenseSearchDependencyError("vector_search_timeout") from None
        except (httpx2.RequestError, httpx2.InvalidURL):
            raise DenseSearchDependencyError("vector_search_unavailable") from None
        if response.status_code != 200:
            raise DenseSearchDependencyError("vector_search_failed")
        if len(response.content) > _MAX_RESPONSE_BYTES:
            raise DenseSearchDependencyError("vector_search_response_too_large")
        try:
            document: object = response.json()
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            raise DenseSearchDependencyError("vector_search_response_invalid") from None
        if not isinstance(document, dict) or document.get("code") != 0:
            raise DenseSearchDependencyError("vector_search_failed")
        raw_data = document.get("data")
        if not isinstance(raw_data, list):
            raise DenseSearchDependencyError("vector_search_response_invalid")
        rows: list[dict[str, object]] = (
            raw_data[0] if len(raw_data) == 1 and isinstance(raw_data[0], list) else raw_data
        )
        candidates: list[DenseCandidate] = []
        seen: set[UUID] = set()
        try:
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError
                entity = row.get("entity", row)
                if not isinstance(entity, dict):
                    raise ValueError
                chunk_id = UUID(str(entity["chunk_id"]))
                version_id = UUID(str(entity["document_version_id"]))
                raw_score = row.get("distance", row.get("score"))
                if isinstance(raw_score, bool) or not isinstance(raw_score, (int, float)):
                    raise ValueError
                if chunk_id in seen:
                    continue
                seen.add(chunk_id)
                candidates.append(
                    DenseCandidate(
                        chunk_id=chunk_id,
                        document_version_id=version_id,
                        score=max(0.0, min(1.0, (float(raw_score) + 1.0) / 2.0)),
                    )
                )
        except (KeyError, TypeError, ValueError):
            raise DenseSearchDependencyError("vector_search_response_invalid") from None
        return tuple(candidates[:limit])
