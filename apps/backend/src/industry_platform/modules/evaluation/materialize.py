"""Materialize and validate pinned fixed-context benchmark artifacts."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Final, cast

import httpx2

from industry_platform.adapters.public_egress import create_public_egress_http_client
from industry_platform.modules.evaluation.finqa import FINQA_DATASET_ID, FinQaAdapter
from industry_platform.modules.evaluation.fixed_context import (
    AdapterValidationReport,
    FixedContextArtifactStore,
    FixedContextSplitSummary,
    VerifiedArtifact,
    stable_case_digest,
    write_adapter_report,
)
from industry_platform.modules.evaluation.release import (
    DatasetArtifact,
    DatasetRecord,
    DatasetRegistry,
    load_dataset_registry,
)
from industry_platform.modules.evaluation.tatqa import TATQA_DATASET_ID, TatQaAdapter

_SUPPORTED_DATASETS: Final = (FINQA_DATASET_ID, TATQA_DATASET_ID)
_CHUNK_SIZE: Final = 1024 * 1024


async def materialize_fixed_context_datasets(
    registry: DatasetRegistry,
    *,
    root: Path,
    dataset_ids: tuple[str, ...] = _SUPPORTED_DATASETS,
    client: httpx2.AsyncClient | None = None,
) -> dict[str, tuple[VerifiedArtifact, ...]]:
    records = {record.dataset_id: record for record in registry.records}
    if len(set(dataset_ids)) != len(dataset_ids):
        raise ValueError("Materialization dataset ids must be unique")
    if any(dataset_id not in _SUPPORTED_DATASETS for dataset_id in dataset_ids):
        raise ValueError("Materialization only supports FinQA and TAT-QA")
    selected = tuple(records[dataset_id] for dataset_id in dataset_ids if dataset_id in records)
    if len(selected) != len(dataset_ids):
        raise ValueError("Materialization dataset is not registered")

    owned_client = client is None
    active_client = client or create_public_egress_http_client()
    try:
        materialized: dict[str, tuple[VerifiedArtifact, ...]] = {}
        for record in selected:
            verified = []
            for artifact in record.artifacts:
                verified.append(
                    await _materialize_artifact(
                        record,
                        artifact,
                        root=root,
                        client=active_client,
                    )
                )
            materialized[record.dataset_id] = tuple(verified)
        return materialized
    finally:
        if owned_client:
            await active_client.aclose()


async def _materialize_artifact(
    record: DatasetRecord,
    artifact: DatasetArtifact,
    *,
    root: Path,
    client: httpx2.AsyncClient,
) -> VerifiedArtifact:
    store = FixedContextArtifactStore(root)
    target = store.path_for(record, artifact)
    if target.exists():
        return store.verify(record, artifact)

    await materialize_verified_download(
        artifact_id=artifact.artifact_id,
        download_url=artifact.download_url,
        byte_size=artifact.byte_size,
        sha256=artifact.sha256,
        target=target,
        client=client,
        accept="application/json",
        error_prefix="Dataset artifact",
    )
    return store.verify(record, artifact)


async def materialize_verified_download(
    *,
    artifact_id: str,
    download_url: str,
    byte_size: int,
    sha256: str,
    target: Path,
    client: httpx2.AsyncClient,
    accept: str,
    error_prefix: str,
) -> None:
    """Download a pinned public artifact without publishing partial or unverified bytes."""

    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f".{target.name}.{sha256[:12]}.partial")
    partial.unlink(missing_ok=True)
    received = 0
    digest = hashlib.sha256()
    try:
        async with client.stream(
            "GET",
            download_url,
            headers={"Accept": accept, "Accept-Encoding": "identity"},
        ) as response:
            if response.status_code != 200:
                raise ValueError(
                    f"{error_prefix} download failed: {artifact_id} (status={response.status_code})"
                )
            content_length = response.headers.get("content-length")
            if content_length is not None and (
                not content_length.isdigit() or int(content_length) != byte_size
            ):
                raise ValueError(f"{error_prefix} Content-Length mismatch: {artifact_id}")
            with partial.open("xb") as handle:
                async for chunk in response.aiter_raw(chunk_size=_CHUNK_SIZE):
                    received += len(chunk)
                    if received > byte_size:
                        raise ValueError(f"{error_prefix} exceeds registered size: {artifact_id}")
                    handle.write(chunk)
                    digest.update(chunk)
                handle.flush()
                os.fsync(handle.fileno())
        if received != byte_size:
            raise ValueError(f"{error_prefix} size mismatch: {artifact_id}")
        if digest.hexdigest() != sha256:
            raise ValueError(f"{error_prefix} checksum mismatch: {artifact_id}")
        os.replace(partial, target)
    except (httpx2.HTTPError, OSError) as error:
        raise ValueError(f"{error_prefix} download failed: {artifact_id}") from error
    finally:
        partial.unlink(missing_ok=True)


def build_adapter_report(
    record: DatasetRecord,
    *,
    root: Path,
) -> AdapterValidationReport:
    store = FixedContextArtifactStore(root)
    artifacts = tuple(store.verify(record, artifact) for artifact in record.artifacts)
    if record.dataset_id == FINQA_DATASET_ID:
        adapter = FinQaAdapter(record, store)
        splits = tuple(
            _finqa_split_summary(record, adapter, split) for split in ("train", "dev", "test")
        )
    elif record.dataset_id == TATQA_DATASET_ID:
        tatqa_adapter = TatQaAdapter(record, store)
        tatqa_adapter.validate_unscored_test_input()
        splits = tuple(
            _tatqa_split_summary(record, tatqa_adapter, split) for split in ("train", "dev", "test")
        )
    else:
        raise ValueError(f"No fixed-context adapter for dataset: {record.dataset_id}")
    return AdapterValidationReport(
        dataset_id=record.dataset_id,
        dataset_version=record.dataset_version,
        artifacts=artifacts,
        splits=splits,
        limitations=(
            "This report validates artifact integrity, conversion, and scorer contracts only.",
            "It is not an offline-capability or live-model benchmark result.",
            (
                "One FinQA train case has an unresolvable text_-1 supporting-fact sentinel."
                if record.dataset_id == FINQA_DATASET_ID
                else (
                    "The 1669-question test input and 1663-question released gold "
                    "have no shared UIDs."
                )
            ),
        ),
    )


def _finqa_split_summary(
    record: DatasetRecord,
    adapter: FinQaAdapter,
    split: str,
) -> FixedContextSplitSummary:
    artifact = _artifact(record, f"finqa-{split}")
    case_count, digest = stable_case_digest(adapter.iter_split(split))
    document_count, question_count = _registered_counts(artifact)
    return FixedContextSplitSummary(
        split=split,
        input_document_count=document_count,
        input_question_count=question_count,
        scorable_case_count=case_count,
        excluded_question_count=question_count - case_count,
        case_sha256=digest,
    )


def _tatqa_split_summary(
    record: DatasetRecord,
    adapter: TatQaAdapter,
    split: str,
) -> FixedContextSplitSummary:
    input_artifact = _artifact(
        record,
        "tatqa-test-gold" if split == "test" else f"tatqa-{split}",
    )
    case_count, digest = stable_case_digest(adapter.iter_split(split))
    document_count, question_count = _registered_counts(input_artifact)
    return FixedContextSplitSummary(
        split=split,
        input_document_count=document_count,
        input_question_count=question_count,
        scorable_case_count=case_count,
        excluded_question_count=question_count - case_count,
        case_sha256=digest,
    )


def _artifact(record: DatasetRecord, artifact_id: str) -> DatasetArtifact:
    artifact = next(
        (candidate for candidate in record.artifacts if candidate.artifact_id == artifact_id),
        None,
    )
    if artifact is None:
        raise ValueError(f"Dataset artifact is not registered: {artifact_id}")
    return artifact


def _registered_counts(artifact: DatasetArtifact) -> tuple[int, int]:
    if artifact.document_count is None or artifact.question_count is None:
        raise ValueError(f"Dataset split counts are not registered: {artifact.artifact_id}")
    return artifact.document_count, artifact.question_count


def _write_markdown_report(report: AdapterValidationReport, path: Path) -> None:
    lines = [
        f"# {report.dataset_id} Adapter Validation",
        "",
        f"- Dataset version: `{report.dataset_version}`",
        f"- Adapter version: `{report.adapter_version}`",
        "- Evidence layer: `deterministic_contract`",
        "- Model executed: `false`",
        "- Official metric scores: `null`",
        "",
        "| Split | Documents | Input questions | Scorable | Excluded | Case SHA-256 |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    lines.extend(
        "| "
        f"{split.split} | {split.input_document_count} | {split.input_question_count} | "
        f"{split.scorable_case_count} | {split.excluded_question_count} | "
        f"`{split.case_sha256}` |"
        for split in report.splits
    )
    lines.extend(["", "This report does not contain a model benchmark result.", ""])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


async def _run(args: argparse.Namespace) -> None:
    registry = load_dataset_registry(cast(Path, args.registry))
    dataset_ids = tuple(cast(list[str], args.dataset))
    root = cast(Path, args.root)
    await materialize_fixed_context_datasets(
        registry,
        root=root,
        dataset_ids=dataset_ids,
    )
    records = {record.dataset_id: record for record in registry.records}
    report_directory = cast(Path, args.report_directory)
    for dataset_id in dataset_ids:
        report = build_adapter_report(records[dataset_id], root=root)
        stem = "finqa-adapter-v1" if dataset_id == FINQA_DATASET_ID else "tatqa-adapter-v1"
        write_adapter_report(report, report_directory / f"{stem}.json")
        _write_markdown_report(report, report_directory / f"{stem}.md")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Materialize and validate pinned FinQA and TAT-QA artifacts"
    )
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--report-directory", required=True, type=Path)
    parser.add_argument(
        "--dataset",
        action="append",
        choices=_SUPPORTED_DATASETS,
        default=None,
    )
    args = parser.parse_args(argv)
    if args.dataset is None:
        args.dataset = list(_SUPPORTED_DATASETS)
    asyncio.run(_run(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
