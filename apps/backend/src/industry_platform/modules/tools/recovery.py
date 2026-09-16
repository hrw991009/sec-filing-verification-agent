"""Reconcile an uncertain idempotent write before retrying its original identity.

Providers opting into this port MUST atomically deduplicate send() by delivery_key,
retain the payload digest, and make their receipt lookup authoritative. A transport
without that contract must not be registered here (for example, plain SMTP).
"""

import asyncio
import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from industry_platform.modules.agent_runtime.context import TrustedRuntimeContext
from industry_platform.modules.tools.domain import (
    ToolApprovalOutcome,
    ToolCall,
    ToolSideEffectClass,
    canonical_mapping_sha256,
    side_effect_idempotency_key_sha256,
)


class DeliveryLookupStatus(StrEnum):
    APPLIED = "applied"
    ABSENT = "absent"
    UNKNOWN = "unknown"


class DeliveryRecoveryError(RuntimeError):
    """The original write cannot yet be safely reconciled."""


@dataclass(frozen=True, slots=True)
class DeliveryIdentity:
    workspace_id: UUID
    operation: str
    idempotency_key_sha256: str = field(repr=False)
    payload_sha256: str

    def __post_init__(self) -> None:
        if self.workspace_id.int == 0 or not self.operation or len(self.operation) > 128:
            raise ValueError("Invalid delivery scope")
        for digest in (self.idempotency_key_sha256, self.payload_sha256):
            if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                raise ValueError("Invalid delivery digest")

    @property
    def delivery_key(self) -> str:
        return hashlib.sha256(
            f"delivery:v1:{self.workspace_id}:{self.operation}:{self.idempotency_key_sha256}".encode()
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class DeliveryReceipt:
    delivery_key: str
    payload_sha256: str
    reference: str


@dataclass(frozen=True, slots=True)
class DeliveryLookup:
    status: DeliveryLookupStatus
    receipt: DeliveryReceipt | None = None

    def __post_init__(self) -> None:
        if (self.status is DeliveryLookupStatus.APPLIED) != (self.receipt is not None):
            raise ValueError("Only a confirmed write may carry a receipt")


class IdempotentDeliveryProvider(Protocol):
    """Provider-side durable uniqueness is required, including concurrent/in-flight sends."""

    async def lookup(self, identity: DeliveryIdentity) -> DeliveryLookup: ...

    async def send(
        self, identity: DeliveryIdentity, payload: Mapping[str, object]
    ) -> DeliveryReceipt: ...


@dataclass(frozen=True, slots=True)
class IdempotentWriteRecovery:
    provider: IdempotentDeliveryProvider = field(repr=False)
    lookup_timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if not 0 < self.lookup_timeout_seconds <= 30:
            raise ValueError("Delivery lookup timeout must be bounded")

    async def reconcile(self, identity: DeliveryIdentity) -> DeliveryLookup:
        try:
            async with asyncio.timeout(self.lookup_timeout_seconds):
                result = await self.provider.lookup(identity)
        except (TimeoutError, OSError):
            # A failed lookup is not proof that the external write was absent.
            return DeliveryLookup(DeliveryLookupStatus.UNKNOWN)
        if result.receipt is not None:
            self._validate_receipt(identity, result.receipt)
        return result

    async def recover(
        self,
        identity: DeliveryIdentity,
        payload: Mapping[str, object],
        *,
        original_idempotency_key: str,
    ) -> DeliveryReceipt:
        """Retry only the key/digest retained by the original authorized Tool call.

        This does not grant authorization or amend historical Tool/Run outcomes. The
        caller must obtain the identity from its durable original command, not a model.
        RegistryToolExecutor remains responsible for bounding the send operation.
        """
        try:
            key_hash = side_effect_idempotency_key_sha256(original_idempotency_key)
            payload_hash = canonical_mapping_sha256(payload)
        except ValueError:
            raise DeliveryRecoveryError("delivery_identity_conflict") from None
        if key_hash != identity.idempotency_key_sha256 or payload_hash != identity.payload_sha256:
            raise DeliveryRecoveryError("delivery_identity_conflict")
        result = await self.reconcile(identity)
        if result.status is DeliveryLookupStatus.UNKNOWN:
            raise DeliveryRecoveryError("delivery_outcome_unknown")
        if result.receipt is not None:
            return result.receipt
        receipt = await self.provider.send(identity, payload)
        self._validate_receipt(identity, receipt)
        return receipt

    async def recover_call(
        self, call: ToolCall, runtime_context: TrustedRuntimeContext
    ) -> DeliveryReceipt:
        """Use the retained authorized Tool call, never model-supplied recovery coordinates."""
        if (
            call.workspace_id != runtime_context.workspace_scope.workspace_id
            or call.requested_by_user_id != runtime_context.principal.user_id
            or call.definition.capability not in runtime_context.capabilities
            or call.decision.outcome is not ToolApprovalOutcome.ALLOW
            or call.definition.side_effect_class is not ToolSideEffectClass.IDEMPOTENT_WRITE
            or call.idempotency_key_sha256 is None
            or call.side_effect_idempotency_key is None
        ):
            raise DeliveryRecoveryError("delivery_recovery_denied")
        return await self.recover(
            DeliveryIdentity(
                workspace_id=call.workspace_id,
                operation=f"{call.definition.name}@{call.definition.version}",
                idempotency_key_sha256=call.idempotency_key_sha256,
                payload_sha256=canonical_mapping_sha256(call.arguments),
            ),
            call.arguments,
            original_idempotency_key=call.side_effect_idempotency_key,
        )

    @staticmethod
    def _validate_receipt(identity: DeliveryIdentity, receipt: DeliveryReceipt) -> None:
        if (
            receipt.delivery_key != identity.delivery_key
            or receipt.payload_sha256 != identity.payload_sha256
            or not receipt.reference
            or len(receipt.reference) > 200
        ):
            raise DeliveryRecoveryError("delivery_receipt_conflict")
