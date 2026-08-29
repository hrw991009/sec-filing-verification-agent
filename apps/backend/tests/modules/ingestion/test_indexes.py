"""External index adapter request and response contracts."""

import json
from uuid import UUID

import httpx2
import pytest

from industry_platform.modules.ingestion.adapters.indexes import (
    ElasticsearchLexicalIndexWriter,
    MilvusVectorIndexWriter,
)
from industry_platform.modules.ingestion.domain import IndexableChunk, IngestionDependencyError


def _indexable_chunk() -> IndexableChunk:
    return IndexableChunk(
        workspace_id=UUID("10000000-0000-0000-0000-000000000001"),
        knowledge_base_id=UUID("20000000-0000-0000-0000-000000000002"),
        document_id=UUID("30000000-0000-0000-0000-000000000003"),
        document_version_id=UUID("40000000-0000-0000-0000-000000000004"),
        chunk_id=UUID("50000000-0000-0000-0000-000000000005"),
        ordinal=1,
        page_number=1,
        text="Auditable filing text",
        content_hash="a" * 64,
        vector=(1.0,),
        external_id="chunk-1:knowledge-index-v1",
    )


def _writer(client: httpx2.AsyncClient) -> MilvusVectorIndexWriter:
    return MilvusVectorIndexWriter(
        client=client,
        endpoint="http://milvus.test",
        token=None,
        collection="knowledge_chunks_v1",
        dimension=64,
        timeout_seconds=1.0,
    )


@pytest.mark.asyncio
async def test_milvus_delete_uses_an_escaped_primary_key_filter() -> None:
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.url.path.endswith("/collections/has"):
            return httpx2.Response(200, json={"code": 0, "data": {"has": True}})
        return httpx2.Response(200, json={"code": 0, "data": {"deleteCount": 1}})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        await _writer(client).delete(('chunk-1:"quoted"',))

    assert len(requests) == 2
    assert requests[1].url.path == "/v2/vectordb/entities/delete"
    assert json.loads(requests[1].content) == {
        "collectionName": "knowledge_chunks_v1",
        "filter": 'id == "chunk-1:\\"quoted\\""',
    }


@pytest.mark.asyncio
async def test_milvus_delete_rejects_http_200_with_a_failed_business_code() -> None:
    def respond(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"code": 1802, "message": "invalid filter"})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        with pytest.raises(IngestionDependencyError) as caught:
            await _writer(client).delete(("chunk-1:knowledge-index-v1",))

    assert caught.value.code == "vector_index_failed"


@pytest.mark.asyncio
async def test_milvus_delete_is_a_noop_when_collection_does_not_exist() -> None:
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json={"code": 0, "data": {"has": False}})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        await _writer(client).delete(("chunk-1:knowledge-index-v1",))

    assert [request.url.path for request in requests] == ["/v2/vectordb/collections/has"]


@pytest.mark.asyncio
async def test_elasticsearch_delete_is_a_noop_when_index_does_not_exist() -> None:
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(404, json={"error": {"type": "index_not_found_exception"}})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        writer = ElasticsearchLexicalIndexWriter(
            client=client,
            endpoint="http://elasticsearch.test",
            api_key=None,
            index="knowledge_chunks_v1",
            timeout_seconds=1.0,
        )
        await writer.delete(("chunk-1:knowledge-index-v1",))

    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].url.path == "/knowledge_chunks_v1"


@pytest.mark.asyncio
async def test_elasticsearch_writes_request_an_immediate_refresh() -> None:
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx2.Response(200, json={"knowledge_chunks_v1": {}})
        return httpx2.Response(200, json={"errors": False, "items": []})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        writer = ElasticsearchLexicalIndexWriter(
            client=client,
            endpoint="http://elasticsearch.test",
            api_key=None,
            index="knowledge_chunks_v1",
            timeout_seconds=1.0,
        )
        await writer.upsert((_indexable_chunk(),))
        await writer.delete(("chunk-1:knowledge-index-v1",))

    bulk_requests = [request for request in requests if request.method == "POST"]
    assert len(bulk_requests) == 2
    assert all(request.url.params.get("refresh") == "true" for request in bulk_requests)
