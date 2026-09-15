from uuid import UUID

import pytest

from industry_platform.workflows.research.runtime import _cited_evidence_ids

SOURCES = (UUID(int=1), UUID(int=2))


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("I have no user question.", ()),
        ("A result without citations.", ()),
        ("Revenue [S1]", (SOURCES[0],)),
        ("Revenue [S1], margin [S2], repeated [S1]", SOURCES),
        ("Broken source [S3]", ()),
        ("Valid and broken [S1] [S999]", ()),
        ("Zero is invalid [S0]", ()),
    ],
)
def test_collected_sources_do_not_automatically_support_the_answer(
    answer: str, expected: tuple[UUID, ...]
) -> None:
    assert _cited_evidence_ids(answer, SOURCES) == expected


def test_stable_tool_labels_do_not_shift_with_excluded_or_deduplicated_sources() -> None:
    labels = {"T1S2": SOURCES[0], "T3S1": SOURCES[1], "T4S1": SOURCES[0]}
    assert _cited_evidence_ids("[T3S1] [T1S2] [T4S1]", SOURCES, labels) == SOURCES[::-1]
    assert _cited_evidence_ids("[T1S1] [T3S1]", SOURCES, labels) == ()
