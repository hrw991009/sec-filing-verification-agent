"""PostgreSQL persistence for immutable SEC submissions and filing coverage."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from industry_platform.core.database import AsyncSessionFactory, safe_sqlstate
from industry_platform.modules.disclosures.domain import (
    SEC_VISIBILITY_POLICY_VERSION,
    FilingSelectionScope,
    SecAmendmentRelationStatus,
    SecDisclosurePersistenceError,
    SecFilingCandidate,
    SecFilingDataset,
    SecFilingForm,
    SecFilingObservation,
    SecSubmissionSet,
    SecSubmissionSourceKind,
    SecSubmissionSourceReference,
    SecSubmissionSourceSnapshot,
    base_form,
    is_amendment,
)
from industry_platform.modules.disclosures.models import (
    SecFilingCoverageRecord,
    SecFilingCoverageSourceRecord,
    SecFilingObservationRecord,
    SecFilingRecord,
    SecSubmissionSourceRecord,
)


class SqlAlchemySecFilingRepository:
    """Persist source bytes by reference, then freeze exact scope/source manifests."""

    def __init__(self, session_factory: AsyncSessionFactory, *, object_bucket: str) -> None:
        if not object_bucket.strip():
            raise ValueError("SEC filing object bucket is invalid")
        self._session_factory = session_factory
        self._object_bucket = object_bucket

    async def replace_submission_set(
        self,
        snapshot: SecSubmissionSet,
        *,
        object_keys: dict[str, str],
        scope: FilingSelectionScope,
    ) -> str:
        expected_versions = {source.source_version for source in snapshot.sources}
        if set(object_keys) != expected_versions or any(
            not value for value in object_keys.values()
        ):
            raise SecDisclosurePersistenceError
        coverage_version = _coverage_version(scope, snapshot)
        try:
            async with self._session_factory.begin() as session:
                source_records: dict[str, SecSubmissionSourceRecord] = {}
                for source in snapshot.sources:
                    record = await session.scalar(
                        select(SecSubmissionSourceRecord).where(
                            SecSubmissionSourceRecord.source_url == source.source_url,
                            SecSubmissionSourceRecord.source_version == source.source_version,
                        )
                    )
                    if record is None:
                        record = _source_record(
                            source,
                            object_bucket=self._object_bucket,
                            object_key=object_keys[source.source_version],
                        )
                        session.add(record)
                        await session.flush()
                    elif not _source_record_matches(
                        record,
                        source,
                        object_bucket=self._object_bucket,
                        object_key=object_keys[source.source_version],
                    ):
                        raise SecDisclosurePersistenceError
                    source_records[source.source_version] = record
                    await _persist_observations(session, record.id, source)

                await _advance_canonical_projection(
                    session,
                    snapshot,
                    source_records=source_records,
                )
                manifest = await session.scalar(
                    select(SecFilingCoverageRecord).where(
                        SecFilingCoverageRecord.coverage_version == coverage_version
                    )
                )
                if manifest is None:
                    manifest = SecFilingCoverageRecord(
                        coverage_version=coverage_version,
                        schema_version=scope.schema_version,
                        cik=scope.cik,
                        allowed_forms=[form.value for form in scope.allowed_forms],
                        report_period_start=scope.report_period_start,
                        report_period_end=scope.report_period_end,
                        as_of=scope.as_of,
                        amendment_policy=scope.amendment_policy.value,
                        source_count=len(source_records),
                    )
                    session.add(manifest)
                    await session.flush()
                    session.add_all(
                        SecFilingCoverageSourceRecord(
                            coverage_id=manifest.id,
                            source_id=record.id,
                        )
                        for record in source_records.values()
                    )
                else:
                    if not _manifest_matches(manifest, scope, len(source_records)):
                        raise SecDisclosurePersistenceError
                    persisted_source_ids = set(
                        await session.scalars(
                            select(SecFilingCoverageSourceRecord.source_id).where(
                                SecFilingCoverageSourceRecord.coverage_id == manifest.id
                            )
                        )
                    )
                    if persisted_source_ids != {record.id for record in source_records.values()}:
                        raise SecDisclosurePersistenceError
            return coverage_version
        except SecDisclosurePersistenceError:
            raise
        except SQLAlchemyError as error:
            raise SecDisclosurePersistenceError(sqlstate=safe_sqlstate(error)) from None

    async def load_dataset(
        self,
        *,
        coverage_version: str,
        scope: FilingSelectionScope,
    ) -> SecFilingDataset:
        try:
            async with self._session_factory() as session:
                manifest = await session.scalar(
                    select(SecFilingCoverageRecord).where(
                        SecFilingCoverageRecord.coverage_version == coverage_version
                    )
                )
                if manifest is None or not _manifest_matches(
                    manifest, scope, manifest.source_count
                ):
                    raise SecDisclosurePersistenceError
                sources = tuple(
                    await session.scalars(
                        select(SecSubmissionSourceRecord)
                        .join(
                            SecFilingCoverageSourceRecord,
                            SecFilingCoverageSourceRecord.source_id == SecSubmissionSourceRecord.id,
                        )
                        .where(SecFilingCoverageSourceRecord.coverage_id == manifest.id)
                        .order_by(SecSubmissionSourceRecord.source_url.asc())
                    )
                )
                if len(sources) != manifest.source_count:
                    raise SecDisclosurePersistenceError
                observations = tuple(
                    await session.scalars(
                        select(SecFilingObservationRecord)
                        .where(
                            SecFilingObservationRecord.source_id.in_(
                                tuple(source.id for source in sources)
                            ),
                            SecFilingObservationRecord.cik == scope.cik,
                        )
                        .order_by(SecFilingObservationRecord.accession.asc())
                    )
                )
        except SecDisclosurePersistenceError:
            raise
        except SQLAlchemyError as error:
            raise SecDisclosurePersistenceError(sqlstate=safe_sqlstate(error)) from None

        sources_by_id = {source.id: source for source in sources}
        selected: dict[str, tuple[SecFilingObservation, SecSubmissionSourceRecord]] = {}
        for row in observations:
            source = sources_by_id.get(row.source_id)
            if source is None:
                raise SecDisclosurePersistenceError
            observation = _observation_domain(row)
            existing = selected.get(row.accession)
            if existing is not None and existing[0] != observation:
                raise SecDisclosurePersistenceError
            if existing is None or _source_order(source) > _source_order(existing[1]):
                selected[row.accession] = (observation, source)
        relations = _amendment_relations(tuple(item[0] for item in selected.values()))
        candidates = tuple(
            _candidate(observation, source, relations[observation.accession])
            for observation, source in sorted(
                selected.values(),
                key=lambda item: (item[0].accepted_at, item[0].accession),
            )
        )
        return SecFilingDataset(
            coverage_version=coverage_version,
            scope=scope,
            filings=candidates,
            sources=tuple(_source_reference(source) for source in sources),
        )


async def _persist_observations(
    session: object,
    source_id: UUID,
    source: SecSubmissionSourceSnapshot,
) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    if not isinstance(session, AsyncSession):
        raise TypeError("SEC filing session is invalid")
    existing = {
        row.accession: row
        for row in await session.scalars(
            select(SecFilingObservationRecord).where(
                SecFilingObservationRecord.source_id == source_id
            )
        )
    }
    for observation in source.filings:
        row = existing.get(observation.accession)
        if row is None:
            session.add(
                SecFilingObservationRecord(
                    source_id=source_id,
                    cik=observation.cik,
                    accession=observation.accession,
                    form=observation.form.value,
                    report_date=observation.report_date,
                    filed_date=observation.filed_date,
                    accepted_at=observation.accepted_at,
                    primary_document=observation.primary_document,
                )
            )
        elif _observation_domain(row) != observation:
            raise SecDisclosurePersistenceError


async def _advance_canonical_projection(
    session: object,
    snapshot: SecSubmissionSet,
    *,
    source_records: dict[str, SecSubmissionSourceRecord],
) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    if not isinstance(session, AsyncSession):
        raise TypeError("SEC filing session is invalid")
    observations = snapshot.filings
    relations = _amendment_relations(observations)
    source_by_accession: dict[str, SecSubmissionSourceRecord] = {}
    for source in snapshot.sources:
        record = source_records[source.source_version]
        for observation in source.filings:
            current = source_by_accession.get(observation.accession)
            if current is None or _source_order(record) > _source_order(current):
                source_by_accession[observation.accession] = record
    existing = {
        row.accession: row
        for row in await session.scalars(
            select(SecFilingRecord).where(
                SecFilingRecord.accession.in_(tuple(item.accession for item in observations))
            )
        )
    }
    existing_source_ids = {row.source_id for row in existing.values()}
    existing_sources = {
        row.id: row
        for row in await session.scalars(
            select(SecSubmissionSourceRecord).where(
                SecSubmissionSourceRecord.id.in_(tuple(existing_source_ids))
            )
        )
    }
    for observation in observations:
        source_record = source_by_accession[observation.accession]
        relation_status, base_accession = relations[observation.accession]
        row = existing.get(observation.accession)
        if row is not None:
            previous_source = existing_sources.get(row.source_id)
            if previous_source is None:
                raise SecDisclosurePersistenceError
            if _source_order(previous_source) > _source_order(source_record):
                continue
            row.source_id = source_record.id
            row.cik = observation.cik
            row.form = observation.form.value
            row.report_date = observation.report_date
            row.filed_date = observation.filed_date
            row.accepted_at = observation.accepted_at
            row.public_available_at = observation.accepted_at
            row.visibility_policy_version = SEC_VISIBILITY_POLICY_VERSION
            row.primary_document = observation.primary_document
            row.amendment_relation_status = relation_status.value
            row.base_accession = base_accession
            continue
        session.add(
            SecFilingRecord(
                source_id=source_record.id,
                cik=observation.cik,
                accession=observation.accession,
                form=observation.form.value,
                report_date=observation.report_date,
                filed_date=observation.filed_date,
                accepted_at=observation.accepted_at,
                public_available_at=observation.accepted_at,
                visibility_policy_version=SEC_VISIBILITY_POLICY_VERSION,
                primary_document=observation.primary_document,
                amendment_relation_status=relation_status.value,
                base_accession=base_accession,
            )
        )


def _source_record(
    source: SecSubmissionSourceSnapshot,
    *,
    object_bucket: str,
    object_key: str,
) -> SecSubmissionSourceRecord:
    return SecSubmissionSourceRecord(
        id=uuid4(),
        cik=source.cik,
        source_kind=source.source_kind.value,
        source_name=source.source_name,
        source_url=source.source_url,
        source_version=source.source_version,
        content_sha256=bytes.fromhex(source.content_sha256),
        object_bucket=object_bucket,
        object_key=object_key,
        retrieved_at=source.retrieved_at,
        source_available_at=source.source_available_at,
        filing_from=source.filing_from,
        filing_to=source.filing_to,
    )


def _source_record_matches(
    record: SecSubmissionSourceRecord,
    source: SecSubmissionSourceSnapshot,
    *,
    object_bucket: str,
    object_key: str,
) -> bool:
    return (
        record.cik == source.cik
        and record.source_kind == source.source_kind.value
        and record.source_name == source.source_name
        and record.content_sha256 == bytes.fromhex(source.content_sha256)
        and record.object_bucket == object_bucket
        and record.object_key == object_key
        and record.source_available_at.astimezone(UTC) == source.source_available_at
        and record.filing_from == source.filing_from
        and record.filing_to == source.filing_to
    )


def _manifest_matches(
    record: SecFilingCoverageRecord,
    scope: FilingSelectionScope,
    source_count: int,
) -> bool:
    return (
        record.schema_version == scope.schema_version
        and record.cik == scope.cik
        and record.allowed_forms == [form.value for form in scope.allowed_forms]
        and record.report_period_start == scope.report_period_start
        and record.report_period_end == scope.report_period_end
        and record.as_of.astimezone(UTC) == scope.as_of
        and record.amendment_policy == scope.amendment_policy.value
        and record.source_count == source_count
    )


def _coverage_version(scope: FilingSelectionScope, snapshot: SecSubmissionSet) -> str:
    document = {
        "scope": dict(scope.to_mapping()),
        "sources": [
            {
                "source_url": source.source_url,
                "source_version": source.source_version,
                "content_sha256": source.content_sha256,
                "source_available_at": source.source_available_at.isoformat(),
            }
            for source in sorted(snapshot.sources, key=lambda item: item.source_url)
        ],
    }
    encoded = json.dumps(
        document,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sec-filings-{hashlib.sha256(encoded).hexdigest()[:32]}"


def _observation_domain(record: SecFilingObservationRecord) -> SecFilingObservation:
    return SecFilingObservation(
        cik=record.cik,
        accession=record.accession,
        form=SecFilingForm(record.form),
        report_date=record.report_date,
        filed_date=record.filed_date,
        accepted_at=record.accepted_at.astimezone(UTC),
        primary_document=record.primary_document,
    )


def _amendment_relations(
    observations: tuple[SecFilingObservation, ...],
) -> dict[str, tuple[SecAmendmentRelationStatus, str | None]]:
    bases: dict[tuple[str, SecFilingForm, object], list[SecFilingObservation]] = {}
    for observation in observations:
        if not is_amendment(observation.form):
            bases.setdefault(
                (observation.cik, observation.form, observation.report_date), []
            ).append(observation)
    result: dict[str, tuple[SecAmendmentRelationStatus, str | None]] = {}
    for observation in observations:
        if not is_amendment(observation.form):
            result[observation.accession] = (SecAmendmentRelationStatus.NOT_AMENDMENT, None)
            continue
        candidates = tuple(
            item
            for item in bases.get(
                (observation.cik, base_form(observation.form), observation.report_date), []
            )
            if item.accepted_at <= observation.accepted_at
        )
        if len(candidates) == 1:
            result[observation.accession] = (
                SecAmendmentRelationStatus.RESOLVED,
                candidates[0].accession,
            )
        else:
            result[observation.accession] = (SecAmendmentRelationStatus.UNRESOLVED, None)
    return result


def _candidate(
    observation: SecFilingObservation,
    source: SecSubmissionSourceRecord,
    relation: tuple[SecAmendmentRelationStatus, str | None],
) -> SecFilingCandidate:
    return SecFilingCandidate(
        cik=observation.cik,
        accession=observation.accession,
        form=observation.form,
        report_date=observation.report_date,
        filed_date=observation.filed_date,
        accepted_at=observation.accepted_at,
        public_available_at=observation.accepted_at,
        primary_document=observation.primary_document,
        amendment_relation_status=relation[0],
        base_accession=relation[1],
        source_version=source.source_version,
        source_url=source.source_url,
        content_sha256=source.content_sha256.hex(),
        source_available_at=source.source_available_at.astimezone(UTC),
    )


def _source_reference(source: SecSubmissionSourceRecord) -> SecSubmissionSourceReference:
    return SecSubmissionSourceReference(
        source_kind=SecSubmissionSourceKind(source.source_kind),
        source_version=source.source_version,
        source_url=source.source_url,
        content_sha256=source.content_sha256.hex(),
        source_available_at=source.source_available_at.astimezone(UTC),
        retrieved_at=source.retrieved_at.astimezone(UTC),
    )


def _source_order(source: SecSubmissionSourceRecord) -> tuple[object, ...]:
    return (
        source.source_available_at.astimezone(UTC),
        source.retrieved_at.astimezone(UTC),
        source.source_version,
    )
