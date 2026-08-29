"""LangGraph adapter for the single versioned Research L5 graph."""

from collections.abc import AsyncIterator
from typing import Protocol, cast

from langgraph.graph import END, START, StateGraph

from industry_platform.modules.research.domain import ResearchNode
from industry_platform.workflows.research.contracts import ResearchGraphState


class ResearchNodeExecutor(Protocol):
    async def execute(
        self, node: ResearchNode, state: ResearchGraphState
    ) -> ResearchGraphState: ...


class CompiledResearchGraph(Protocol):
    def astream(self, state: ResearchGraphState, *, stream_mode: str) -> AsyncIterator[object]: ...


_LINEAR_NEXT = {
    ResearchNode.CLARIFY_SCOPE: ResearchNode.WRITE_RESEARCH_BRIEF,
    ResearchNode.WRITE_RESEARCH_BRIEF: ResearchNode.PLAN,
    ResearchNode.PLAN: ResearchNode.RESEARCH_LOOP,
    ResearchNode.RESEARCH_LOOP: ResearchNode.NORMALIZE_EVIDENCE,
    ResearchNode.NORMALIZE_EVIDENCE: ResearchNode.SYNTHESIZE_CLAIMS,
    ResearchNode.SYNTHESIZE_CLAIMS: ResearchNode.OUTLINE,
    ResearchNode.OUTLINE: ResearchNode.DRAFT,
    ResearchNode.DRAFT: ResearchNode.VERIFY,
    ResearchNode.FINALIZE: None,
}


def next_research_node(
    node: ResearchNode,
    state: ResearchGraphState,
) -> ResearchNode | None:
    """Return the one legal successor used by graph routing and Checkpoints."""

    if state["stop_reason"] is not None:
        return None
    if node is ResearchNode.VERIFY:
        if state["verification_action"] is not None and state["revise_count"] == 0:
            return ResearchNode.REVISE
        return ResearchNode.FINALIZE
    if node is ResearchNode.REVISE:
        return (
            ResearchNode.VERIFY
            if state["verification_observation_digest"] is not None
            else ResearchNode.FINALIZE
        )
    return _LINEAR_NEXT[node]


def build_research_graph(
    executor: ResearchNodeExecutor,
    *,
    start_node: ResearchNode = ResearchNode.CLARIFY_SCOPE,
) -> CompiledResearchGraph:
    """Compile the Research nodes; Runtime and domain work stay behind the executor."""

    builder = StateGraph(ResearchGraphState)
    for node in ResearchNode:

        async def execute_node(
            state: ResearchGraphState,
            *,
            selected: ResearchNode = node,
        ) -> ResearchGraphState:
            return await executor.execute(selected, state)

        builder.add_node(node.value, execute_node)
    builder.add_edge(START, start_node.value)
    for node in ResearchNode:

        def route(
            state: ResearchGraphState,
            *,
            current: ResearchNode = node,
        ) -> str:
            following = next_research_node(current, state)
            return END if following is None else following.value

        builder.add_conditional_edges(node.value, route)
    return cast(CompiledResearchGraph, builder.compile())
