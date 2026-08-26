"""LangGraph-only adapter for the one ordered Research L3/L4 graph."""

from collections.abc import AsyncIterator
from itertools import pairwise
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
    nodes = tuple(ResearchNode)
    start_index = nodes.index(start_node)
    for current, following in pairwise(nodes[start_index:]):

        def route(
            state: ResearchGraphState,
            *,
            next_node: ResearchNode = following,
        ) -> str:
            return END if state["stop_reason"] is not None else next_node.value

        builder.add_conditional_edges(current.value, route)
    builder.add_edge(ResearchNode.DRAFT.value, END)
    return cast(CompiledResearchGraph, builder.compile())
