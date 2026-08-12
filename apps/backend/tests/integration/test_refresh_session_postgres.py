"""Exercise atomic refresh rotation and lost-response recovery in PostgreSQL."""

import asyncio
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe
from uuid import UUID

import pytest
from sqlalchemy import select

from industry_platform.core.config import Settings
from industry_platform.core.database import (
    AsyncSessionFactory,
    create_database_engine,
    create_database_session_factory,
)
from industry_platform.modules.identity.adapters.access_tokens import (
    Ed25519AccessTokenCodec,
)
from industry_platform.modules.identity.adapters.browser_requests import (
    ExactBrowserSessionRequestGuard,
)
from industry_platform.modules.identity.adapters.refresh_recovery import (
    AesGcmRefreshRecoveryCodec,
)
from industry_platform.modules.identity.adapters.session_tokens import (
    HmacSessionTokenService,
)
from industry_platform.modules.identity.adapters.sqlalchemy import (
    REFRESH_AUDIT_ACTION,
    REFRESH_RECOVERY_AUDIT_ACTION,
    REFRESH_REPLAY_AUDIT_ACTION,
    SqlAlchemyRefreshSessionTransactionFactory,
)
from industry_platform.modules.identity.domain import (
    AccessTokenGenerationError,
    InvalidRefreshSessionError,
    IssuedLoginSessionTokens,
    RefreshSessionCommand,
    TraceId,
)
from industry_platform.modules.identity.models import (
    AuditLog,
    RefreshSession,
    RefreshSessionFamily,
    User,
    UserStatus,
)
from industry_platform.modules.identity.ports import AccessTokenCodec
from industry_platform.modules.identity.service import RefreshSessionService
from industry_platform.server import create_selector_event_loop

from .postgres import PostgresProbe

USER_ID = UUID("11111111-1111-4111-8111-111111111111")
FAMILY_ID = UUID("22222222-2222-4222-8222-222222222222")
PREDECESSOR_ID = UUID("33333333-3333-4333-8333-333333333333")
SUCCESSOR_ID = UUID("44444444-4444-4444-8444-444444444444")
SECOND_SUCCESSOR_ID = UUID("55555555-5555-4555-8555-555555555555")


class SequenceSessionIds:
    """Return deterministic successor IDs without adding an await boundary."""

    def __init__(self, *values: UUID) -> None:
        self._values = iter(values)

    def __call__(self) -> UUID:
        try:
            return next(self._values)
        except StopIteration:
            raise AssertionError("Unexpected refresh successor allocation") from None


def nil_uuid() -> UUID:
    """Force the real Access Token adapter to reject token generation."""

    return UUID(int=0)


def session_token_service(settings: Settings) -> HmacSessionTokenService:
    return HmacSessionTokenService(
        refresh_hmac_key=settings.refresh_token_hmac_key,
        csrf_hmac_key=settings.csrf_token_hmac_key,
        device_hmac_key=settings.device_token_hmac_key,
    )


def access_token_codec(
    settings: Settings,
    *,
    fail_generation: bool = False,
) -> Ed25519AccessTokenCodec:
    if fail_generation:
        return Ed25519AccessTokenCodec(
            current_kid=settings.access_token_current_kid,
            private_key=settings.access_token_private_key,
            public_keys=dict(settings.access_token_public_keys),
            jti_source=nil_uuid,
        )

    return Ed25519AccessTokenCodec(
        current_kid=settings.access_token_current_kid,
        private_key=settings.access_token_private_key,
        public_keys=dict(settings.access_token_public_keys),
    )


def refresh_service(
    *,
    settings: Settings,
    session_factory: AsyncSessionFactory,
    token_service: HmacSessionTokenService,
    access_codec: AccessTokenCodec,
    session_ids: SequenceSessionIds,
) -> RefreshSessionService:
    return RefreshSessionService(
        session_token_service=token_service,
        access_token_codec=access_codec,
        browser_request_guard=ExactBrowserSessionRequestGuard(
            trusted_origins=settings.browser_trusted_origins,
            token_service=token_service,
        ),
        recovery_codec=AesGcmRefreshRecoveryCodec(
            settings.refresh_recovery_aead_key,
        ),
        transaction_factory=SqlAlchemyRefreshSessionTransactionFactory(session_factory),
        session_id_source=session_ids,
    )


async def seed_initial_session(
    session_factory: AsyncSessionFactory,
    token_service: HmacSessionTokenService,
) -> IssuedLoginSessionTokens:
    """Insert one active family using real token digests but no login machinery."""

    issued = token_service.issue()
    issued_at = datetime.now(UTC).replace(microsecond=0)
    absolute_expires_at = issued_at + timedelta(days=30)

    async with session_factory.begin() as session:
        user = User(
            id=USER_ID,
            email="refresh-owner@example.com",
            password_hash=token_urlsafe(32),
            status=UserStatus.ACTIVE,
        )
        session.add(user)
        await session.flush()

        family = RefreshSessionFamily(
            id=FAMILY_ID,
            user_id=USER_ID,
            absolute_expires_at=absolute_expires_at,
        )
        session.add(family)
        await session.flush()

        predecessor = RefreshSession(
            id=PREDECESSOR_ID,
            user_id=USER_ID,
            rotation_family_id=FAMILY_ID,
            token_hash=bytes(issued.refresh_token_hash),
            csrf_token_hash=bytes(issued.csrf_token_hash),
            device_hash=bytes(issued.device_token_hash),
            idle_expires_at=issued_at + timedelta(days=7),
            absolute_expires_at=absolute_expires_at,
        )
        session.add(predecessor)
        await session.flush()
        family.current_session_id = predecessor.id

    return issued


def refresh_command(
    *,
    origin: str,
    issued: IssuedLoginSessionTokens,
    trace_id: str,
) -> RefreshSessionCommand:
    csrf_value = issued.csrf_token.reveal_for_transport()
    return RefreshSessionCommand(
        origin=origin,
        refresh_token=issued.refresh_token,
        csrf_cookie_value=csrf_value,
        csrf_header_value=csrf_value,
        device_token=issued.device_token,
        trace_id=TraceId(trace_id),
    )


def test_concurrent_refresh_recovers_one_successor_and_replay_revokes_family(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    async def exercise() -> None:
        settings = migrated_postgres_probe.settings
        engine = create_database_engine(settings)
        session_factory = create_database_session_factory(engine)
        token_service = session_token_service(settings)
        access_codec = access_token_codec(settings)
        service = refresh_service(
            settings=settings,
            session_factory=session_factory,
            token_service=token_service,
            access_codec=access_codec,
            session_ids=SequenceSessionIds(SUCCESSOR_ID, SECOND_SUCCESSOR_ID),
        )

        try:
            initial = await seed_initial_session(session_factory, token_service)
            original_command = refresh_command(
                origin=settings.browser_trusted_origins[0],
                issued=initial,
                trace_id="concurrent-refresh-trace",
            )

            async with asyncio.timeout(30):
                first_result, second_result = await asyncio.gather(
                    service.refresh(original_command),
                    service.refresh(original_command),
                )
            rotated, recovered = sorted(
                (first_result, second_result),
                key=lambda result: result.recovered,
            )

            assert rotated.recovered is False
            assert recovered.recovered is True
            assert rotated.session.session_id == SUCCESSOR_ID
            assert recovered.session.session_id == SUCCESSOR_ID
            assert recovered.refresh_token == rotated.refresh_token
            assert recovered.csrf_token == rotated.csrf_token

            for result in (rotated, recovered):
                claims = access_codec.verify(
                    result.access_token,
                    now=result.session.issued_at,
                )
                assert claims.user_id == USER_ID
                assert claims.session_id == SUCCESSOR_ID
                assert claims.expires_at == result.access_token_expires_at

            async with session_factory() as session:
                family = await session.get(RefreshSessionFamily, FAMILY_ID)
                predecessor = await session.get(RefreshSession, PREDECESSOR_ID)
                successor = await session.get(RefreshSession, SUCCESSOR_ID)
                audit_actions = list(await session.scalars(select(AuditLog.action)))

            assert family is not None
            assert predecessor is not None
            assert successor is not None
            assert family.current_session_id == SUCCESSOR_ID
            assert predecessor.used_at is not None
            assert predecessor.replaced_by_session_id == SUCCESSOR_ID
            recovery_envelope = predecessor.recovery_envelope
            assert recovery_envelope is not None
            assert predecessor.recovery_expires_at is not None
            assert successor.previous_session_id == PREDECESSOR_ID
            assert successor.used_at is None
            assert successor.token_hash == bytes(
                token_service.digest_refresh(rotated.refresh_token)
            )
            assert successor.csrf_token_hash == bytes(token_service.digest_csrf(rotated.csrf_token))
            assert successor.device_hash == bytes(initial.device_token_hash)
            assert audit_actions.count(REFRESH_AUDIT_ACTION) == 1
            assert audit_actions.count(REFRESH_RECOVERY_AUDIT_ACTION) == 1

            plaintext_values = (
                initial.refresh_token.reveal_for_transport(),
                initial.csrf_token.reveal_for_transport(),
                initial.device_token.reveal_for_transport(),
                rotated.refresh_token.reveal_for_transport(),
                rotated.csrf_token.reveal_for_transport(),
            )
            persisted_values = (
                predecessor.token_hash,
                predecessor.csrf_token_hash,
                predecessor.device_hash,
                recovery_envelope,
                successor.token_hash,
                successor.csrf_token_hash,
                successor.device_hash,
            )
            assert all(
                raw_value.encode("ascii") not in persisted_value
                for raw_value in plaintext_values
                for persisted_value in persisted_values
            )

            successor_issued = IssuedLoginSessionTokens(
                refresh_token=rotated.refresh_token,
                csrf_token=rotated.csrf_token,
                device_token=initial.device_token,
                refresh_token_hash=token_service.digest_refresh(rotated.refresh_token),
                csrf_token_hash=token_service.digest_csrf(rotated.csrf_token),
                device_token_hash=initial.device_token_hash,
            )
            await service.refresh(
                refresh_command(
                    origin=settings.browser_trusted_origins[0],
                    issued=successor_issued,
                    trace_id="advance-current-refresh-trace",
                )
            )

            with pytest.raises(InvalidRefreshSessionError):
                await service.refresh(original_command)

            async with session_factory() as session:
                revoked_family = await session.get(RefreshSessionFamily, FAMILY_ID)
                family_sessions = list(
                    await session.scalars(
                        select(RefreshSession).where(RefreshSession.rotation_family_id == FAMILY_ID)
                    )
                )
                final_audit_actions = list(await session.scalars(select(AuditLog.action)))

            assert revoked_family is not None
            assert revoked_family.revoked_at is not None
            assert len(family_sessions) == 3
            assert all(item.revoked_at is not None for item in family_sessions)
            assert all(item.recovery_envelope is None for item in family_sessions)
            assert final_audit_actions.count(REFRESH_AUDIT_ACTION) == 2
            assert final_audit_actions.count(REFRESH_RECOVERY_AUDIT_ACTION) == 1
            assert final_audit_actions.count(REFRESH_REPLAY_AUDIT_ACTION) == 1
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())


def test_access_signing_failure_rolls_back_real_rotation_state(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    async def exercise() -> None:
        settings = migrated_postgres_probe.settings
        engine = create_database_engine(settings)
        session_factory = create_database_session_factory(engine)
        token_service = session_token_service(settings)
        service = refresh_service(
            settings=settings,
            session_factory=session_factory,
            token_service=token_service,
            access_codec=access_token_codec(settings, fail_generation=True),
            session_ids=SequenceSessionIds(SUCCESSOR_ID),
        )

        try:
            initial = await seed_initial_session(session_factory, token_service)

            with pytest.raises(AccessTokenGenerationError):
                await service.refresh(
                    refresh_command(
                        origin=settings.browser_trusted_origins[0],
                        issued=initial,
                        trace_id="refresh-signing-rollback-trace",
                    )
                )

            async with session_factory() as session:
                family = await session.get(RefreshSessionFamily, FAMILY_ID)
                predecessor = await session.get(RefreshSession, PREDECESSOR_ID)
                family_sessions = list(
                    await session.scalars(
                        select(RefreshSession).where(RefreshSession.rotation_family_id == FAMILY_ID)
                    )
                )
                refresh_audits = list(
                    await session.scalars(
                        select(AuditLog).where(AuditLog.action == REFRESH_AUDIT_ACTION)
                    )
                )

            assert family is not None
            assert predecessor is not None
            assert family.current_session_id == PREDECESSOR_ID
            assert len(family_sessions) == 1
            assert predecessor.used_at is None
            assert predecessor.replaced_by_session_id is None
            assert predecessor.recovery_envelope is None
            assert predecessor.recovery_expires_at is None
            assert refresh_audits == []
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())
