"""加权 RRF 融合（P5 核心）。

重点验证三件容易错的事：
1. **rank 是 1-based**（Qdrant 原生是 0-based，混用会让分数全错）；
2. 两路都命中的文档必须排在单路命中之前（多路一致的先验）；
3. 单路缺失时不能崩，且 `degraded` 要标出来——否则上层不知道结果质量下降了。
"""

from __future__ import annotations

import pytest

from codeatlas.retrieval.bm25 import ScoredDoc
from codeatlas.retrieval.fusion import (
    degrade_note,
    normalize_by_max,
    weighted_rrf,
)


def docs(*specs: tuple[str, float]) -> list[ScoredDoc]:
    return [
        ScoredDoc(chunk_id=cid, score=score, rank=rank)
        for rank, (cid, score) in enumerate(specs, start=1)
    ]


def test_rrf_formula_is_one_based():
    """单路单条：score 必须等于 w/(k+1)，不是 w/k。"""
    result = weighted_rrf(docs(("a", 9.0)), [], w_dense=1.0, w_bm25=0.0, k=60)

    assert result.hits[0].score == pytest.approx(1.0 / 61)


def test_rrf_second_rank():
    result = weighted_rrf(docs(("a", 9.0), ("b", 8.0)), [], w_dense=1.0, w_bm25=0.0, k=60)

    assert result.hits[1].score == pytest.approx(1.0 / 62)


def test_both_lists_contribute():
    dense = docs(("shared", 1.0))
    bm25 = docs(("shared", 2.0))

    result = weighted_rrf(dense, bm25, w_dense=1.0, w_bm25=0.75, k=60)

    assert result.hits[0].chunk_id == "shared"
    assert result.hits[0].score == pytest.approx(1.0 / 61 + 0.75 / 61)
    assert result.hits[0].in_both


def test_doc_in_both_beats_single_route_with_same_score():
    """两路都命中的应当优先，这是多路一致的可信度先验。"""
    dense = docs(("only_dense", 1.0), ("both", 1.0))
    bm25 = docs(("only_bm25", 1.0), ("both", 1.0))

    result = weighted_rrf(dense, bm25, w_dense=1.0, w_bm25=1.0, k=60)

    assert result.hits[0].chunk_id == "both"


def test_weights_matter():
    dense = docs(("d", 1.0))
    bm25 = docs(("b", 1.0))

    result = weighted_rrf(dense, bm25, w_dense=1.0, w_bm25=0.25, k=60)

    assert result.hits[0].chunk_id == "d"
    assert result.hits[1].chunk_id == "b"


def test_dense_only_degraded():
    result = weighted_rrf(docs(("a", 1.0)), None)

    assert result.degraded == ["bm25_unavailable"]
    assert result.is_degraded
    assert "BM25 不可用" in result.note()


def test_bm25_only_degraded():
    result = weighted_rrf(None, docs(("a", 1.0)))

    assert result.degraded == ["dense_unavailable"]
    assert "向量检索不可用" in result.note()


def test_both_unavailable_is_empty_not_crash():
    result = weighted_rrf(None, None, w_dense=1.0, w_bm25=0.75)

    assert result.hits == []
    assert set(result.degraded) == {"dense_unavailable", "bm25_unavailable"}


def test_zero_weight_route_ignored():
    result = weighted_rrf(docs(("d", 1.0)), docs(("b", 1.0)), w_dense=1.0, w_bm25=0.0)

    assert [h.chunk_id for h in result.hits] == ["d"]
    assert result.hits[0].bm25_rank is None


def test_limit_truncates():
    result = weighted_rrf(docs(*[(f"c{i}", 1.0) for i in range(20)]), [], limit=5)

    assert len(result.hits) == 5
    assert result.top_ids(3) == ["c0", "c1", "c2"]


def test_ordering_is_deterministic():
    """同分同路时必须稳定排序，否则评测结果无法复现。"""
    a = docs(("x", 1.0), ("y", 1.0))
    b = docs(("z", 1.0))

    first = weighted_rrf(a, b).top_ids(3)
    second = weighted_rrf(a, b).top_ids(3)

    assert first == second


def test_invalid_params():
    with pytest.raises(ValueError, match="rrf_k"):
        weighted_rrf([], [], k=0)
    with pytest.raises(ValueError, match="权重"):
        weighted_rrf([], [], w_dense=-1.0)


def test_normalize_by_max_scales_top_to_one():
    result = weighted_rrf(docs(("a", 1.0), ("b", 1.0)), [], w_dense=1.0, w_bm25=0.0)
    normalized = normalize_by_max(result)

    assert normalized.hits[0].score == pytest.approx(1.0)
    assert normalized.hits[1].score == pytest.approx((1 / 62) / (1 / 61))


def test_normalize_handles_empty():
    assert normalize_by_max(weighted_rrf(None, None)).hits == []


def test_degrade_note_empty():
    assert degrade_note([]) == ""
