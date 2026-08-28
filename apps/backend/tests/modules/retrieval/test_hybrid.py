"""Deterministic RRF contracts for hybrid SEC retrieval."""

from uuid import UUID

import pytest

from industry_platform.modules.retrieval.domain import (
    DenseCandidate,
    LexicalCandidate,
    RetrievalChannel,
    reciprocal_rank_fusion,
)

VERSION_ID = UUID("11111111-1111-4111-8111-111111111111")
CHUNK_A = UUID("22222222-2222-4222-8222-222222222222")
CHUNK_B = UUID("33333333-3333-4333-8333-333333333333")
CHUNK_C = UUID("44444444-4444-4444-8444-444444444444")


def test_rrf_fuses_ranks_without_mixing_raw_score_spaces() -> None:
    result = reciprocal_rank_fusion(
        (
            DenseCandidate(CHUNK_A, VERSION_ID, 0.99),
            DenseCandidate(CHUNK_B, VERSION_ID, 0.70),
        ),
        (
            LexicalCandidate(CHUNK_B, VERSION_ID, 120.0),
            LexicalCandidate(CHUNK_C, VERSION_ID, 2.0),
        ),
        limit=3,
    )

    assert [item.chunk_id for item in result] == [CHUNK_B, CHUNK_A, CHUNK_C]
    assert result[0].channels == (RetrievalChannel.DENSE, RetrievalChannel.LEXICAL)
    assert result[0].dense_rank == 2
    assert result[0].lexical_rank == 1
    assert result[0].score == pytest.approx((1 / 62 + 1 / 61) / (2 / 61))


def test_rrf_deduplicates_each_channel_and_has_stable_uuid_tiebreak() -> None:
    result = reciprocal_rank_fusion(
        (
            DenseCandidate(CHUNK_B, VERSION_ID, 0.9),
            DenseCandidate(CHUNK_B, VERSION_ID, 0.8),
            DenseCandidate(CHUNK_A, VERSION_ID, 0.7),
        ),
        (),
        limit=3,
    )

    assert [item.chunk_id for item in result] == [CHUNK_B, CHUNK_A]
    assert [item.dense_rank for item in result] == [1, 2]
