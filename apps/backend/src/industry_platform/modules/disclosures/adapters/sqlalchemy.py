"""PostgreSQL adapter for the canonical SEC filer identity catalog."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC
from uuid import UUID, uuid4

from sqlalchemy import case, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.sql.elements import ColumnElement

from industry_platform.core.database import AsyncSessionFactory, safe_sqlstate
from industry_platform.modules.disclosures.domain import (
    SecAliasKind,
    SecDisclosurePersistenceError,
    SecFiler,
    SecFilerAlias,
    SecFilerCatalogSnapshot,
)
from industry_platform.modules.disclosures.models import (
    SecCatalogSyncRecord,
    SecFilerAliasRecord,
    SecFilerRecord,
)

_WRITE_BATCH_SIZE = 500


class SqlAlchemySecFilerCatalogRepository:
    """Atomically advance current filer aliases while retaining prior versions."""

    def __init__(self, session_factory: AsyncSessionFactory) -> None:
        self._session_factory = session_factory

    async def replace_catalog(self, snapshot: SecFilerCatalogSnapshot) -> None:
        try:
            async with self._session_factory.begin() as session:
                existing = await session.scalar(
                    select(SecCatalogSyncRecord).where(
                        SecCatalogSyncRecord.source_kind == snapshot.source_kind,
                        SecCatalogSyncRecord.source_version == snapshot.source_version,
                    )
                )
                if existing is not None:
                    if existing.content_sha256 != bytes.fromhex(snapshot.content_sha256):
                        raise SecDisclosurePersistenceError
                    return

                latest = await session.scalar(
                    select(SecCatalogSyncRecord)
                    .where(SecCatalogSyncRecord.source_kind == snapshot.source_kind)
                    .order_by(SecCatalogSyncRecord.retrieved_at.desc())
                    .limit(1)
                )
                if (
                    latest is not None
                    and latest.retrieved_at.astimezone(UTC) > snapshot.retrieved_at
                ):
                    raise SecDisclosurePersistenceError

                await session.execute(
                    update(SecFilerAliasRecord)
                    .where(
                        SecFilerAliasRecord.source_kind == snapshot.source_kind,
                        SecFilerAliasRecord.valid_to.is_(None),
                    )
                    .values(valid_to=snapshot.retrieved_at, updated_at=func.now())
                )

                for filer_batch in _batches(snapshot.filers):
                    values = [
                        {
                            "id": uuid4(),
                            "cik": filer.cik,
                            "canonical_name": filer.canonical_name,
                            "normalized_name": filer.normalized_name,
                            "source_kind": filer.source_kind,
                            "source_version": filer.source_version,
                            "source_url": filer.source_url,
                            "source_content_sha256": bytes.fromhex(filer.content_sha256),
                            "source_observed_at": filer.observed_at,
                        }
                        for filer in filer_batch
                    ]
                    statement = insert(SecFilerRecord).values(values)
                    await session.execute(
                        statement.on_conflict_do_update(
                            index_elements=(SecFilerRecord.cik,),
                            set_={
                                "canonical_name": statement.excluded.canonical_name,
                                "normalized_name": statement.excluded.normalized_name,
                                "source_kind": statement.excluded.source_kind,
                                "source_version": statement.excluded.source_version,
                                "source_url": statement.excluded.source_url,
                                "source_content_sha256": statement.excluded.source_content_sha256,
                                "source_observed_at": statement.excluded.source_observed_at,
                                "updated_at": func.now(),
                            },
                        )
                    )

                filer_rows = tuple(
                    await session.execute(
                        select(SecFilerRecord.id, SecFilerRecord.cik).where(
                            SecFilerRecord.cik.in_(tuple(filer.cik for filer in snapshot.filers))
                        )
                    )
                )
                filer_ids = {cik: filer_id for filer_id, cik in filer_rows}
                if len(filer_ids) != len(snapshot.filers):
                    raise SecDisclosurePersistenceError

                alias_values = [
                    {
                        "id": uuid4(),
                        "filer_id": filer_ids[filer.cik],
                        "kind": alias.kind.value,
                        "display_value": alias.display_value,
                        "normalized_value": alias.normalized_value,
                        "source_kind": alias.source_kind,
                        "source_version": alias.source_version,
                        "source_url": alias.source_url,
                        "source_content_sha256": bytes.fromhex(alias.content_sha256),
                        "observed_at": alias.observed_at,
                        "valid_from": alias.valid_from,
                        "valid_to": alias.valid_to,
                    }
                    for filer in snapshot.filers
                    for alias in filer.aliases
                ]
                for alias_batch in _batches(alias_values):
                    await session.execute(
                        insert(SecFilerAliasRecord)
                        .values(list(alias_batch))
                        .on_conflict_do_nothing()
                    )

                session.add(
                    SecCatalogSyncRecord(
                        source_kind=snapshot.source_kind,
                        source_version=snapshot.source_version,
                        source_url=snapshot.source_url,
                        content_sha256=bytes.fromhex(snapshot.content_sha256),
                        retrieved_at=snapshot.retrieved_at,
                        filer_count=len(snapshot.filers),
                    )
                )
        except SecDisclosurePersistenceError:
            raise
        except SQLAlchemyError as error:
            raise SecDisclosurePersistenceError(sqlstate=safe_sqlstate(error)) from None

    async def search(
        self,
        *,
        cik: str | None,
        normalized_name: str,
        ticker: str | None,
        limit: int,
    ) -> tuple[SecFiler, ...]:
        conditions = []
        if cik is not None:
            conditions.append(SecFilerRecord.cik == cik)
        alias_conditions = [
            (
                (SecFilerAliasRecord.kind == SecAliasKind.NAME.value)
                & SecFilerAliasRecord.normalized_value.contains(normalized_name)
            )
        ]
        if ticker is not None:
            alias_conditions.append(
                (SecFilerAliasRecord.kind == SecAliasKind.TICKER.value)
                & (SecFilerAliasRecord.normalized_value == ticker)
            )
        conditions.append(or_(*alias_conditions))
        match_precedence: list[tuple[ColumnElement[bool], int]] = [
            (
                (SecFilerAliasRecord.kind == SecAliasKind.NAME.value)
                & (SecFilerAliasRecord.normalized_value == normalized_name),
                2,
            ),
            (
                (SecFilerAliasRecord.kind == SecAliasKind.NAME.value)
                & SecFilerAliasRecord.normalized_value.startswith(normalized_name),
                3,
            ),
        ]
        if ticker is not None:
            match_precedence.insert(
                0,
                (
                    (SecFilerAliasRecord.kind == SecAliasKind.TICKER.value)
                    & (SecFilerAliasRecord.normalized_value == ticker),
                    1,
                ),
            )
        if cik is not None:
            match_precedence.insert(0, (SecFilerRecord.cik == cik, 0))
        match_rank = case(*match_precedence, else_=4)
        ranked_ids = (
            select(
                SecFilerRecord.id,
                func.min(match_rank).label("match_rank"),
                SecFilerRecord.cik,
            )
            .join(
                SecFilerAliasRecord,
                SecFilerAliasRecord.filer_id == SecFilerRecord.id,
            )
            .where(or_(*conditions))
            .group_by(SecFilerRecord.id, SecFilerRecord.cik)
            .order_by("match_rank", SecFilerRecord.cik.asc())
            .limit(limit * 4)
        )
        try:
            async with self._session_factory() as session:
                filer_ids = tuple(row.id for row in await session.execute(ranked_ids))
                if not filer_ids:
                    return ()
                filers = tuple(
                    await session.scalars(
                        select(SecFilerRecord).where(SecFilerRecord.id.in_(filer_ids))
                    )
                )
                aliases = tuple(
                    await session.scalars(
                        select(SecFilerAliasRecord)
                        .where(
                            SecFilerAliasRecord.filer_id.in_(tuple(filer.id for filer in filers))
                        )
                        .order_by(
                            SecFilerAliasRecord.filer_id.asc(),
                            SecFilerAliasRecord.kind.asc(),
                            SecFilerAliasRecord.normalized_value.asc(),
                            SecFilerAliasRecord.observed_at.desc(),
                        )
                    )
                )
        except SQLAlchemyError as error:
            raise SecDisclosurePersistenceError(sqlstate=safe_sqlstate(error)) from None

        by_filer: dict[UUID, list[SecFilerAliasRecord]] = {}
        for alias in aliases:
            by_filer.setdefault(alias.filer_id, []).append(alias)
        return tuple(
            _filer_domain(filer, by_filer.get(filer.id, []))
            for filer in filers
            if by_filer.get(filer.id)
        )


def _filer_domain(
    record: SecFilerRecord,
    aliases: list[SecFilerAliasRecord],
) -> SecFiler:
    return SecFiler(
        cik=record.cik,
        canonical_name=record.canonical_name,
        normalized_name=record.normalized_name,
        aliases=tuple(
            SecFilerAlias(
                kind=SecAliasKind(alias.kind),
                display_value=alias.display_value,
                normalized_value=alias.normalized_value,
                source_kind=alias.source_kind,
                source_version=alias.source_version,
                source_url=alias.source_url,
                content_sha256=alias.source_content_sha256.hex(),
                observed_at=alias.observed_at.astimezone(UTC),
                valid_from=(None if alias.valid_from is None else alias.valid_from.astimezone(UTC)),
                valid_to=None if alias.valid_to is None else alias.valid_to.astimezone(UTC),
            )
            for alias in aliases
        ),
        source_kind=record.source_kind,
        source_version=record.source_version,
        source_url=record.source_url,
        content_sha256=record.source_content_sha256.hex(),
        observed_at=record.source_observed_at.astimezone(UTC),
    )


def _batches[T](values: Sequence[T] | tuple[T, ...]) -> Iterable[Sequence[T]]:
    for index in range(0, len(values), _WRITE_BATCH_SIZE):
        yield values[index : index + _WRITE_BATCH_SIZE]
