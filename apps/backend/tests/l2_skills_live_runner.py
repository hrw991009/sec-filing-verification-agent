"""Opt-in real-provider acceptance of lightweight skills via formal Turn/Job delivery.

No fake model, forced skill choice, workspace creation or global job consumer.
Fixtures here are user-supplied example excerpts, not claims about real companies.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from dupont_live_runner import emit, execute_published_job
from sec_browser_e2e_runner import _start, _stop
from sqlalchemy import select

from industry_platform.core.config import Settings
from industry_platform.core.database import create_database_session_factory
from industry_platform.main import create_app
from industry_platform.modules.agent_runtime.models import AgentRunRecord
from industry_platform.modules.conversations.domain import TurnSearchMode
from industry_platform.modules.conversations.models import Message, MessageRole
from industry_platform.modules.conversations.submission import SubmitConversationTurn
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.identity.models import WorkspaceMembership
from industry_platform.modules.industry.domain import FINTECH_INDUSTRY_ID
from industry_platform.modules.tools.models import ToolCallRecord
from industry_platform.modules.workspaces.domain import WorkspaceScope
from industry_platform.server import create_selector_event_loop

CASES = {
    "excerpt": (
        "financial-excerpt-explanation",
        "请帮我读懂这段用户提供的示例财报, 用中文解释术语和局限, 不需要联网。"
        "示例: 甲公司2025财年合并利润表, 单位人民币百万元: 营业收入120, "
        "归属于母公司股东的净利润18。请勿计算新比率或声称外部核验。",
    ),
    "comparison": (
        "financial-basis-comparison",
        "请检查以下用户提供的示例数字能否直接比较, 用中文列表指出口径不一致及缺失信息。"
        "甲: 2025全年合并收入120百万元人民币; 乙: 2025年第一季度母公司收入30万元人民币。"
        "不要计算比率, 不需要联网。",
    ),
    "news": (
        "industry-news-brief",
        "请给我一份金融科技行业近期公开新闻简报, 只用当前可用的真实来源, "
        "用中文区分事实和影响判断, 带来源引用。缺失日期不要编造。",
    ),
}


async def run(args: argparse.Namespace, record: dict[str, Any]) -> None:
    settings = Settings()
    if settings.agent_model_controlled_loopback or settings.agent_model_route is None:
        raise RuntimeError("A real configured model route is required")
    app = create_app(settings=settings)
    async with app.router.lifespan_context(app):
        sessions = create_database_session_factory(app.state.resources.database_engine)
        async with sessions() as session:
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
        expected, question = CASES[args.case]
        receipt = await app.state.conversation_resources.submission_service.submit(
            scope,
            SubmitConversationTurn(
                trace_id=TraceId(f"l2-skills-live-{uuid4()}"),
                idempotency_key=f"l2-skills-live:{uuid4()}",
                question=question,
                search_mode=TurnSearchMode.WEB,
                industry_id=FINTECH_INDUSTRY_ID,
            ),
        )
        record.update(run_id=str(receipt.run_id), case=args.case, expected_skill=expected)
        emit({"stage": "submitted", **record})
        try:
            await execute_published_job(sessions, scope, receipt.job_id, settings)
        finally:
            async with sessions() as session:
                agent_run = await session.get(AgentRunRecord, receipt.run_id)
                calls = list(
                    await session.scalars(
                        select(ToolCallRecord)
                        .where(
                            ToolCallRecord.run_id == receipt.run_id,
                            ToolCallRecord.workspace_id == scope.workspace_id,
                        )
                        .order_by(ToolCallRecord.created_at)
                    )
                )
                answer = await session.scalar(
                    select(Message).where(
                        Message.agent_run_id == receipt.run_id,
                        Message.role == MessageRole.ASSISTANT,
                    )
                )
            record.update(
                status=None if agent_run is None else agent_run.status.value,
                harness_version=None if agent_run is None else agent_run.harness_version,
                stop_reason=None if agent_run is None else str(agent_run.stop_reason),
                answer=None if answer is None else answer.content_markdown,
                tools=[
                    {
                        "name": call.resolved_tool_name,
                        "status": call.status,
                        "error_code": call.error_code,
                        "observation": call.observation,
                    }
                    for call in calls
                ],
            )
        activated = [
            call
            for call in calls
            if call.resolved_tool_name == "skill.read"
            and call.status == "completed"
            and expected in json.dumps(call.observation)
        ]
        if record["status"] != "completed" or not activated or not record["answer"]:
            raise RuntimeError("L2 did not complete with the expected on-demand skill")
        if args.case == "news" and not any(
            call.resolved_tool_name == "industry.web_search" and call.status == "completed"
            for call in calls
        ):
            raise RuntimeError("News skill did not complete real industry search")
        if args.case != "news" and any(call.resolved_tool_name != "skill.read" for call in calls):
            raise RuntimeError("Excerpt-only task unexpectedly used an external tool")
        record["passed"] = True
        emit({"stage": "passed", "case": args.case, "run_id": str(receipt.run_id)})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-id", type=UUID, required=True)
    parser.add_argument("--user-id", type=UUID, required=True)
    parser.add_argument("--case", choices=CASES, default="excerpt")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    record: dict[str, Any] = {"execution_kind": "real_provider_production_l2", "passed": False}
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
