"""Verify live/ready behavior with real dependency connection failures."""

import os

import pytest
from fastapi.testclient import TestClient

from industry_platform.main import create_app
from industry_platform.server import create_selector_event_loop

from .postgres import PostgresProbe

REDIS_TESTS_REQUIRED = "REDIS_TESTS_REQUIRED"
UNREACHABLE_LOCAL_PORT = 1


@pytest.mark.parametrize(
    ("unavailable_dependency", "expected_checks"),
    [
        ("postgres", {"postgres": "failed", "redis": "ok"}),
        ("redis", {"postgres": "ok", "redis": "failed"}),
    ],
)
def test_live_survives_while_ready_reports_real_dependency_failure(
    migrated_postgres_probe: PostgresProbe,
    unavailable_dependency: str,
    expected_checks: dict[str, str],
) -> None:
    if os.getenv(REDIS_TESTS_REQUIRED) != "1":
        pytest.skip(f"Set {REDIS_TESTS_REQUIRED}=1 to run Redis integration tests")

    settings_update = {
        f"{unavailable_dependency}_port": UNREACHABLE_LOCAL_PORT,
    }
    settings = migrated_postgres_probe.settings.model_copy(
        update=settings_update,
    )
    application = create_app(settings=settings)

    with TestClient(
        application,
        base_url=settings.browser_trusted_origins[0],
        backend_options={"loop_factory": create_selector_event_loop},
    ) as client:
        live_response = client.get("/health/live")
        ready_response = client.get("/health/ready")

    assert live_response.status_code == 200
    assert live_response.json() == {"status": "ok"}
    assert ready_response.status_code == 503
    assert ready_response.json() == {
        "status": "not_ready",
        "checks": expected_checks,
    }
    assert "password" not in ready_response.text.lower()
