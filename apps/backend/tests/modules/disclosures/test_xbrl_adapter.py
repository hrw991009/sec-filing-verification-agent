"""Source-typed aggregate and raw XBRL parsing contracts."""

import json
from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from industry_platform.modules.disclosures.adapters.xbrl import (
    parse_companyfacts,
    parse_raw_xbrl,
)
from industry_platform.modules.disclosures.domain import (
    SecCanonicalFiling,
    SecFilingForm,
    SecSourceError,
    SecSourceErrorCode,
    SecXbrlPeriodKind,
    SecXbrlSourceKind,
    SecXbrlSourceSnapshot,
    sec_companyfacts_url,
    sec_filing_document_url,
    sec_xbrl_source_version,
    sha256_hex,
)

NOW = datetime(2026, 8, 27, 2, 0, tzinfo=UTC)
AVAILABLE = datetime(2023, 11, 3, 18, 1, tzinfo=UTC)
SNAPSHOT_ID = UUID("11111111-1111-4111-8111-111111111111")


def filing() -> SecCanonicalFiling:
    return SecCanonicalFiling(
        id=UUID("22222222-2222-4222-8222-222222222222"),
        cik="0000320193",
        accession="0000320193-23-000106",
        form=SecFilingForm.TEN_K,
        report_date=date(2023, 9, 30),
        filed_date=date(2023, 11, 3),
        accepted_at=AVAILABLE,
        public_available_at=AVAILABLE,
        primary_document="aapl-20230930.htm",
        source_available_at=AVAILABLE,
    )


def source(
    body: bytes,
    kind: SecXbrlSourceKind,
    *,
    filename: str | None = None,
) -> SecXbrlSourceSnapshot:
    canonical = filing()
    content_sha256 = sha256_hex(body)
    aggregate = kind is SecXbrlSourceKind.COMPANYFACTS_AGGREGATE
    return SecXbrlSourceSnapshot(
        source_kind=kind,
        cik=canonical.cik,
        source_url=(
            sec_companyfacts_url(canonical.cik)
            if aggregate
            else sec_filing_document_url(
                canonical.cik,
                canonical.accession,
                filename or "aapl-20230930_htm.xml",
            )
        ),
        source_version=sec_xbrl_source_version(kind, content_sha256),
        content_type=(
            "application/json"
            if aggregate
            else ("application/xml" if kind is SecXbrlSourceKind.RAW_INSTANCE else "text/html")
        ),
        content_sha256=content_sha256,
        byte_size=len(body),
        retrieved_at=NOW,
        source_available_at=AVAILABLE,
        body=body,
        filing_snapshot_id=None if aggregate else SNAPSHOT_ID,
    )


def test_companyfacts_keeps_only_locked_accession_and_has_aggregate_locator() -> None:
    canonical = filing()
    body = json.dumps(
        {
            "cik": 320193,
            "entityName": "Apple Inc.",
            "facts": {
                "us-gaap": {
                    "RevenueFromContractWithCustomerExcludingAssessedTax": {
                        "label": "Revenue",
                        "description": "Revenue",
                        "units": {
                            "USD": [
                                {
                                    "accn": canonical.accession,
                                    "filed": "2023-11-03",
                                    "form": "10-K",
                                    "start": "2022-09-25",
                                    "end": "2023-09-30",
                                    "val": 383285000000,
                                },
                                {
                                    "accn": "0000320193-22-000108",
                                    "filed": "2022-10-28",
                                    "form": "10-K",
                                    "start": "2021-09-26",
                                    "end": "2022-09-24",
                                    "val": 394328000000,
                                },
                            ]
                        },
                    }
                }
            },
        },
        separators=(",", ":"),
    ).encode()

    batch = parse_companyfacts(
        source(body, SecXbrlSourceKind.COMPANYFACTS_AGGREGATE),
        canonical,
    )

    assert batch.contexts == ()
    assert len(batch.facts) == 1
    fact = batch.facts[0]
    assert fact.taxonomy == "us-gaap"
    assert fact.value == "383285000000"
    assert fact.period.kind is SecXbrlPeriodKind.DURATION
    assert canonical.accession in fact.locator_key
    assert fact.context_id is None
    assert fact.is_custom is False


def test_raw_instance_preserves_context_dimensions_unit_and_custom_tag() -> None:
    body = b"""<?xml version="1.0" encoding="UTF-8"?>
<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"
 xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
 xmlns:iso4217="http://www.xbrl.org/2003/iso4217"
 xmlns:aapl="https://www.apple.com/20230930">
 <xbrli:context id="D2023">
  <xbrli:entity>
   <xbrli:identifier scheme="http://www.sec.gov/CIK">0000320193</xbrli:identifier>
   <xbrli:segment>
    <xbrldi:explicitMember dimension="aapl:BusinessAxis">
     aapl:ProductsMember
    </xbrldi:explicitMember>
   </xbrli:segment>
  </xbrli:entity>
  <xbrli:period>
   <xbrli:startDate>2022-09-25</xbrli:startDate>
   <xbrli:endDate>2023-09-30</xbrli:endDate>
  </xbrli:period>
 </xbrli:context>
 <xbrli:unit id="USD"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
 <aapl:RecurringRevenue contextRef="D2023" unitRef="USD" decimals="-6" scale="0">
  125000000
 </aapl:RecurringRevenue>
</xbrli:xbrl>"""

    batch = parse_raw_xbrl(
        source(body, SecXbrlSourceKind.RAW_INSTANCE),
        filing(),
    )

    assert len(batch.contexts) == 1
    assert batch.contexts[0].dimensions == (("aapl:BusinessAxis", "aapl:ProductsMember"),)
    assert len(batch.facts) == 1
    fact = batch.facts[0]
    assert fact.taxonomy == "aapl"
    assert fact.concept == "RecurringRevenue"
    assert fact.unit == "iso4217:USD"
    assert fact.context_id == "D2023"
    assert fact.decimals == "-6"
    assert fact.is_custom is True


def test_inline_xbrl_preserves_format_and_continuation() -> None:
    body = b"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"
 xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
 xmlns:xbrli="http://www.xbrl.org/2003/instance"
 xmlns:dei="http://xbrl.sec.gov/dei/2023">
 <body>
  <ix:resources>
   <xbrli:context id="I2023">
    <xbrli:entity>
     <xbrli:identifier scheme="http://www.sec.gov/CIK">0000320193</xbrli:identifier>
    </xbrli:entity>
    <xbrli:period><xbrli:instant>2023-09-30</xbrli:instant></xbrli:period>
   </xbrli:context>
  </ix:resources>
  <ix:nonNumeric name="dei:EntityRegistrantName" contextRef="I2023"
   continuedAt="name-tail">Apple<ix:exclude>ignored</ix:exclude></ix:nonNumeric>
  <ix:continuation id="name-tail"> Inc.</ix:continuation>
 </body>
</html>"""

    batch = parse_raw_xbrl(
        source(body, SecXbrlSourceKind.RAW_INLINE, filename="aapl-20230930.htm"),
        filing(),
    )

    assert len(batch.facts) == 1
    assert batch.facts[0].value == "Apple Inc."
    assert batch.facts[0].taxonomy == "dei"
    assert batch.facts[0].unit is None


def test_raw_xbrl_rejects_doctype_before_fact_extraction() -> None:
    body = b"""<?xml version="1.0"?>
<!DOCTYPE xbrl [<!ENTITY unsafe "unsafe">]>
<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"></xbrli:xbrl>"""

    with pytest.raises(SecSourceError) as caught:
        parse_raw_xbrl(source(body, SecXbrlSourceKind.RAW_INSTANCE), filing())

    assert caught.value.code is SecSourceErrorCode.RESPONSE_INVALID
