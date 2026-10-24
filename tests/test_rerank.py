"""重排：下标回填与降级（P5）。

最容易出错的不是调 API，而是 `index` 的语义：它指向**原始候选数组**，
不是排序位置。按顺序硬配会让每一条都指向错的 chunk，而且不会报错——
答案里的引用会全部错误，却看起来"有来源"。
"""

from __future__ import annotations

import pytest

from codeatlas.providers.base import RerankHit
from codeatlas.retrieval.rerank import rerank_candidates


class FakeReranker:
    def __init__(self, hits=None, error: Exception | None = None):
        self.hits = hits or []
        self.error = error
        self.calls: list[tuple[str, list[str], int]] = []

    async def rerank(self, query, documents, top_n):
        self.calls.append((query, list(documents), top_n))
        if self.error:
            raise self.error
        return self.hits

    async def aclose(self):
        return None


CANDIDATES = [
    ("id0", "text0"),
    ("id1", "text1"),
    ("id2", "text2"),
]


async def test_index_maps_back_to_original_position():
    """reranker 说 index=2 最相关，返回的必须是 id2 而不是 id0。"""
    reranker = FakeReranker(hits=[RerankHit(index=2, score=0.9), RerankHit(index=0, score=0.1)])

    result = await rerank_candidates(reranker, "q", CANDIDATES, top_n=2)

    assert result.chunk_ids == ["id2", "id0"]
    assert result.scores == [0.9, 0.1]


async def test_out_of_range_index_is_skipped():
    """provider 返回脏下标时跳过，不能崩也不能错配。"""
    reranker = FakeReranker(hits=[RerankHit(index=99, score=0.9), RerankHit(index=1, score=0.5)])

    result = await rerank_candidates(reranker, "q", CANDIDATES, top_n=2)

    assert result.chunk_ids == ["id1"]


async def test_negative_index_is_skipped():
    reranker = FakeReranker(hits=[RerankHit(index=-1, score=0.9), RerankHit(index=0, score=0.5)])

    result = await rerank_candidates(reranker, "q", CANDIDATES, top_n=2)

    assert result.chunk_ids == ["id0"]


async def test_duplicate_index_kept_once():
    reranker = FakeReranker(hits=[RerankHit(index=1, score=0.9), RerankHit(index=1, score=0.8)])

    result = await rerank_candidates(reranker, "q", CANDIDATES, top_n=2)

    assert result.chunk_ids == ["id1"]


async def test_failure_degrades_to_original_order():
    """rerank 挂了要退回原始顺序，不能返回空——空意味着整个问答失败。"""
    reranker = FakeReranker(error=RuntimeError("503"))

    result = await rerank_candidates(reranker, "q", CANDIDATES, top_n=2)

    assert result.chunk_ids == ["id0", "id1"]
    assert result.degraded == ["rerank_unavailable"]
    assert result.is_degraded


async def test_empty_result_degrades():
    reranker = FakeReranker(hits=[])

    result = await rerank_candidates(reranker, "q", CANDIDATES, top_n=2)

    assert result.chunk_ids == ["id0", "id1"]
    assert result.degraded == ["rerank_empty"]


async def test_top_n_caps_result():
    reranker = FakeReranker(hits=[RerankHit(index=i, score=1.0 - i / 10) for i in range(3)])

    result = await rerank_candidates(reranker, "q", CANDIDATES, top_n=1)

    assert len(result.chunk_ids) == 1


async def test_top_n_larger_than_candidates():
    reranker = FakeReranker(hits=[RerankHit(index=i, score=1.0) for i in range(3)])

    result = await rerank_candidates(reranker, "q", CANDIDATES, top_n=10)

    assert len(result.chunk_ids) == 3
    # 请求 top_n 时应按候选数收敛，不能向 API 要超过候选数的结果
    assert reranker.calls[0][2] == 3


async def test_empty_candidates_short_circuits():
    reranker = FakeReranker(hits=[RerankHit(index=0, score=1.0)])

    result = await rerank_candidates(reranker, "q", [], top_n=5)

    assert result.chunk_ids == []
    assert reranker.calls == []


async def test_zero_top_n_short_circuits():
    reranker = FakeReranker(hits=[])

    result = await rerank_candidates(reranker, "q", CANDIDATES, top_n=0)

    assert result.chunk_ids == []


async def test_top_score_is_first():
    reranker = FakeReranker(hits=[RerankHit(index=1, score=0.42)])

    result = await rerank_candidates(reranker, "q", CANDIDATES, top_n=1)

    assert result.top_score == pytest.approx(0.42)


async def test_texts_passed_through_in_order():
    reranker = FakeReranker(hits=[RerankHit(index=0, score=1.0)])

    await rerank_candidates(reranker, "my query", CANDIDATES, top_n=1)

    query, documents, _ = reranker.calls[0]
    assert query == "my query"
    assert documents == ["text0", "text1", "text2"]
