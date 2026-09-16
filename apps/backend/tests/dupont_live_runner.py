"""Opt-in official SEC + production-runtime DuPont acceptance (never a CI fixture).

Uses an explicitly selected local workspace and its current owner. Only this
runner's ingestion/Research deliveries are executed; no global Celery consumer.
Credentials are loaded by Settings and are never included in the artifact.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sec_browser_e2e_runner import _start, _stop
from sqlalchemy import select

from industry_platform.core.config import Settings
from industry_platform.core.database import AsyncSessionFactory, create_database_session_factory
from industry_platform.main import create_app
from industry_platform.modules.conversations.domain import TurnSearchMode
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.identity.models import WorkspaceMembership
from industry_platform.modules.jobs.domain import JobDispatchMessage, OutboxStatus
from industry_platform.modules.jobs.models import Job, OutboxEvent
from industry_platform.modules.knowledge.domain import CreateKnowledgeBase
from industry_platform.modules.research.domain import ResearchBriefInput
from industry_platform.modules.research.results import ResearchResultService
from industry_platform.modules.research.service import StartResearch
from industry_platform.modules.workspaces.domain import WorkspaceScope
from industry_platform.server import create_selector_event_loop
from industry_platform.workers.runtime import run_job_delivery


def emit(value: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(value, ensure_ascii=True) + "\n")
    sys.stdout.flush()


async def execute_published_job(
    session_factory: AsyncSessionFactory, scope: WorkspaceScope, job_id: UUID, settings: Settings
) -> None:
    for _ in range(30):
        async with session_factory() as session:
            row = (
                await session.execute(
                    select(Job, OutboxEvent)
                    .join(
                        OutboxEvent,
                        OutboxEvent.source_job_id == Job.id,
                    )
                    .where(
                        Job.id == job_id,
                        Job.workspace_id == scope.workspace_id,
                        OutboxEvent.job_dispatch_generation == Job.dispatch_generation,
                        OutboxEvent.status == OutboxStatus.PUBLISHED,
                    )
                )
            ).one_or_none()
        if row is not None:
            job, outbox = row
            emit({"stage": "executing", "job_id": str(job_id), "task": job.task_name})
            disposition = await run_job_delivery(
                JobDispatchMessage(
                    job.id,
                    job.dispatch_generation,
                    outbox.id,
                    TraceId(job.trace_id),
                ),
                settings=settings,
            )
            emit(
                {
                    "stage": "executed",
                    "job_id": str(job_id),
                    "disposition": disposition.value,
                }
            )
            if disposition.value not in {"succeeded", "no_op"}:
                raise RuntimeError(f"Job did not succeed: {disposition.value}")
            return
        await asyncio.sleep(1)
    raise RuntimeError("Target outbox message was not published")


async def run(args: argparse.Namespace, record: dict[str, Any]) -> None:
    settings = Settings()
    if (
        settings.sec_controlled_source_manifest_path is not None
        or settings.agent_model_controlled_loopback
    ):
        raise RuntimeError("Live acceptance rejects controlled SEC/model sources")
    app = create_app(settings=settings)
    async with app.router.lifespan_context(app):
        session_factory = create_database_session_factory(app.state.resources.database_engine)
        async with session_factory() as session:
            membership = await session.scalar(
                select(WorkspaceMembership).where(
                    WorkspaceMembership.workspace_id == args.workspace_id,
                    WorkspaceMembership.user_id == args.user_id,
                    WorkspaceMembership.role == "owner",
                )
            )
            if membership is None:
                raise RuntimeError("Explicit workspace owner membership is required")
        scope = WorkspaceScope(args.workspace_id, args.user_id, "owner")
        trace_id = TraceId(f"dupont-live-{uuid4()}")
        knowledge = app.state.knowledge_resources.service
        bases = await knowledge.list_knowledge_bases(scope)
        name = "SEC DuPont official v1"
        kb = next((item for item in bases if item.name == name), None)
        if kb is None:
            kb = await knowledge.create_knowledge_base(
                scope,
                CreateKnowledgeBase(
                    name,
                    "Official SEC annual filings; separate from controlled derivatives.",
                    trace_id,
                ),
            )
        record.update(
            workspace_id=str(scope.workspace_id),
            knowledge_base_id=str(kb.id),
            cik=args.cik,
            fiscal_year=args.fiscal_year,
            years=args.years,
        )
        emit({"stage": "preparing", **record})

        async def execute(job_id: UUID) -> None:
            await execute_published_job(session_factory, scope, job_id, settings)

        disclosures = app.state.disclosure_resources
        cutoff = datetime.now(UTC)
        preparation = await disclosures.dupont_preparation_service.prepare(
            scope,
            cik=args.cik,
            fiscal_year=args.fiscal_year,
            years=args.years,
            knowledge_base_id=kb.id,
            as_of=cutoff,
            trace_id=trace_id,
        )
        record["preparation_status"] = preparation.status
        record["issues"] = list(preparation.issues)
        emit({"stage": "prepared", "status": preparation.status, "issues": preparation.issues})
        if preparation.status == "awaiting_ingestion":
            for imported in preparation.imports:
                if imported.status.value != "ready":
                    await execute(imported.ingestion_job_id)
            preparation = await disclosures.dupont_preparation_service.prepare(
                scope,
                cik=args.cik,
                fiscal_year=args.fiscal_year,
                years=args.years,
                knowledge_base_id=kb.id,
                as_of=cutoff,
                trace_id=trace_id,
            )
        record["preparation_status"] = preparation.status
        record["issues"] = list(preparation.issues)
        if preparation.status != "ready" or preparation.financial_scope is None:
            raise RuntimeError(f"DuPont inputs not ready: {preparation.issues}")
        financial_scope = preparation.financial_scope
        record["financial_scope"] = dict(financial_scope.to_mapping())
        selection = await disclosures.xbrl_service.get_dupont_facts(
            scope,
            knowledge_base_ids=(kb.id,),
            financial_scope=financial_scope,
        )
        record["periods"] = [
            {
                "start": period.start_date.isoformat(),
                "end": period.end_date.isoformat(),
                "facts": [
                    {
                        "id": str(fact.id),
                        "accession": fact.accession,
                        "concept": fact.concept,
                        "value": fact.value,
                        "scale": fact.scale,
                        "source_kind": fact.source_kind.value,
                        "source_url": fact.source_url,
                        "source_version": fact.source_version,
                        "source_sha256": fact.source_content_sha256,
                    }
                    for fact in period.operands
                ],
            }
            for period in selection.periods
        ]
        emit(
            {
                "stage": "inputs_ready",
                "periods": len(selection.periods),
                "facts": len(selection.facts),
            }
        )
        if args.prepare_only:
            return
        research = app.state.research_resources
        receipt = await research.submission_service.start(
            scope,
            StartResearch(
                trace_id=trace_id,
                industry_id=None,
                brief=ResearchBriefInput(
                    original_question=(
                        f"请用中文完成连续 {args.years} 年三因素杜邦分析, "
                        "给出净利率、总资产周转率、权益乘数、ROE的对比表、计算引用及数据口径局限。"
                    ),
                    confirmed_scope=(
                        f"CIK {args.cik}, "
                        f"fiscal years {args.fiscal_year - args.years + 1} "
                        f"through {args.fiscal_year}",
                    ),
                    exclusions=(
                        "DCF",
                        "CAGR",
                        "monitor subscriptions",
                        "unsupported causal explanations",
                    ),
                    completion_criteria=(
                        "All selected years have source-backed calculations and verified citations",
                    ),
                    financial_scope=financial_scope,
                ),
                idempotency_key=f"dupont-live:{uuid4()}",
                search_mode=TurnSearchMode.LOCAL,
                knowledge_base_ids=(kb.id,),
                max_steps=40,
                max_total_tokens=100_000,
                max_cost_micro_usd=1_000_000,
                timeout_seconds=1200,
                task_name="sec.dupont-analysis",
                task_version="v1" if args.years == 2 else "v2",
            ),
        )
        record["research_run_id"] = str(receipt.research_run_id)
        record["agent_run_id"] = str(receipt.agent_run_id)
        emit({"stage": "research_started", "research_run_id": str(receipt.research_run_id)})
        await execute(receipt.job_id)
        view = await research.query_service.get(scope, receipt.research_run_id)
        report = await research.verification_service.latest(scope, receipt.research_run_id)
        record.update(
            agent_status=view.agent_status.value,
            stop_reason=str(view.stop_reason),
            input_tokens=view.input_tokens_used,
            output_tokens=view.output_tokens_used,
            report_markdown=None if view.draft is None else view.draft.content_markdown,
            verification_status=None if report is None else report.status.value,
            verification_issues=[]
            if report is None
            else [item.code.value for item in report.issues],
        )
        result_view = await ResearchResultService(
            research.query_service,
            research.verification_service.evidence_service,
            research.verification_service,
        ).get(scope, receipt.research_run_id)
        record["result_view"] = result_view.model_dump(mode="json")
        if (
            view.agent_status.value != "completed"
            or report is None
            or report.status.value != "verified"
            or report.issues
            or result_view.status != "ready"
            or result_view.verification_status != "verified"
        ):
            raise RuntimeError(
                "Live DuPont Research did not finish with a clean verification report"
            )
        record["verified"] = True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-id", type=UUID, required=True)
    parser.add_argument("--user-id", type=UUID, required=True)
    parser.add_argument("--cik", default="0000789019")
    parser.add_argument("--fiscal-year", type=int, default=2025)
    parser.add_argument("--years", type=int, choices=(2, 3, 4, 5), default=5)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--output", type=Path, default=Path(".data/evals/dupont-live-v1.json"))
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    record: dict[str, Any] = {
        "execution_kind": "official_sec_production_runtime",
        "verified": False,
    }
    with args.output.with_suffix(".dispatcher.log").open("wb") as log:
        dispatcher = _start(
            [sys.executable, "-m", "industry_platform.workers.dispatcher"],
            environment=dict(os.environ),
            output=log,
        )
        try:
            asyncio.run(run(args, record), loop_factory=create_selector_event_loop)
        except Exception as error:
            record["error_type"] = type(error).__name__
            record["error_code"] = str(getattr(error, "code", "acceptance_failed"))
            raise
        finally:
            _stop(dispatcher)
            args.output.write_text(
                json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )


if __name__ == "__main__":
    main()
