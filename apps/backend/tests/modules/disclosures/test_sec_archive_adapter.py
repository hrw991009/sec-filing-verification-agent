"""Bounded official SEC accession archive adapter contracts."""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID

import httpx2
import pytest

from industry_platform.modules.disclosures.adapters import sec_archives
from industry_platform.modules.disclosures.adapters.sec_archives import (
    LiveSecFilingArchiveAdapter,
)
from industry_platform.modules.disclosures.domain import (
    SEC_MAX_ARCHIVE_ATTACHMENTS,
    SEC_MAX_ARCHIVE_DOCUMENT_BYTES,
    SecCanonicalFiling,
    SecFilingDocumentKind,
    SecFilingForm,
    SecSourceError,
    SecSourceErrorCode,
    sec_complete_submission_url,
    sec_filing_document_url,
    sec_primary_document_url,
)

NOW = datetime(2026, 8, 26, 4, 0, tzinfo=UTC)
ACCEPTED = datetime(2023, 11, 3, 18, 1, tzinfo=UTC)
USER_AGENT = "IndustryIntelligencePlatform/0.1 edgar-ops@example.test"


def filing() -> SecCanonicalFiling:
    return SecCanonicalFiling(
        id=UUID("11111111-1111-4111-8111-111111111111"),
        cik="0000320193",
        accession="0000320193-23-000106",
        form=SecFilingForm.TEN_K,
        report_date=date(2023, 9, 30),
        filed_date=date(2023, 11, 3),
        accepted_at=ACCEPTED,
        public_available_at=ACCEPTED,
        primary_document="aapl-20230930.htm",
        source_available_at=ACCEPTED,
    )


@dataclass(slots=True)
class CountingBudget:
    calls: int = 0

    async def acquire(self) -> None:
        self.calls += 1


@pytest.mark.asyncio
async def test_archive_fetches_only_complete_and_primary_documents_with_shared_budget() -> None:
    canonical = filing()
    complete_url = sec_complete_submission_url(canonical.cik, canonical.accession)
    primary_url = sec_primary_document_url(
        canonical.cik,
        canonical.accession,
        canonical.primary_document,
    )
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if str(request.url) == complete_url:
            return httpx2.Response(
                200,
                headers={
                    "content-type": "text/plain",
                    "last-modified": "Fri, 03 Nov 2023 00:00:00 GMT",
                },
                content=b"<SEC-DOCUMENT>complete</SEC-DOCUMENT>",
            )
        assert str(request.url) == primary_url
        return httpx2.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=b"<html><p>Primary filing</p></html>",
        )

    budget = CountingBudget()
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler),
        trust_env=False,
    ) as client:
        archive = await LiveSecFilingArchiveAdapter(
            client,
            budget,
            user_agent=USER_AGENT,
            clock=lambda: NOW,
        ).fetch_archive(canonical)

    assert {str(request.url) for request in requests} == {complete_url, primary_url}
    assert all(request.headers["user-agent"] == USER_AGENT for request in requests)
    assert budget.calls == 2
    assert archive.document(SecFilingDocumentKind.COMPLETE_SUBMISSION).filename.endswith(".txt")
    assert archive.document(SecFilingDocumentKind.PRIMARY_DOCUMENT).body.startswith(b"<html>")
    assert all(source.source_available_at >= ACCEPTED for source in archive.documents)


@pytest.mark.asyncio
async def test_archive_discovers_and_fetches_only_xbrl_documents_declared_by_submission() -> None:
    canonical = filing()
    complete_url = sec_complete_submission_url(canonical.cik, canonical.accession)
    primary_url = sec_primary_document_url(
        canonical.cik,
        canonical.accession,
        canonical.primary_document,
    )
    instance_url = sec_filing_document_url(
        canonical.cik,
        canonical.accession,
        "aapl-20230930_htm.xml",
    )
    schema_url = sec_filing_document_url(
        canonical.cik,
        canonical.accession,
        "aapl-20230930.xsd",
    )
    complete = b"""<SEC-DOCUMENT>
<DOCUMENT>
<TYPE>10-K
<FILENAME>aapl-20230930.htm
<TEXT><html>primary</html>
</DOCUMENT>
<DOCUMENT>
<TYPE>EX-101.INS
<FILENAME>aapl-20230930_htm.xml
<TEXT><xbrl>instance</xbrl>
</DOCUMENT>
<DOCUMENT>
<TYPE>EX-101.SCH
<FILENAME>aapl-20230930.xsd
<TEXT><schema>taxonomy</schema>
</DOCUMENT>
<DOCUMENT>
<TYPE>EX-99.1
<FILENAME>unrelated-exhibit.htm
<TEXT><html>unrelated</html>
</DOCUMENT>"""
    requests: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        requests.append(url)
        if url == complete_url:
            return httpx2.Response(200, headers={"content-type": "text/plain"}, content=complete)
        if url == primary_url:
            return httpx2.Response(
                200,
                headers={"content-type": "text/html"},
                content=b"<html>primary</html>",
            )
        if url == instance_url:
            return httpx2.Response(
                200,
                headers={"content-type": "application/xml"},
                content=b"<xbrl>instance</xbrl>",
            )
        assert url == schema_url
        return httpx2.Response(
            200,
            headers={"content-type": "text/xml"},
            content=b"<schema>taxonomy</schema>",
        )

    budget = CountingBudget()
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler),
        trust_env=False,
    ) as client:
        archive = await LiveSecFilingArchiveAdapter(
            client,
            budget,
            user_agent=USER_AGENT,
            clock=lambda: NOW,
        ).fetch_archive(canonical)

    assert set(requests) == {complete_url, primary_url, instance_url, schema_url}
    assert budget.calls == 4
    assert archive.document(SecFilingDocumentKind.XBRL_INSTANCE).filename.endswith("_htm.xml")
    assert [
        document.filename
        for document in archive.documents
        if document.kind is SecFilingDocumentKind.XBRL_ATTACHMENT
    ] == ["aapl-20230930.xsd"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "complete",
    [
        b"<DOCUMENT>\n<TYPE>EX-101.INS\n<FILENAME>../escape.xml\n<TEXT>x",
        (
            b"<DOCUMENT>\n<TYPE>EX-101.INS\n<FILENAME>one.xml\n<TEXT>x"
            b"<DOCUMENT>\n<TYPE>EX-101.INS\n<FILENAME>two.xml\n<TEXT>x"
        ),
        b"".join(
            f"<DOCUMENT>\n<TYPE>EX-101.LAB\n<FILENAME>label-{index}.xml\n<TEXT>x".encode()
            for index in range(SEC_MAX_ARCHIVE_ATTACHMENTS + 1)
        ),
    ],
)
async def test_invalid_xbrl_manifest_fails_before_related_document_fetch(
    complete: bytes,
) -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            headers={"content-type": "text/plain"},
            content=complete,
        )

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler),
        trust_env=False,
    ) as client:
        with pytest.raises(SecSourceError) as caught:
            await LiveSecFilingArchiveAdapter(
                client,
                CountingBudget(),
                user_agent=USER_AGENT,
                clock=lambda: NOW,
            ).fetch_archive(filing())

    assert caught.value.code is SecSourceErrorCode.RESPONSE_INVALID
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_archive_enforces_remaining_total_budget_before_reading_next_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sec_archives, "SEC_MAX_ARCHIVE_TOTAL_BYTES", 64)
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        content_type = "text/plain" if len(requests) == 1 else "text/html"
        return httpx2.Response(
            200,
            headers={"content-type": content_type, "content-length": "40"},
            content=b"x" * 40,
        )

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler),
        trust_env=False,
    ) as client:
        with pytest.raises(SecSourceError) as caught:
            await LiveSecFilingArchiveAdapter(
                client,
                CountingBudget(),
                user_agent=USER_AGENT,
                clock=lambda: NOW,
                maximum_attempts=1,
            ).fetch_archive(filing())

    assert caught.value.code is SecSourceErrorCode.RESPONSE_TOO_LARGE
    assert len(requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "expected_code"),
    [
        (
            httpx2.Response(302, headers={"location": "https://example.com"}),
            SecSourceErrorCode.REDIRECT_REJECTED,
        ),
        (
            httpx2.Response(200, headers={"content-type": "application/json"}, content=b"{}"),
            SecSourceErrorCode.CONTENT_TYPE_INVALID,
        ),
        (
            httpx2.Response(
                200,
                headers={
                    "content-type": "text/plain",
                    "content-length": str(SEC_MAX_ARCHIVE_DOCUMENT_BYTES + 1),
                },
            ),
            SecSourceErrorCode.RESPONSE_TOO_LARGE,
        ),
    ],
)
async def test_untrusted_archive_responses_fail_closed(
    response: httpx2.Response,
    expected_code: SecSourceErrorCode,
) -> None:
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(
            lambda _request: httpx2.Response(
                response.status_code,
                headers=response.headers,
                content=response.content,
            )
        ),
        trust_env=False,
    ) as client:
        with pytest.raises(SecSourceError) as caught:
            await LiveSecFilingArchiveAdapter(
                client,
                CountingBudget(),
                user_agent=USER_AGENT,
                clock=lambda: NOW,
                maximum_attempts=1,
            ).fetch_archive(filing())

    assert caught.value.code is expected_code
