"""Bounded internal REST adapters for Milvus and Elasticsearch index writes."""

import asyncio
import json
from dataclasses import dataclass

import httpx2

from industry_platform.modules.ingestion.domain import (
    IndexableChunk,
    IngestionDependencyError,
)

_MAX_RESPONSE_BYTES = 1_048_576


async def _json_request(
    client: httpx2.AsyncClient,
    method: str,
    url: str,
    *,
    timeout_seconds: float,
    headers: dict[str, str] | None = None,
    json_body: dict[str, object] | None = None,
    content: bytes | None = None,
    expected_statuses: frozenset[int] = frozenset({200}),
    error_code: str,
) -> tuple[int, dict[str, object]]:
    try:
        async with asyncio.timeout(timeout_seconds):
            response = await client.request(
                method,
                url,
                headers=headers,
                json=json_body,
                content=content,
                follow_redirects=False,
                timeout=timeout_seconds,
            )
    except (TimeoutError, httpx2.TimeoutException):
        raise IngestionDependencyError(f"{error_code}_timeout") from None
    except (httpx2.RequestError, httpx2.InvalidURL):
        raise IngestionDependencyError(f"{error_code}_unavailable") from None
    if response.status_code not in expected_statuses:
        raise IngestionDependencyError(error_code)
    if len(response.content) > _MAX_RESPONSE_BYTES:
        raise IngestionDependencyError(f"{error_code}_response_too_large")
    if not response.content:
        return response.status_code, {}
    try:
        decoded: object = response.json()
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise IngestionDependencyError(f"{error_code}_response_invalid") from None
    if not isinstance(decoded, dict):
        raise IngestionDependencyError(f"{error_code}_response_invalid")
    return response.status_code, decoded


def _milvus_success(document: dict[str, object], *, error_code: str) -> None:
    code = document.get("code")
    if isinstance(code, bool) or not isinstance(code, int) or code != 0:
        raise IngestionDependencyError(error_code)


@dataclass(frozen=True, slots=True)
class MilvusVectorIndexWriter:
    client: httpx2.AsyncClient
    endpoint: str | None
    token: str | None
    collection: str
    dimension: int
    timeout_seconds: float

    async def _post(self, path: str, body: dict[str, object]) -> dict[str, object]:
        if self.endpoint is None:
            raise IngestionDependencyError("vector_index_not_configured")
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        _status, document = await _json_request(
            self.client,
            "POST",
            f"{self.endpoint.rstrip('/')}{path}",
            timeout_seconds=self.timeout_seconds,
            headers=headers,
            json_body=body,
            error_code="vector_index_failed",
        )
        _milvus_success(document, error_code="vector_index_failed")
        return document

    async def _ensure_collection(self) -> None:
        if await self._collection_exists():
            return
        try:
            await self._post(
                "/v2/vectordb/collections/create",
                {
                    "autoID": False,
                    "collectionName": self.collection,
                    "dimension": self.dimension,
                    "enableDynamicField": True,
                    "idType": "VarChar",
                    "metricType": "COSINE",
                    "params": {"max_length": "200"},
                    "primaryFieldName": "id",
                    "vectorFieldName": "vector",
                },
            )
        except IngestionDependencyError:
            if not await self._collection_exists():
                raise

    async def _collection_exists(self) -> bool:
        result = await self._post(
            "/v2/vectordb/collections/has",
            {"collectionName": self.collection},
        )
        data = result.get("data")
        has_collection = data.get("has") if isinstance(data, dict) else None
        if not isinstance(has_collection, bool):
            raise IngestionDependencyError("vector_index_response_invalid")
        return has_collection

    async def upsert(self, chunks: tuple[IndexableChunk, ...]) -> tuple[str, ...]:
        if not chunks:
            raise ValueError("Vector index write requires chunks")
        if any(len(chunk.vector) != self.dimension for chunk in chunks):
            raise ValueError("Vector index dimension does not match")
        await self._ensure_collection()
        await self._post(
            "/v2/vectordb/entities/upsert",
            {
                "collectionName": self.collection,
                "data": [
                    {
                        "chunk_id": str(chunk.chunk_id),
                        "document_id": str(chunk.document_id),
                        "document_version_id": str(chunk.document_version_id),
                        "id": chunk.external_id,
                        "knowledge_base_id": str(chunk.knowledge_base_id),
                        "page_number": chunk.page_number,
                        "vector": list(chunk.vector),
                        "workspace_id": str(chunk.workspace_id),
                    }
                    for chunk in chunks
                ],
            },
        )
        return tuple(chunk.external_id for chunk in chunks)

    async def delete(self, external_ids: tuple[str, ...]) -> None:
        if not external_ids:
            return
        if not await self._collection_exists():
            return
        for external_id in external_ids:
            await self._post(
                "/v2/vectordb/entities/delete",
                {
                    "collectionName": self.collection,
                    "filter": f"id == {json.dumps(external_id)}",
                },
            )


@dataclass(frozen=True, slots=True)
class ElasticsearchLexicalIndexWriter:
    client: httpx2.AsyncClient
    endpoint: str | None
    api_key: str | None
    index: str
    timeout_seconds: float

    def _headers(self, *, ndjson: bool = False) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/x-ndjson" if ndjson else "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"ApiKey {self.api_key}"
        return headers

    async def _ensure_index(self) -> None:
        if self.endpoint is None:
            raise IngestionDependencyError("lexical_index_not_configured")
        status, _document = await _json_request(
            self.client,
            "GET",
            f"{self.endpoint.rstrip('/')}/{self.index}",
            timeout_seconds=self.timeout_seconds,
            headers=self._headers(),
            expected_statuses=frozenset({200, 404}),
            error_code="lexical_index_failed",
        )
        if status == 200:
            return
        await _json_request(
            self.client,
            "PUT",
            f"{self.endpoint.rstrip('/')}/{self.index}",
            timeout_seconds=self.timeout_seconds,
            headers=self._headers(),
            json_body={
                "mappings": {
                    "dynamic": "strict",
                    "properties": {
                        "chunk_id": {"type": "keyword"},
                        "content_hash": {"type": "keyword"},
                        "document_id": {"type": "keyword"},
                        "document_version_id": {"type": "keyword"},
                        "knowledge_base_id": {"type": "keyword"},
                        "page_number": {"type": "integer"},
                        "text": {"type": "text"},
                        "workspace_id": {"type": "keyword"},
                    },
                },
                "settings": {"number_of_replicas": 0, "number_of_shards": 1},
            },
            expected_statuses=frozenset({200}),
            error_code="lexical_index_failed",
        )

    async def upsert(self, chunks: tuple[IndexableChunk, ...]) -> tuple[str, ...]:
        if not chunks:
            raise ValueError("Lexical index write requires chunks")
        endpoint = self.endpoint
        if endpoint is None:
            raise IngestionDependencyError("lexical_index_not_configured")
        await self._ensure_index()
        lines: list[str] = []
        for chunk in chunks:
            lines.append(json.dumps({"index": {"_id": chunk.external_id}}, separators=(",", ":")))
            lines.append(
                json.dumps(
                    {
                        "chunk_id": str(chunk.chunk_id),
                        "content_hash": chunk.content_hash,
                        "document_id": str(chunk.document_id),
                        "document_version_id": str(chunk.document_version_id),
                        "knowledge_base_id": str(chunk.knowledge_base_id),
                        "page_number": chunk.page_number,
                        "text": chunk.text,
                        "workspace_id": str(chunk.workspace_id),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
        _status, result = await _json_request(
            self.client,
            "POST",
            f"{endpoint.rstrip('/')}/{self.index}/_bulk?refresh=wait_for",
            timeout_seconds=self.timeout_seconds,
            headers=self._headers(ndjson=True),
            content=("\n".join(lines) + "\n").encode("utf-8"),
            error_code="lexical_index_failed",
        )
        if result.get("errors") is not False:
            raise IngestionDependencyError("lexical_index_failed")
        return tuple(chunk.external_id for chunk in chunks)

    async def delete(self, external_ids: tuple[str, ...]) -> None:
        if not external_ids:
            return
        endpoint = self.endpoint
        if endpoint is None:
            raise IngestionDependencyError("lexical_index_not_configured")
        status, _document = await _json_request(
            self.client,
            "GET",
            f"{endpoint.rstrip('/')}/{self.index}",
            timeout_seconds=self.timeout_seconds,
            headers=self._headers(),
            expected_statuses=frozenset({200, 404}),
            error_code="lexical_index_delete_failed",
        )
        if status == 404:
            return
        lines = [
            json.dumps({"delete": {"_id": external_id}}, separators=(",", ":"))
            for external_id in external_ids
        ]
        _status, result = await _json_request(
            self.client,
            "POST",
            f"{endpoint.rstrip('/')}/{self.index}/_bulk?refresh=wait_for",
            timeout_seconds=self.timeout_seconds,
            headers=self._headers(ndjson=True),
            content=("\n".join(lines) + "\n").encode("utf-8"),
            error_code="lexical_index_delete_failed",
        )
        if result.get("errors") is not False:
            raise IngestionDependencyError("lexical_index_delete_failed")
