"""Milvus REST contract tests for the Dense-only baseline."""

import json
from uuid import UUID

import httpx2
import pytest

from industry_platform.modules.retrieval.adapters.milvus import MilvusDenseIndex
from industry_platform.modules.retrieval.ports import DenseSearchDependencyError

WORKSPACE_ID = UUID("11111111-1111-4111-8111-111111111111")
KNOWLEDGE_BASE_ID = UUID("22222222-2222-4222-8222-222222222222")
VERSION_ID = UUID("33333333-3333-4333-8333-333333333333")
CHUNK_ID = UUID("44444444-4444-4444-8444-444444444444")


@pytest.mark.asyncio
async def test_milvus_search_pins_workspace_knowledge_base_and_version_filters() -> None:
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            json={
                "code": 0,
                "data": [
                    {
                        "distance": 0.8,
                        "entity": {
                            "chunk_id": str(CHUNK_ID),
                            "document_version_id": str(VERSION_ID),
                        },
                    }
                ],
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        result = await MilvusDenseIndex(
            client=client,
            endpoint="http://milvus.test",
            token="internal-token",  # noqa: S106 - test-only Milvus credential
            collection="knowledge_chunks_v1",
            timeout_seconds=1,
        ).search(
            (0.0,) * 64,
            workspace_id=WORKSPACE_ID,
            knowledge_base_ids=(KNOWLEDGE_BASE_ID,),
            document_version_ids=(VERSION_ID,),
            limit=5,
        )

    payload = json.loads(requests[0].content)
    assert str(WORKSPACE_ID) in payload["filter"]
    assert str(KNOWLEDGE_BASE_ID) in payload["filter"]
    assert str(VERSION_ID) in payload["filter"]
    assert payload["consistencyLevel"] == "Strong"
    assert requests[0].headers["authorization"] == "Bearer internal-token"
    assert result[0].chunk_id == CHUNK_ID
    assert result[0].score == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_unconfigured_milvus_fails_with_a_stable_dependency_code() -> None:
    async with httpx2.AsyncClient() as client:
        index = MilvusDenseIndex(client, None, None, "knowledge_chunks_v1", 1)
        with pytest.raises(DenseSearchDependencyError) as exc_info:
            await index.search(
                (0.0,) * 64,
                workspace_id=WORKSPACE_ID,
                knowledge_base_ids=(KNOWLEDGE_BASE_ID,),
                document_version_ids=(VERSION_ID,),
                limit=5,
            )

    assert exc_info.value.code == "vector_index_not_configured"
