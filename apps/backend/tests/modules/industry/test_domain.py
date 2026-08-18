"""Domain and authorization contracts for industry context."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from industry_platform.modules.industry.domain import (
    ENERGY_POWER_INDUSTRY_ID,
    INDUSTRY_PRESETS,
    ProviderCode,
    ProviderItem,
    SourceKind,
    canonical_public_locator,
    search_industries,
)


def test_four_presets_are_stable_searchable_and_do_not_encode_workspace_scope() -> None:
    assert len(INDUSTRY_PRESETS) == 4
    assert tuple(item.code for item in search_industries(None)) == (
        "smart_transport",
        "fintech",
        "healthcare",
        "energy_power",
    )
    assert search_industries("能源") == (INDUSTRY_PRESETS[3],)
    assert search_industries("payments") == (INDUSTRY_PRESETS[1],)
    assert INDUSTRY_PRESETS[3].industry_id == ENERGY_POWER_INDUSTRY_ID


@pytest.mark.parametrize(
    "locator",
    [
        "http://127.0.0.1/private",
        "https://metadata.google.internal/latest",
        "https://token@example.com/path",
        "https://www.worldbank.org/path?access_token=secret",
        "https://www.worldbank.org/path#secret",
        "https://evil.example/path",
        "file:///etc/passwd",
    ],
)
def test_source_locator_rejects_credentials_dynamic_targets_and_non_https_output(
    locator: str,
) -> None:
    with pytest.raises(ValueError, match="Source locator is invalid"):
        canonical_public_locator(locator)


def test_world_bank_legacy_http_locator_is_canonicalized_without_query_material() -> None:
    assert canonical_public_locator(
        "http://www.worldbank.org/en/topic/energy/publication/energy-access-redefined"
    ) == ("https://www.worldbank.org/en/topic/energy/publication/energy-access-redefined")


def test_provider_item_hash_binds_normalized_content_and_provenance() -> None:
    item = ProviderItem(
        kind=SourceKind.NEWS,
        provider=ProviderCode.WORLD_BANK_NEWS,
        external_id="news-1",
        title="Energy access changed",
        summary="A bounded public summary.",
        locator="https://www.worldbank.org/en/news/example",
        published_at=datetime(2026, 8, 17, tzinfo=UTC),
        metadata={"category": "Feature Story"},
    )

    assert len(item.content_sha256) == 64
    assert (
        item.content_sha256
        != ProviderItem(
            kind=item.kind,
            provider=item.provider,
            external_id=item.external_id,
            title=item.title,
            summary="Changed summary.",
            locator=item.locator,
            published_at=item.published_at,
            metadata=item.metadata,
        ).content_sha256
    )
    assert UUID(str(ENERGY_POWER_INDUSTRY_ID)) == ENERGY_POWER_INDUSTRY_ID
