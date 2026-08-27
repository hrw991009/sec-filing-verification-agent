"""Frozen and live adapters for official SEC submissions history."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, date, datetime

from industry_platform.modules.disclosures.adapters.sec_edgar import (
    OfficialSecJsonClient,
    SecResponseCache,
)
from industry_platform.modules.disclosures.domain import (
    SEC_MAX_SUBMISSIONS_RESPONSE_BYTES,
    SEC_SUBMISSIONS_URL_PREFIX,
    FilingSelectionScope,
    SecFilingForm,
    SecFilingObservation,
    SecSourceError,
    SecSourceErrorCode,
    SecSubmissionSet,
    SecSubmissionSourceKind,
    SecSubmissionSourceSnapshot,
    SecSupplementalDescriptor,
    normalize_cik,
    required_supplemental_descriptors,
    sec_submissions_current_url,
    sec_submissions_source_version,
    sha256_hex,
)

type SecResponseCacheFactory = Callable[[str], SecResponseCache]


class FrozenSecSubmissionsAdapter:
    def __init__(self, snapshot: SecSubmissionSet) -> None:
        self._snapshot = snapshot

    async def fetch_submission_set(self, scope: FilingSelectionScope) -> SecSubmissionSet:
        if scope.cik != self._snapshot.current.cik:
            raise SecSourceError(SecSourceErrorCode.COVERAGE_INCOMPLETE, retryable=False)
        expected = tuple(
            item.name
            for item in required_supplemental_descriptors(
                self._snapshot.current.descriptors,
                scope,
            )
        )
        if expected != self._snapshot.required_supplemental_names:
            raise SecSourceError(SecSourceErrorCode.COVERAGE_INCOMPLETE, retryable=False)
        return self._snapshot


class LiveSecSubmissionsAdapter:
    def __init__(
        self,
        client: OfficialSecJsonClient,
        cache_factory: SecResponseCacheFactory,
        *,
        cache_ttl_seconds: int = 3_600,
    ) -> None:
        self._client = client
        self._cache_factory = cache_factory
        self._cache_ttl_seconds = cache_ttl_seconds

    async def fetch_submission_set(self, scope: FilingSelectionScope) -> SecSubmissionSet:
        current_url = sec_submissions_current_url(scope.cik)
        current_response = await self._client.fetch(
            current_url,
            self._cache_factory(_cache_key(current_url)),
            cache_ttl_seconds=self._cache_ttl_seconds,
            maximum_bytes=SEC_MAX_SUBMISSIONS_RESPONSE_BYTES,
        )
        current = _parse_current(
            current_response.body,
            cik=scope.cik,
            retrieved_at=current_response.retrieved_at,
            source_available_at=_required_available_at(current_response.source_available_at),
        )
        required = required_supplemental_descriptors(current.descriptors, scope)
        supplementals: list[SecSubmissionSourceSnapshot] = []
        for descriptor in required:
            response = await self._client.fetch(
                descriptor.source_url,
                self._cache_factory(_cache_key(descriptor.source_url)),
                cache_ttl_seconds=self._cache_ttl_seconds,
                maximum_bytes=SEC_MAX_SUBMISSIONS_RESPONSE_BYTES,
            )
            supplementals.append(
                _parse_supplemental(
                    response.body,
                    cik=scope.cik,
                    descriptor=descriptor,
                    retrieved_at=response.retrieved_at,
                    source_available_at=_required_available_at(response.source_available_at),
                )
            )
        return SecSubmissionSet(
            current=current,
            supplementals=tuple(supplementals),
            required_supplemental_names=tuple(item.name for item in required),
        )


def _parse_current(
    body: bytes,
    *,
    cik: str,
    retrieved_at: datetime,
    source_available_at: datetime,
) -> SecSubmissionSourceSnapshot:
    document = _json_object(body)
    try:
        document_cik = normalize_cik(_required_int(document.get("cik")))
        if document_cik != cik:
            raise ValueError
        filings = _required_object(document.get("filings"))
        recent = _required_object(filings.get("recent"))
        raw_descriptors = filings.get("files")
        if not isinstance(raw_descriptors, list):
            raise ValueError
        descriptors = tuple(
            sorted(
                (_descriptor(item, cik=cik) for item in raw_descriptors),
                key=lambda item: item.name,
            )
        )
        source_name = f"CIK{cik}.json"
        return _source_snapshot(
            body,
            cik=cik,
            source_kind=SecSubmissionSourceKind.CURRENT,
            source_name=source_name,
            filings=_filing_rows(recent, cik=cik),
            descriptors=descriptors,
            retrieved_at=retrieved_at,
            source_available_at=source_available_at,
        )
    except (TypeError, ValueError):
        raise SecSourceError(SecSourceErrorCode.RESPONSE_INVALID, retryable=False) from None


def _parse_supplemental(
    body: bytes,
    *,
    cik: str,
    descriptor: SecSupplementalDescriptor,
    retrieved_at: datetime,
    source_available_at: datetime,
) -> SecSubmissionSourceSnapshot:
    document = _json_object(body)
    try:
        return _source_snapshot(
            body,
            cik=cik,
            source_kind=SecSubmissionSourceKind.SUPPLEMENTAL,
            source_name=descriptor.name,
            filings=_filing_rows(document, cik=cik),
            descriptors=(),
            retrieved_at=retrieved_at,
            source_available_at=source_available_at,
            filing_from=descriptor.filing_from,
            filing_to=descriptor.filing_to,
        )
    except (TypeError, ValueError):
        raise SecSourceError(SecSourceErrorCode.RESPONSE_INVALID, retryable=False) from None


def _source_snapshot(
    body: bytes,
    *,
    cik: str,
    source_kind: SecSubmissionSourceKind,
    source_name: str,
    filings: tuple[SecFilingObservation, ...],
    descriptors: tuple[SecSupplementalDescriptor, ...],
    retrieved_at: datetime,
    source_available_at: datetime,
    filing_from: date | None = None,
    filing_to: date | None = None,
) -> SecSubmissionSourceSnapshot:
    content_sha256 = sha256_hex(body)
    return SecSubmissionSourceSnapshot(
        cik=cik,
        source_kind=source_kind,
        source_name=source_name,
        source_url=f"{SEC_SUBMISSIONS_URL_PREFIX}{source_name}",
        source_version=sec_submissions_source_version(source_kind, content_sha256),
        content_sha256=content_sha256,
        retrieved_at=retrieved_at,
        source_available_at=source_available_at,
        body=body,
        filings=filings,
        filing_from=filing_from,
        filing_to=filing_to,
        descriptors=descriptors,
    )


def _filing_rows(document: dict[str, object], *, cik: str) -> tuple[SecFilingObservation, ...]:
    column_names = (
        "accessionNumber",
        "filingDate",
        "reportDate",
        "acceptanceDateTime",
        "form",
        "primaryDocument",
    )
    columns = {name: document.get(name) for name in column_names}
    if any(not isinstance(value, list) for value in columns.values()):
        raise ValueError("SEC submission columns are invalid")
    typed_columns = {name: value for name, value in columns.items() if isinstance(value, list)}
    lengths = {len(value) for value in typed_columns.values()}
    if len(lengths) != 1:
        raise ValueError("SEC submission column lengths are inconsistent")
    row_count = lengths.pop()
    if row_count > 100_000:
        raise ValueError("SEC submission row count is invalid")
    rows: list[SecFilingObservation] = []
    for index in range(row_count):
        raw_form = _required_text(typed_columns["form"][index], maximum=20)
        try:
            form = SecFilingForm(raw_form)
        except ValueError:
            continue
        accepted_at = _parse_datetime(typed_columns["acceptanceDateTime"][index])
        rows.append(
            SecFilingObservation(
                cik=cik,
                accession=_required_text(
                    typed_columns["accessionNumber"][index],
                    maximum=20,
                ),
                form=form,
                report_date=_parse_date(typed_columns["reportDate"][index]),
                filed_date=_parse_date(typed_columns["filingDate"][index]),
                accepted_at=accepted_at,
                primary_document=_required_text(
                    typed_columns["primaryDocument"][index],
                    maximum=255,
                ),
            )
        )
    return tuple(sorted(rows, key=lambda item: item.accession))


def _descriptor(value: object, *, cik: str) -> SecSupplementalDescriptor:
    document = _required_object(value)
    descriptor = SecSupplementalDescriptor(
        name=_required_text(document.get("name"), maximum=64),
        filing_from=_parse_date(document.get("filingFrom")),
        filing_to=_parse_date(document.get("filingTo")),
        filing_count=_required_int(document.get("filingCount")),
    )
    if not descriptor.name.startswith(f"CIK{cik}-"):
        raise ValueError("SEC supplemental CIK is invalid")
    return descriptor


def _json_object(body: bytes) -> dict[str, object]:
    try:
        document = json.loads(
            body.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_json_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        raise SecSourceError(SecSourceErrorCode.RESPONSE_INVALID, retryable=False) from None
    if not isinstance(document, dict):
        raise SecSourceError(SecSourceErrorCode.RESPONSE_INVALID, retryable=False)
    return document


def _required_object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError("SEC object is invalid")
    return value


def _required_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("SEC integer is invalid")
    return value


def _required_text(value: object, *, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > maximum
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError("SEC text is invalid")
    return " ".join(value.split())


def _parse_date(value: object) -> date:
    try:
        return date.fromisoformat(_required_text(value, maximum=10))
    except ValueError:
        raise ValueError("SEC date is invalid") from None


def _parse_datetime(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(_required_text(value, maximum=40))
    except ValueError:
        raise ValueError("SEC datetime is invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("SEC datetime is invalid")
    return parsed.astimezone(UTC)


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _cache_key(url: str) -> str:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return f"iip:sec:submissions:v1:{digest}"


def _required_available_at(value: datetime | None) -> datetime:
    if value is None:
        raise SecSourceError(SecSourceErrorCode.RESPONSE_INVALID, retryable=False)
    return value
