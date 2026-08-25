"""Versioned SEC fixture identity and hash tests."""

import json
from pathlib import Path

import pytest

from industry_platform.modules.retrieval.fixtures import load_sec_fixture_catalog

REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
MANIFEST = REPOSITORY_ROOT / "evals" / "fixtures" / "sec" / "sec-fixture-v1" / "manifest.json"


def test_checked_in_sec_fixture_loads_only_after_hash_verification() -> None:
    catalog = load_sec_fixture_catalog(MANIFEST, repository_root=REPOSITORY_ROOT)

    assert catalog.dataset_version == "sec-fixture-v1"
    assert len(catalog.filings) == 1
    filing = catalog.filings[0]
    assert filing.accession == "0000320193-23-000106"
    assert filing.content_sha256 == (
        "821b7526e6cde71b432dcc972c1ba8141db41bc73426200500d27d8635a19d2b"
    )
    assert {fact.key for fact in filing.facts} == {
        "net_sales_2023",
        "net_sales_2022",
        "net_income_2023",
        "net_income_2022",
    }


def test_fixture_loader_fails_closed_on_content_tampering(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture.md"
    fixture.write_text("tampered", encoding="utf-8")
    document = json.loads(MANIFEST.read_text(encoding="utf-8"))
    document["filings"][0]["fixture_path"] = "fixture.md"
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="hash"):
        load_sec_fixture_catalog(manifest, repository_root=tmp_path)
