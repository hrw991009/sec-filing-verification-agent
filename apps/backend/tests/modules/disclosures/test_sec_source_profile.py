"""The Day 6 profile reuses Tool L2 while exposing only SEC read Tools."""

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest

from industry_platform.modules.agent_harness.direct_answer import HarnessTrustedIdentity
from industry_platform.modules.agent_harness.scenarios import (
    Scenario,
    ScenarioBudget,
    VersionedReference,
)
from industry_platform.modules.agent_harness.tool_use import (
    ToolL2HarnessExecutionIdentity,
    ToolL2ScenarioMaterializer,
)
from industry_platform.modules.agent_runtime.domain import AgentRunType
from industry_platform.modules.agent_runtime.tool_runtime_contracts import TOOL_L2_RUNTIME_VERSION
from industry_platform.modules.disclosures.profile import (
    SEC_L4_MAX_TOOL_CALLS,
    SEC_L4_PROFILE_VERSION,
    SEC_L4_TOOL_REFERENCES,
    SEC_SOURCE_HARNESS_VERSION,
    SEC_SOURCE_MODEL_FIXTURE_VERSION,
    SEC_SOURCE_PROFILE_VERSION,
    SEC_SOURCE_PROMPT_VERSION,
    SEC_SOURCE_TOOL_REFERENCES,
    SEC_SOURCE_TOOLSET_VERSION,
    create_sec_l4_profile,
    create_sec_source_profile,
    require_sec_l4_tool_adapters,
    require_sec_source_profile,
    require_sec_source_tool_adapters,
)
from industry_platform.modules.disclosures.tool import (
    sec_diff_filings_definition,
    sec_get_xbrl_facts_definition,
    sec_list_filings_definition,
    sec_read_filing_section_definition,
    sec_resolve_filer_definition,
    sec_search_filing_definition,
)
from industry_platform.modules.financial_verification.tool import finance_calculate_definition
from industry_platform.modules.identity.domain import (
    AuthenticatedPrincipal,
    AuthenticatedWorkspace,
    NormalizedEmail,
    TraceId,
)
from industry_platform.modules.retrieval.tool import knowledge_search_definition
from industry_platform.modules.tools.domain import ToolReference
from industry_platform.modules.tools.registry import RegisteredToolAdapter
from industry_platform.modules.workspaces.domain import WorkspaceAction, WorkspaceScope


def test_sec_source_profile_projects_exactly_five_tools_to_shared_runtime() -> None:
    profile = create_sec_source_profile(model="sec-source-model-v1")
    policy = profile.to_runtime_policy()

    assert profile.profile_name == "tool-l2"
    assert profile.profile_version == SEC_SOURCE_PROFILE_VERSION
    assert profile.prompt_version == SEC_SOURCE_PROMPT_VERSION
    assert profile.toolset_version == SEC_SOURCE_TOOLSET_VERSION
    assert profile.available_tools == SEC_SOURCE_TOOL_REFERENCES
    assert policy.available_tools == SEC_SOURCE_TOOL_REFERENCES
    assert policy.profile_version == SEC_SOURCE_PROFILE_VERSION
    assert policy.max_tool_calls == 5
    assert {reference.name for reference in policy.available_tools} == {
        "sec.resolve_filer",
        "sec.list_filings",
        "sec.get_xbrl_facts",
        "sec.search_filing",
        "sec.read_filing_section",
    }


def test_sec_l4_profile_freezes_one_bilingual_scope_and_six_tool_surface() -> None:
    profile = create_sec_l4_profile(model="sec-l4-model-v1")
    chinese_policy = profile.to_runtime_policy()
    english_policy = create_sec_l4_profile(model="sec-l4-model-v1").to_runtime_policy()

    assert chinese_policy == english_policy
    assert chinese_policy.profile_version == SEC_L4_PROFILE_VERSION
    assert chinese_policy.available_tools == SEC_L4_TOOL_REFERENCES
    assert chinese_policy.max_tool_calls == SEC_L4_MAX_TOOL_CALLS
    assert chinese_policy.context_compiler_version == "financial-context-v1"
    assert "默认使用中文" in chinese_policy.system_instructions
    assert "不得因语言改变 FinancialScope" in chinese_policy.system_instructions


def test_sec_l4_adapter_surface_uses_existing_runtime_tools_plus_diff() -> None:
    definitions = (
        knowledge_search_definition(),
        finance_calculate_definition(),
        sec_search_filing_definition(),
        sec_read_filing_section_definition(),
        sec_get_xbrl_facts_definition(),
        sec_diff_filings_definition(),
    )
    adapters = tuple(
        cast(RegisteredToolAdapter, SimpleNamespace(definition=definition))
        for definition in definitions
    )

    assert (
        tuple(adapter.definition.reference for adapter in require_sec_l4_tool_adapters(adapters))
        == SEC_L4_TOOL_REFERENCES
    )


def test_sec_source_profile_rejects_an_extra_non_sec_tool() -> None:
    profile = create_sec_source_profile(model="sec-source-model-v1")
    tampered = replace(
        profile,
        available_tools=(*profile.available_tools, ToolReference("finance.calculate", "v1")),
    )

    with pytest.raises(ValueError, match="frozen five-Tool contract"):
        require_sec_source_profile(tampered)


def test_sec_source_adapter_surface_is_bound_to_real_tool_definitions() -> None:
    definitions = (
        sec_resolve_filer_definition(),
        sec_list_filings_definition(),
        sec_get_xbrl_facts_definition(),
        sec_search_filing_definition(),
        sec_read_filing_section_definition(),
    )
    adapters = tuple(
        cast(RegisteredToolAdapter, SimpleNamespace(definition=definition))
        for definition in definitions
    )

    assert (
        tuple(
            adapter.definition.reference for adapter in require_sec_source_tool_adapters(adapters)
        )
        == SEC_SOURCE_TOOL_REFERENCES
    )
    with pytest.raises(ValueError, match="frozen five-Tool contract"):
        require_sec_source_tool_adapters(tuple(reversed(adapters)))


def test_sec_source_scenario_materializes_through_the_shared_tool_l2_harness() -> None:
    profile = create_sec_source_profile(model=SEC_SOURCE_MODEL_FIXTURE_VERSION)
    policy = profile.to_runtime_policy()
    workspace_id = uuid5(NAMESPACE_URL, "sec-source-workspace")
    user_id = uuid5(NAMESPACE_URL, "sec-source-user")
    session_id = uuid5(NAMESPACE_URL, "sec-source-session")

    def identifier(name: str) -> UUID:
        return uuid5(NAMESPACE_URL, f"sec-source-profile:{name}")

    scenario = Scenario(
        schema_version=1,
        scenario_id="sec-source-five-tool-materialization",
        scenario_version="v1",
        run_type=AgentRunType.TOOL_LOOP,
        profile=VersionedReference(name="tool-l2", version=SEC_SOURCE_PROFILE_VERSION),
        input={"question": "Resolve the filer before selecting the filing."},
        runtime_version=TOOL_L2_RUNTIME_VERSION,
        harness_version=SEC_SOURCE_HARNESS_VERSION,
        model_version=SEC_SOURCE_MODEL_FIXTURE_VERSION,
        prompt_version=profile.prompt_version,
        context_version=profile.context_compiler_version,
        budget=ScenarioBudget(
            max_steps=16,
            max_total_tokens=8_192,
            max_cost_micro_usd=10_000,
            timeout_seconds=30,
        ),
        deterministic_fixture_refs=(VersionedReference(name="sec-source-v1", version="v1"),),
        toolset_version=profile.toolset_version,
        available_tools=tuple(
            VersionedReference(name=item.name, version=item.version)
            for item in profile.available_tools
        ),
    )
    materialized = ToolL2ScenarioMaterializer(
        profile=profile,
        execution=ToolL2HarnessExecutionIdentity(
            run_id=identifier("run"),
            stream_id=identifier("stream"),
            decision_model_step_ids=tuple(
                identifier(f"decision-{index}") for index in range(policy.model_call_limit)
            ),
            tool_step_ids=tuple(
                identifier(f"tool-step-{index}") for index in range(policy.tool_call_limit)
            ),
            decision_manifest_ids=tuple(
                identifier(f"manifest-{index}") for index in range(policy.model_call_limit)
            ),
            tool_call_ids=tuple(
                identifier(f"tool-call-{index}") for index in range(policy.tool_call_limit)
            ),
            approval_request_ids=tuple(
                identifier(f"approval-{index}") for index in range(policy.tool_call_limit)
            ),
            final_step_id=identifier("final"),
            trace_id=TraceId("trace-sec-source-profile"),
            created_at=datetime(2026, 8, 27, tzinfo=UTC),
        ),
        identity=HarnessTrustedIdentity(
            principal=AuthenticatedPrincipal(
                user_id=user_id,
                session_id=session_id,
                email=NormalizedEmail("sec-source@example.test"),
                workspaces=(
                    AuthenticatedWorkspace(
                        workspace_id=workspace_id,
                        name="SEC Source Harness",
                        role="member",
                    ),
                ),
            ),
            workspace_scope=WorkspaceScope(
                workspace_id=workspace_id,
                user_id=user_id,
                role="member",
            ),
            capabilities=frozenset({WorkspaceAction.VIEW, WorkspaceAction.RUN_TOOL}),
            secret_references=(),
        ),
        model_version=SEC_SOURCE_MODEL_FIXTURE_VERSION,
        harness_version=SEC_SOURCE_HARNESS_VERSION,
    ).materialize(scenario)

    assert materialized.command.policy == policy
    assert materialized.command.policy.available_tools == SEC_SOURCE_TOOL_REFERENCES
    assert materialized.runtime_context.workspace_scope.workspace_id == workspace_id
