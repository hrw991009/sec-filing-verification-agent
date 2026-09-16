"""Controlled notification provider: UNKNOWN, durable receipt lookup, same-key retry."""

import asyncio
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from uuid import UUID

import pytest
from modules.tools.test_registry import (
    NOW,
    RAW_IDEMPOTENCY_KEY,
    RecordingAdapter,
    prepare_call,
    runtime_context,
    write_definition,
)
from sqlalchemy import text

from industry_platform.core.database import (
    AsyncSessionFactory,
    create_database_engine,
    create_database_session_factory,
)
from industry_platform.modules.agent_runtime.context import TrustedRuntimeContext
from industry_platform.modules.tools.domain import (
    ToolObservation,
    canonical_mapping_sha256,
    side_effect_idempotency_key_sha256,
)
from industry_platform.modules.tools.recovery import (
    DeliveryIdentity,
    DeliveryLookup,
    DeliveryLookupStatus,
    DeliveryReceipt,
    DeliveryRecoveryError,
    IdempotentWriteRecovery,
)
from industry_platform.modules.tools.registry import (
    RegistryToolExecutor,
    ToolExecutionError,
    ToolRegistry,
)
from industry_platform.server import create_selector_event_loop

from .postgres import PostgresProbe


class _DurableControlledProvider:
    """Test-owned provider database, not an actual email/webhook integration."""

    def __init__(self, factory: AsyncSessionFactory) -> None:
        self.factory = factory
        self.uncertain = False

    async def lookup(self, identity: DeliveryIdentity) -> DeliveryLookup:
        if self.uncertain:
            return DeliveryLookup(DeliveryLookupStatus.UNKNOWN)
        async with self.factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT payload_sha256 FROM recovery_provider_receipts "
                        "WHERE delivery_key = :key"
                    ),
                    {"key": identity.delivery_key},
                )
            ).one_or_none()
        if row is None:
            return DeliveryLookup(DeliveryLookupStatus.ABSENT)
        return DeliveryLookup(
            DeliveryLookupStatus.APPLIED,
            DeliveryReceipt(
                identity.delivery_key,
                str(row[0]),
                f"receipt:{identity.delivery_key}",
            ),
        )

    async def send(
        self, identity: DeliveryIdentity, payload: Mapping[str, object]
    ) -> DeliveryReceipt:
        assert canonical_mapping_sha256(payload) == identity.payload_sha256
        async with self.factory.begin() as session:
            await session.execute(
                text(
                    "INSERT INTO recovery_provider_receipts (delivery_key, payload_sha256) "
                    "VALUES (:key, :digest) ON CONFLICT (delivery_key) DO NOTHING"
                ),
                {"key": identity.delivery_key, "digest": identity.payload_sha256},
            )
        result = await self.lookup(identity)
        assert result.receipt is not None
        return result.receipt


def test_unknown_delivery_is_reconciled_before_original_key_retry(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    async def exercise() -> None:
        engine = create_database_engine(migrated_postgres_probe.settings)
        factory = create_database_session_factory(engine)
        release = asyncio.Event()
        finished = asyncio.Event()
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "CREATE TABLE recovery_provider_receipts "
                        "(delivery_key text PRIMARY KEY, payload_sha256 text NOT NULL)"
                    )
                )
            provider = _DurableControlledProvider(factory)
            definition = replace(write_definition(), timeout_ms=10)
            context = runtime_context()
            payload = {"query": "steel"}
            identity = DeliveryIdentity(
                context.workspace_scope.workspace_id,
                f"{definition.name}@{definition.version}",
                side_effect_idempotency_key_sha256(RAW_IDEMPOTENCY_KEY),
                canonical_mapping_sha256(payload),
            )

            class SlowNotification(RecordingAdapter):
                async def execute(
                    self,
                    arguments: Mapping[str, object],
                    runtime_context: TrustedRuntimeContext,
                    *,
                    call_id: UUID,
                    run_id: UUID,
                    observed_at: datetime,
                    idempotency_key: str | None,
                ) -> tuple[ToolObservation, int]:
                    try:
                        await release.wait()
                    except asyncio.CancelledError:
                        await release.wait()
                    try:
                        await provider.send(identity, arguments)
                        return await super().execute(
                            arguments,
                            runtime_context,
                            call_id=call_id,
                            run_id=run_id,
                            observed_at=observed_at,
                            idempotency_key=idempotency_key,
                        )
                    finally:
                        finished.set()

            registry = ToolRegistry(
                (SlowNotification(definition=definition, actual_cost_micro_usd=0),)
            )
            call = prepare_call(registry, definition, idempotency_key=RAW_IDEMPOTENCY_KEY)
            with pytest.raises(ToolExecutionError) as unknown:
                await RegistryToolExecutor(
                    registry, clock=lambda: NOW, adapter_drain_timeout_seconds=0.01
                ).execute(call, context)
            assert unknown.value.code == "tool_outcome_unknown"
            provider.uncertain = True
            recovery = IdempotentWriteRecovery(provider)
            with pytest.raises(DeliveryRecoveryError, match="delivery_outcome_unknown"):
                await recovery.recover_call(call, context)
            provider.uncertain = False
            release.set()
            await asyncio.wait_for(finished.wait(), timeout=5)
            # Recreate the provider/service to prove deduplication is durable, not an
            # in-memory cache attached to the original cancellation-resistant task.
            restored = IdempotentWriteRecovery(_DurableControlledProvider(factory))
            receipts = await asyncio.gather(
                *(restored.recover_call(call, context) for _ in range(3))
            )
            assert len({item.reference for item in receipts}) == 1
            with pytest.raises(DeliveryRecoveryError, match="identity_conflict"):
                await restored.recover(
                    identity, payload, original_idempotency_key="a-new-wrong-key"
                )
            with pytest.raises(DeliveryRecoveryError, match="identity_conflict"):
                await restored.recover(
                    identity, {"query": "changed"}, original_idempotency_key=RAW_IDEMPOTENCY_KEY
                )
            # Concurrent ABSENT observations may race with the original in-flight
            # send; provider-side atomic idempotency remains the final safety boundary.
            second = replace(
                identity,
                idempotency_key_sha256=side_effect_idempotency_key_sha256("second-authorized-key"),
            )
            await asyncio.gather(
                *(
                    restored.recover(
                        second, payload, original_idempotency_key="second-authorized-key"
                    )
                    for _ in range(3)
                )
            )
            async with factory() as session:
                assert (
                    await session.scalar(text("SELECT count(*) FROM recovery_provider_receipts"))
                    == 2
                )
                assert (
                    await session.scalar(
                        text(
                            "SELECT count(*) FROM recovery_provider_receipts "
                            "WHERE delivery_key = :key"
                        ),
                        {"key": identity.delivery_key},
                    )
                    == 1
                )
        finally:
            release.set()
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())
