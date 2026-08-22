"""PostgreSQL-backed, deterministic Memory recall for queued Agent Runs."""

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from industry_platform.core.database import AsyncSessionFactory, safe_sqlstate
from industry_platform.modules.agent_runtime.context import (
    MAX_CONTEXT_LONG_TERM_MEMORY_CANDIDATES,
    ContextDecisionReason,
    LongTermMemoryContextSource,
    MemoryContextBundle,
    ShortTermMemoryContextSource,
)
from industry_platform.modules.conversations.models import (
    Conversation,
    ConversationStatus,
    Message,
    MessageStatus,
    Turn,
)
from industry_platform.modules.memory.domain import (
    MemoryFeedbackValue,
    MemoryKind,
    MemoryRevisionValidity,
    MemoryScope,
    MemoryStatus,
    memory_content_is_sensitive,
    utc_now,
)
from industry_platform.modules.memory.models import (
    MemoryFeedbackRecord,
    MemoryRecord,
    MemoryRevisionRecord,
    ThreadMemoryStateRecord,
)
from industry_platform.modules.workspaces.domain import WorkspaceScope

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+|[\u3400-\u9fff]", re.IGNORECASE)
_STALE_AFTER = timedelta(days=365)


class MemoryContextLoadError(RuntimeError):
    """Sanitized failure: Runtime must fail instead of silently dropping Memory."""

    def __init__(self, *, sqlstate: str | None = None) -> None:
        super().__init__("Memory Context loading failed")
        self.sqlstate = sqlstate


@dataclass(frozen=True, slots=True)
class _RecallRow:
    memory: MemoryRecord
    revision: MemoryRevisionRecord
    source_status: ConversationStatus
    feedback: MemoryFeedbackValue | None


@dataclass(frozen=True, slots=True)
class SqlAlchemyMemoryContextLoader:
    """Reload and rank only currently authorized Memory facts."""

    session_factory: AsyncSessionFactory
    clock: Callable[[], datetime] = utc_now

    async def load(
        self,
        scope: WorkspaceScope,
        *,
        conversation_id: UUID,
        current_goal: str,
        max_input_tokens: int,
    ) -> MemoryContextBundle:
        if max_input_tokens < 1:
            raise ValueError("Memory Context budget is invalid")
        now = self.clock()
        try:
            async with self.session_factory() as session:
                short_record = await session.scalar(
                    select(ThreadMemoryStateRecord).where(
                        ThreadMemoryStateRecord.workspace_id == scope.workspace_id,
                        ThreadMemoryStateRecord.conversation_id == conversation_id,
                        ThreadMemoryStateRecord.owner_user_id == scope.user_id,
                    )
                )
                short_term = await self._short_term(
                    session,
                    scope=scope,
                    conversation_id=conversation_id,
                    record=short_record,
                )
                rows = (
                    (
                        await session.execute(
                            select(
                                MemoryRecord,
                                MemoryRevisionRecord,
                                Conversation.status,
                                MemoryFeedbackRecord.value,
                            )
                            .join(
                                MemoryRevisionRecord,
                                and_(
                                    MemoryRevisionRecord.id == MemoryRecord.current_revision_id,
                                    MemoryRevisionRecord.memory_id == MemoryRecord.id,
                                    MemoryRevisionRecord.workspace_id == MemoryRecord.workspace_id,
                                ),
                            )
                            .join(
                                Conversation,
                                and_(
                                    Conversation.id == MemoryRecord.source_conversation_id,
                                    Conversation.workspace_id == MemoryRecord.workspace_id,
                                ),
                            )
                            .outerjoin(
                                MemoryFeedbackRecord,
                                and_(
                                    MemoryFeedbackRecord.memory_id == MemoryRecord.id,
                                    MemoryFeedbackRecord.memory_revision_id
                                    == MemoryRecord.current_revision_id,
                                    MemoryFeedbackRecord.actor_user_id == scope.user_id,
                                ),
                            )
                            .where(
                                MemoryRecord.workspace_id == scope.workspace_id,
                                MemoryRecord.status != MemoryStatus.DELETED,
                                or_(
                                    MemoryRecord.owner_user_id == scope.user_id,
                                    MemoryRecord.scope == MemoryScope.WORKSPACE,
                                ),
                            )
                            .order_by(MemoryRecord.updated_at.desc(), MemoryRecord.id)
                            .limit(100)
                        )
                    )
                    .tuples()
                    .all()
                )
        except SQLAlchemyError as error:
            raise MemoryContextLoadError(sqlstate=safe_sqlstate(error)) from None

        recall_rows = tuple(_RecallRow(*row) for row in rows)
        return MemoryContextBundle(
            short_term=short_term,
            long_term=self._rank(
                scope,
                rows=recall_rows,
                current_goal=current_goal,
                now=now,
            ),
        )

    @staticmethod
    async def _short_term(
        session: AsyncSession,
        *,
        scope: WorkspaceScope,
        conversation_id: UUID,
        record: ThreadMemoryStateRecord | None,
    ) -> ShortTermMemoryContextSource | None:
        if record is None:
            return None
        source_ids = tuple(record.source_message_ids)
        rows = (
            (
                await session.execute(
                    select(Message.id)
                    .join(
                        Turn,
                        and_(
                            Turn.id == Message.turn_id,
                            Turn.workspace_id == Message.workspace_id,
                        ),
                    )
                    .where(
                        Message.workspace_id == scope.workspace_id,
                        Turn.conversation_id == conversation_id,
                        Message.id.in_(source_ids),
                        Message.status.in_({MessageStatus.COMMITTED, MessageStatus.FINAL}),
                    )
                )
            )
            .scalars()
            .all()
        )
        if set(rows) != set(source_ids):
            return None
        return ShortTermMemoryContextSource(
            state_id=record.id,
            workspace_id=record.workspace_id,
            conversation_id=record.conversation_id,
            owner_user_id=record.owner_user_id,
            source_message_ids=source_ids,
            compaction_revision=record.compaction_revision,
            freshness_at=record.freshness_at,
            summary=record.summary,
        )

    def _rank(
        self,
        scope: WorkspaceScope,
        *,
        rows: tuple[_RecallRow, ...],
        current_goal: str,
        now: datetime,
    ) -> tuple[LongTermMemoryContextSource, ...]:
        goal_tokens = _tokens(current_goal)
        candidates: list[tuple[_RecallRow, float, ContextDecisionReason]] = []
        for row in rows:
            memory = row.memory
            revision = row.revision
            content = revision.content
            relevance = _relevance(goal_tokens, _tokens(content))
            if memory.status is MemoryStatus.DISABLED:
                reason = ContextDecisionReason.EXCLUDED_DISABLED
            elif memory.status is MemoryStatus.EXPIRED or (
                memory.expires_at is not None and memory.expires_at <= now
            ):
                reason = ContextDecisionReason.EXCLUDED_EXPIRED
            elif (
                revision.validity is not MemoryRevisionValidity.VALID
                or row.source_status is not ConversationStatus.ACTIVE
            ):
                reason = ContextDecisionReason.EXCLUDED_STALE
            elif memory_content_is_sensitive(content):
                reason = ContextDecisionReason.EXCLUDED_SENSITIVE
            elif row.feedback is MemoryFeedbackValue.NOT_HELPFUL:
                reason = ContextDecisionReason.EXCLUDED_NEGATIVE_FEEDBACK
            elif (
                memory.kind in {MemoryKind.FACT, MemoryKind.NOTE}
                and now - memory.updated_at > _STALE_AFTER
            ):
                reason = ContextDecisionReason.EXCLUDED_STALE
            elif relevance == 0:
                reason = ContextDecisionReason.EXCLUDED_NOT_RELEVANT
            else:
                reason = ContextDecisionReason.INCLUDED
            candidates.append((row, relevance, reason))

        candidates.sort(
            key=lambda item: (
                item[2] is ContextDecisionReason.INCLUDED,
                item[1],
                item[0].feedback is MemoryFeedbackValue.HELPFUL,
                item[0].memory.scope is MemoryScope.USER,
                item[0].memory.updated_at,
                str(item[0].memory.id),
            ),
            reverse=True,
        )
        seen_content: set[str] = set()
        accepted_topics: list[tuple[MemoryKind, frozenset[str], str]] = []
        resolved: list[LongTermMemoryContextSource] = []
        for row, relevance, original_reason in candidates[:MAX_CONTEXT_LONG_TERM_MEMORY_CANDIDATES]:
            memory = row.memory
            revision = row.revision
            normalized = " ".join(revision.content.lower().split())
            reason = original_reason
            if reason is ContextDecisionReason.INCLUDED and normalized in seen_content:
                reason = ContextDecisionReason.EXCLUDED_DUPLICATE
            content_tokens = _tokens(revision.content)
            if reason is ContextDecisionReason.INCLUDED and any(
                kind is memory.kind
                and prior_content != normalized
                and _jaccard(content_tokens, topic_tokens) >= 0.6
                for kind, topic_tokens, prior_content in accepted_topics
            ):
                reason = ContextDecisionReason.EXCLUDED_CONFLICTED
            if reason is ContextDecisionReason.INCLUDED:
                seen_content.add(normalized)
                accepted_topics.append((memory.kind, content_tokens, normalized))
            expose_digest = reason not in {
                ContextDecisionReason.EXCLUDED_SENSITIVE,
                ContextDecisionReason.EXCLUDED_DELETED,
            }
            resolved.append(
                LongTermMemoryContextSource(
                    memory_id=memory.id,
                    revision_id=revision.id,
                    workspace_id=memory.workspace_id,
                    owner_user_id=memory.owner_user_id,
                    revision=revision.version,
                    scope=memory.scope.value,
                    kind=memory.kind.value,
                    decision_reason=reason,
                    relevance_score=relevance,
                    feedback_score=(
                        1
                        if row.feedback is MemoryFeedbackValue.HELPFUL
                        else -1
                        if row.feedback is MemoryFeedbackValue.NOT_HELPFUL
                        else 0
                    ),
                    updated_at=memory.updated_at,
                    content=(
                        revision.content if reason is ContextDecisionReason.INCLUDED else None
                    ),
                    content_sha256=(_sha256(revision.content) if expose_digest else None),
                )
            )
        return tuple(resolved)


def _tokens(value: str) -> frozenset[str]:
    return frozenset(match.group(0).lower() for match in _TOKEN_PATTERN.finditer(value))


def _relevance(goal: frozenset[str], memory: frozenset[str]) -> float:
    if not goal or not memory:
        return 0
    return round(len(goal & memory) / len(goal), 6)


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    union = left | right
    return 0 if not union else len(left & right) / len(union)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
