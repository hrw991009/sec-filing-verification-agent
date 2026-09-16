"""Opt-in current-product stability suite; real models, official SEC, no ablation overrides.

Every attempted Run is retained, including failures. Existing output directories are
refused. A diagnostic subset is explicitly not a complete 50-Run acceptance batch.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from dupont_live_runner import emit, execute_published_job
from sec_browser_e2e_runner import _start, _stop
from sqlalchemy import select

from industry_platform.core.config import Settings
from industry_platform.core.database import create_database_session_factory
from industry_platform.main import create_app
from industry_platform.modules.agent_runtime.models import AgentEventRecord
from industry_platform.modules.conversations.domain import TurnSearchMode
from industry_platform.modules.conversations.submission import SubmitConversationTurn
from industry_platform.modules.evaluation.product_stability import check_case
from industry_platform.modules.financial_verification.domain import FinancialForm, FinancialScope
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.identity.models import WorkspaceMembership
from industry_platform.modules.research.domain import ResearchBriefInput
from industry_platform.modules.research.results import ResearchResultService
from industry_platform.modules.research.service import StartResearch
from industry_platform.modules.workspaces.domain import WorkspaceScope
from industry_platform.server import create_selector_event_loop


def source_identity() -> dict[str, str]:
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("Git is required to identify the acceptance source")
    paths = subprocess.check_output(  # noqa: S603 -- resolved Git, fixed read-only arguments
        [git, "ls-files", "--cached", "--others", "--exclude-standard"], text=True
    ).splitlines()
    digest = hashlib.sha256()
    for name in sorted(set(paths)):
        if name.startswith(("apps/", "packages/")) or name in (
            "uv.lock",
            "pnpm-lock.yaml",
            "evals/scenarios/product-stability-v1.json",
        ):
            path = Path(name)
            if path.is_file():
                digest.update(name.encode())
                digest.update(b"\0")
                digest.update(path.read_bytes())
    return {
        "head": subprocess.check_output([git, "rev-parse", "HEAD"], text=True).strip(),  # noqa: S603
        "working_source_sha256": digest.hexdigest(),
    }


async def run(args: argparse.Namespace) -> None:
    settings = Settings()
    if (
        settings.sec_controlled_source_manifest_path is not None
        or settings.agent_model_controlled_loopback
        or settings.agent_model_route is None
    ):
        raise ValueError("This suite requires configured real model and official SEC sources")
    manifest_bytes = args.manifest.read_bytes()
    manifest = json.loads(manifest_bytes)
    all_cases = manifest["cases"]
    if (
        manifest["schema_version"] != 1
        or len(all_cases) != 10
        or len({case["id"] for case in all_cases}) != 10
        or manifest["repetitions"] != 5
    ):
        raise ValueError("Expected the frozen ten-case, five-repetition contract")
    cases = [case for case in all_cases if not args.case or case["id"] in args.case]
    if not cases or (args.case and set(args.case) - {case["id"] for case in all_cases}):
        raise ValueError("Unknown case selection")
    repetitions = args.repetitions or manifest["repetitions"]
    identity = source_identity()
    batch: dict[str, Any] = {
        "suite_id": manifest["suite_id"],
        "batch_id": str(uuid4()),
        "execution_kind": "real_model_current_product_official_sec",
        "diagnostic": len(cases) != 10 or repetitions != 5,
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "source_identity": identity,
        "model_route": settings.agent_model_route.model_dump(mode="json"),
        "provider_host": urlsplit(str(settings.agent_model_provider_base_url)).hostname,
        "started_at": datetime.now(UTC).isoformat(),
        "records": [],
    }
    scope = WorkspaceScope(args.workspace_id, args.user_id, "owner")
    app = create_app(settings=settings)
    async with app.router.lifespan_context(app):
        session_factory = create_database_session_factory(app.state.resources.database_engine)
        async with session_factory() as session:
            owner = await session.scalar(
                select(WorkspaceMembership).where(
                    WorkspaceMembership.workspace_id == args.workspace_id,
                    WorkspaceMembership.user_id == args.user_id,
                    WorkspaceMembership.role == "owner",
                )
            )
        if owner is None:
            raise ValueError("An existing workspace owner is required")
        research = app.state.research_resources
        # The official five annual imports must already be ready; never hide preparation in a run.
        prepared = await app.state.disclosure_resources.dupont_preparation_service.prepare(
            scope,
            cik="0000789019",
            fiscal_year=2025,
            years=5,
            knowledge_base_id=args.knowledge_base_id,
            as_of=datetime.now(UTC),
            trace_id=TraceId(f"stability-prepare-{uuid4()}"),
        )
        if prepared.status != "ready" or prepared.financial_scope is None:
            raise ValueError("Prepare the official five annual filings before stability acceptance")
        batch["financial_scope"] = dict(prepared.financial_scope.to_mapping())
        semaphore = asyncio.Semaphore(args.concurrency)

        async def execute(case: dict[str, Any], repetition: int) -> None:
            async with semaphore:
                if not batch["diagnostic"] and source_identity() != identity:
                    raise RuntimeError("Source changed during frozen acceptance; start a new batch")
                started = time.monotonic()
                record: dict[str, Any] = {
                    "case_id": case["id"],
                    "repetition": repetition,
                    "passed": False,
                }
                key = f"product-stability:{batch['batch_id']}:{case['id']}:{repetition}"
                try:
                    if case["mode"] == "l2":
                        receipt = await app.state.conversation_resources.submission_service.submit(
                            scope,
                            SubmitConversationTurn(
                                trace_id=TraceId(f"stability-{uuid4()}"),
                                idempotency_key=key,
                                question=case["question"],
                                search_mode=TurnSearchMode.WEB,
                                industry_id=args.industry_id,
                            ),
                        )
                        run_id = receipt.run_id
                    else:
                        financial_scope = prepared.financial_scope
                        if case["mode"] != "dupont":
                            financial_scope = FinancialScope(
                                financial_scope.cik,
                                financial_scope.accession,
                                FinancialForm.TEN_K,
                                financial_scope.report_period,
                                datetime(2025, 7, 1, tzinfo=UTC)
                                if case["mode"] == "unavailable"
                                else financial_scope.as_of,
                                "USD",
                                6,
                            )
                        else:
                            financial_scope = FinancialScope.from_mapping(
                                {**financial_scope.to_mapping(), "analysis_years": case["years"]}
                            )
                        tools = (
                            ("sec.get_xbrl_facts", "finance.calculate")
                            if case["mode"] == "dupont" or case.get("metric") == "margin"
                            else ("sec.get_xbrl_facts",)
                        )
                        receipt = await research.submission_service.start(
                            scope,
                            StartResearch(
                                trace_id=TraceId(f"stability-{uuid4()}"),
                                industry_id=None,
                                idempotency_key=key,
                                brief=ResearchBriefInput(
                                    original_question=case["question"],
                                    confirmed_scope=(
                                        "Only the locked official SEC scope; no future sources",
                                    ),
                                    exclusions=(
                                        "Investment advice",
                                        "Monitoring",
                                        "Invented operands",
                                    ),
                                    completion_criteria=(
                                        "Source-backed result, or explicit unavailable evidence",
                                    ),
                                    financial_scope=financial_scope,
                                    required_tool_names=tools,
                                ),
                                search_mode=TurnSearchMode.LOCAL,
                                knowledge_base_ids=(args.knowledge_base_id,),
                                task_name="sec.dupont-analysis"
                                if case["mode"] == "dupont"
                                else "sec.filing-verification",
                                task_version="v2" if case["mode"] == "dupont" else "v1",
                                max_steps=40,
                                max_total_tokens=100_000,
                                max_cost_micro_usd=1_000_000,
                                timeout_seconds=1200,
                            ),
                        )
                        run_id = receipt.agent_run_id
                        record["research_run_id"] = str(receipt.research_run_id)
                    record["run_id"] = str(run_id)
                    record["job_id"] = str(receipt.job_id)
                    emit(
                        {
                            "case": case["id"],
                            "repetition": repetition,
                            "run_id": str(run_id),
                            "stage": "started",
                        }
                    )
                    execution_failure = False
                    try:
                        await execute_published_job(
                            session_factory, scope, receipt.job_id, settings
                        )
                    except Exception as error:
                        # Still collect the persisted Run trace on retry/failed delivery.
                        # Never replay it silently or lose the original failure evidence.
                        execution_failure = True
                        record["execution_error_type"] = type(error).__name__
                        record["execution_error_code"] = str(
                            getattr(error, "code", "job_execution_not_succeeded")
                        )
                    trace = await app.state.agent_trace_resources.query.get(
                        scope=scope, run_id=run_id
                    )
                    async with session_factory() as session:
                        events = tuple(
                            await session.scalars(
                                select(AgentEventRecord)
                                .where(AgentEventRecord.run_id == run_id)
                                .order_by(AgentEventRecord.sequence)
                            )
                        )
                    messages = (
                        await app.state.conversation_resources.management_service.list_messages(
                            scope=scope,
                            conversation_id=receipt.conversation_id,
                            page_size=100,
                            cursor=None,
                        )
                    )
                    record.update(
                        status=trace.run.status.value,
                        stop_reason=None
                        if trace.run.stop_reason is None
                        else trace.run.stop_reason.value,
                        harness_version=trace.run.harness_version,
                        input_tokens=trace.run.usage.input_tokens,
                        output_tokens=trace.run.usage.output_tokens,
                        max_total_tokens=trace.run.max_total_tokens,
                        step_count=trace.run.step_count,
                        max_steps=trace.run.max_steps,
                        cost_micro_usd=trace.run.usage.cost_micro_usd,
                        max_cost_micro_usd=trace.run.max_cost_micro_usd,
                        deadline=trace.run.deadline.isoformat(),
                        terminal_at=None
                        if trace.run.terminal_at is None
                        else trace.run.terminal_at.isoformat(),
                        model_calls=sum(
                            event.event_type.value == "agent.model.completed" for event in events
                        ),
                        model_attempts=sum(
                            event.event_type.value == "agent.model.started" for event in events
                        ),
                        tool_requests=[
                            event.payload.get("requested_tool_name")
                            for event in events
                            if event.event_type.value == "agent.tool.requested"
                        ],
                        terminal_event_count=sum(
                            event.event_type.value
                            in {"agent.run.completed", "agent.run.failed", "agent.run.cancelled"}
                            for event in events
                        ),
                        answer="\n".join(
                            message.content_markdown
                            for message in messages.items
                            if message.role == "assistant" and message.agent_run_id == run_id
                        ),
                        errors=[
                            {
                                "event_type": event.event_type.value,
                                "code": event.payload.get("error_code"),
                            }
                            for event in events
                            if event.event_type.value in {"agent.tool.failed", "agent.step.failed"}
                        ],
                        observations=[],
                    )
                    for event in events:
                        if event.event_type.value != "agent.tool.completed":
                            continue
                        observation = event.payload["observation"]
                        if not isinstance(observation, dict):
                            raise ValueError("Invalid persisted Tool observation")
                        output = json.loads(observation["model_text"])
                        record["observations"].append(
                            {
                                "tool": observation["tool_name"],
                                "status": output.get("status"),
                                "error_code": output.get("error_code"),
                                "skill": output.get("name"),
                                "source_count": len(observation["sources"]),
                                "sha256": observation["content_sha256"],
                                "result": output.get("result"),
                            }
                        )
                    if case["mode"] != "l2":
                        research_id = UUID(record["research_run_id"])
                        report = await research.verification_service.latest(scope, research_id)
                        view = await research.query_service.get(scope, research_id)
                        record["verification_status"] = (
                            None if report is None else report.status.value
                        )
                        record["verification_issues"] = (
                            [] if report is None else [issue.code.value for issue in report.issues]
                        )
                        record["answer"] = "" if view.draft is None else view.draft.content_markdown
                        result = await ResearchResultService(
                            research.query_service,
                            research.verification_service.evidence_service,
                            research.verification_service,
                        ).get(scope, research_id)
                        record["result_view"] = result.model_dump(mode="json")
                    record["failures"] = check_case(case, record)
                    if execution_failure:
                        record["failures"].insert(0, "execution_exception")
                    record["passed"] = not record["failures"]
                except Exception as error:
                    record["failures"] = ["execution_exception"]
                    record["error_type"] = type(error).__name__
                    # No raw provider exceptions, credentials, request headers or URL queries.
                    record["error_code"] = str(getattr(error, "code", "acceptance_error"))
                record["elapsed_seconds"] = round(time.monotonic() - started, 3)
                (args.output / f"{case['id']}-{repetition}.json").write_text(
                    json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
                batch["records"].append(record)
                (args.output / "batch.json").write_text(
                    json.dumps(batch, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
                emit(
                    {
                        "stage": "finished",
                        "case": case["id"],
                        "repetition": repetition,
                        "passed": record["passed"],
                        "failures": record.get("failures"),
                    }
                )

        await asyncio.gather(
            *(
                execute(case, repetition)
                for repetition in range(1, repetitions + 1)
                for case in cases
            )
        )
    batch["source_unchanged"] = source_identity() == identity
    batch["completed_at"] = datetime.now(UTC).isoformat()
    batch["passed_runs"] = sum(record["passed"] for record in batch["records"])
    batch["total_runs"] = len(batch["records"])
    batch["accepted"] = (
        not batch["diagnostic"]
        and batch["source_unchanged"]
        and batch["total_runs"] == 50
        and batch["passed_runs"] == 50
    )
    (args.output / "batch.json").write_text(
        json.dumps(batch, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    emit(
        {
            key: batch[key]
            for key in ("total_runs", "passed_runs", "accepted", "diagnostic", "source_unchanged")
        }
    )
    if batch["passed_runs"] != batch["total_runs"]:
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("workspace-id", "user-id", "knowledge-base-id", "industry-id"):
        parser.add_argument(f"--{name}", type=UUID, required=True)
    parser.add_argument(
        "--manifest", type=Path, default=Path("evals/scenarios/product-stability-v1.json")
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", action="append")
    parser.add_argument("--repetitions", type=int, choices=range(1, 6))
    parser.add_argument("--concurrency", type=int, choices=(1, 2), default=2)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    with (args.output / "dispatcher.log").open("wb") as log:
        dispatcher = _start(
            [sys.executable, "-m", "industry_platform.workers.dispatcher"],
            environment=dict(os.environ),
            output=log,
        )
        try:
            asyncio.run(run(args), loop_factory=create_selector_event_loop)
        finally:
            _stop(dispatcher)


if __name__ == "__main__":
    main()
