"""Technology-independent rules for reliable background execution."""

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from itertools import pairwise
from types import MappingProxyType
from typing import Final, NewType, TypedDict
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter

from industry_platform.modules.identity.domain import TraceId

JobIdempotencyKeyHash = NewType("JobIdempotencyKeyHash", bytes)
JobRequestFingerprint = NewType("JobRequestFingerprint", bytes)

_IDEMPOTENCY_HASH_DOMAIN: Final = b"industry-platform:job-idempotency:v1\x00"
_REQUEST_FINGERPRINT_DOMAIN: Final = b"industry-platform:job-request:v1\x00"
_OUTBOX_RETRY_JITTER_DOMAIN: Final = b"industry-platform:outbox-retry:v1\x00"
_JOB_RETRY_JITTER_DOMAIN: Final = b"industry-platform:job-retry:v1\x00"

CELERY_JOB_DISPATCH_TASK_NAME: Final = "industry_platform.jobs.execute"
JOB_DISPATCH_OUTBOX_TOPIC: Final = "jobs.dispatch"
JOB_DISPATCH_OUTBOX_EVENT_TYPE: Final = "job.dispatch.requested"
OUTBOX_RETRY_BASE_SECONDS: Final = 2
OUTBOX_RETRY_MAX_SECONDS: Final = 300
JOB_RETRY_BASE_SECONDS: Final = 2
JOB_RETRY_MAX_SECONDS: Final = 300


class JobStatus(StrEnum):
    """Persisted lifecycle of one logical job."""

    PENDING = "pending"
    DISPATCHED = "dispatched"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DEAD_LETTER = "dead_letter"


class JobEventType(StrEnum):
    """Append-only facts emitted while a job changes state."""

    CREATED = "created"
    DISPATCHED = "dispatched"
    STARTED = "started"
    HEARTBEAT = "heartbeat"
    RETRY_SCHEDULED = "retry_scheduled"
    LEASE_EXPIRED = "lease_expired"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DEAD_LETTER = "dead_letter"


class OutboxStatus(StrEnum):
    """Delivery lifecycle for one transactional outbox event."""

    PENDING = "pending"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    DEAD_LETTER = "dead_letter"


class OutboxPublishErrorCode(StrEnum):
    """Stable, non-sensitive failures persisted by the dispatcher."""

    CELERY_PUBLISH_FAILED = "celery_publish_failed"


class OutboxFailureDisposition(StrEnum):
    """Result of settling one failed publish using its exact claim proof."""

    RETRY_SCHEDULED = "retry_scheduled"
    DEAD_LETTER = "dead_letter"
    CLAIM_LOST = "claim_lost"


class JobExecutionErrorCode(StrEnum):
    """Stable, non-sensitive worker outcomes safe for persistence and metrics."""

    UNKNOWN_HANDLER = "unknown_job_handler"
    INVALID_PAYLOAD = "invalid_job_payload"
    HANDLER_FAILED = "job_handler_failed"
    CLEANUP_UNAVAILABLE = "cleanup_unavailable"
    SOFT_TIME_LIMIT_EXCEEDED = "soft_time_limit_exceeded"
    UNSTARTED_TIMEOUT = "job_unstarted_timeout"
    LEASE_EXPIRED = "job_lease_expired"


class JobRetryDisposition(StrEnum):
    """Authoritative result of settling one retryable execution failure."""

    RETRY_SCHEDULED = "retry_scheduled"
    DEAD_LETTER = "dead_letter"


class ScheduleMisfirePolicy(StrEnum):
    """Explicit behavior for occurrences discovered after their due time."""

    CATCH_UP_EACH = "catch_up_each"
    COALESCE_LATEST = "coalesce_latest"
    MANUAL = "manual"


class ScheduleTriggerKind(StrEnum):
    """How one durable schedule occurrence was requested."""

    SCHEDULED = "scheduled"
    MANUAL = "manual"


class ScheduleOccurrenceStatus(StrEnum):
    """Whether a durable occurrence produced work or stopped for review."""

    MATERIALIZED = "materialized"
    MISFIRE_BLOCKED = "misfire_blocked"


class ScheduleMisfireErrorCode(StrEnum):
    """Stable reasons why an automatic schedule did not advance."""

    WINDOW_EXCEEDED = "misfire_window_exceeded"
    LIMIT_EXCEEDED = "misfire_limit_exceeded"
    MANUAL_REVIEW_REQUIRED = "misfire_manual_review_required"


TERMINAL_JOB_STATUSES: Final = frozenset(
    {
        JobStatus.SUCCEEDED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
        JobStatus.DEAD_LETTER,
    }
)
TERMINAL_JOB_EVENT_TYPES: Final = frozenset(
    {
        JobEventType.SUCCEEDED,
        JobEventType.FAILED,
        JobEventType.CANCELLED,
        JobEventType.DEAD_LETTER,
    }
)
TERMINAL_OUTBOX_STATUSES: Final = frozenset({OutboxStatus.PUBLISHED, OutboxStatus.DEAD_LETTER})

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def require_utc(value: datetime, *, field_name: str) -> None:
    """Reject naive or non-UTC timestamps at application boundaries."""

    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{field_name} must use timezone-aware UTC")


def snapshot_json_mapping(
    value: Mapping[str, object],
    *,
    error_message: str,
) -> Mapping[str, object]:
    """Deep-copy JSON data into a canonical, top-level immutable mapping."""

    try:
        encoded = json.dumps(
            dict(value),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        decoded = json.loads(encoded)
    except (TypeError, ValueError):
        raise ValueError(error_message) from None
    if not isinstance(decoded, dict):
        raise ValueError(error_message)
    return MappingProxyType(decoded)


@dataclass(frozen=True, slots=True)
class ExecutionScope:
    """Exactly one tenant Workspace or named system scope."""

    workspace_id: UUID | None = None
    system_scope_key: str | None = None

    def __post_init__(self) -> None:
        if (self.workspace_id is None) == (self.system_scope_key is None):
            raise ValueError("Execution scope must be workspace or system, not both")
        if self.workspace_id is not None and self.workspace_id.int == 0:
            raise ValueError("Workspace scope must not use a nil UUID")
        if self.system_scope_key is not None and not _IDENTIFIER_PATTERN.fullmatch(
            self.system_scope_key
        ):
            raise ValueError("System scope key is invalid")


@dataclass(frozen=True, slots=True)
class JobDefinition:
    """Validated logical job input before persistence."""

    scope: ExecutionScope
    task_name: str
    queue_name: str
    payload: Mapping[str, object] = field(repr=False)
    available_at: datetime
    max_attempts: int
    idempotency_key: str | None = field(default=None, repr=False)
    priority: int = 0
    soft_time_limit_seconds: int = 1_500
    hard_time_limit_seconds: int = 1_800

    def __post_init__(self) -> None:
        if not _IDENTIFIER_PATTERN.fullmatch(self.task_name):
            raise ValueError("Job task name is invalid")
        if not _IDENTIFIER_PATTERN.fullmatch(self.queue_name):
            raise ValueError("Job queue name is invalid")
        if not 1 <= self.max_attempts <= 100:
            raise ValueError("Job max attempts must be between 1 and 100")
        if not -100 <= self.priority <= 100:
            raise ValueError("Job priority must be between -100 and 100")
        if self.idempotency_key is not None and (
            not self.idempotency_key.strip() or len(self.idempotency_key) > 200
        ):
            raise ValueError("Job idempotency key is invalid")
        if not (1 <= self.soft_time_limit_seconds < self.hard_time_limit_seconds <= 1_800):
            raise ValueError("Job time limits are invalid")
        require_utc(self.available_at, field_name="available_at")

        object.__setattr__(
            self,
            "payload",
            snapshot_json_mapping(
                self.payload,
                error_message="Job payload must be canonical JSON data",
            ),
        )


def hash_job_idempotency_key(raw_key: str) -> JobIdempotencyKeyHash:
    """Return a domain-separated digest without persisting the caller's key."""

    if not raw_key.strip() or len(raw_key) > 200:
        raise ValueError("Job idempotency key is invalid")
    digest = hashlib.sha256(_IDEMPOTENCY_HASH_DOMAIN + raw_key.encode("utf-8"))
    return JobIdempotencyKeyHash(digest.digest())


def fingerprint_job_request(definition: JobDefinition) -> JobRequestFingerprint:
    """Hash the stable semantic request using canonical JSON serialization."""

    canonical_request = {
        "available_at": definition.available_at.isoformat().replace("+00:00", "Z"),
        "hard_time_limit_seconds": definition.hard_time_limit_seconds,
        "max_attempts": definition.max_attempts,
        "payload": dict(definition.payload),
        "payload_schema_version": 1,
        "priority": definition.priority,
        "queue_name": definition.queue_name,
        "scope": {
            "system_scope_key": definition.scope.system_scope_key,
            "workspace_id": (
                str(definition.scope.workspace_id)
                if definition.scope.workspace_id is not None
                else None
            ),
        },
        "soft_time_limit_seconds": definition.soft_time_limit_seconds,
        "task_name": definition.task_name,
    }
    canonical_json = json.dumps(
        canonical_request,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    digest = hashlib.sha256(_REQUEST_FINGERPRINT_DOMAIN + canonical_json)
    return JobRequestFingerprint(digest.digest())


@dataclass(frozen=True, slots=True)
class JobLease:
    """Worker ownership proof guarded by generation and fencing token."""

    owner: str
    lease_token: UUID
    generation: int
    fencing_token: int
    heartbeat_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        if not _IDENTIFIER_PATTERN.fullmatch(self.owner):
            raise ValueError("Lease owner is invalid")
        if self.lease_token.int == 0:
            raise ValueError("Lease token must not be a nil UUID")
        if self.generation < 0 or self.fencing_token < 1:
            raise ValueError("Lease generation or fencing token is invalid")
        require_utc(self.heartbeat_at, field_name="heartbeat_at")
        require_utc(self.expires_at, field_name="expires_at")
        if self.expires_at <= self.heartbeat_at:
            raise ValueError("Lease expiration must be after its heartbeat")


@dataclass(frozen=True, slots=True)
class ScheduleDefinition:
    """Validated IANA-timezone cron schedule."""

    scope: ExecutionScope
    name: str
    task_name: str
    cron_expression: str
    timezone_name: str
    payload: Mapping[str, object] = field(repr=False)
    queue_name: str = "default"
    max_attempts: int = 3
    priority: int = 0
    soft_time_limit_seconds: int = 1_500
    hard_time_limit_seconds: int = 1_800
    misfire_policy: ScheduleMisfirePolicy = ScheduleMisfirePolicy.CATCH_UP_EACH
    catch_up_window_seconds: int = 86_400
    max_catch_up: int = 100

    def __post_init__(self) -> None:
        if not _IDENTIFIER_PATTERN.fullmatch(self.name):
            raise ValueError("Schedule name is invalid")
        if not _IDENTIFIER_PATTERN.fullmatch(self.task_name):
            raise ValueError("Scheduled task name is invalid")
        if (
            len(self.cron_expression) > 120
            or len(self.cron_expression.split()) != 5
            or not croniter.is_valid(self.cron_expression)
        ):
            raise ValueError("Cron expression is invalid")
        try:
            ZoneInfo(self.timezone_name)
        except ZoneInfoNotFoundError:
            raise ValueError("Schedule timezone is invalid") from None
        if not 1 <= self.catch_up_window_seconds <= 604_800:
            raise ValueError("Schedule catch-up window is invalid")
        if not 1 <= self.max_catch_up <= 1_000:
            raise ValueError("Schedule catch-up limit is invalid")
        JobDefinition(
            scope=self.scope,
            task_name=self.task_name,
            queue_name=self.queue_name,
            payload=self.payload,
            available_at=datetime(2000, 1, 1, tzinfo=UTC),
            max_attempts=self.max_attempts,
            priority=self.priority,
            soft_time_limit_seconds=self.soft_time_limit_seconds,
            hard_time_limit_seconds=self.hard_time_limit_seconds,
        )

        object.__setattr__(
            self,
            "payload",
            snapshot_json_mapping(
                self.payload,
                error_message="Schedule payload must be canonical JSON data",
            ),
        )

    def describe_occurrence(self, scheduled_for: datetime) -> "CronOccurrence":
        """Attach the IANA offset and explicit DST-adjustment evidence."""

        require_utc(scheduled_for, field_name="scheduled_for")
        local_value = scheduled_for.astimezone(ZoneInfo(self.timezone_name))
        offset = local_value.utcoffset()
        if offset is None:
            raise ValueError("Schedule timezone did not provide a UTC offset")
        return CronOccurrence(
            scheduled_for=scheduled_for,
            dst_adjusted=not croniter.match(self.cron_expression, local_value),
            utc_offset_seconds=int(offset.total_seconds()),
        )

    def next_occurrence_after(self, reference: datetime) -> "CronOccurrence":
        """Calculate the next canonical occurrence in the configured timezone."""

        require_utc(reference, field_name="reference")
        timezone = ZoneInfo(self.timezone_name)
        local_reference = reference.astimezone(timezone).replace(tzinfo=None)
        iterator = croniter(self.cron_expression, local_reference)
        for _ in range(10_000):
            local_candidate = iterator.get_next(datetime)
            if not isinstance(local_candidate, datetime):
                raise ValueError("Cron calculation did not return a datetime")
            aware_candidate, dst_adjusted = _resolve_local_cron_candidate(
                local_candidate,
                timezone,
            )
            scheduled_for = aware_candidate.astimezone(UTC)
            if scheduled_for > reference:
                offset = aware_candidate.utcoffset()
                if offset is None:
                    raise ValueError("Schedule timezone did not provide a UTC offset")
                return CronOccurrence(
                    scheduled_for=scheduled_for,
                    dst_adjusted=dst_adjusted,
                    utc_offset_seconds=int(offset.total_seconds()),
                )
        raise ValueError("Cron calculation did not make forward progress")

    def previous_occurrence_at_or_before(
        self,
        reference: datetime,
    ) -> "CronOccurrence":
        """Find the latest due instant without enumerating an unbounded outage."""

        require_utc(reference, field_name="reference")
        timezone = ZoneInfo(self.timezone_name)
        local_reference = reference.astimezone(timezone).replace(tzinfo=None)
        candidates: list[CronOccurrence] = []
        iterator = croniter(
            self.cron_expression,
            local_reference + timedelta(seconds=1),
        )
        for _ in range(10_000):
            local_candidate = iterator.get_prev(datetime)
            if not isinstance(local_candidate, datetime):
                raise ValueError("Cron calculation did not return a datetime")
            aware_candidate, dst_adjusted = _resolve_local_cron_candidate(
                local_candidate,
                timezone,
            )
            scheduled_for = aware_candidate.astimezone(UTC)
            if scheduled_for <= reference:
                candidates.append(
                    _cron_occurrence(
                        aware_candidate,
                        dst_adjusted=dst_adjusted,
                    )
                )
                break
        else:
            raise ValueError("Cron calculation did not make backward progress")

        # During a fall-back fold, a later local wall time using the canonical
        # earlier offset can already be in the past in absolute UTC time. Scan
        # forward across that bounded fold so the result remains the latest
        # canonical occurrence at or before the database clock.
        forward_iterator = croniter(self.cron_expression, local_reference)
        for _ in range(10_000):
            local_candidate = forward_iterator.get_next(datetime)
            if not isinstance(local_candidate, datetime):
                raise ValueError("Cron calculation did not return a datetime")
            aware_candidate, dst_adjusted = _resolve_local_cron_candidate(
                local_candidate,
                timezone,
            )
            scheduled_for = aware_candidate.astimezone(UTC)
            if scheduled_for > reference:
                break
            candidates.append(
                _cron_occurrence(
                    aware_candidate,
                    dst_adjusted=dst_adjusted,
                )
            )
        else:
            raise ValueError("Cron calculation did not make forward progress")

        return max(candidates, key=lambda occurrence: occurrence.scheduled_for)

    def next_after(self, reference: datetime) -> datetime:
        """Compatibility helper returning only the next UTC instant."""

        return self.next_occurrence_after(reference).scheduled_for


@dataclass(frozen=True, slots=True)
class CronOccurrence:
    """One canonical UTC cron instant with enough evidence to explain DST."""

    scheduled_for: datetime
    dst_adjusted: bool
    utc_offset_seconds: int

    def __post_init__(self) -> None:
        require_utc(self.scheduled_for, field_name="scheduled_for")
        if not -86_400 < self.utc_offset_seconds < 86_400:
            raise ValueError("Schedule UTC offset is invalid")


def _resolve_local_cron_candidate(
    local_candidate: datetime,
    timezone: ZoneInfo,
) -> tuple[datetime, bool]:
    """Choose fold zero, or move a nonexistent wall time to the first valid minute."""

    candidate = local_candidate.replace(tzinfo=None, second=0, microsecond=0)
    for minute_offset in range(2_881):
        shifted = candidate + timedelta(minutes=minute_offset)
        aware = shifted.replace(tzinfo=timezone, fold=0)
        round_trip = aware.astimezone(UTC).astimezone(timezone)
        if round_trip.replace(tzinfo=None) == shifted:
            return aware, minute_offset > 0
    raise ValueError("Schedule timezone gap exceeds the supported bound")


def _cron_occurrence(
    aware_candidate: datetime,
    *,
    dst_adjusted: bool,
) -> CronOccurrence:
    """Convert one resolved local candidate into persisted UTC evidence."""

    offset = aware_candidate.utcoffset()
    if offset is None:
        raise ValueError("Schedule timezone did not provide a UTC offset")
    return CronOccurrence(
        scheduled_for=aware_candidate.astimezone(UTC),
        dst_adjusted=dst_adjusted,
        utc_offset_seconds=int(offset.total_seconds()),
    )


@dataclass(frozen=True, slots=True)
class ScheduleOccurrenceDefinition:
    """A scheduled instant or an independently identified manual trigger."""

    schedule_id: UUID
    schedule_version: int
    trigger_kind: ScheduleTriggerKind
    scheduled_for: datetime | None = None
    trigger_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.schedule_id.int == 0:
            raise ValueError("Schedule ID must not be a nil UUID")
        if self.schedule_version < 1:
            raise ValueError("Schedule version must be positive")

        is_scheduled = self.trigger_kind is ScheduleTriggerKind.SCHEDULED
        if is_scheduled != (self.scheduled_for is not None):
            raise ValueError("Scheduled triggers require exactly one scheduled instant")
        if is_scheduled == (self.trigger_id is not None):
            raise ValueError("Manual triggers require exactly one independent trigger ID")

        if self.scheduled_for is not None:
            require_utc(self.scheduled_for, field_name="scheduled_for")
        if self.trigger_id is not None and self.trigger_id.int == 0:
            raise ValueError("Manual trigger ID must not be a nil UUID")


@dataclass(frozen=True, slots=True)
class PlannedScheduleOccurrence:
    """Persistence-independent materialization selected by the misfire policy."""

    status: ScheduleOccurrenceStatus
    scheduled_for: datetime
    window_start: datetime
    window_end: datetime
    coalesced_count: int
    dst_adjusted: bool
    utc_offset_seconds: int
    error_code: ScheduleMisfireErrorCode | None = None

    def __post_init__(self) -> None:
        for field_name, value in (
            ("scheduled_for", self.scheduled_for),
            ("window_start", self.window_start),
            ("window_end", self.window_end),
        ):
            require_utc(value, field_name=field_name)
        if self.window_start > self.scheduled_for or self.scheduled_for > self.window_end:
            raise ValueError("Schedule occurrence window is invalid")
        if self.coalesced_count < 1:
            raise ValueError("Schedule occurrence count must be positive")
        if not -86_400 < self.utc_offset_seconds < 86_400:
            raise ValueError("Schedule occurrence UTC offset is invalid")
        blocked = self.status is ScheduleOccurrenceStatus.MISFIRE_BLOCKED
        if blocked != (self.error_code is not None):
            raise ValueError("Schedule occurrence error state is inconsistent")


@dataclass(frozen=True, slots=True)
class SchedulePlan:
    """Bounded result of evaluating one locked Schedule at database time."""

    occurrences: tuple[PlannedScheduleOccurrence, ...]
    next_due_at: datetime
    blocked: bool
    missed_from: datetime | None = None
    missed_through: datetime | None = None
    missed_count: int = 0
    missed_count_is_lower_bound: bool = False
    error_code: ScheduleMisfireErrorCode | None = None

    def __post_init__(self) -> None:
        require_utc(self.next_due_at, field_name="next_due_at")
        if self.blocked:
            if (
                len(self.occurrences) != 1
                or self.occurrences[0].status is not ScheduleOccurrenceStatus.MISFIRE_BLOCKED
                or self.missed_from is None
                or self.missed_through is None
                or self.missed_count < 1
                or self.error_code is None
            ):
                raise ValueError("Blocked schedule plan is inconsistent")
            blocked_occurrence = self.occurrences[0]
            expected_lower_bound = (
                self.error_code is not ScheduleMisfireErrorCode.MANUAL_REVIEW_REQUIRED
            )
            if (
                self.next_due_at != self.missed_from
                or blocked_occurrence.scheduled_for != self.missed_from
                or blocked_occurrence.window_start != self.missed_from
                or blocked_occurrence.window_end != self.missed_through
                or blocked_occurrence.coalesced_count != self.missed_count
                or blocked_occurrence.error_code is not self.error_code
                or self.missed_count_is_lower_bound != expected_lower_bound
            ):
                raise ValueError("Blocked schedule evidence is inconsistent")
        else:
            if (
                self.missed_from is not None
                or self.missed_through is not None
                or self.missed_count != 0
                or self.missed_count_is_lower_bound
                or self.error_code is not None
            ):
                raise ValueError("Runnable schedule plan contains misfire evidence")
            if any(
                occurrence.status is not ScheduleOccurrenceStatus.MATERIALIZED
                for occurrence in self.occurrences
            ):
                raise ValueError("Runnable schedule plan contains a blocked occurrence")
            scheduled_times = tuple(occurrence.scheduled_for for occurrence in self.occurrences)
            if any(current >= following for current, following in pairwise(scheduled_times)):
                raise ValueError("Schedule plan occurrences must strictly increase")
            if scheduled_times and self.next_due_at <= scheduled_times[-1]:
                raise ValueError("Schedule plan did not advance beyond its occurrences")


def plan_due_schedule(
    definition: ScheduleDefinition,
    *,
    next_due_at: datetime,
    database_now: datetime,
) -> SchedulePlan:
    """Apply bounded catch-up rules without consulting a broker or wall clock."""

    require_utc(next_due_at, field_name="next_due_at")
    require_utc(database_now, field_name="database_now")
    if next_due_at > database_now:
        return SchedulePlan((), next_due_at, False)

    first = definition.describe_occurrence(next_due_at)
    if database_now - next_due_at > timedelta(seconds=definition.catch_up_window_seconds):
        latest = definition.previous_occurrence_at_or_before(database_now)
        return _blocked_schedule_plan(
            first,
            latest,
            1,
            error_code=ScheduleMisfireErrorCode.WINDOW_EXCEEDED,
            count_is_lower_bound=True,
        )

    due: list[CronOccurrence] = [first]
    following = definition.next_occurrence_after(next_due_at)
    while following.scheduled_for <= database_now:
        due.append(following)
        if len(due) > definition.max_catch_up:
            latest = definition.previous_occurrence_at_or_before(database_now)
            return _blocked_schedule_plan(
                first,
                latest,
                definition.max_catch_up + 1,
                error_code=ScheduleMisfireErrorCode.LIMIT_EXCEEDED,
                count_is_lower_bound=True,
            )
        following = definition.next_occurrence_after(following.scheduled_for)

    latest = due[-1]
    if definition.misfire_policy is ScheduleMisfirePolicy.MANUAL:
        return _blocked_schedule_plan(
            first,
            latest,
            len(due),
            error_code=ScheduleMisfireErrorCode.MANUAL_REVIEW_REQUIRED,
            count_is_lower_bound=False,
        )

    if definition.misfire_policy is ScheduleMisfirePolicy.CATCH_UP_EACH:
        planned = tuple(
            PlannedScheduleOccurrence(
                status=ScheduleOccurrenceStatus.MATERIALIZED,
                scheduled_for=occurrence.scheduled_for,
                window_start=occurrence.scheduled_for,
                window_end=occurrence.scheduled_for,
                coalesced_count=1,
                dst_adjusted=occurrence.dst_adjusted,
                utc_offset_seconds=occurrence.utc_offset_seconds,
            )
            for occurrence in due
        )
    else:
        planned = (
            PlannedScheduleOccurrence(
                status=ScheduleOccurrenceStatus.MATERIALIZED,
                scheduled_for=latest.scheduled_for,
                window_start=first.scheduled_for,
                window_end=latest.scheduled_for,
                coalesced_count=len(due),
                dst_adjusted=latest.dst_adjusted,
                utc_offset_seconds=latest.utc_offset_seconds,
            ),
        )
    return SchedulePlan(planned, following.scheduled_for, False)


def _blocked_schedule_plan(
    first: CronOccurrence,
    latest: CronOccurrence,
    count: int,
    *,
    error_code: ScheduleMisfireErrorCode,
    count_is_lower_bound: bool,
) -> SchedulePlan:
    occurrence = PlannedScheduleOccurrence(
        status=ScheduleOccurrenceStatus.MISFIRE_BLOCKED,
        scheduled_for=first.scheduled_for,
        window_start=first.scheduled_for,
        window_end=latest.scheduled_for,
        coalesced_count=count,
        dst_adjusted=first.dst_adjusted,
        utc_offset_seconds=first.utc_offset_seconds,
        error_code=error_code,
    )
    return SchedulePlan(
        (occurrence,),
        first.scheduled_for,
        True,
        missed_from=first.scheduled_for,
        missed_through=latest.scheduled_for,
        missed_count=count,
        missed_count_is_lower_bound=count_is_lower_bound,
        error_code=error_code,
    )


@dataclass(frozen=True, slots=True)
class ScheduleTickCommand:
    """Bound one high-availability database schedule scan."""

    batch_size: int

    def __post_init__(self) -> None:
        if not 1 <= self.batch_size <= 100:
            raise ValueError("Schedule tick batch size is invalid")


@dataclass(frozen=True, slots=True)
class ManualScheduleTriggerCommand:
    """Idempotently run one Schedule without advancing its cron cursor."""

    schedule_id: UUID
    trigger_id: UUID

    def __post_init__(self) -> None:
        if self.schedule_id.int == 0 or self.trigger_id.int == 0:
            raise ValueError("Manual schedule identifiers must not be nil")


@dataclass(frozen=True, slots=True)
class EnsuredSchedule:
    """Result of idempotently installing one named schedule."""

    schedule_id: UUID
    created: bool


@dataclass(frozen=True, slots=True)
class ManualScheduleTriggerResult:
    """Committed manual occurrence and its single logical Job."""

    occurrence_id: UUID
    job_id: UUID
    created: bool


@dataclass(frozen=True, slots=True)
class ScheduleTickResult:
    """Payload-free outcome of one committed Beat transaction."""

    selected_schedules: int
    materialized_occurrences: int
    jobs_created: int
    blocked_schedules: int

    def __post_init__(self) -> None:
        counts = (
            self.selected_schedules,
            self.materialized_occurrences,
            self.jobs_created,
            self.blocked_schedules,
        )
        if any(count < 0 for count in counts):
            raise ValueError("Schedule tick counts cannot be negative")
        if self.blocked_schedules > self.selected_schedules:
            raise ValueError("Schedule tick blocked count exceeds selected schedules")
        if self.selected_schedules > (self.blocked_schedules + self.materialized_occurrences):
            raise ValueError("Schedule tick selected schedules have no outcome")
        if self.jobs_created > self.materialized_occurrences:
            raise ValueError("Schedule tick created too many Jobs")


@dataclass(frozen=True, slots=True)
class SubmitJobCommand:
    """Trusted application request to durably enqueue one logical job."""

    definition: JobDefinition = field(repr=False)
    trace_id: TraceId

    def __post_init__(self) -> None:
        if not self.trace_id or len(self.trace_id) > 64:
            raise ValueError("Job trace ID is invalid")


@dataclass(frozen=True, slots=True)
class PreparedJobSubmission:
    """Persistence-safe submission with the raw idempotency key removed."""

    job_id: UUID
    outbox_event_id: UUID
    scope: ExecutionScope
    task_name: str
    queue_name: str
    payload: Mapping[str, object] = field(repr=False)
    available_at: datetime
    max_attempts: int
    priority: int
    soft_time_limit_seconds: int
    hard_time_limit_seconds: int
    trace_id: TraceId
    idempotency_key_hash: JobIdempotencyKeyHash | None = field(repr=False)
    request_fingerprint: JobRequestFingerprint | None = field(repr=False)
    submitted_at: datetime

    def __post_init__(self) -> None:
        if self.job_id.int == 0 or self.outbox_event_id.int == 0:
            raise ValueError("Job submission identifiers must not be nil")
        if (self.idempotency_key_hash is None) != (self.request_fingerprint is None):
            raise ValueError("Job idempotency digests must be paired")
        require_utc(self.available_at, field_name="available_at")
        require_utc(self.submitted_at, field_name="submitted_at")
        object.__setattr__(
            self,
            "payload",
            snapshot_json_mapping(
                self.payload,
                error_message="Job payload must be canonical JSON data",
            ),
        )


@dataclass(frozen=True, slots=True)
class JobSubmissionRecord:
    """Committed submission result, including whether this call inserted it."""

    job_id: UUID
    outbox_event_id: UUID
    status: JobStatus
    dispatch_generation: int
    created: bool


@dataclass(frozen=True, slots=True)
class ClaimOutboxCommand:
    """Bounded request for one short PostgreSQL outbox-claim transaction."""

    dispatcher_id: str
    batch_size: int
    claim_seconds: int

    def __post_init__(self) -> None:
        if not _IDENTIFIER_PATTERN.fullmatch(self.dispatcher_id):
            raise ValueError("Outbox dispatcher ID is invalid")
        if not 1 <= self.batch_size <= 1_000:
            raise ValueError("Outbox claim batch size is invalid")
        if not 1 <= self.claim_seconds <= 3_600:
            raise ValueError("Outbox claim duration is invalid")


@dataclass(frozen=True, slots=True)
class OutboxClaimProof:
    """Exact CAS coordinates proving ownership of one outbox claim."""

    outbox_id: UUID
    locked_by: str
    claim_token: UUID
    claim_generation: int

    def __post_init__(self) -> None:
        if self.outbox_id.int == 0 or self.claim_token.int == 0:
            raise ValueError("Outbox claim identifiers are invalid")
        if not _IDENTIFIER_PATTERN.fullmatch(self.locked_by):
            raise ValueError("Outbox claim owner is invalid")
        if self.claim_generation < 1:
            raise ValueError("Outbox claim generation must be positive")


@dataclass(frozen=True, slots=True)
class JobDispatchMessage:
    """The complete and intentionally minimal Celery message body."""

    job_id: UUID
    dispatch_generation: int
    outbox_id: UUID
    trace_id: TraceId

    def __post_init__(self) -> None:
        if self.job_id.int == 0 or self.outbox_id.int == 0:
            raise ValueError("Job dispatch message identifiers are invalid")
        if self.dispatch_generation < 1:
            raise ValueError("Job dispatch generation must be positive")
        if not self.trace_id or len(self.trace_id) > 64:
            raise ValueError("Job dispatch trace ID is invalid")

    def as_json_kwargs(self) -> "JobDispatchKwargs":
        """Return only broker-safe coordinates; never include business input."""

        return {
            "job_id": str(self.job_id),
            "dispatch_generation": self.dispatch_generation,
            "outbox_id": str(self.outbox_id),
            "trace_id": str(self.trace_id),
        }


class JobDispatchKwargs(TypedDict):
    """Statically typed form of the four-field Celery delivery contract."""

    job_id: str
    dispatch_generation: int
    outbox_id: str
    trace_id: str


@dataclass(frozen=True, slots=True)
class ClaimedJobDispatch:
    """A committed outbox claim plus routing data read from its related Job."""

    proof: OutboxClaimProof
    message: JobDispatchMessage
    queue_name: str
    attempt_count: int
    max_attempts: int
    soft_time_limit_seconds: int
    hard_time_limit_seconds: int

    def __post_init__(self) -> None:
        if not _IDENTIFIER_PATTERN.fullmatch(self.queue_name):
            raise ValueError("Claimed job queue name is invalid")
        if not 1 <= self.attempt_count <= self.max_attempts:
            raise ValueError("Claimed outbox attempt bounds are invalid")
        if not (1 <= self.soft_time_limit_seconds < self.hard_time_limit_seconds <= 1_800):
            raise ValueError("Claimed job time limits are invalid")


def outbox_retry_delay_seconds(outbox_id: UUID, attempt_count: int) -> int:
    """Return capped exponential delay with stable per-outbox jitter."""

    if outbox_id.int == 0:
        raise ValueError("Outbox retry identifier must not be nil")
    if attempt_count < 1:
        raise ValueError("Outbox retry attempt must be positive")

    exponent = min(attempt_count - 1, 30)
    exponential_delay = min(
        OUTBOX_RETRY_MAX_SECONDS,
        OUTBOX_RETRY_BASE_SECONDS * (1 << exponent),
    )
    remaining_capacity = OUTBOX_RETRY_MAX_SECONDS - exponential_delay
    if remaining_capacity == 0:
        return OUTBOX_RETRY_MAX_SECONDS

    jitter_bound = min(max(1, exponential_delay // 4), remaining_capacity)
    digest = hashlib.sha256(
        _OUTBOX_RETRY_JITTER_DOMAIN
        + outbox_id.bytes
        + attempt_count.to_bytes(8, byteorder="big", signed=False)
    ).digest()
    jitter = int.from_bytes(digest[:4], byteorder="big") % (jitter_bound + 1)
    return exponential_delay + jitter


@dataclass(frozen=True, slots=True)
class AcquireJobCommand:
    """Broker delivery coordinates presented by one worker."""

    job_id: UUID
    dispatch_generation: int
    worker_id: str
    outbox_id: UUID | None = None
    trace_id: TraceId | None = None

    def __post_init__(self) -> None:
        if self.job_id.int == 0 or self.dispatch_generation < 1:
            raise ValueError("Job delivery coordinates are invalid")
        if not _IDENTIFIER_PATTERN.fullmatch(self.worker_id):
            raise ValueError("Worker ID is invalid")
        if (self.outbox_id is None) != (self.trace_id is None):
            raise ValueError("Job delivery correlation coordinates must be paired")
        if self.outbox_id is not None and self.outbox_id.int == 0:
            raise ValueError("Job delivery outbox ID is invalid")
        if self.trace_id is not None and (not self.trace_id or len(self.trace_id) > 64):
            raise ValueError("Job delivery trace ID is invalid")


@dataclass(frozen=True, slots=True)
class JobLeaseProof:
    """Unforgeable lease coordinates required by every worker mutation."""

    job_id: UUID
    owner: str
    lease_token: UUID
    fencing_token: int

    def __post_init__(self) -> None:
        if self.job_id.int == 0 or self.lease_token.int == 0:
            raise ValueError("Job lease identifiers are invalid")
        if not _IDENTIFIER_PATTERN.fullmatch(self.owner) or self.fencing_token < 1:
            raise ValueError("Job lease proof is invalid")


@dataclass(frozen=True, slots=True)
class AcquiredJob:
    """Sensitive execution input returned only to the winning worker."""

    job_id: UUID
    task_name: str
    queue_name: str
    payload: Mapping[str, object] = field(repr=False)
    dispatch_generation: int
    lease: JobLease
    stage_sequence: int
    attempt_count: int
    max_attempts: int
    soft_time_limit_seconds: int
    hard_time_limit_seconds: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))
        if not 1 <= self.attempt_count <= self.max_attempts:
            raise ValueError("Acquired job attempt bounds are invalid")
        if not (1 <= self.soft_time_limit_seconds < self.hard_time_limit_seconds <= 1_800):
            raise ValueError("Acquired job time limits are invalid")

    @property
    def lease_proof(self) -> JobLeaseProof:
        """Return the minimal CAS coordinates for subsequent worker calls."""

        return JobLeaseProof(
            job_id=self.job_id,
            owner=self.lease.owner,
            lease_token=self.lease.lease_token,
            fencing_token=self.lease.fencing_token,
        )


@dataclass(frozen=True, slots=True)
class HeartbeatJobCommand:
    """Request a bounded lease extension using current fencing proof."""

    proof: JobLeaseProof


@dataclass(frozen=True, slots=True)
class CheckpointJobCommand:
    """Advance a visible execution stage while refreshing the same lease."""

    proof: JobLeaseProof
    stage_name: str
    stage_sequence: int

    def __post_init__(self) -> None:
        if not _IDENTIFIER_PATTERN.fullmatch(self.stage_name):
            raise ValueError("Job stage name is invalid")
        if self.stage_sequence < 1:
            raise ValueError("Job stage sequence must be positive")


@dataclass(frozen=True, slots=True)
class FinishJobCommand:
    """Commit exactly one terminal result under a live lease."""

    proof: JobLeaseProof
    outcome: JobStatus
    result: Mapping[str, object] | None = field(default=None, repr=False)
    error_code: str | None = None

    def __post_init__(self) -> None:
        if self.outcome not in TERMINAL_JOB_STATUSES:
            raise ValueError("Job finish outcome must be terminal")
        if self.result is not None and self.outcome is not JobStatus.SUCCEEDED:
            raise ValueError("Only successful jobs may persist a result")
        if self.error_code is not None and not _IDENTIFIER_PATTERN.fullmatch(self.error_code):
            raise ValueError("Job error code is invalid")
        if self.outcome is JobStatus.SUCCEEDED and self.error_code is not None:
            raise ValueError("Successful jobs cannot persist an error code")
        if self.result is not None:
            object.__setattr__(
                self,
                "result",
                snapshot_json_mapping(
                    self.result,
                    error_message="Job result must be canonical JSON data",
                ),
            )


@dataclass(frozen=True, slots=True)
class RetryJobCommand:
    """Settle a retryable failure under the current live lease and fence."""

    proof: JobLeaseProof
    error_code: JobExecutionErrorCode
    retry_delay_seconds: int

    def __post_init__(self) -> None:
        if not 1 <= self.retry_delay_seconds <= JOB_RETRY_MAX_SECONDS:
            raise ValueError("Job retry delay is invalid")


@dataclass(frozen=True, slots=True)
class JobRetryRecord:
    """Committed retry settlement, with an outbox only for another attempt."""

    disposition: JobRetryDisposition
    dispatch_generation: int
    outbox_event_id: UUID | None

    def __post_init__(self) -> None:
        if self.dispatch_generation < 1:
            raise ValueError("Job retry dispatch generation is invalid")
        scheduled = self.disposition is JobRetryDisposition.RETRY_SCHEDULED
        if scheduled != (self.outbox_event_id is not None):
            raise ValueError("Scheduled job retries require exactly one outbox")
        if self.outbox_event_id is not None and self.outbox_event_id.int == 0:
            raise ValueError("Job retry outbox ID is invalid")


@dataclass(frozen=True, slots=True)
class ReconcileJobsCommand:
    """Bound one database-clock scan for jobs whose delivery stopped progressing."""

    unstarted_timeout_seconds: int
    batch_size: int

    def __post_init__(self) -> None:
        if not 1 <= self.unstarted_timeout_seconds <= 86_400:
            raise ValueError("Job unstarted timeout is invalid")
        if not 1 <= self.batch_size <= 1_000:
            raise ValueError("Job reconciliation batch size is invalid")


@dataclass(frozen=True, slots=True)
class JobReconciliationResult:
    """Payload-free outcome of one committed reconciliation transaction."""

    selected: int
    unstarted: int
    expired_leases: int
    retry_scheduled: int
    cancelled: int
    dead_lettered: int

    def __post_init__(self) -> None:
        counts = (
            self.selected,
            self.unstarted,
            self.expired_leases,
            self.retry_scheduled,
            self.cancelled,
            self.dead_lettered,
        )
        if any(count < 0 for count in counts):
            raise ValueError("Job reconciliation counts cannot be negative")
        if self.selected != self.unstarted + self.expired_leases:
            raise ValueError("Job reconciliation candidate counts are inconsistent")
        if self.selected != (self.retry_scheduled + self.cancelled + self.dead_lettered):
            raise ValueError("Job reconciliation outcome counts are inconsistent")


def job_retry_delay_seconds(job_id: UUID, attempt_count: int) -> int:
    """Return a capped deterministic backoff for the next execution attempt."""

    if job_id.int == 0 or attempt_count < 1:
        raise ValueError("Job retry coordinates are invalid")

    exponent = min(attempt_count - 1, 30)
    exponential_delay = min(
        JOB_RETRY_MAX_SECONDS,
        JOB_RETRY_BASE_SECONDS * (1 << exponent),
    )
    remaining_capacity = JOB_RETRY_MAX_SECONDS - exponential_delay
    if remaining_capacity == 0:
        return JOB_RETRY_MAX_SECONDS

    jitter_bound = min(max(1, exponential_delay // 4), remaining_capacity)
    digest = hashlib.sha256(
        _JOB_RETRY_JITTER_DOMAIN
        + job_id.bytes
        + attempt_count.to_bytes(8, byteorder="big", signed=False)
    ).digest()
    jitter = int.from_bytes(digest[:4], byteorder="big") % (jitter_bound + 1)
    return exponential_delay + jitter


class JobIdempotencyConflictError(RuntimeError):
    """Raised when one key is reused for a semantically different request."""


class ScheduleNotFoundError(RuntimeError):
    """Raised when a trusted manual trigger names no durable Schedule."""


class ScheduleDefinitionConflictError(RuntimeError):
    """Raised when a stable scope/name is reused for a different definition."""


class ScheduleTriggerConflictError(RuntimeError):
    """Raised when one manual trigger ID is reused for another Schedule."""


class JobNotAcquirableError(RuntimeError):
    """Raised when a delivery is stale, premature, cancelled, or already owned."""


class LostJobLeaseError(RuntimeError):
    """Raised whenever worker CAS proof no longer owns a live lease."""


class JobPersistenceError(RuntimeError):
    """Carry only a safe SQLSTATE beyond the PostgreSQL adapter."""

    def __init__(self, *, sqlstate: str | None = None) -> None:
        super().__init__("Job persistence failed")
        self.sqlstate = sqlstate


class OutboxPublishError(RuntimeError):
    """Expose only a stable error code when broker publication fails."""

    def __init__(self, error_code: OutboxPublishErrorCode) -> None:
        super().__init__("Outbox publication failed")
        self.error_code = error_code


class OutboxPersistenceError(RuntimeError):
    """Carry only a safe SQLSTATE beyond the outbox PostgreSQL adapter."""

    def __init__(self, *, sqlstate: str | None = None) -> None:
        super().__init__("Outbox persistence failed")
        self.sqlstate = sqlstate
