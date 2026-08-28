"""Elasticsearch BM25 read contract for authorized Knowledge chunks."""

import json
from uuid import UUID

import httpx2
import pytest

from industry_platform.modules.retrieval.adapters.elasticsearch import ElasticsearchLexicalIndex
from industry_platform.modules.retrieval.ports import LexicalSearchDependencyError

WORKSPACE_ID = UUID("11111111-1111-4111-8111-111111111111")
KNOWLEDGE_BASE_ID = UUID("22222222-2222-4222-8222-222222222222")
VERSION_ID = UUID("33333333-3333-4333-8333-333333333333")
CHUNK_ID = UUID("44444444-4444-4444-8444-444444444444")


@pytest.mark.asyncio
async def test_bm25_search_pins_workspace_knowledge_base_and_version_filters() -> None:
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            json={
                "hits": {
                    "hits": [
                        {
                            "_score": 4.2,
                            "_source": {
                                "chunk_id": str(CHUNK_ID),
                                "document_version_id": str(VERSION_ID),
                            },
                        }
                    ]
                }
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        result = await ElasticsearchLexicalIndex(
            client=client,
            endpoint="http://elasticsearch.test",
            api_key="internal-key",
            index="knowledge_chunks_v1",
            timeout_seconds=1,
        ).search(
            "net sales",
            workspace_id=WORKSPACE_ID,
            knowledge_base_ids=(KNOWLEDGE_BASE_ID,),
            document_version_ids=(VERSION_ID,),
            limit=5,
        )

    payload = json.loads(requests[0].content)
    filters = payload["query"]["bool"]["filter"]
    assert {"term": {"workspace_id": str(WORKSPACE_ID)}} in filters
    assert str(KNOWLEDGE_BASE_ID) in filters[1]["terms"]["knowledge_base_id"]
    assert str(VERSION_ID) in filters[2]["terms"]["document_version_id"]
    assert requests[0].headers["authorization"] == "ApiKey internal-key"
    assert result[0].chunk_id == CHUNK_ID
    assert result[0].score == 4.2


@pytest.mark.asyncio
async def test_unconfigured_elasticsearch_fails_with_stable_dependency_code() -> None:
    async with httpx2.AsyncClient() as client:
        index = ElasticsearchLexicalIndex(client, None, None, "knowledge_chunks_v1", 1)
        with pytest.raises(LexicalSearchDependencyError) as exc_info:
            await index.search(
                "net sales",
                workspace_id=WORKSPACE_ID,
                knowledge_base_ids=(KNOWLEDGE_BASE_ID,),
                document_version_ids=(VERSION_ID,),
                limit=5,
            )

    assert exc_info.value.code == "lexical_index_not_configured"
