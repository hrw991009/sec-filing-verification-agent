"""Restore a populated disposable database and verify all public business tables."""

import asyncio

from industry_platform.core.database import create_database_engine, create_database_session_factory
from industry_platform.modules.evaluation.release_recovery_exercise import (
    _backup_restore,
    _database_state,
)
from industry_platform.server import create_selector_event_loop

from .postgres import PostgresProbe
from .test_jobs_postgres import job_service, submission


def test_backup_restore_preserves_populated_jobs_and_all_public_tables(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    settings = migrated_postgres_probe.settings

    async def seed() -> None:
        engine = create_database_engine(settings)
        try:
            service = job_service(create_database_session_factory(engine))
            await service.submit(submission(raw_key=None, max_attempts=2))
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(seed())
    before = _database_state(settings)
    assert before["jobs_count"] == before["outbox_events_count"] == 1
    assert "file_objects_count" in before
    assert "document_chunks_count" in before
    assert "tool_calls_count" in before
    restored = _backup_restore(settings)
    assert restored["source_sha256"] == before["database_sha256"]
    assert restored["restored_sha256"] == before["database_sha256"]
    assert _database_state(settings) == before
