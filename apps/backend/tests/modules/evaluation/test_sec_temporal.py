from __future__ import annotations

import hashlib
import runpy
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import cast
from xml.etree import ElementTree

import pytest
from pydantic import ValidationError

from industry_platform.modules.evaluation.release import QuestionLanguage, canonical_sha256
from industry_platform.modules.evaluation.sec_temporal import (
    SecTemporalArtifactKind,
    SecTemporalEvidenceKind,
    SecTemporalManifest,
    build_sec_temporal_report,
    load_sec_temporal_manifest,
)

ROOT = Path(__file__).resolve().parents[5]
MANIFEST_PATH = ROOT / "evals" / "scenarios" / "sec-temporal-v1.json"
GENERATOR_PATH = ROOT / "evals" / "generators" / "sec_temporal_v1.py"
_XBRLI = "http://www.xbrl.org/2003/instance"
_US_GAAP = "http://fasb.org/us-gaap/2025"
_AAPL = "http://www.apple.com/2025"


def _manifest_payload() -> dict[str, object]:
    return deepcopy(load_sec_temporal_manifest(MANIFEST_PATH).model_dump(mode="json"))


def _records(payload: dict[str, object], key: str) -> list[dict[str, object]]:
    value = payload[key]
    assert isinstance(value, list)
    return cast(list[dict[str, object]], value)


def test_manifest_expands_to_frozen_bilingual_release_cases() -> None:
    manifest = load_sec_temporal_manifest(MANIFEST_PATH)
    cases = manifest.expand_cases()

    assert len(manifest.pairs) == 30
    assert len(cases) == 60
    assert len({source.accession for source in manifest.sources}) == 11
    assert {case.question.language for case in cases} == {
        QuestionLanguage.EN,
        QuestionLanguage.ZH,
    }
    for pair in manifest.pairs:
        paired = tuple(case for case in cases if case.pair_id == pair.pair_id)
        assert len(paired) == 2
        assert paired[0].answer_gold == paired[1].answer_gold
        assert paired[0].sec_gold == paired[1].sec_gold
        assert paired[0].evidence_keys == paired[1].evidence_keys
        assert paired[0].budget == paired[1].budget
        assert paired[0].trajectory == paired[1].trajectory


def test_generated_manifest_matches_the_committed_contract() -> None:
    namespace = runpy.run_path(str(GENERATOR_PATH))
    build_manifest = cast(Callable[[], SecTemporalManifest], namespace["build_manifest"])

    assert canonical_sha256(build_manifest()) == canonical_sha256(
        load_sec_temporal_manifest(MANIFEST_PATH)
    )


def test_manifest_rejects_split_leakage() -> None:
    payload = _manifest_payload()
    source = _records(payload, "sources")[0]
    source["split"] = "release_holdout"

    with pytest.raises(ValidationError, match="crosses a split"):
        SecTemporalManifest.model_validate(payload)


def test_manifest_rejects_future_source_made_visible_by_cutoff() -> None:
    payload = _manifest_payload()
    gold = next(
        item
        for item in _records(payload, "gold")
        if item["gold_id"] == "gold-p25-aapl-2025-before-filing"
    )
    scope = gold["scope"]
    assert isinstance(scope, dict)
    scope["as_of"] = "2025-11-01T00:00:00Z"

    with pytest.raises(ValidationError, match="point-in-time cutoff"):
        SecTemporalManifest.model_validate(payload)


def test_manifest_rejects_future_evidence_in_visible_gold() -> None:
    payload = _manifest_payload()
    gold = next(
        item
        for item in _records(payload, "gold")
        if item["gold_id"] == "gold-p25-aapl-2025-before-filing"
    )
    evidence = gold["evidence_keys"]
    assert isinstance(evidence, list)
    evidence.append("aapl-rd-2025")

    with pytest.raises(ValidationError, match="leaks non-visible Evidence"):
        SecTemporalManifest.model_validate(payload)


def test_manifest_rejects_noncanonical_evidence_locator() -> None:
    payload = _manifest_payload()
    _records(payload, "evidence")[0]["locator"] = "sec-xbrl://fabricated"

    with pytest.raises(ValidationError, match="locator is not canonical"):
        SecTemporalManifest.model_validate(payload)


def test_manifest_rejects_language_or_category_denominator_drift() -> None:
    language_payload = _manifest_payload()
    questions = _records(language_payload, "pairs")[0]["questions"]
    assert isinstance(questions, list)
    questions.reverse()
    with pytest.raises(ValidationError, match="ordered English and Chinese"):
        SecTemporalManifest.model_validate(language_payload)

    coverage_payload = _manifest_payload()
    pairs = _records(coverage_payload, "pairs")
    pairs[:] = [item for item in pairs if item["category"] != "direct_fact"]
    with pytest.raises(ValidationError, match=r"pair and case minimums|category coverage"):
        SecTemporalManifest.model_validate(coverage_payload)

    review_payload = _manifest_payload()
    review_payload["language_review_sample_pair_ids"] = ["missing-pair"]
    with pytest.raises(ValidationError, match="language review sample"):
        SecTemporalManifest.model_validate(review_payload)


def test_manifest_rejects_no_answer_without_a_strict_future_source() -> None:
    payload = _manifest_payload()
    gold = next(
        item
        for item in _records(payload, "gold")
        if item["gold_id"] == "gold-p26-quest-before-amendment"
    )
    scope = gold["scope"]
    assert isinstance(scope, dict)
    scope["forbidden_future_source_ids"] = []

    with pytest.raises(ValidationError, match="no-answer gold"):
        SecTemporalManifest.model_validate(payload)


def _xbrl_bytes(evidence: list[dict[str, object]]) -> bytes:
    ElementTree.register_namespace("xbrli", _XBRLI)
    ElementTree.register_namespace("us-gaap", _US_GAAP)
    ElementTree.register_namespace("aapl", _AAPL)
    root = ElementTree.Element(f"{{{_XBRLI}}}xbrl")
    for index, item in enumerate(evidence):
        period = item["period"]
        assert isinstance(period, dict)
        context_id = f"c{index}"
        context = ElementTree.SubElement(root, f"{{{_XBRLI}}}context", id=context_id)
        entity = ElementTree.SubElement(context, f"{{{_XBRLI}}}entity")
        ElementTree.SubElement(entity, f"{{{_XBRLI}}}identifier").text = "fixture"
        period_node = ElementTree.SubElement(context, f"{{{_XBRLI}}}period")
        if period["instant"] is not None:
            ElementTree.SubElement(period_node, f"{{{_XBRLI}}}instant").text = str(
                period["instant"]
            )
        else:
            ElementTree.SubElement(period_node, f"{{{_XBRLI}}}startDate").text = str(
                period["start_date"]
            )
            ElementTree.SubElement(period_node, f"{{{_XBRLI}}}endDate").text = str(
                period["end_date"]
            )
        unit_id = f"u{index}"
        unit = ElementTree.SubElement(root, f"{{{_XBRLI}}}unit", id=unit_id)
        measure = "iso4217:USD" if item["unit"] == "USD" else str(item["unit"])
        ElementTree.SubElement(unit, f"{{{_XBRLI}}}measure").text = measure
        namespace = _US_GAAP if item["taxonomy"] == "us-gaap" else _AAPL
        fact = ElementTree.SubElement(
            root,
            f"{{{namespace}}}{item['concept']}",
            contextRef=context_id,
            unitRef=unit_id,
        )
        fact.text = str(item["expected_value"])
    return cast(bytes, ElementTree.tostring(root, encoding="utf-8", xml_declaration=True))


def _fixture_manifest(
    tmp_path: Path,
    *,
    malicious_source_id: str | None = None,
) -> SecTemporalManifest:
    payload = _manifest_payload()
    evidence_records = _records(payload, "evidence")
    evidence_by_source: dict[str, list[dict[str, object]]] = {}
    for item in evidence_records:
        source_id = item["source_id"]
        if isinstance(source_id, str):
            evidence_by_source.setdefault(source_id, []).append(item)
    html_digests: dict[str, str] = {}
    for source in _records(payload, "sources"):
        source_id = source["source_id"]
        assert isinstance(source_id, str)
        source_evidence = evidence_by_source.get(source_id, [])
        anchors = " ".join(
            str(item["anchor_text"])
            for item in source_evidence
            if item["kind"] == SecTemporalEvidenceKind.HTML_ANCHOR.value
        )
        html_payload = f"<html><body>{anchors}</body></html>".encode()
        facts = [
            item
            for item in source_evidence
            if item["kind"] == SecTemporalEvidenceKind.XBRL_FACT.value
        ]
        xbrl_payload = (
            b'<!DOCTYPE x [<!ENTITY y "boom">]><x>&y;</x>'
            if source_id == malicious_source_id
            else _xbrl_bytes(facts)
        )
        artifacts = source["artifacts"]
        assert isinstance(artifacts, list)
        for artifact in artifacts:
            assert isinstance(artifact, dict)
            content = (
                html_payload
                if artifact["kind"] == SecTemporalArtifactKind.HTML.value
                else xbrl_payload
            )
            artifact["byte_size"] = len(content)
            artifact["sha256"] = hashlib.sha256(content).hexdigest()
            relative_path = artifact["relative_path"]
            assert isinstance(relative_path, str)
            path = tmp_path / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            if artifact["kind"] == SecTemporalArtifactKind.HTML.value:
                html_digests[source_id] = str(artifact["sha256"])
    source_records = {str(source["source_id"]): source for source in _records(payload, "sources")}
    for item in evidence_records:
        if item["kind"] != SecTemporalEvidenceKind.SOURCE_SNAPSHOT.value:
            continue
        source_id = item["source_id"]
        assert isinstance(source_id, str)
        source = source_records[source_id]
        item["locator"] = (
            f"sec-source://{source['cik']}/{source['accession']}/{source['primary_document']}"
            f"#sha256={html_digests[source_id]}"
        )
    return SecTemporalManifest.model_validate(payload)


def test_contract_report_resolves_all_fixture_artifacts_and_evidence(tmp_path: Path) -> None:
    manifest = _fixture_manifest(tmp_path)

    report = build_sec_temporal_report(manifest, root=tmp_path)

    assert report.verified_artifact_count == 22
    assert report.resolved_evidence_count == len(manifest.evidence) == 35
    assert report.expanded_case_count == 60
    assert report.pair_gold_identity_rate == 1.0
    assert report.future_leakage_violations == 0
    assert report.model_executed is False
    assert report.offline_capability_scored is False


def test_contract_report_rejects_xbrl_entity_declarations(tmp_path: Path) -> None:
    manifest = _fixture_manifest(tmp_path, malicious_source_id="aapl-2020-10k")

    with pytest.raises(ValueError, match="DTD or entity"):
        build_sec_temporal_report(manifest, root=tmp_path)
