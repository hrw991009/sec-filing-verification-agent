"""Controlled SEC bundle remains temporal, hashed, and runtime-ID independent."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from industry_platform.modules.disclosures.adapters.controlled import (
    load_controlled_sec_source_bundle,
)
from industry_platform.modules.disclosures.adapters.sec_archives import (
    FrozenSecFilingArchiveAdapter,
)
from industry_platform.modules.disclosures.adapters.sec_submissions import (
    FrozenSecSubmissionsAdapter,
)
from industry_platform.modules.disclosures.adapters.xbrl import (
    FrozenSecCompanyFactsAdapter,
    parse_companyfacts,
)
from industry_platform.modules.disclosures.domain import (
    FilingSelectionScope,
    SecAmendmentPolicy,
    SecFilingForm,
    SecSourceError,
)

MANIFEST = Path("evals/fixtures/sec/sec-browser-v1/manifest.json")


def scope(as_of: datetime) -> FilingSelectionScope:
    return FilingSelectionScope(
        cik="0000320193",
        allowed_forms=(SecFilingForm.TEN_K,),
        report_period_start=date(2023, 1, 1),
        report_period_end=date(2024, 12, 31),
        as_of=as_of,
        amendment_policy=SecAmendmentPolicy.AS_FILED,
    )


@pytest.mark.asyncio
async def test_controlled_source_selects_point_in_time_snapshots_and_runtime_filing_ids() -> None:
    bundle = load_controlled_sec_source_bundle(MANIFEST)
    submissions = FrozenSecSubmissionsAdapter(bundle.submissions)

    snapshot_2023 = await submissions.fetch_submission_set(scope(datetime(2023, 11, 4, tzinfo=UTC)))
    snapshot_2024 = await submissions.fetch_submission_set(scope(datetime(2025, 1, 1, tzinfo=UTC)))
    assert tuple(item.accession for item in snapshot_2023.filings) == ("0000320193-23-000106",)
    assert tuple(item.accession for item in snapshot_2024.filings) == (
        "0000320193-23-000106",
        "0000320193-24-000123",
    )
    with pytest.raises(SecSourceError):
        await submissions.fetch_submission_set(scope(datetime(2023, 1, 1, tzinfo=UTC)))

    frozen_archive = bundle.archives["0000320193-23-000106"]
    runtime_filing = replace(
        frozen_archive.filing,
        id=uuid4(),
        source_available_at=frozen_archive.filing.source_available_at + timedelta(days=365),
    )
    restored = await FrozenSecFilingArchiveAdapter(bundle.archives).fetch_archive(runtime_filing)
    assert restored.filing == runtime_filing
    assert restored.documents == frozen_archive.documents

    companyfacts = FrozenSecCompanyFactsAdapter(bundle.companyfacts)
    source = await companyfacts.fetch(runtime_filing)
    parsed = parse_companyfacts(source, runtime_filing)
    assert tuple(fact.value for fact in parsed.facts) == ("383285000000",)
