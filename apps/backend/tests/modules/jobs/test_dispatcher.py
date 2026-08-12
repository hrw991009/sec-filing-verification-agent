"""Unit contracts for fixed-shape Celery publication and outbox orchestration."""

from contextlib import AbstractAsyncContextManager
from types import TracebackType
from typing import cast
from uuid import UUID

import pytest
from celery import Celery

from industry_platform.core.config import Settings
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.jobs.adapters.celery import (
    CeleryJobDispatchPublisher,
)
from industry_platform.modules.jobs.domain import (
    CELERY_JOB_DISPATCH_TASK_NAME,
    OUTBOX_RETRY_MAX_SECONDS,
    ClaimedJobDispatch,
    ClaimOutboxCommand,
    JobDispatchMessage,
    OutboxClaimProof,
    OutboxFailureDisposition,
    OutboxPublishError,
    OutboxPublishErrorCode,
    outbox_retry_delay_seconds,
)
from industry_platform.modules.jobs.ports import OutboxWriter
from industry_platform.workers.celery_app import create_celery_app
from industry_platform.workers.dispatcher import OutboxDispatcher

JOB_ID = UUID("11111111-1111-4111-8111-111111111111")
OUTBOX_ID = UUID("22222222-2222-4222-8222-222222222222")
CLAIM_TOKEN = UUID("33333333-3333-4333-8333-333333333333")


def claimed_dispatch(*, attempt_count: int = 1, max_attempts: int = 3) -> ClaimedJobDispatch:
    return ClaimedJobDispatch(
        proof=OutboxClaimProof(
            outbox_id=OUTBOX_ID,
            locked_by="dispatcher-one",
            claim_token=CLAIM_TOKEN,
            claim_generation=1,
        ),
        message=JobDispatchMessage(
            job_id=JOB_ID,
            dispatch_generation=7,
            outbox_id=OUTBOX_ID,
            trace_id=TraceId("dispatch-trace"),
        ),
        queue_name="research",
        attempt_count=attempt_count,
        max_attempts=max_attempts,
        soft_time_limit_seconds=120,
        hard_time_limit_seconds=180,
    )


class RecordingOutboxWriter:
    """Record exact claim and settlement operations crossing the port."""

    def __init__(
        self,
        events: list[str],
        *,
        dispatch: ClaimedJobDispatch,
        failure_disposition: OutboxFailureDisposition,
    ) -> None:
        self.events = events
        self.dispatch = dispatch
        self.failure_disposition = failure_disposition
        self.published_proofs: list[OutboxClaimProof] = []
        self.failed: list[tuple[OutboxClaimProof, OutboxPublishErrorCode, int]] = []

    async def claim_job_dispatches(
        self,
        command: ClaimOutboxCommand,
    ) -> tuple[ClaimedJobDispatch, ...]:
        self.events.append(f"claim:{command.dispatcher_id}")
        return (self.dispatch,)

    async def mark_published(self, proof: OutboxClaimProof) -> bool:
        self.events.append("mark_published")
        self.published_proofs.append(proof)
        return True

    async def mark_failed(
        self,
        proof: OutboxClaimProof,
        *,
        error_code: OutboxPublishErrorCode,
        retry_delay_seconds: int,
    ) -> OutboxFailureDisposition:
        self.events.append("mark_failed")
        self.failed.append((proof, error_code, retry_delay_seconds))
        return self.failure_disposition


class RecordingTransaction(AbstractAsyncContextManager[OutboxWriter]):
    def __init__(self, writer: RecordingOutboxWriter, events: list[str]) -> None:
        self.writer = writer
        self.events = events

    async def __aenter__(self) -> OutboxWriter:
        self.events.append("transaction.enter")
        return self.writer

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        self.events.append("transaction.exit")


class RecordingTransactionFactory:
    def __init__(self, writer: RecordingOutboxWriter, events: list[str]) -> None:
        self.writer = writer
        self.events = events

    def __call__(self) -> RecordingTransaction:
        self.events.append("transaction.create")
        return RecordingTransaction(self.writer, self.events)


class RecordingPublisher:
    def __init__(self, events: list[str], *, fail: bool = False) -> None:
        self.events = events
        self.fail = fail

    async def publish(self, dispatch: ClaimedJobDispatch) -> None:
        self.events.append(f"publish:{dispatch.message.outbox_id}")
        if self.fail:
            raise OutboxPublishError(OutboxPublishErrorCode.CELERY_PUBLISH_FAILED)


class RecordingCeleryApp:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, dict[str, object]]] = []

    def send_task(self, task_name: str, **options: object) -> None:
        if self.fail:
            raise RuntimeError("sensitive broker detail")
        self.calls.append((task_name, options))


@pytest.mark.asyncio
async def test_dispatcher_commits_claim_before_publish_and_settles_afterward() -> None:
    events: list[str] = []
    dispatch = claimed_dispatch()
    writer = RecordingOutboxWriter(
        events,
        dispatch=dispatch,
        failure_disposition=OutboxFailureDisposition.RETRY_SCHEDULED,
    )
    dispatcher = OutboxDispatcher(
        transaction_factory=RecordingTransactionFactory(writer, events),
        publisher=RecordingPublisher(events),
        dispatcher_id="dispatcher-one",
        batch_size=10,
        claim_seconds=60,
    )

    result = await dispatcher.dispatch_once()

    assert result.published == 1
    assert result.claimed == 1
    assert writer.published_proofs == [dispatch.proof]
    assert events == [
        "transaction.create",
        "transaction.enter",
        "claim:dispatcher-one",
        "transaction.exit",
        f"publish:{OUTBOX_ID}",
        "transaction.create",
        "transaction.enter",
        "mark_published",
        "transaction.exit",
    ]


@pytest.mark.asyncio
async def test_dispatcher_releases_failed_publish_with_stable_bounded_backoff() -> None:
    events: list[str] = []
    dispatch = claimed_dispatch(attempt_count=2)
    writer = RecordingOutboxWriter(
        events,
        dispatch=dispatch,
        failure_disposition=OutboxFailureDisposition.RETRY_SCHEDULED,
    )
    dispatcher = OutboxDispatcher(
        transaction_factory=RecordingTransactionFactory(writer, events),
        publisher=RecordingPublisher(events, fail=True),
        dispatcher_id="dispatcher-one",
        batch_size=10,
        claim_seconds=60,
    )

    result = await dispatcher.dispatch_once()

    expected_delay = outbox_retry_delay_seconds(OUTBOX_ID, 2)
    assert result.retry_scheduled == 1
    assert writer.failed == [
        (
            dispatch.proof,
            OutboxPublishErrorCode.CELERY_PUBLISH_FAILED,
            expected_delay,
        )
    ]
    assert expected_delay == outbox_retry_delay_seconds(OUTBOX_ID, 2)
    assert expected_delay <= OUTBOX_RETRY_MAX_SECONDS


@pytest.mark.asyncio
async def test_celery_publisher_uses_fixed_task_and_minimal_message() -> None:
    app = RecordingCeleryApp()
    dispatch = claimed_dispatch()
    publisher = CeleryJobDispatchPublisher(cast(Celery, app))

    await publisher.publish(dispatch)

    assert app.calls == [
        (
            CELERY_JOB_DISPATCH_TASK_NAME,
            {
                "args": (),
                "kwargs": {
                    "job_id": str(JOB_ID),
                    "dispatch_generation": 7,
                    "outbox_id": str(OUTBOX_ID),
                    "trace_id": "dispatch-trace",
                },
                "task_id": str(OUTBOX_ID),
                "queue": "research",
                "routing_key": "research",
                "serializer": "json",
                "ignore_result": True,
                "retry": False,
                "time_limit": 180,
            },
        )
    ]
    published_kwargs = cast(dict[str, object], app.calls[0][1]["kwargs"])
    assert "task_name" not in published_kwargs
    assert "payload" not in published_kwargs


@pytest.mark.asyncio
async def test_celery_publisher_hides_broker_exception_details() -> None:
    publisher = CeleryJobDispatchPublisher(cast(Celery, RecordingCeleryApp(fail=True)))

    with pytest.raises(OutboxPublishError) as exc_info:
        await publisher.publish(claimed_dispatch())

    assert exc_info.value.error_code is OutboxPublishErrorCode.CELERY_PUBLISH_FAILED
    assert "sensitive" not in str(exc_info.value)


def test_celery_app_is_json_only_late_ack_broker_only(
    test_settings: Settings,
) -> None:
    app = create_celery_app(test_settings)
    try:
        assert app.conf.accept_content == ["json"]
        assert app.conf.task_serializer == "json"
        assert app.conf.task_ignore_result is True
        assert app.conf.result_backend is None
        assert app.conf.task_acks_late is True
        assert app.conf.task_reject_on_worker_lost is True
        assert app.conf.worker_cancel_long_running_tasks_on_connection_loss is True
        assert (
            app.conf.worker_prefetch_multiplier == test_settings.celery_worker_prefetch_multiplier
        )
        assert app.conf.enable_utc is True
        assert app.conf.timezone == "UTC"
        assert app.conf.task_default_queue == test_settings.job_default_queue
        assert app.conf.task_soft_time_limit is None
        assert app.conf.task_time_limit == 1_800
        assert app.conf.broker_transport_options == {"visibility_timeout": 3_600}
        assert app.conf.task_routes[CELERY_JOB_DISPATCH_TASK_NAME] == {
            "queue": test_settings.job_default_queue,
            "routing_key": test_settings.job_default_queue,
        }
    finally:
        app.close()
