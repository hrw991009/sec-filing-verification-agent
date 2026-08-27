"""Fenced execution runtime and the fixed production job-handler registry."""

import asyncio
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from time import monotonic
from types import MappingProxyType
from typing import Protocol
from uuid import UUID, uuid4

import httpx2

from industry_platform.adapters.public_egress import create_public_egress_http_client
from industry_platform.core.config import Settings
from industry_platform.core.database import (
    AsyncSessionFactory,
    create_database_engine,
    create_database_session_factory,
)
from industry_platform.modules.agent_runtime.execution import (
    DirectAnswerRunExecutionUseCase,
    RecoverableAgentRunInterruption,
)
from industry_platform.modules.agent_runtime.resources import (
    create_direct_answer_runtime_resources,
)
from industry_platform.modules.conversations.domain import (
    DIRECT_ANSWER_TASK_NAME,
    TurnSearchMode,
)
from industry_platform.modules.disclosures.resources import create_sec_filing_read_tools
from industry_platform.modules.files.resources import create_private_file_object_store
from industry_platform.modules.identity.adapters.refresh_cleanup import (
    SqlAlchemyRefreshRecoveryCleanupTransactionFactory,
)
from industry_platform.modules.identity.domain import (
    RefreshRecoveryCleanupCommand,
    RefreshRecoveryCleanupUnavailableError,
)
from industry_platform.modules.identity.ports import RefreshRecoveryCleanupUseCase
from industry_platform.modules.identity.service import RefreshRecoveryCleanupService
from industry_platform.modules.industry.domain import (
    INDUSTRY_COLLECTION_TASK_NAME,
    IndustryNotFoundError,
    IndustryPersistenceError,
    IndustryProviderError,
    ProviderErrorCode,
)
from industry_platform.modules.industry.ports import IndustryCollectionUseCase
from industry_platform.modules.industry.resources import create_industry_resources
from industry_platform.modules.ingestion.deletion import KnowledgeDeletionService
from industry_platform.modules.ingestion.domain import (
    DocumentParserError,
    IngestionCancelledError,
    IngestionConflictError,
    IngestionDependencyError,
    IngestionNotFoundError,
    IngestionPersistenceError,
)
from industry_platform.modules.ingestion.resources import create_ingestion_resources
from industry_platform.modules.ingestion.service import KnowledgeIngestionService
from industry_platform.modules.jobs.domain import (
    AcquiredJob,
    AcquireJobCommand,
    FinishJobCommand,
    HeartbeatJobCommand,
    JobDispatchMessage,
    JobExecutionErrorCode,
    JobNotAcquirableError,
    JobPersistenceError,
    JobRetryDisposition,
    JobStatus,
    LostJobLeaseError,
    RetryJobCommand,
    job_retry_delay_seconds,
)
from industry_platform.modules.jobs.ports import JobApplicationUseCase
from industry_platform.modules.jobs.resources import create_job_resources
from industry_platform.modules.knowledge.domain import (
    KNOWLEDGE_DELETION_TASK_NAME,
    KNOWLEDGE_INGESTION_TASK_NAME,
)
from industry_platform.modules.research.domain import RESEARCH_TASK_NAME
from industry_platform.modules.retrieval.resources import create_retrieval_resources

IDENTITY_REFRESH_RECOVERY_CLEANUP_HANDLER = "identity.refresh_recovery.cleanup.v1"
logger = logging.getLogger(__name__)


class JobExecutionDisposition(StrEnum):
    """Payload-free result returned to the Celery acknowledgement boundary."""

    NO_OP = "no_op"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRY_SCHEDULED = "retry_scheduled"
    DEAD_LETTER = "dead_letter"
    LEASE_LOST = "lease_lost"
    CANCELLED = "cancelled"


class JobHandler(Protocol):
    """One statically registered business handler."""

    async def execute(
        self,
        job: AcquiredJob,
    ) -> Mapping[str, object]: ...


class UnknownJobHandlerError(RuntimeError):
    """A persisted task name has no production handler in this release."""


class InvalidJobPayloadError(RuntimeError):
    """A persisted payload does not match its handler's bounded schema."""


class RetryableJobHandlerError(RuntimeError):
    """Carry only a stable retry code across the handler boundary."""

    def __init__(self, error_code: JobExecutionErrorCode) -> None:
        super().__init__("Job handler infrastructure is unavailable")
        self.error_code = error_code


class PermanentJobHandlerError(RuntimeError):
    """Carry one stable non-retryable business failure to Job settlement."""

    def __init__(self, error_code: JobExecutionErrorCode) -> None:
        super().__init__("Job handler request failed")
        self.error_code = error_code


class CancelledJobHandlerError(RuntimeError):
    """Carry one cooperative cancellation to the Job terminal boundary."""

    def __init__(self, error_code: JobExecutionErrorCode) -> None:
        super().__init__("Job handler was cancelled")
        self.error_code = error_code


@dataclass(frozen=True, slots=True)
class IdentityRefreshRecoveryCleanupHandler:
    """Parse one bounded cleanup batch and invoke the existing identity use case."""

    cleanup_use_case: RefreshRecoveryCleanupUseCase = field(repr=False)

    async def execute(
        self,
        job: AcquiredJob,
    ) -> Mapping[str, object]:
        payload = job.payload
        if set(payload) != {"batch_size"}:
            raise InvalidJobPayloadError

        batch_size = payload["batch_size"]
        if isinstance(batch_size, bool) or not isinstance(batch_size, int):
            raise InvalidJobPayloadError

        try:
            command = RefreshRecoveryCleanupCommand(
                batch_size=batch_size,
            )
        except (KeyError, TypeError, ValueError):
            raise InvalidJobPayloadError from None

        try:
            result = await self.cleanup_use_case.cleanup_expired(command)
        except RefreshRecoveryCleanupUnavailableError:
            raise RetryableJobHandlerError(JobExecutionErrorCode.CLEANUP_UNAVAILABLE) from None

        return {
            "scanned_count": result.scanned_count,
            "cleared_count": result.cleared_count,
        }


@dataclass(frozen=True, slots=True)
class DirectAnswerJobHandler:
    """Parse one bounded Run reference and delegate all execution to Agent Runtime."""

    execution_use_case: DirectAnswerRunExecutionUseCase = field(repr=False)

    async def execute(self, job: AcquiredJob) -> Mapping[str, object]:
        payload = job.payload
        if set(payload) != {"schema_version", "agent_run_id"}:
            raise InvalidJobPayloadError
        schema_version = payload["schema_version"]
        raw_run_id = payload["agent_run_id"]
        if (
            schema_version != 1
            or isinstance(schema_version, bool)
            or not isinstance(raw_run_id, str)
        ):
            raise InvalidJobPayloadError
        try:
            run_id = UUID(raw_run_id)
        except ValueError:
            raise InvalidJobPayloadError from None
        if run_id.int == 0:
            raise InvalidJobPayloadError

        try:
            result = await self.execution_use_case.execute_run(run_id)
        except RecoverableAgentRunInterruption:
            raise RetryableJobHandlerError(JobExecutionErrorCode.AGENT_RECOVERY_RETRYABLE) from None
        return {
            "agent_run_id": str(result.run_id),
            "run_status": result.status.value,
            "stop_reason": (None if result.stop_reason is None else result.stop_reason.value),
            "terminal_event_sequence": result.terminal_event_sequence,
        }


@dataclass(frozen=True, slots=True)
class IndustryCollectionJobHandler:
    """Resolve the atomic Collection Run projection and invoke its Application Service."""

    collection_use_case: IndustryCollectionUseCase = field(repr=False)

    async def execute(self, job: AcquiredJob) -> Mapping[str, object]:
        if job.scope.workspace_id is None or job.scope.system_scope_key is not None:
            raise InvalidJobPayloadError
        if set(job.payload) != {"schema_version", "industry_id", "source_kind", "query"}:
            raise InvalidJobPayloadError
        try:
            result = await self.collection_use_case.collect_job(
                job_id=job.job_id,
                workspace_id=job.scope.workspace_id,
                trace_id=job.trace_id,
            )
        except IndustryProviderError as error:
            if error.code is ProviderErrorCode.NOT_CONFIGURED:
                raise PermanentJobHandlerError(
                    JobExecutionErrorCode.COLLECTION_PROVIDER_NOT_CONFIGURED
                ) from None
            if error.retryable:
                raise RetryableJobHandlerError(
                    JobExecutionErrorCode.COLLECTION_PROVIDER_RETRYABLE
                ) from None
            raise PermanentJobHandlerError(
                JobExecutionErrorCode.COLLECTION_PROVIDER_FAILED
            ) from None
        except IndustryNotFoundError:
            raise InvalidJobPayloadError from None
        except IndustryPersistenceError:
            raise RetryableJobHandlerError(JobExecutionErrorCode.COLLECTION_UNAVAILABLE) from None
        return {
            "collection_run_id": str(result.collection_run_id),
            "provider": result.provider.value,
            "fetched_count": result.fetched_count,
            "inserted_count": result.inserted_count,
            "duplicate_count": result.duplicate_count,
        }


@dataclass(frozen=True, slots=True)
class KnowledgeIngestionJobHandler:
    ingestion: KnowledgeIngestionService = field(repr=False)

    async def execute(self, job: AcquiredJob) -> Mapping[str, object]:
        try:
            result = await self.ingestion.execute(job)
        except ValueError:
            raise InvalidJobPayloadError from None
        except IngestionCancelledError:
            raise CancelledJobHandlerError(JobExecutionErrorCode.INGESTION_CANCELLED) from None
        except DocumentParserError as error:
            code = (
                JobExecutionErrorCode.INGESTION_PARSER_RETRYABLE
                if error.retryable
                else JobExecutionErrorCode.INGESTION_PARSER_FAILED
            )
            if error.retryable:
                raise RetryableJobHandlerError(code) from None
            raise PermanentJobHandlerError(code) from None
        except IngestionDependencyError as error:
            logger.warning(
                "knowledge_ingestion_dependency_failed job_id=%s code=%s",
                job.job_id,
                error.code,
            )
            raise RetryableJobHandlerError(
                JobExecutionErrorCode.INGESTION_DEPENDENCY_RETRYABLE
            ) from None
        except IngestionPersistenceError as error:
            logger.error(
                "knowledge_ingestion_persistence_failed job_id=%s sqlstate=%s constraint=%s",
                job.job_id,
                error.sqlstate or "unknown",
                error.constraint_name or "unknown",
            )
            raise RetryableJobHandlerError(JobExecutionErrorCode.INGESTION_UNAVAILABLE) from None
        except (IngestionConflictError, IngestionNotFoundError):
            raise PermanentJobHandlerError(JobExecutionErrorCode.INGESTION_STATE_INVALID) from None
        return {
            "asset_count": result.asset_count,
            "chunk_count": result.chunk_count,
            "document_version_id": str(result.document_version_id),
            "page_count": result.page_count,
            "status": result.status,
        }


@dataclass(frozen=True, slots=True)
class KnowledgeDeletionJobHandler:
    deletion: KnowledgeDeletionService = field(repr=False)

    async def execute(self, job: AcquiredJob) -> Mapping[str, object]:
        try:
            document_id = await self.deletion.execute(job)
        except ValueError:
            raise InvalidJobPayloadError from None
        except IngestionDependencyError:
            raise RetryableJobHandlerError(
                JobExecutionErrorCode.INGESTION_DEPENDENCY_RETRYABLE
            ) from None
        except IngestionPersistenceError as error:
            logger.error(
                "knowledge_deletion_persistence_failed job_id=%s sqlstate=%s constraint=%s",
                job.job_id,
                error.sqlstate or "unknown",
                error.constraint_name or "unknown",
            )
            raise RetryableJobHandlerError(JobExecutionErrorCode.INGESTION_UNAVAILABLE) from None
        except (IngestionConflictError, IngestionNotFoundError):
            raise PermanentJobHandlerError(JobExecutionErrorCode.INGESTION_STATE_INVALID) from None
        return {"document_id": str(document_id), "status": "deleted"}


@dataclass(frozen=True, slots=True)
class FixedJobHandlerRegistry:
    """Immutable allowlist; persisted names never trigger dynamic imports."""

    _handlers: Mapping[str, JobHandler] = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_handlers", MappingProxyType(dict(self._handlers)))

    @classmethod
    def production(
        cls,
        cleanup_use_case: RefreshRecoveryCleanupUseCase,
        direct_answer_use_case: DirectAnswerRunExecutionUseCase | None = None,
        collection_use_case: IndustryCollectionUseCase | None = None,
        ingestion_use_case: KnowledgeIngestionService | None = None,
        deletion_use_case: KnowledgeDeletionService | None = None,
    ) -> "FixedJobHandlerRegistry":
        handlers: dict[str, JobHandler] = {
            IDENTITY_REFRESH_RECOVERY_CLEANUP_HANDLER: (
                IdentityRefreshRecoveryCleanupHandler(cleanup_use_case)
            )
        }
        if direct_answer_use_case is not None:
            handlers[DIRECT_ANSWER_TASK_NAME] = DirectAnswerJobHandler(direct_answer_use_case)
            handlers[RESEARCH_TASK_NAME] = DirectAnswerJobHandler(direct_answer_use_case)
        if collection_use_case is not None:
            handlers[INDUSTRY_COLLECTION_TASK_NAME] = IndustryCollectionJobHandler(
                collection_use_case
            )
        if ingestion_use_case is not None:
            handlers[KNOWLEDGE_INGESTION_TASK_NAME] = KnowledgeIngestionJobHandler(
                ingestion_use_case
            )
        if deletion_use_case is not None:
            handlers[KNOWLEDGE_DELETION_TASK_NAME] = KnowledgeDeletionJobHandler(deletion_use_case)
        return cls(handlers)

    def resolve(self, task_name: str) -> JobHandler:
        handler = self._handlers.get(task_name)
        if handler is None:
            raise UnknownJobHandlerError
        return handler


@dataclass(frozen=True, slots=True)
class JobExecutionRuntime:
    """Acquire from PostgreSQL, heartbeat independently, then settle by fence."""

    jobs: JobApplicationUseCase = field(repr=False)
    handlers: FixedJobHandlerRegistry = field(repr=False)
    worker_id: str
    heartbeat_seconds: float

    def __post_init__(self) -> None:
        if self.heartbeat_seconds <= 0:
            raise ValueError("Job heartbeat interval must be positive")

    async def execute(
        self,
        delivery: JobDispatchMessage,
    ) -> JobExecutionDisposition:
        started_at = monotonic()
        try:
            disposition = await self._execute_delivery(delivery)
        except Exception:
            logger.exception(
                "job_execution_unsettled job_id=%s trace_id=%s status=error duration_ms=%d",
                delivery.job_id,
                delivery.trace_id,
                max(0, int((monotonic() - started_at) * 1_000)),
            )
            raise
        logger.info(
            "job_execution_terminal job_id=%s trace_id=%s status=%s duration_ms=%d",
            delivery.job_id,
            delivery.trace_id,
            disposition.value,
            max(0, int((monotonic() - started_at) * 1_000)),
        )
        return disposition

    async def _execute_delivery(
        self,
        delivery: JobDispatchMessage,
    ) -> JobExecutionDisposition:
        try:
            acquired = await self.jobs.acquire(
                AcquireJobCommand(
                    job_id=delivery.job_id,
                    dispatch_generation=delivery.dispatch_generation,
                    worker_id=self.worker_id,
                    outbox_id=delivery.outbox_id,
                    trace_id=delivery.trace_id,
                )
            )
        except JobNotAcquirableError:
            return JobExecutionDisposition.NO_OP

        try:
            async with asyncio.timeout(acquired.soft_time_limit_seconds):
                result = await self._execute_with_heartbeat(acquired)
        except LostJobLeaseError:
            return JobExecutionDisposition.LEASE_LOST
        except TimeoutError:
            return await self._retry(
                acquired,
                JobExecutionErrorCode.SOFT_TIME_LIMIT_EXCEEDED,
            )
        except RetryableJobHandlerError as error:
            return await self._retry(acquired, error.error_code)
        except PermanentJobHandlerError as error:
            return await self._fail(acquired, error.error_code)
        except CancelledJobHandlerError as error:
            return await self._cancel(acquired, error.error_code)
        except UnknownJobHandlerError:
            return await self._fail(
                acquired,
                JobExecutionErrorCode.UNKNOWN_HANDLER,
            )
        except InvalidJobPayloadError:
            return await self._fail(
                acquired,
                JobExecutionErrorCode.INVALID_PAYLOAD,
            )
        except JobPersistenceError:
            # The lease state is unknown; a later reconciler must decide recovery.
            raise
        except Exception:
            return await self._fail(
                acquired,
                JobExecutionErrorCode.HANDLER_FAILED,
            )

        try:
            await self.jobs.finish(
                FinishJobCommand(
                    proof=acquired.lease_proof,
                    outcome=JobStatus.SUCCEEDED,
                    result=result,
                )
            )
        except LostJobLeaseError:
            return JobExecutionDisposition.LEASE_LOST
        return JobExecutionDisposition.SUCCEEDED

    async def _execute_with_heartbeat(
        self,
        acquired: AcquiredJob,
    ) -> Mapping[str, object]:
        stop = asyncio.Event()
        handler_task = asyncio.create_task(self._invoke_handler(acquired))
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(acquired, stop))

        try:
            done, _ = await asyncio.wait(
                (handler_task, heartbeat_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if heartbeat_task in done:
                await heartbeat_task
                raise LostJobLeaseError

            result = await handler_task
            stop.set()
            await heartbeat_task
            return result
        finally:
            stop.set()
            for task in (handler_task, heartbeat_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(handler_task, heartbeat_task, return_exceptions=True)

    async def _invoke_handler(self, acquired: AcquiredJob) -> Mapping[str, object]:
        handler = self.handlers.resolve(acquired.task_name)
        return await handler.execute(acquired)

    async def _heartbeat_loop(
        self,
        acquired: AcquiredJob,
        stop: asyncio.Event,
    ) -> None:
        while True:
            try:
                await asyncio.wait_for(
                    stop.wait(),
                    timeout=self.heartbeat_seconds,
                )
            except TimeoutError:
                await self.jobs.heartbeat(HeartbeatJobCommand(proof=acquired.lease_proof))
            else:
                return

    async def _fail(
        self,
        acquired: AcquiredJob,
        error_code: JobExecutionErrorCode,
    ) -> JobExecutionDisposition:
        try:
            await self.jobs.finish(
                FinishJobCommand(
                    proof=acquired.lease_proof,
                    outcome=JobStatus.FAILED,
                    error_code=error_code.value,
                )
            )
        except LostJobLeaseError:
            return JobExecutionDisposition.LEASE_LOST
        return JobExecutionDisposition.FAILED

    async def _retry(
        self,
        acquired: AcquiredJob,
        error_code: JobExecutionErrorCode,
    ) -> JobExecutionDisposition:
        try:
            record = await self.jobs.retry(
                RetryJobCommand(
                    proof=acquired.lease_proof,
                    error_code=error_code,
                    retry_delay_seconds=job_retry_delay_seconds(
                        acquired.job_id,
                        acquired.attempt_count,
                    ),
                )
            )
        except LostJobLeaseError:
            return JobExecutionDisposition.LEASE_LOST

        if record.disposition is JobRetryDisposition.RETRY_SCHEDULED:
            return JobExecutionDisposition.RETRY_SCHEDULED
        return JobExecutionDisposition.DEAD_LETTER

    async def _cancel(
        self,
        acquired: AcquiredJob,
        error_code: JobExecutionErrorCode,
    ) -> JobExecutionDisposition:
        try:
            await self.jobs.finish(
                FinishJobCommand(
                    proof=acquired.lease_proof,
                    outcome=JobStatus.CANCELLED,
                    error_code=error_code.value,
                )
            )
        except LostJobLeaseError:
            return JobExecutionDisposition.LEASE_LOST
        return JobExecutionDisposition.CANCELLED


async def run_job_delivery(
    delivery: JobDispatchMessage,
    *,
    settings: Settings,
    provider_http_client_factory: Callable[[], httpx2.AsyncClient] = (
        create_public_egress_http_client
    ),
) -> JobExecutionDisposition:
    """Compose fresh task resources; every operation borrows its own session."""

    engine = create_database_engine(settings)
    try:
        session_factory = create_database_session_factory(engine)
        async with (
            provider_http_client_factory() as provider_http_client,
            httpx2.AsyncClient(trust_env=False) as internal_http_client,
        ):
            runtime = create_job_delivery_runtime(
                settings,
                session_factory,
                provider_http_client,
                internal_http_client,
            )
            return await runtime.execute(delivery)
    finally:
        await engine.dispose()


def create_job_delivery_runtime(
    settings: Settings,
    session_factory: AsyncSessionFactory,
    provider_http_client: httpx2.AsyncClient,
    internal_http_client: httpx2.AsyncClient | None = None,
) -> JobExecutionRuntime:
    """Compose every fixed production handler, including the unified Agent Runtime."""

    cleanup_service = RefreshRecoveryCleanupService(
        transaction_factory=SqlAlchemyRefreshRecoveryCleanupTransactionFactory(session_factory)
    )
    job_resources = create_job_resources(settings, session_factory)
    industry = create_industry_resources(
        settings,
        session_factory,
        provider_http_client,
        job_resources.schedule_service,
    )
    tool_http_client = internal_http_client or provider_http_client
    retrieval = create_retrieval_resources(
        settings,
        session_factory,
        tool_http_client,
    )
    search_filing_tool, read_filing_section_tool = create_sec_filing_read_tools(
        settings,
        session_factory,
        tool_http_client,
    )
    direct_answer = create_direct_answer_runtime_resources(
        settings,
        session_factory,
        provider_http_client,
        tool_adapters=(
            industry.web_search_tool,
            retrieval.knowledge_search_tool,
            retrieval.finance_calculate_tool,
            search_filing_tool,
            read_filing_section_tool,
        ),
        tool_surfaces={
            TurnSearchMode.WEB: (industry.web_search_tool.definition.reference,),
            TurnSearchMode.LOCAL: (
                retrieval.knowledge_search_tool.definition.reference,
                retrieval.finance_calculate_tool.definition.reference,
                search_filing_tool.definition.reference,
                read_filing_section_tool.definition.reference,
            ),
        },
        fixture_catalog=retrieval.catalog,
    )
    ingestion = create_ingestion_resources(
        settings,
        session_factory,
        job_resources.application_service,
        create_private_file_object_store(settings),
        tool_http_client,
    )
    return JobExecutionRuntime(
        jobs=job_resources.application_service,
        handlers=FixedJobHandlerRegistry.production(
            cleanup_service,
            direct_answer.execution_service,
            industry.collection_service,
            ingestion.service,
            ingestion.deletion_service,
        ),
        worker_id=f"celery-{uuid4().hex}",
        heartbeat_seconds=settings.job_heartbeat_seconds,
    )
