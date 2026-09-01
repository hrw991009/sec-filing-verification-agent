"""Bounded official companyfacts access and raw XBRL fact extraction."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Final
from xml.parsers import expat

from industry_platform.modules.disclosures.adapters.sec_edgar import (
    OfficialSecJsonClient,
    SecResponseCache,
)
from industry_platform.modules.disclosures.domain import (
    SEC_MAX_XBRL_CONTEXTS,
    SEC_MAX_XBRL_FACTS,
    SEC_MAX_XBRL_RESPONSE_BYTES,
    SecCanonicalFiling,
    SecFilingForm,
    SecSourceError,
    SecSourceErrorCode,
    SecXbrlContextData,
    SecXbrlFactData,
    SecXbrlPeriod,
    SecXbrlPeriodKind,
    SecXbrlSourceBatch,
    SecXbrlSourceKind,
    SecXbrlSourceSnapshot,
    normalize_cik,
    sec_companyfacts_url,
    sec_xbrl_source_version,
    sha256_hex,
)

_INLINE_NAMESPACE: Final = "http://www.xbrl.org/2013/inlineXBRL"
_XBRLI_NAMESPACE: Final = "http://www.xbrl.org/2003/instance"
_XBRLDI_NAMESPACE: Final = "http://xbrl.org/2006/xbrldi"
_STANDARD_TAXONOMIES: Final = frozenset(
    {
        "cef",
        "country",
        "currency",
        "dei",
        "exch",
        "ifrs-full",
        "invest",
        "naics",
        "sic",
        "srt",
        "stpr",
        "us-gaap",
        "vip",
    }
)
_MAX_XML_ELEMENTS: Final = 1_000_000
_MAX_CONTINUATION_DEPTH: Final = 100


class FrozenSecCompanyFactsAdapter:
    def __init__(self, source: SecXbrlSourceSnapshot) -> None:
        if source.source_kind is not SecXbrlSourceKind.COMPANYFACTS_AGGREGATE:
            raise ValueError("Frozen companyfacts source is invalid")
        self._source = source

    async def fetch(self, filing: SecCanonicalFiling) -> SecXbrlSourceSnapshot:
        if filing.cik != self._source.cik:
            raise SecSourceError(SecSourceErrorCode.RESPONSE_INVALID, retryable=False)
        return self._source

    async def fetch_after(
        self,
        filing: SecCanonicalFiling,
        *,
        watermark: datetime,
    ) -> SecXbrlSourceSnapshot:
        del watermark
        return await self.fetch(filing)


class UnavailableSecCompanyFactsAdapter:
    async def fetch(self, filing: SecCanonicalFiling) -> SecXbrlSourceSnapshot:
        del filing
        raise SecSourceError(SecSourceErrorCode.NOT_CONFIGURED, retryable=False)

    async def fetch_after(
        self,
        filing: SecCanonicalFiling,
        *,
        watermark: datetime,
    ) -> SecXbrlSourceSnapshot:
        del watermark
        return await self.fetch(filing)


class LiveSecCompanyFactsAdapter:
    def __init__(
        self,
        client: OfficialSecJsonClient,
        cache_factory: Callable[[str], SecResponseCache],
        *,
        cache_ttl_seconds: int = 3_600,
    ) -> None:
        self._client = client
        self._cache_factory = cache_factory
        self._cache_ttl_seconds = cache_ttl_seconds

    async def fetch(self, filing: SecCanonicalFiling) -> SecXbrlSourceSnapshot:
        return await self._fetch(filing, cache_scope="ordinary")

    async def fetch_after(
        self,
        filing: SecCanonicalFiling,
        *,
        watermark: datetime,
    ) -> SecXbrlSourceSnapshot:
        if watermark.tzinfo is None or watermark.utcoffset() is None:
            raise ValueError("SEC post-watermark boundary must be timezone-aware")
        return await self._fetch(
            filing,
            cache_scope=f"post-{int(watermark.timestamp())}",
        )

    async def _fetch(
        self,
        filing: SecCanonicalFiling,
        *,
        cache_scope: str,
    ) -> SecXbrlSourceSnapshot:
        url = sec_companyfacts_url(filing.cik)
        response = await self._client.fetch(
            url,
            self._cache_factory(f"iip:sec:xbrl:companyfacts:{filing.cik}:v1:{cache_scope}"),
            cache_ttl_seconds=self._cache_ttl_seconds,
            maximum_bytes=SEC_MAX_XBRL_RESPONSE_BYTES,
        )
        content_sha256 = sha256_hex(response.body)
        return SecXbrlSourceSnapshot(
            source_kind=SecXbrlSourceKind.COMPANYFACTS_AGGREGATE,
            cik=filing.cik,
            source_url=url,
            source_version=sec_xbrl_source_version(
                SecXbrlSourceKind.COMPANYFACTS_AGGREGATE,
                content_sha256,
            ),
            content_type="application/json",
            content_sha256=content_sha256,
            byte_size=len(response.body),
            retrieved_at=response.retrieved_at,
            source_available_at=response.source_available_at or response.retrieved_at,
            body=response.body,
        )


def validate_companyfacts_bulk_entry(body: bytes, *, cik: str) -> None:
    """Validate one companyfacts bulk member before accepting its watermark."""

    document = _json_object(body)
    try:
        if normalize_cik(_required_scalar(document.get("cik"))) != cik:
            raise ValueError
        _required_object(document.get("facts"))
    except (TypeError, ValueError):
        raise SecSourceError(SecSourceErrorCode.RESPONSE_INVALID, retryable=False) from None


def parse_companyfacts(
    source: SecXbrlSourceSnapshot,
    filing: SecCanonicalFiling,
) -> SecXbrlSourceBatch:
    if (
        source.source_kind is not SecXbrlSourceKind.COMPANYFACTS_AGGREGATE
        or source.cik != filing.cik
    ):
        raise ValueError("SEC aggregate XBRL source does not match filing")
    document = _json_object(source.body)
    try:
        if normalize_cik(_required_scalar(document.get("cik"))) != filing.cik:
            raise ValueError
        raw_taxonomies = _required_object(document.get("facts"))
        facts: list[SecXbrlFactData] = []
        ordinal = 0
        for taxonomy in sorted(raw_taxonomies):
            concepts = _required_object(raw_taxonomies[taxonomy])
            for concept in sorted(concepts):
                concept_document = _required_object(concepts[concept])
                units = _required_object(concept_document.get("units"))
                for unit in sorted(units):
                    entries = units[unit]
                    if not isinstance(entries, list):
                        raise ValueError
                    for entry in entries:
                        row = _required_object(entry)
                        if row.get("accn") != filing.accession:
                            continue
                        if ordinal >= SEC_MAX_XBRL_FACTS:
                            raise ValueError
                        form = SecFilingForm(_required_text(row.get("form"), maximum=20))
                        filed_date = _required_date(row.get("filed"))
                        if form is not filing.form or filed_date != filing.filed_date:
                            raise ValueError
                        period = _aggregate_period(row)
                        facts.append(
                            SecXbrlFactData(
                                source_version=source.source_version,
                                locator_key=(
                                    f"aggregate:{filing.accession}:{taxonomy}:{concept}:{unit}:"
                                    f"{period.key}:{ordinal}"
                                ),
                                taxonomy=taxonomy,
                                concept=concept,
                                value=_fact_value(row.get("val")),
                                unit=unit,
                                period=period,
                                filed_date=filed_date,
                                form=form,
                                context_id=None,
                                dimensions=(),
                                decimals=None,
                                scale=None,
                                format=None,
                                is_custom=taxonomy not in _STANDARD_TAXONOMIES,
                                ordinal=ordinal,
                            )
                        )
                        ordinal += 1
        return SecXbrlSourceBatch(source=source, contexts=(), facts=tuple(facts))
    except (KeyError, TypeError, ValueError):
        raise SecSourceError(SecSourceErrorCode.RESPONSE_INVALID, retryable=False) from None


def parse_raw_xbrl(
    source: SecXbrlSourceSnapshot,
    filing: SecCanonicalFiling,
) -> SecXbrlSourceBatch:
    if (
        source.source_kind
        not in {
            SecXbrlSourceKind.RAW_INLINE,
            SecXbrlSourceKind.RAW_INSTANCE,
        }
        or source.cik != filing.cik
    ):
        raise ValueError("SEC raw XBRL source does not match filing")
    if source.source_kind is SecXbrlSourceKind.RAW_INLINE and not _looks_inline(source.body):
        return SecXbrlSourceBatch(source=source, contexts=(), facts=())
    collector = _RawXbrlCollector(source=source, filing=filing)
    parser = expat.ParserCreate(namespace_separator="}")
    parser.StartNamespaceDeclHandler = collector.start_namespace
    parser.StartElementHandler = collector.start_element
    parser.EndElementHandler = collector.end_element
    parser.CharacterDataHandler = collector.characters
    parser.StartDoctypeDeclHandler = collector.reject_doctype
    parser.EntityDeclHandler = collector.reject_entity
    parser.ExternalEntityRefHandler = collector.reject_external_entity
    try:
        parser.Parse(source.body, True)
        return collector.batch()
    except (ValueError, expat.ExpatError):
        raise SecSourceError(SecSourceErrorCode.RESPONSE_INVALID, retryable=False) from None


@dataclass(slots=True)
class _ContextBuilder:
    depth: int
    context_id: str
    entity_identifier: str | None = None
    instant: date | None = None
    start_date: date | None = None
    end_date: date | None = None
    forever: bool = False
    dimensions: list[tuple[str, str]] = field(default_factory=list)


@dataclass(slots=True)
class _UnitBuilder:
    depth: int
    unit_id: str
    numerator: list[str] = field(default_factory=list)
    denominator: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _TextCapture:
    kind: str
    depth: int
    dimension: str | None = None
    chunks: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _FactBuilder:
    depth: int
    taxonomy: str
    concept: str
    context_id: str
    unit_id: str | None
    decimals: str | None
    scale: int | None
    format: str | None
    continued_at: str | None
    chunks: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _ContinuationBuilder:
    depth: int
    continuation_id: str
    continued_at: str | None
    chunks: list[str] = field(default_factory=list)


class _RawXbrlCollector:
    def __init__(self, *, source: SecXbrlSourceSnapshot, filing: SecCanonicalFiling) -> None:
        self._source = source
        self._filing = filing
        self._depth = 0
        self._element_count = 0
        self._uri_prefixes: dict[str, str] = {}
        self._stack: list[tuple[str, str]] = []
        self._context: _ContextBuilder | None = None
        self._unit: _UnitBuilder | None = None
        self._capture: _TextCapture | None = None
        self._fact: _FactBuilder | None = None
        self._continuation: _ContinuationBuilder | None = None
        self._exclude_depth: int | None = None
        self._contexts: list[SecXbrlContextData] = []
        self._units: dict[str, str] = {}
        self._facts: list[_FactBuilder] = []
        self._continuations: dict[str, tuple[str, str | None]] = {}

    def start_namespace(self, prefix: str | None, uri: str) -> None:
        if uri and uri not in self._uri_prefixes:
            self._uri_prefixes[uri] = prefix or "default"

    def start_element(self, name: str, attributes: dict[str, str]) -> None:
        self._depth += 1
        self._element_count += 1
        if self._element_count > _MAX_XML_ELEMENTS:
            raise ValueError("SEC XBRL element budget exceeded")
        namespace, local = _expanded_name(name)
        local_lower = local.casefold()
        self._stack.append((namespace, local_lower))
        if namespace == _INLINE_NAMESPACE and local_lower == "exclude":
            self._exclude_depth = self._depth

        if namespace == _XBRLI_NAMESPACE and local_lower == "context":
            if self._context is not None or len(self._contexts) >= SEC_MAX_XBRL_CONTEXTS:
                raise ValueError("SEC XBRL context structure is invalid")
            self._context = _ContextBuilder(
                depth=self._depth,
                context_id=_required_attribute(attributes, "id"),
            )
        elif namespace == _XBRLI_NAMESPACE and local_lower == "unit":
            if self._unit is not None:
                raise ValueError("SEC XBRL unit structure is invalid")
            self._unit = _UnitBuilder(
                depth=self._depth,
                unit_id=_required_attribute(attributes, "id"),
            )

        if self._context is not None and local_lower in {
            "identifier",
            "instant",
            "startdate",
            "enddate",
            "explicitmember",
            "typedmember",
        }:
            if self._capture is not None:
                raise ValueError("SEC XBRL context text is nested unexpectedly")
            dimension = (
                _required_attribute(attributes, "dimension")
                if local_lower in {"explicitmember", "typedmember"}
                else None
            )
            self._capture = _TextCapture(local_lower, self._depth, dimension)
        elif self._unit is not None and local_lower == "measure":
            if self._capture is not None:
                raise ValueError("SEC XBRL unit text is nested unexpectedly")
            self._capture = _TextCapture("measure", self._depth)

        context_ref = _attribute(attributes, "contextref")
        inline_fact = namespace == _INLINE_NAMESPACE and local_lower in {
            "nonfraction",
            "nonnumeric",
        }
        if context_ref is not None or inline_fact:
            if self._fact is not None:
                raise ValueError("Nested SEC XBRL facts are unsupported")
            if context_ref is None:
                raise ValueError("Inline SEC XBRL fact has no context")
            if inline_fact:
                taxonomy, concept = _qname(
                    _required_attribute(attributes, "name"),
                    self._uri_prefixes,
                )
            else:
                resolved_taxonomy = self._uri_prefixes.get(namespace)
                if resolved_taxonomy is None:
                    raise ValueError("SEC XBRL fact namespace is unknown")
                taxonomy = resolved_taxonomy
                concept = local
            raw_scale = _attribute(attributes, "scale")
            self._fact = _FactBuilder(
                depth=self._depth,
                taxonomy=taxonomy,
                concept=concept,
                context_id=context_ref,
                unit_id=_attribute(attributes, "unitref"),
                decimals=_attribute(attributes, "decimals"),
                scale=None if raw_scale is None else int(raw_scale),
                format=_attribute(attributes, "format"),
                continued_at=_attribute(attributes, "continuedat"),
            )
        elif namespace == _INLINE_NAMESPACE and local_lower == "continuation":
            if self._continuation is not None:
                raise ValueError("Nested SEC XBRL continuations are unsupported")
            self._continuation = _ContinuationBuilder(
                depth=self._depth,
                continuation_id=_required_attribute(attributes, "id"),
                continued_at=_attribute(attributes, "continuedat"),
            )
        elif namespace == _XBRLI_NAMESPACE and local_lower == "forever":
            if self._context is None:
                raise ValueError("SEC XBRL forever period has no context")
            self._context.forever = True

    def characters(self, value: str) -> None:
        if not value or self._exclude_depth is not None:
            return
        if self._capture is not None:
            self._capture.chunks.append(value)
        if self._fact is not None:
            self._fact.chunks.append(value)
        if self._continuation is not None:
            self._continuation.chunks.append(value)

    def end_element(self, name: str) -> None:
        namespace, local = _expanded_name(name)
        local_lower = local.casefold()
        if self._capture is not None and self._capture.depth == self._depth:
            self._finish_capture(self._capture)
            self._capture = None
        if self._fact is not None and self._fact.depth == self._depth:
            if len(self._facts) >= SEC_MAX_XBRL_FACTS:
                raise ValueError("SEC XBRL fact budget exceeded")
            self._facts.append(self._fact)
            self._fact = None
        if self._continuation is not None and self._continuation.depth == self._depth:
            value = _normalized_text(self._continuation.chunks)
            if self._continuation.continuation_id in self._continuations:
                raise ValueError("SEC XBRL continuation ID is duplicated")
            self._continuations[self._continuation.continuation_id] = (
                value,
                self._continuation.continued_at,
            )
            self._continuation = None
        if self._unit is not None and self._unit.depth == self._depth:
            self._finish_unit(self._unit)
            self._unit = None
        if self._context is not None and self._context.depth == self._depth:
            self._finish_context(self._context)
            self._context = None
        if self._exclude_depth == self._depth:
            self._exclude_depth = None
        if not self._stack or self._stack[-1] != (namespace, local_lower):
            raise ValueError("SEC XBRL element stack is invalid")
        self._stack.pop()
        self._depth -= 1

    def reject_doctype(self, *_args: object) -> None:
        raise ValueError("SEC XBRL DTD is forbidden")

    def reject_entity(self, *_args: object) -> None:
        raise ValueError("SEC XBRL entity declarations are forbidden")

    def reject_external_entity(self, *_args: object) -> int:
        return 0

    def batch(self) -> SecXbrlSourceBatch:
        if self._depth != 0 or self._context is not None or self._unit is not None:
            raise ValueError("SEC XBRL document is incomplete")
        contexts = {context.context_id: context for context in self._contexts}
        facts: list[SecXbrlFactData] = []
        for ordinal, raw in enumerate(self._facts):
            context = contexts.get(raw.context_id)
            if context is None:
                raise ValueError("SEC XBRL fact context is missing")
            value = _normalized_text(raw.chunks)
            if raw.continued_at is not None:
                value = " ".join((value, self._continuation_text(raw.continued_at))).strip()
            if not value:
                continue
            unit = None if raw.unit_id is None else self._units.get(raw.unit_id)
            if raw.unit_id is not None and unit is None:
                raise ValueError("SEC XBRL fact unit is missing")
            facts.append(
                SecXbrlFactData(
                    source_version=self._source.source_version,
                    locator_key=(f"raw:{ordinal}:{raw.taxonomy}:{raw.concept}:{raw.context_id}"),
                    taxonomy=raw.taxonomy,
                    concept=raw.concept,
                    value=value,
                    unit=unit,
                    period=context.period,
                    filed_date=self._filing.filed_date,
                    form=self._filing.form,
                    context_id=context.context_id,
                    dimensions=context.dimensions,
                    decimals=raw.decimals,
                    scale=raw.scale,
                    format=raw.format,
                    is_custom=raw.taxonomy not in _STANDARD_TAXONOMIES,
                    ordinal=ordinal,
                )
            )
        return SecXbrlSourceBatch(
            source=self._source,
            contexts=tuple(self._contexts),
            facts=tuple(facts),
        )

    def _finish_capture(self, capture: _TextCapture) -> None:
        value = _normalized_text(capture.chunks)
        if not value:
            raise ValueError("SEC XBRL captured text is empty")
        if capture.kind == "measure":
            if self._unit is None:
                raise ValueError("SEC XBRL measure has no unit")
            target = (
                self._unit.denominator
                if any(local == "unitdenominator" for _namespace, local in self._stack)
                else self._unit.numerator
            )
            target.append(value)
            return
        if self._context is None:
            raise ValueError("SEC XBRL context capture has no context")
        if capture.kind == "identifier":
            self._context.entity_identifier = normalize_cik(value)
        elif capture.kind == "instant":
            self._context.instant = date.fromisoformat(value)
        elif capture.kind == "startdate":
            self._context.start_date = date.fromisoformat(value)
        elif capture.kind == "enddate":
            self._context.end_date = date.fromisoformat(value)
        elif capture.dimension is not None:
            taxonomy, concept = _qname(capture.dimension, self._uri_prefixes)
            self._context.dimensions.append((f"{taxonomy}:{concept}", value))

    def _finish_unit(self, unit: _UnitBuilder) -> None:
        if unit.unit_id in self._units or not unit.numerator:
            raise ValueError("SEC XBRL unit is invalid")
        numerator = "*".join(unit.numerator)
        value = numerator
        if unit.denominator:
            value = f"{numerator}/{'*'.join(unit.denominator)}"
        self._units[unit.unit_id] = value

    def _finish_context(self, context: _ContextBuilder) -> None:
        if context.entity_identifier != self._filing.cik:
            raise ValueError("SEC XBRL context entity does not match filing")
        if context.forever:
            period = SecXbrlPeriod(SecXbrlPeriodKind.FOREVER)
        elif context.instant is not None:
            period = SecXbrlPeriod(SecXbrlPeriodKind.INSTANT, instant=context.instant)
        else:
            period = SecXbrlPeriod(
                SecXbrlPeriodKind.DURATION,
                start_date=context.start_date,
                end_date=context.end_date,
            )
        self._contexts.append(
            SecXbrlContextData(
                source_version=self._source.source_version,
                context_id=context.context_id,
                entity_identifier=context.entity_identifier,
                period=period,
                dimensions=tuple(context.dimensions),
            )
        )

    def _continuation_text(self, continuation_id: str) -> str:
        values: list[str] = []
        seen: set[str] = set()
        current: str | None = continuation_id
        while current is not None:
            if current in seen or len(seen) >= _MAX_CONTINUATION_DEPTH:
                raise ValueError("SEC XBRL continuation chain is invalid")
            seen.add(current)
            try:
                value, current = self._continuations[current]
            except KeyError:
                raise ValueError("SEC XBRL continuation is missing") from None
            values.append(value)
        return " ".join(values)


def _looks_inline(body: bytes) -> bool:
    lowered = body.lower()
    return b"<ix:" in lowered or _INLINE_NAMESPACE.lower().encode("ascii") in lowered


def _expanded_name(name: str) -> tuple[str, str]:
    namespace, separator, local = name.rpartition("}")
    return (namespace, local) if separator else ("", name)


def _attribute(
    attributes: dict[str, str],
    local_name: str,
    *,
    required: bool = False,
) -> str | None:
    expected = local_name.casefold()
    matches = [
        value
        for name, value in attributes.items()
        if _expanded_name(name)[1].casefold() == expected
    ]
    if len(matches) > 1 or (required and not matches):
        raise ValueError("SEC XBRL attribute is invalid")
    value = None if not matches else matches[0].strip()
    if value == "":
        raise ValueError("SEC XBRL attribute is blank")
    return value


def _required_attribute(attributes: dict[str, str], local_name: str) -> str:
    value = _attribute(attributes, local_name, required=True)
    if value is None:
        raise AssertionError("Required SEC XBRL attribute disappeared")
    return value


def _qname(value: str, uri_prefixes: dict[str, str]) -> tuple[str, str]:
    prefix, separator, concept = value.partition(":")
    if not separator or not prefix or not concept or prefix not in uri_prefixes.values():
        raise ValueError("SEC XBRL QName is invalid")
    return prefix, concept


def _normalized_text(chunks: list[str]) -> str:
    return " ".join("".join(chunks).split())


def _aggregate_period(row: dict[str, object]) -> SecXbrlPeriod:
    end = _required_date(row.get("end"))
    start = row.get("start")
    if start is None:
        return SecXbrlPeriod(SecXbrlPeriodKind.INSTANT, instant=end)
    return SecXbrlPeriod(
        SecXbrlPeriodKind.DURATION,
        start_date=_required_date(start),
        end_date=end,
    )


def _fact_value(value: object) -> str:
    if isinstance(value, bool) or value is None:
        raise ValueError("SEC XBRL fact value is invalid")
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, int | str):
        text = str(value).strip()
        if text and not any(ord(character) < 32 for character in text):
            return text
    raise ValueError("SEC XBRL fact value is invalid")


def _required_scalar(value: object) -> str | int:
    if isinstance(value, bool) or not isinstance(value, str | int):
        raise ValueError("SEC scalar is invalid")
    return value


def _required_text(value: object, *, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > maximum
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError("SEC text is invalid")
    return value.strip()


def _required_date(value: object) -> date:
    return date.fromisoformat(_required_text(value, maximum=10))


def _required_object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError("SEC JSON object is invalid")
    return value


def _json_object(body: bytes) -> dict[str, object]:
    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("SEC JSON contains a duplicate key")
            result[key] = value
        return result

    try:
        value = json.loads(
            body,
            object_pairs_hook=unique_object,
            parse_float=Decimal,
            parse_int=int,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise SecSourceError(SecSourceErrorCode.RESPONSE_INVALID, retryable=False) from None
    try:
        return _required_object(value)
    except ValueError:
        raise SecSourceError(SecSourceErrorCode.RESPONSE_INVALID, retryable=False) from None
