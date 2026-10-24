"""加权 RRF 融合（P5）。

公式：`score(d) = Σ_i w_i / (k + rank_i(d))`，k=60，rank **1-based**。

为什么在应用层自己实现、不用 Qdrant 的 `FusionQuery`：
Qdrant 原生 RRF 默认 `k=2` 且 rank 从 0 开始。这两个差异会让同样的候选集算出
完全不同的分数，历史上跑过的评测数字无法与新数字比较——对一个"权重必须实验得出"
的项目来说，这是致命的。自己实现还能把每一路的 rank 与原始分数都带出来，
排查"为什么这条没进 top-5"时才有东西可看。

单路缺失的处理：不做权重重归一化。理由见 `degrade_note()`。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from codeatlas.retrieval.bm25 import ScoredDoc


@dataclass(slots=True)
class FusedHit:
    chunk_id: str
    score: float
    dense_rank: int | None = None
    bm25_rank: int | None = None
    dense_score: float | None = None
    bm25_score: float | None = None

    @property
    def in_both(self) -> bool:
        return self.dense_rank is not None and self.bm25_rank is not None


@dataclass(slots=True)
class FusionResult:
    hits: list[FusedHit]
    degraded: list[str] = field(default_factory=list)

    @property
    def is_degraded(self) -> bool:
        return bool(self.degraded)

    def top_ids(self, n: int) -> list[str]:
        return [hit.chunk_id for hit in self.hits[:n]]

    def note(self) -> str:
        return degrade_note(self.degraded)


def weighted_rrf(
    dense: list[ScoredDoc] | None,
    bm25: list[ScoredDoc] | None,
    *,
    w_dense: float = 1.0,
    w_bm25: float = 0.75,
    k: int = 60,
    limit: int | None = None,
) -> FusionResult:
    """两路候选融合。任一路传 None 视为该路不可用（降级）。"""
    if k <= 0:
        raise ValueError(f"rrf_k must be > 0, got {k}")
    if w_dense < 0 or w_bm25 < 0:
        raise ValueError("RRF 权重不能为负")

    degraded: list[str] = []
    if dense is None:
        degraded.append("dense_unavailable")
        dense = []
    if bm25 is None:
        degraded.append("bm25_unavailable")
        bm25 = []

    hits: dict[str, FusedHit] = {}

    if w_dense > 0:
        for doc in dense:
            entry = hits.setdefault(doc.chunk_id, FusedHit(doc.chunk_id, 0.0))
            entry.dense_rank = doc.rank
            entry.dense_score = doc.score
            entry.score += w_dense / (k + doc.rank)

    if w_bm25 > 0:
        for doc in bm25:
            entry = hits.setdefault(doc.chunk_id, FusedHit(doc.chunk_id, 0.0))
            entry.bm25_rank = doc.rank
            entry.bm25_score = doc.score
            entry.score += w_bm25 / (k + doc.rank)

    # 排序键：融合分降序；同分时优先"两路都命中"（多路一致通常更可信），
    # 再按 chunk_id 稳定排序，保证同样输入永远得到同样顺序（评测需要可复现）。
    ordered = sorted(
        hits.values(),
        key=lambda h: (-h.score, 0 if h.in_both else 1, h.chunk_id),
    )
    if limit is not None:
        ordered = ordered[:limit]
    return FusionResult(hits=ordered, degraded=degraded)


def normalize_by_max(result: FusionResult) -> FusionResult:
    """把融合分按 top1 归一化到 [0,1]。

    降级时（只剩一路）分数量级会变——两个 weight 都参与时最高分接近
    1/(k+1) + 0.75/(k+1)，只剩一路时只有 1/(k+1)。拒答阈值若直接比较融合分，
    降级会让系统误判为"不相关"从而全部拒答，所以降级场景必须用归一化分数。
    """
    if not result.hits:
        return result
    top = result.hits[0].score
    if top <= 0:
        return result
    scaled = [
        FusedHit(
            chunk_id=h.chunk_id,
            score=h.score / top,
            dense_rank=h.dense_rank,
            bm25_rank=h.bm25_rank,
            dense_score=h.dense_score,
            bm25_score=h.bm25_score,
        )
        for h in result.hits
    ]
    return FusionResult(hits=scaled, degraded=list(result.degraded))


def degrade_note(degraded: list[str]) -> str:
    """降级说明，写进响应与日志，避免"结果变差但不知道为什么"。"""
    if not degraded:
        return ""
    mapping = {
        "dense_unavailable": "向量检索不可用，仅用 BM25",
        "bm25_unavailable": "BM25 不可用，仅用向量检索",
        "rerank_unavailable": "重排不可用，按融合分排序",
    }
    return "；".join(mapping.get(item, item) for item in degraded)
