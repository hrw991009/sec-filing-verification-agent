"""Unknown receipt lookup is never permission to send with a different identity."""

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, replace
from uuid import uuid4

import pytest

from industry_platform.modules.tools.domain import (
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


@dataclass
class Provider:
    status: DeliveryLookupStatus = DeliveryLookupStatus.ABSENT
    receipt: DeliveryReceipt | None = None
    sends: int = 0
    delay: bool = False

    async def lookup(self, identity: DeliveryIdentity) -> DeliveryLookup:
        if self.delay:
            await asyncio.sleep(1)
        return DeliveryLookup(self.status, self.receipt)

    async def send(
        self, identity: DeliveryIdentity, payload: Mapping[str, object]
    ) -> DeliveryReceipt:
        self.sends += 1
        return DeliveryReceipt(identity.delivery_key, identity.payload_sha256, "receipt-1")


def identity() -> DeliveryIdentity:
    return DeliveryIdentity(
        uuid4(),
        "notification.send@v1",
        side_effect_idempotency_key_sha256("original-write-key"),
        canonical_mapping_sha256({"notice": "test"}),
    )


@pytest.mark.asyncio
async def test_unknown_and_timeout_never_send() -> None:
    for provider in (Provider(status=DeliveryLookupStatus.UNKNOWN), Provider(delay=True)):
        service = IdempotentWriteRecovery(provider, lookup_timeout_seconds=0.01)
        with pytest.raises(DeliveryRecoveryError, match="outcome_unknown"):
            await service.recover(
                identity(), {"notice": "test"}, original_idempotency_key="original-write-key"
            )
        assert provider.sends == 0


@pytest.mark.asyncio
async def test_receipt_identity_and_arguments_are_checked() -> None:
    command = identity()
    provider = Provider(
        status=DeliveryLookupStatus.APPLIED,
        receipt=DeliveryReceipt("wrong", command.payload_sha256, "receipt"),
    )
    with pytest.raises(DeliveryRecoveryError, match="receipt_conflict"):
        await IdempotentWriteRecovery(provider).reconcile(command)
    provider = Provider()
    receipt = await IdempotentWriteRecovery(provider).recover(
        command, {"notice": "test"}, original_idempotency_key="original-write-key"
    )
    assert receipt.delivery_key == command.delivery_key
    assert provider.sends == 1
    assert replace(command, workspace_id=uuid4()).delivery_key != command.delivery_key


def test_recovery_contract_rejects_invalid_identity_and_result() -> None:
    with pytest.raises(ValueError, match="timeout"):
        IdempotentWriteRecovery(Provider(), lookup_timeout_seconds=0)
    with pytest.raises(ValueError, match="digest"):
        replace(identity(), payload_sha256="bad")
    with pytest.raises(ValueError, match="scope"):
        replace(identity(), operation="")
    with pytest.raises(ValueError, match="confirmed"):
        DeliveryLookup(DeliveryLookupStatus.APPLIED)
