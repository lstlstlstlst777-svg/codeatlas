"""重排：bge-reranker-v2-m3，带降级（P5）。

两个必须遵守的细节：

1. **返回的 `index` 是候选的原始下标**，不是排序位置。上层必须按下标回填 chunk_id，
   按顺序硬配会让重排后的每一条都指向错的 chunk（这个错误不会报错，只会让引用全错）。
2. 分数尺度跨 4 个数量级（相关 ~1e-1，不相关 ~1e-5），所以这里只做相对排序，
   **不要**在这一层加任何阈值判断。阈值属于 gate，必须在 dev 集上标定。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from codeatlas.providers.base import RerankHit


@dataclass(slots=True)
class RerankResult:
    chunk_ids: list[str]
    scores: list[float]
    degraded: list[str] = field(default_factory=list)

    @property
    def top_score(self) -> float | None:
        return self.scores[0] if self.scores else None

    @property
    def is_degraded(self) -> bool:
        return bool(self.degraded)


async def rerank_candidates(
    reranker,
    query: str,
    candidates: list[tuple[str, str]],
    *,
    top_n: int,
) -> RerankResult:
    """`candidates` 是 (chunk_id, text) 列表，顺序即原始候选顺序。

    重排失败一律降级为原顺序，绝不因为 rerank 挂了就返回空——
    降级后至少还有 RRF 的结果可用，返回空等于让整个问答失败。
    """
    if not candidates:
        return RerankResult(chunk_ids=[], scores=[], degraded=[])
    if top_n <= 0:
        return RerankResult(chunk_ids=[], scores=[], degraded=[])

    ids = [cid for cid, _ in candidates]
    texts = [text for _, text in candidates]

    try:
        hits: list[RerankHit] = await reranker.rerank(query, texts, min(top_n, len(texts)))
    except Exception:  # noqa: BLE001 - rerank 是可选增强，任何失败都降级
        return RerankResult(
            chunk_ids=ids[:top_n],
            scores=[0.0] * min(top_n, len(ids)),
            degraded=["rerank_unavailable"],
        )

    ordered: list[tuple[str, float]] = []
    seen: set[str] = set()
    for hit in hits:
        # index 是原始下标，必须回填；越界说明 provider 返回了脏数据，跳过而不是崩
        if not 0 <= hit.index < len(ids):
            continue
        chunk_id = ids[hit.index]
        if chunk_id in seen:
            continue
        seen.add(chunk_id)
        ordered.append((chunk_id, float(hit.score)))

    if not ordered:
        return RerankResult(
            chunk_ids=ids[:top_n],
            scores=[0.0] * min(top_n, len(ids)),
            degraded=["rerank_empty"],
        )

    ordered = ordered[:top_n]
    return RerankResult(
        chunk_ids=[cid for cid, _ in ordered],
        scores=[score for _, score in ordered],
    )
