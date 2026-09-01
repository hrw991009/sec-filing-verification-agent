"""PostgreSQL ledger for SEC bulk snapshots and post-watermark gap proofs."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from industry_platform.core.database import AsyncSessionFactory, safe_sqlstate
from industry_platform.modules.disclosures.bulk import SecBulkSyncReceipt
from industry_platform.modules.disclosures.domain import SecDisclosurePersistenceError
from industry_platform.modules.disclosures.models import (
    SecBulkEntryRecord,
    SecBulkGapClosureRecord,
    SecBulkGapSourceRecord,
    SecBulkSourceRecord,
)


class SqlAlchemySecBulkSyncRepository:
    def __init__(self, session_factory: AsyncSessionFactory) -> None:
        self._session_factory = session_factory

    async def persist(self, receipt: SecBulkSyncReceipt) -> bool:
        try:
            async with self._session_factory.begin() as session:
                archive = await session.scalar(
                    select(SecBulkSourceRecord).where(
                        SecBulkSourceRecord.source_url == receipt.archive.source_url,
                        SecBulkSourceRecord.source_version == receipt.archive.source_version,
                    )
                )
                if archive is None:
                    archive = SecBulkSourceRecord(
                        id=uuid4(),
                        dataset_kind=receipt.archive.dataset_kind.value,
                        source_url=receipt.archive.source_url,
                        source_version=receipt.archive.source_version,
                        content_sha256=bytes.fromhex(receipt.archive.content_sha256),
                        byte_size=receipt.archive.byte_size,
                        object_bucket=receipt.archive.object_bucket,
                        object_key=receipt.archive.object_key,
                        retrieved_at=receipt.archive.retrieved_at,
                        bulk_published_at=receipt.archive.bulk_published_at,
                        coverage_through=receipt.archive.coverage_through,
                        adapter_version=receipt.archive.adapter_version,
                        watermark_policy_version=receipt.archive.watermark_policy_version,
                    )
                    session.add(archive)
                    await session.flush()
                elif not _archive_matches(archive, receipt):
                    raise SecDisclosurePersistenceError

                entry = await session.scalar(
                    select(SecBulkEntryRecord).where(
                        SecBulkEntryRecord.bulk_source_id == archive.id,
                        SecBulkEntryRecord.cik == receipt.entry.cik,
                    )
                )
                if entry is None:
                    entry = SecBulkEntryRecord(
                        id=uuid4(),
                        bulk_source_id=archive.id,
                        cik=receipt.entry.cik,
                        entry_name=receipt.entry.entry_name,
                        content_sha256=bytes.fromhex(receipt.entry.content_sha256),
                        byte_size=receipt.entry.byte_size,
                    )
                    session.add(entry)
                    await session.flush()
                elif not _entry_matches(entry, receipt):
                    raise SecDisclosurePersistenceError

                closure = await session.scalar(
                    select(SecBulkGapClosureRecord).where(
                        SecBulkGapClosureRecord.bulk_entry_id == entry.id,
                        SecBulkGapClosureRecord.gap_observed_through
                        == receipt.gap_observed_through,
                    )
                )
                committed = closure is None
                if closure is None:
                    closure = SecBulkGapClosureRecord(
                        id=uuid4(),
                        bulk_entry_id=entry.id,
                        coverage_from_exclusive=receipt.archive.coverage_through,
                        gap_observed_through=receipt.gap_observed_through,
                    )
                    session.add(closure)
                    await session.flush()
                    session.add_all(
                        SecBulkGapSourceRecord(
                            id=uuid4(),
                            gap_closure_id=closure.id,
                            source_kind=source.source_kind,
                            source_url=source.source_url,
                            source_version=source.source_version,
                            content_sha256=bytes.fromhex(source.content_sha256),
                            source_available_at=source.source_available_at,
                            retrieved_at=source.retrieved_at,
                        )
                        for source in receipt.incremental_sources
                    )
                else:
                    if _utc(closure.coverage_from_exclusive) != receipt.archive.coverage_through:
                        raise SecDisclosurePersistenceError
                    sources = tuple(
                        await session.scalars(
                            select(SecBulkGapSourceRecord).where(
                                SecBulkGapSourceRecord.gap_closure_id == closure.id
                            )
                        )
                    )
                    if _source_facts(sources) != _receipt_source_facts(receipt):
                        raise SecDisclosurePersistenceError
            return committed
        except SecDisclosurePersistenceError:
            raise
        except SQLAlchemyError as error:
            raise SecDisclosurePersistenceError(sqlstate=safe_sqlstate(error)) from None


def _archive_matches(record: SecBulkSourceRecord, receipt: SecBulkSyncReceipt) -> bool:
    archive = receipt.archive
    return (
        record.dataset_kind == archive.dataset_kind.value
        and record.content_sha256 == bytes.fromhex(archive.content_sha256)
        and record.byte_size == archive.byte_size
        and record.object_bucket == archive.object_bucket
        and record.object_key == archive.object_key
        and _utc(record.retrieved_at) == archive.retrieved_at
        and _utc(record.bulk_published_at) == archive.bulk_published_at
        and _utc(record.coverage_through) == archive.coverage_through
        and record.adapter_version == archive.adapter_version
        and record.watermark_policy_version == archive.watermark_policy_version
    )


def _entry_matches(record: SecBulkEntryRecord, receipt: SecBulkSyncReceipt) -> bool:
    return (
        record.entry_name == receipt.entry.entry_name
        and record.content_sha256 == bytes.fromhex(receipt.entry.content_sha256)
        and record.byte_size == receipt.entry.byte_size
    )


def _source_facts(
    records: tuple[SecBulkGapSourceRecord, ...],
) -> set[tuple[object, ...]]:
    return {
        (
            item.source_kind,
            item.source_url,
            item.source_version,
            item.content_sha256,
            _utc(item.source_available_at),
            _utc(item.retrieved_at),
        )
        for item in records
    }


def _receipt_source_facts(receipt: SecBulkSyncReceipt) -> set[tuple[object, ...]]:
    return {
        (
            item.source_kind,
            item.source_url,
            item.source_version,
            bytes.fromhex(item.content_sha256),
            item.source_available_at,
            item.retrieved_at,
        )
        for item in receipt.incremental_sources
    }


def _utc(value: datetime) -> datetime:
    return value.astimezone(UTC)
