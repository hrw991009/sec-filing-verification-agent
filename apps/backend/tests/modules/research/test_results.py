"""Visual result values share the report's immutable calculation/source lineage."""

from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from industry_platform.core.config import Settings
from industry_platform.main import create_app
from industry_platform.modules.evidence.domain import FinancialCalculationLocatorV1
from industry_platform.modules.identity.http_auth import get_principal_resolver
from industry_platform.modules.research.domain import ResearchRunView
from industry_platform.modules.research.results import ResearchResultService, build_result_view
from industry_platform.modules.research.router import get_result_service
from industry_platform.modules.research.service import ResearchNotFoundError
from industry_platform.modules.research.verification import (
    VerificationEvidenceState,
    VerificationReport,
    evaluate_verification_snapshot,
)
from industry_platform.modules.workspaces.domain import WorkspaceScope

from .test_api import StubPrincipalResolver, headers, principal, research_view
from .test_dupont_verification import dupont_snapshot


def result_inputs(
    years: int = 2,
) -> tuple[ResearchRunView, tuple[VerificationEvidenceState, ...], VerificationReport]:
    selected = dupont_snapshot(years)
    view = research_view()
    assert view.draft is not None
    view = replace(
        view,
        brief=replace(
            view.brief, input=replace(view.brief.input, financial_scope=selected.financial_scope)
        ),
        draft=replace(
            view.draft,
            evidence_refs=tuple(item.evidence.evidence_id for item in selected.evidence_states),
        ),
    )
    assert view.draft is not None
    report = evaluate_verification_snapshot(replace(selected, draft_id=view.draft.draft_id))
    return view, selected.evidence_states, report


@pytest.mark.parametrize("years", [2, 3, 4, 5])
def test_result_view_preserves_exact_values_units_sources_and_versions(years: int) -> None:
    view, states, report = result_inputs(years)
    result = build_result_view(view, states, report)
    assert result.status == "ready"
    assert result.verification_status == "verified"
    assert result.verification_report_id == report.report_id
    assert view.draft is not None
    assert result.draft_id == view.draft.draft_id
    assert len(result.periods) == years  # Historical reports remain readable.
    assert len(result.rows) == 12
    rows = {row.key: row for row in result.rows}
    assert rows["revenue"].values[0].value == "1000"
    assert rows["average_assets"].values[0].value == "600.0000"
    assert rows["roe_percent"].values[1].value == "40.0000"
    assert rows["roe_percent"].values[1].change == "0.0000"
    assert rows["roe_percent"].change_unit == "百分点"
    assert all(value.evidence_refs for row in result.rows for value in row.values)
    assert "USD" in rows["revenue"].unit


@pytest.mark.parametrize("mutation", ["missing", "unavailable", "scope", "component", "source"])
def test_invalid_sources_never_publish_chart_numbers(mutation: str) -> None:
    view, original, report = result_inputs()
    states = list(original)
    if mutation == "missing":
        states.pop()
    elif mutation == "unavailable":
        states[0] = replace(states[0], available=False)
    elif mutation == "source":
        states[0] = replace(
            states[0], evidence=replace(states[0].evidence, excerpt='{"value":"999"}')
        )
    else:
        item = states[-1].evidence
        assert isinstance(item.locator, FinancialCalculationLocatorV1)
        locator = (
            replace(
                item.locator,
                components=tuple(
                    (key, "999" if key == "roe_percent" else value)
                    for key, value in item.locator.components
                ),
            )
            if mutation == "component"
            else replace(
                item.locator, financial_scope={**item.locator.financial_scope, "unit": "EUR"}
            )
        )
        states[-1] = replace(states[-1], evidence=replace(item, locator=locator))
    result = build_result_view(view, tuple(states), report)
    assert result.status == "unavailable"
    assert result.rows == ()
    assert result.verification_status is None


@pytest.mark.parametrize("mutation", ["no_report", "draft", "snapshot"])
def test_stale_verification_is_not_a_current_badge(mutation: str) -> None:
    view, states, original_report = result_inputs()
    report: VerificationReport | None = original_report
    if mutation == "no_report":
        report = None
    elif mutation == "draft":
        report = replace(original_report, draft_id=uuid4())
    else:
        report = replace(
            original_report,
            evidence_snapshots=tuple(
                replace(item, revision=item.revision + 1)
                for item in original_report.evidence_snapshots
            ),
        )
    result = build_result_view(view, states, report)
    assert result.status == "ready"
    assert result.verification_status is None


def test_general_and_pending_reports_have_no_dupont_visuals() -> None:
    assert build_result_view(research_view(), (), None).status == "not_applicable"
    view, states, report = result_inputs()
    assert build_result_view(replace(view, draft=None), states, report).status == "not_applicable"


@pytest.mark.asyncio
async def test_result_service_authorizes_run_before_resolving_any_evidence() -> None:
    view, states, report = result_inputs()
    query, evidence, verification = AsyncMock(), AsyncMock(), AsyncMock()
    query.get.return_value = view
    evidence.resolve_evidence.side_effect = [(state.evidence, state.available) for state in states]
    verification.latest.return_value = report
    service = ResearchResultService(query, evidence, verification)
    scope = WorkspaceScope(view.research_run.workspace_id, view.research_run.owner_user_id, "owner")
    result = await service.get(scope, view.research_run.research_run_id)
    assert result.status == "ready"
    assert evidence.resolve_evidence.await_count == len(states)
    evidence.reset_mock()
    query.get.side_effect = ResearchNotFoundError()
    with pytest.raises(ResearchNotFoundError):
        await service.get(scope, uuid4())
    evidence.resolve_evidence.assert_not_awaited()


def test_result_endpoint_is_workspace_authorized_and_not_cacheable(test_settings: Settings) -> None:
    view = research_view()
    service = AsyncMock()
    service.get.return_value = build_result_view(view, (), None)
    app = create_app(settings=test_settings)
    app.dependency_overrides[get_principal_resolver] = lambda: StubPrincipalResolver(principal())
    app.dependency_overrides[get_result_service] = lambda: service
    base = (
        f"/api/v1/workspaces/{view.research_run.workspace_id}"
        f"/research-runs/{view.research_run.research_run_id}/result-view"
    )
    with TestClient(app, base_url="https://localhost") as client:
        response = client.get(base, headers=headers())
        assert response.status_code == 200
        assert "no-store" in response.headers["cache-control"]
        assert response.json()["status"] == "not_applicable"
        denied = client.get(
            base.replace(str(view.research_run.workspace_id), str(uuid4())), headers=headers()
        )
        assert denied.status_code == 403
        assert service.get.await_count == 1
