"""Contract tests for current plus supplemental SEC submissions coverage."""

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime

import httpx2
import pytest

from industry_platform.modules.disclosures.adapters.sec_edgar import (
    CachedSecResponse,
    OfficialSecJsonClient,
)
from industry_platform.modules.disclosures.adapters.sec_submissions import (
    LiveSecSubmissionsAdapter,
)
from industry_platform.modules.disclosures.domain import (
    FilingSelectionScope,
    SecAmendmentPolicy,
    SecFilingForm,
    SecSourceError,
    SecSourceErrorCode,
)

NOW = datetime(2026, 8, 26, 4, 0, tzinfo=UTC)
SOURCE_AVAILABLE = "Wed, 26 Aug 2026 03:00:00 GMT"
CIK = "0000320193"
CURRENT_URL = f"https://data.sec.gov/submissions/CIK{CIK}.json"
SUPPLEMENTAL_NAME = f"CIK{CIK}-submissions-001.json"
SUPPLEMENTAL_URL = f"https://data.sec.gov/submissions/{SUPPLEMENTAL_NAME}"
USER_AGENT = "IndustryIntelligencePlatform/0.1 edgar-ops@example.test"


@dataclass(slots=True)
class CountingBudget:
    calls: int = 0

    async def acquire(self) -> None:
        self.calls += 1


@dataclass(slots=True)
class MemoryCache:
    value: CachedSecResponse | None = None

    async def get(self) -> CachedSecResponse | None:
        return self.value

    async def put(self, value: CachedSecResponse) -> None:
        self.value = value


def _columns(rows: list[dict[str, object]]) -> dict[str, list[object]]:
    return {
        name: [row[name] for row in rows]
        for name in (
            "accessionNumber",
            "filingDate",
            "reportDate",
            "acceptanceDateTime",
            "form",
            "primaryDocument",
        )
    }


def current_body(*, inconsistent: bool = False) -> bytes:
    recent = _columns(
        [
            {
                "accessionNumber": "0000320193-25-000001",
                "filingDate": "2025-01-31",
                "reportDate": "2024-12-28",
                "acceptanceDateTime": "2025-01-31T18:00:00Z",
                "form": "10-K",
                "primaryDocument": "aapl-20241228.htm",
            },
            {
                "accessionNumber": "0000320193-25-000002",
                "filingDate": "2025-02-15",
                "reportDate": "2024-12-28",
                "acceptanceDateTime": "2025-02-15T18:00:00Z",
                "form": "10-K/A",
                "primaryDocument": "aapl-20241228x10ka.htm",
            },
            {
                "accessionNumber": "0000320193-25-000003",
                "filingDate": "2025-03-01",
                "reportDate": "2025-03-01",
                "acceptanceDateTime": "2025-03-01T18:00:00Z",
                "form": "8-K",
                "primaryDocument": "aapl-20250301.htm",
            },
        ]
    )
    if inconsistent:
        recent["form"].pop()
    return json.dumps(
        {
            "cik": 320193,
            "filings": {
                "recent": recent,
                "files": [
                    {
                        "name": SUPPLEMENTAL_NAME,
                        "filingCount": 1,
                        "filingFrom": "2023-01-01",
                        "filingTo": "2024-01-31",
                    }
                ],
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")


def supplemental_body() -> bytes:
    return json.dumps(
        _columns(
            [
                {
                    "accessionNumber": "0000320193-24-000001",
                    "filingDate": "2024-01-30",
                    "reportDate": "2023-12-30",
                    "acceptanceDateTime": "2024-01-30T18:00:00Z",
                    "form": "10-K",
                    "primaryDocument": "aapl-20231230.htm",
                }
            ]
        ),
        separators=(",", ":"),
    ).encode("utf-8")


def scope() -> FilingSelectionScope:
    return FilingSelectionScope(
        cik=CIK,
        allowed_forms=tuple(sorted(SecFilingForm, key=lambda item: item.value)),
        report_period_start=date(2023, 1, 1),
        report_period_end=date(2025, 12, 31),
        as_of=NOW,
        amendment_policy=SecAmendmentPolicy.AS_FILED,
    )


@pytest.mark.asyncio
async def test_live_adapter_loads_required_supplemental_and_deduplicates_supported_forms() -> None:
    requests: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(str(request.url))
        body = current_body() if str(request.url) == CURRENT_URL else supplemental_body()
        return httpx2.Response(
            200,
            headers={"content-type": "application/json", "last-modified": SOURCE_AVAILABLE},
            content=body,
        )

    caches: dict[str, MemoryCache] = {}
    budget = CountingBudget()
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler),
        trust_env=False,
    ) as client:
        adapter = LiveSecSubmissionsAdapter(
            OfficialSecJsonClient(
                client,
                budget,
                user_agent=USER_AGENT,
                clock=lambda: NOW,
                maximum_attempts=1,
            ),
            lambda key: caches.setdefault(key, MemoryCache()),
        )
        result = await adapter.fetch_submission_set(scope())

    assert requests == [CURRENT_URL, SUPPLEMENTAL_URL]
    assert budget.calls == 2
    assert result.required_supplemental_names == (SUPPLEMENTAL_NAME,)
    assert [filing.accession for filing in result.filings] == [
        "0000320193-24-000001",
        "0000320193-25-000001",
        "0000320193-25-000002",
    ]
    assert all(filing.form is not None for filing in result.filings)


@pytest.mark.asyncio
async def test_inconsistent_current_columns_fail_as_response_invalid() -> None:
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(
            lambda _request: httpx2.Response(
                200,
                headers={"content-type": "application/json", "last-modified": SOURCE_AVAILABLE},
                content=current_body(inconsistent=True),
            )
        ),
        trust_env=False,
    ) as client:
        adapter = LiveSecSubmissionsAdapter(
            OfficialSecJsonClient(
                client,
                CountingBudget(),
                user_agent=USER_AGENT,
                clock=lambda: NOW,
                maximum_attempts=1,
            ),
            lambda _key: MemoryCache(),
        )
        with pytest.raises(SecSourceError) as caught:
            await adapter.fetch_submission_set(scope())

    assert caught.value.code is SecSourceErrorCode.RESPONSE_INVALID
