"""HTTP scope, revision, and response contracts for the Evidence ledger."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from fastapi.testclient import TestClient

from industry_platform.core.config import Settings
from industry_platform.main import create_app
from industry_platform.modules.evidence.domain import (
    AuthorizationSnapshot,
    ClaimVerificationStatus,
    CreateClaim,
    Evidence,
    EvidenceDecision,
    EvidenceDecisionReason,
    EvidenceKind,
    EvidenceNormalizationItem,
    EvidenceNormalizationResult,
    EvidenceStatus,
    IndustrySourceLocatorV1,
    InvalidateEvidence,
    NormalizeObservation,
    ResearchClaim,
)
from industry_platform.modules.evidence.ports import EvidenceUseCase
from industry_platform.modules.evidence.router import get_evidence_service
from industry_platform.modules.identity.domain import (
    AccessToken,
    AuthenticatedPrincipal,
    AuthenticatedWorkspace,
    NormalizedEmail,
)
from industry_platform.modules.identity.http_auth import get_principal_resolver
from industry_platform.modules.workspaces.domain import WorkspaceScope

NOW = datetime(2026, 8, 21, 8, 0, tzinfo=UTC)
WORKSPACE_ID = UUID("11111111-1111-4111-8111-111111111111")
OTHER_WORKSPACE_ID = UUID("22222222-2222-4222-8222-222222222222")
USER_ID = UUID("33333333-3333-4333-8333-333333333333")
SESSION_ID = UUID("44444444-4444-4444-8444-444444444444")
EVIDENCE_ID = UUID("55555555-5555-4555-8555-555555555555")
RUN_ID = UUID("66666666-6666-4666-8666-666666666666")
STEP_ID = UUID("77777777-7777-4777-8777-777777777777")
CALL_ID = UUID("88888888-8888-4888-8888-888888888888")
OBSERVATION_ID = UUID("99999999-9999-4999-8999-999999999999")
SOURCE_ITEM_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
RESEARCH_RUN_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
CLAIM_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
RAW_ACCESS_VALUE = ".".join(("header", "payload", "signature"))


@dataclass(slots=True)
class StubPrincipalResolver:
    value: AuthenticatedPrincipal

    async def resolve(self, _token: AccessToken) -> AuthenticatedPrincipal:
        return self.value


@dataclass(slots=True)
class StubEvidenceService:
    normalize_calls: list[tuple[WorkspaceScope, NormalizeObservation]] = field(default_factory=list)
    invalidate_calls: list[tuple[WorkspaceScope, InvalidateEvidence]] = field(default_factory=list)
    claim_calls: list[tuple[WorkspaceScope, CreateClaim]] = field(default_factory=list)

    async def normalize_observation(
        self,
        scope: WorkspaceScope,
        command: NormalizeObservation,
    ) -> EvidenceNormalizationResult:
        self.normalize_calls.append((scope, command))
        return EvidenceNormalizationResult(
            observation_id=OBSERVATION_ID,
            tool_call_id=CALL_ID,
            normalizer_version="evidence-normalizer-v1",
            items=(
                EvidenceNormalizationItem(
                    source_ordinal=1,
                    decision=EvidenceDecision.ACCEPTED,
                    reason=EvidenceDecisionReason.ACCEPTED,
                    evidence=active_evidence(),
                ),
            ),
        )

    async def invalidate_evidence(
        self,
        scope: WorkspaceScope,
        command: InvalidateEvidence,
        *,
        invalidated_at: datetime | None = None,
    ) -> Evidence:
        del invalidated_at
        self.invalidate_calls.append((scope, command))
        return replace(
            active_evidence(),
            excerpt=None,
            status=EvidenceStatus.TOMBSTONED,
            revision=2,
            invalidated_at=NOW,
            invalidation_reason=command.reason,
        )

    async def create_claim(
        self,
        scope: WorkspaceScope,
        command: CreateClaim,
        *,
        created_at: datetime | None = None,
    ) -> ResearchClaim:
        del created_at
        self.claim_calls.append((scope, command))
        return ResearchClaim(
            claim_id=CLAIM_ID,
            workspace_id=WORKSPACE_ID,
            research_run_id=command.research_run_id,
            statement=command.statement,
            confidence=command.confidence,
            verification_status=ClaimVerificationStatus.UNCERTAIN,
            coverage=0,
            conflict=False,
            revision=1,
            relations=(),
            created_at=NOW,
            updated_at=NOW,
        )


def active_evidence() -> Evidence:
    return Evidence(
        evidence_id=EVIDENCE_ID,
        workspace_id=WORKSPACE_ID,
        kind=EvidenceKind.NEWS,
        title="Public transport transition",
        canonical_url="https://example.test/source",
        locator=IndustrySourceLocatorV1(
            source_item_id=SOURCE_ITEM_ID,
            source_kind="news",
            provider="world_bank_news",
            source_version="api-v2-2026-08",
            content_sha256="a" * 64,
        ),
        excerpt="A bounded attributable excerpt.",
        content_sha256="a" * 64,
        source_published_at=NOW,
        retrieved_at=NOW,
        license_or_terms="Public metadata with attribution.",
        status=EvidenceStatus.ACTIVE,
        revision=1,
        invalidated_at=None,
        invalidation_reason=None,
        origin_run_id=RUN_ID,
        origin_step_id=STEP_ID,
        origin_tool_call_id=CALL_ID,
        origin_observation_id=OBSERVATION_ID,
        origin_source_ordinal=1,
        normalizer_version="evidence-normalizer-v1",
        authorization_snapshot=AuthorizationSnapshot(
            workspace_id=WORKSPACE_ID,
            actor_user_id=USER_ID,
            role="member",
            action="evidence.normalize",
            captured_at=NOW,
        ),
        source_resource_version="api-v2-2026-08:aaaaaaaa",
        created_at=NOW,
        updated_at=NOW,
    )


def principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        user_id=USER_ID,
        session_id=SESSION_ID,
        email=NormalizedEmail("evidence-member@example.test"),
        workspaces=(AuthenticatedWorkspace(WORKSPACE_ID, "Workspace", "member"),),
    )


@contextmanager
def evidence_client(
    settings: Settings,
    service: StubEvidenceService,
) -> Iterator[TestClient]:
    application = create_app(settings=settings)
    application.dependency_overrides[get_principal_resolver] = lambda: StubPrincipalResolver(
        principal()
    )
    application.dependency_overrides[get_evidence_service] = lambda: cast(EvidenceUseCase, service)
    with TestClient(application, base_url="https://localhost") as client:
        yield client


def headers(**additional: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {RAW_ACCESS_VALUE}", **additional}


def test_normalization_uses_trusted_scope_and_server_trace(test_settings: Settings) -> None:
    service = StubEvidenceService()
    with evidence_client(test_settings, service) as client:
        response = client.post(
            f"/api/v1/workspaces/{WORKSPACE_ID}/evidence/normalizations",
            headers=headers(**{"X-Trace-ID": "untrusted-client-trace"}),
            json={"tool_call_id": str(CALL_ID), "observation_id": str(OBSERVATION_ID)},
        )

    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["items"][0]["decision"] == "accepted"
    scope, command = service.normalize_calls[0]
    assert scope == WorkspaceScope(WORKSPACE_ID, USER_ID, "member")
    assert command.tool_call_id == CALL_ID
    assert str(command.trace_id) == response.headers["X-Trace-ID"]
    assert str(command.trace_id) != "untrusted-client-trace"


def test_invalidation_requires_revision_and_outside_workspace_is_denied(
    test_settings: Settings,
) -> None:
    service = StubEvidenceService()
    root = f"/api/v1/workspaces/{WORKSPACE_ID}/evidence/{EVIDENCE_ID}/invalidate"
    payload = {"status": "tombstoned", "reason": "Source withdrawn"}
    with evidence_client(test_settings, service) as client:
        invalid_revision = client.post(root, headers=headers(**{"If-Match": "stale"}), json=payload)
        outside_scope = client.post(
            f"/api/v1/workspaces/{OTHER_WORKSPACE_ID}/evidence/{EVIDENCE_ID}/invalidate",
            headers=headers(**{"If-Match": "1"}),
            json=payload,
        )
        invalidated = client.post(root, headers=headers(**{"If-Match": '"1"'}), json=payload)

    assert invalid_revision.status_code == 422
    assert invalid_revision.json()["code"] == "EVIDENCE_REQUEST_REJECTED"
    assert outside_scope.status_code == 403
    assert outside_scope.json()["code"] == "WORKSPACE_ACCESS_DENIED"
    assert invalidated.status_code == 200
    assert invalidated.headers["etag"] == '"2"'
    assert invalidated.json()["excerpt"] is None
    assert len(service.invalidate_calls) == 1
    scope, command = service.invalidate_calls[0]
    assert scope == WorkspaceScope(WORKSPACE_ID, USER_ID, "member")
    assert command.expected_revision == 1
    assert command.reason == "Source withdrawn"


def test_claim_route_binds_path_run_and_rejects_duplicate_relations(
    test_settings: Settings,
) -> None:
    service = StubEvidenceService()
    root = f"/api/v1/workspaces/{WORKSPACE_ID}/research-runs/{RESEARCH_RUN_ID}/claims"
    payload: dict[str, object] = {
        "statement": "Public transport is transitioning.",
        "confidence": 0.8,
        "relations": [],
        "origin_run_id": str(RUN_ID),
        "origin_step_id": str(STEP_ID),
    }
    with evidence_client(test_settings, service) as client:
        created = client.post(root, headers=headers(), json=payload)
        duplicate_payload = {
            **payload,
            "relations": [
                {"evidence_id": str(EVIDENCE_ID), "relation": "supports"},
                {"evidence_id": str(EVIDENCE_ID), "relation": "context"},
            ],
        }
        duplicate = client.post(root, headers=headers(), json=duplicate_payload)

    assert created.status_code == 201
    assert created.json()["research_run_id"] == str(RESEARCH_RUN_ID)
    assert created.json()["verification_status"] == "uncertain"
    assert duplicate.status_code == 422
    assert duplicate.json()["code"] == "REQUEST_VALIDATION_FAILED"
    assert len(service.claim_calls) == 1
    scope, command = service.claim_calls[0]
    assert scope == WorkspaceScope(WORKSPACE_ID, USER_ID, "member")
    assert command.research_run_id == RESEARCH_RUN_ID
    assert command.origin_run_id == RUN_ID
