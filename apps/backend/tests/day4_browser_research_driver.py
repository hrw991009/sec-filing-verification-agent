"""Execute one browser-created Research Run through the formal Job and L3 Runtime stack."""

import argparse
import asyncio
import hashlib
import json
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from browser_driver_support import (
    BrowserSuccessDriverError,
    claim_target_delivery,
    non_nil_uuid,
    require_pending_target,
)
from day3_browser_web_tool_driver import BrowserNewsProvider, BrowserProviderRegistry
from sqlalchemy import func, select

from industry_platform.core.config import Settings, get_settings
from industry_platform.core.database import create_database_engine, create_database_session_factory
from industry_platform.modules.agent_runtime.adapters.execution import (
    SqlAlchemyDirectAnswerRunLoader,
)
from industry_platform.modules.agent_runtime.adapters.persistence import (
    SqlAlchemyAgentEventCommitter,
    SqlAlchemyAgentRunControl,
    SqlAlchemyAgentRunTerminalizer,
    SqlAlchemyContextManifestStore,
)
from industry_platform.modules.agent_runtime.context_compiler import (
    ContextCompilerV0,
    ContextCompilerV1,
    Utf8UpperBoundTokenCounter,
)
from industry_platform.modules.agent_runtime.events import AgentEventType
from industry_platform.modules.agent_runtime.execution import DirectAnswerRunExecutionService
from industry_platform.modules.agent_runtime.model import (
    ModelFinishReason,
    ModelRequest,
    ModelResponse,
    ModelStreamItem,
    ModelUsage,
)
from industry_platform.modules.agent_runtime.models import AgentEventRecord
from industry_platform.modules.agent_runtime.runtime import DirectAnswerRuntime
from industry_platform.modules.agent_runtime.runtime_contracts import DirectAnswerRuntimePolicy
from industry_platform.modules.agent_runtime.tool_runtime import ToolL2Runtime, UnifiedAgentRuntime
from industry_platform.modules.agent_runtime.tool_runtime_contracts import ToolL2RuntimePolicy
from industry_platform.modules.evidence.adapters.sqlalchemy import SqlAlchemyEvidenceRepository
from industry_platform.modules.evidence.domain import ClaimVerificationStatus
from industry_platform.modules.evidence.models import ResearchClaimRecord
from industry_platform.modules.evidence.service import EvidenceApplicationService
from industry_platform.modules.industry.tool import IndustryWebSearchTool
from industry_platform.modules.jobs.domain import JobStatus
from industry_platform.modules.jobs.models import Job
from industry_platform.modules.jobs.resources import create_job_resources
from industry_platform.modules.research.adapters.sqlalchemy import (
    SqlAlchemyResearchQueryRepository,
)
from industry_platform.modules.research.domain import (
    RESEARCH_NODE_ORDER,
    RESEARCH_TASK_NAME,
    ResearchDraftStatus,
    ResearchRunStatus,
)
from industry_platform.modules.research.models import ResearchDraftRecord, ResearchRunRecord
from industry_platform.modules.tools.registry import RegistryToolExecutor, ToolRegistry
from industry_platform.server import create_selector_event_loop
from industry_platform.workers.runtime import (
    DirectAnswerJobHandler,
    FixedJobHandlerRegistry,
    JobExecutionDisposition,
    JobExecutionRuntime,
)
from industry_platform.workflows.research.runtime import ResearchL3Runtime

DRAFT_MARKDOWN = (
    "## L3 finding\n\n"
    "The public update remains uncertain because no immutable source snapshot passed the "
    "Evidence gate. This is not a verified final report."
)


@dataclass(slots=True)
class BrowserResearchModelProvider:
    run_id: UUID
    requests: list[ModelRequest] = field(default_factory=list)

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamItem]:
        raise BrowserSuccessDriverError(f"Research L3 must not stream {request.model}")

    async def complete(self, request: ModelRequest) -> ModelResponse:
        if request.run_id != self.run_id or len(self.requests) >= 2:
            raise BrowserSuccessDriverError("The Research model received an unexpected request")
        self.requests.append(request)
        if len(self.requests) == 1:
            output = (
                '{"decision":{"schema_version":1,"kind":"tool_call",'
                '"name":"industry.web_search","version":"v1","arguments":{'
                '"industry_code":"smart_transport","source_kind":"news",'
                '"query":"transport policy","limit":2}}}'
            )
        else:
            output = json.dumps(
                {
                    "decision": {
                        "schema_version": 1,
                        "kind": "final",
                        "content_markdown": DRAFT_MARKDOWN,
                    }
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        return ModelResponse(
            schema_version=1,
            model=request.model,
            finish_reason=ModelFinishReason.STOP,
            usage=ModelUsage(
                input_tokens=20,
                output_tokens=10,
                cached_input_tokens=0,
                cost_micro_usd=40,
                pricing_version="e2e-research-pricing-v1",
            ),
            output_text=output,
            provider_request_id=f"e2e-research-{self.run_id.hex}-{len(self.requests)}",
        )


def direct_policy() -> DirectAnswerRuntimePolicy:
    return DirectAnswerRuntimePolicy(
        schema_version=1,
        profile_version="direct-answer-v0",
        prompt_version="direct-answer-prompt-v0",
        context_compiler_version="context-v0",
        output_contract_version="final-markdown-v1",
        model="openai-compatible/e2e-research-provider",
        max_input_tokens=2_048,
        max_output_tokens=512,
        system_instructions="Answer directly without Tool claims.",
    )


async def execute_browser_research_run(
    settings: Settings,
    *,
    run_id: UUID,
    research_run_id: UUID,
    job_id: UUID,
) -> dict[str, object]:
    engine = create_database_engine(settings)
    try:
        session_factory = create_database_session_factory(engine)
        outbox_id = await require_pending_target(session_factory, run_id=run_id, job_id=job_id)
        delivery = await claim_target_delivery(
            settings,
            session_factory,
            job_id=job_id,
            outbox_id=outbox_id,
        )
        tool = IndustryWebSearchTool(
            BrowserProviderRegistry(BrowserNewsProvider(datetime.now(UTC)))
        )
        tool_policy = ToolL2RuntimePolicy(
            schema_version=1,
            profile_version="conversation-web-l2-v1",
            prompt_version="conversation-web-l2-prompt-v1",
            context_compiler_version="context-v1",
            output_contract_version="final-markdown-v1",
            toolset_version="conversation-web-toolset-v1",
            model="openai-compatible/e2e-research-provider",
            max_input_tokens=4_096,
            max_decision_output_tokens=768,
            max_tool_calls=2,
            system_instructions="Use only the exact public-source Tool catalog.",
            available_tools=(tool.definition.reference,),
        )
        provider = BrowserResearchModelProvider(run_id)
        committer = SqlAlchemyAgentEventCommitter(session_factory)
        manifests = SqlAlchemyContextManifestStore(session_factory)
        control = SqlAlchemyAgentRunControl(session_factory)
        registry = ToolRegistry((tool,))
        compiler = ContextCompilerV1(token_counter=Utf8UpperBoundTokenCounter())
        executor = RegistryToolExecutor(registry)
        evidence_service = EvidenceApplicationService(SqlAlchemyEvidenceRepository(session_factory))
        runtime = UnifiedAgentRuntime(
            direct_answer_runtime=DirectAnswerRuntime(
                context_compiler=ContextCompilerV0(token_counter=Utf8UpperBoundTokenCounter()),
                context_manifest_store=manifests,
                model_provider=provider,
                event_committer=committer,
                cancellation_probe=control,
            ),
            tool_l2_runtime=ToolL2Runtime(
                context_compiler=compiler,
                context_manifest_store=manifests,
                model_provider=provider,
                tool_registry=registry,
                tool_executor=executor,
                event_committer=committer,
                cancellation_probe=control,
            ),
            research_l3_runtime=ResearchL3Runtime(
                workflow_store=SqlAlchemyResearchQueryRepository(session_factory),
                evidence_service=evidence_service,
                context_compiler=compiler,
                context_manifest_store=manifests,
                model_provider=provider,
                tool_registry=registry,
                tool_executor=executor,
                event_committer=committer,
                cancellation_probe=control,
            ),
        )
        execution = DirectAnswerRunExecutionService(
            loader=SqlAlchemyDirectAnswerRunLoader(
                session_factory,
                direct_policy(),
                tool_policy=tool_policy,
            ),
            runtime=runtime,
            terminalizer=SqlAlchemyAgentRunTerminalizer(session_factory),
        )
        disposition = await JobExecutionRuntime(
            jobs=create_job_resources(settings, session_factory).application_service,
            handlers=FixedJobHandlerRegistry(
                {RESEARCH_TASK_NAME: DirectAnswerJobHandler(execution)}
            ),
            worker_id=f"e2e-browser-research-worker-{job_id.hex}",
            heartbeat_seconds=settings.job_heartbeat_seconds,
        ).execute(delivery.message)

        async with session_factory() as session:
            job_status = await session.scalar(select(Job.status).where(Job.id == job_id))
            research_run = await session.get(ResearchRunRecord, research_run_id)
            draft = await session.scalar(
                select(ResearchDraftRecord).where(
                    ResearchDraftRecord.research_run_id == research_run_id
                )
            )
            claim = await session.scalar(
                select(ResearchClaimRecord).where(
                    ResearchClaimRecord.research_run_id == research_run_id
                )
            )
            completed_node_count = await session.scalar(
                select(func.count())
                .select_from(AgentEventRecord)
                .where(
                    AgentEventRecord.run_id == run_id,
                    AgentEventRecord.event_type == AgentEventType.RESEARCH_NODE_COMPLETED,
                )
            )
        if (
            disposition is not JobExecutionDisposition.SUCCEEDED
            or job_status is not JobStatus.SUCCEEDED
            or research_run is None
            or research_run.status is not ResearchRunStatus.COMPLETED
            or draft is None
            or draft.status is not ResearchDraftStatus.UNCERTAIN_DRAFT
            or DRAFT_MARKDOWN not in draft.content_markdown
            or claim is None
            or claim.verification_status is not ClaimVerificationStatus.UNCERTAIN
            or completed_node_count != len(RESEARCH_NODE_ORDER)
            or len(provider.requests) != 2
        ):
            raise BrowserSuccessDriverError("The Research L3 terminal facts are inconsistent")
        return {
            "schema_version": 1,
            "run_id": str(run_id),
            "research_run_id": str(research_run_id),
            "job_id": str(job_id),
            "disposition": disposition.value,
            "provider_calls": len(provider.requests),
            "completed_node_count": completed_node_count,
            "draft_status": draft.status.value,
            "draft_sha256": hashlib.sha256(draft.content_markdown.encode()).hexdigest(),
        }
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True, type=non_nil_uuid)
    parser.add_argument("--research-run-id", required=True, type=non_nil_uuid)
    parser.add_argument("--job-id", required=True, type=non_nil_uuid)
    arguments = parser.parse_args()
    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        result = runner.run(
            execute_browser_research_run(
                get_settings(),
                run_id=arguments.run_id,
                research_run_id=arguments.research_run_id,
                job_id=arguments.job_id,
            )
        )
    sys.stdout.write(f"{json.dumps(result, sort_keys=True)}\n")


if __name__ == "__main__":
    main()
