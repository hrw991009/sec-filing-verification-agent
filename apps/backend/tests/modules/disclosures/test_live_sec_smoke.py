"""The live SEC smoke artifact must be useful without exposing its contact identity."""

import json
from datetime import UTC, datetime, timedelta

from industry_platform.modules.disclosures.adapters.sec_edgar import CachedSecResponse
from industry_platform.modules.disclosures.domain import sha256_hex
from industry_platform.modules.disclosures.live_sec_smoke import (
    build_live_sec_identity_record,
)


def test_live_sec_identity_record_contains_provenance_but_no_contact_email() -> None:
    retrieved_at = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
    body = json.dumps({"cik": "0000320193", "name": "Apple Inc."}).encode()
    response = CachedSecResponse(
        body=body,
        retrieved_at=retrieved_at,
        fresh_until=retrieved_at + timedelta(minutes=1),
        source_available_at=retrieved_at - timedelta(minutes=1),
    )

    record = build_live_sec_identity_record(
        response,
        cik="0000320193",
        requests_per_second=8,
    )

    assert record["live_sec_executed"] is True
    assert record["identity_configured"] is True
    assert record["entity_name"] == "Apple Inc."
    assert record["content_sha256"] == sha256_hex(body)
    assert "@" not in json.dumps(record)
