"""混合检索编排（P5）：双路召回 → 加权 RRF → 重排 → top_k。

设计要点：
- **降级是分级别的**，不是"出错就返回空"。dense 挂只丢一半信号，BM25 仍能答；
  embed 挂则一路都活不了，此时返回 `degraded` 让上层决定是否拒答。
- dense 与 BM25 并行，BM25 在线程外直接跑（0.5ms 级），dense 走 to_thread
  （qdrant 客户端是同步 API，直接 await 会阻塞事件循环）。
- 重排只对候选窗口（rerank_window=30）做，不是对全部 100 条——这是延迟与召回的折中。
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field

from codeatlas.indexing.types import Chunk
from codeatlas.providers.base import EmbeddingProvider, Reranker
from codeatlas.retrieval.bm25 import BM25Index, ScoredDoc
from codeatlas.retrieval.dense import DenseIndex
from codeatlas.retrieval.fusion import FusionResult, degrade_note, weighted_rrf
from codeatlas.retrieval.rerank import RerankResult, rerank_candidates
from codeatlas.settings import Settings

# 形如 `zmalloc_used_memory` / `aeCreateEventLoop` 的完整标识符
_IDENTIFIER_RE = re.compile(r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+|[a-z]+[A-Z][A-Za-z0-9]*)\b")

# 明确在问"模块 API"时才保留 RM_*，否则它们只是给外部模块用的包装层
_MODULE_HINTS = ("module", "模块", "rm_", "redis module")


def extract_identifiers(query: str) -> set[str]:
    """抽出查询里的完整标识符（下划线或驼峰）。"""
    return {m.group(1) for m in _IDENTIFIER_RE.finditer(query or "")}


def is_module_api(chunk: Chunk) -> bool:
    """`src/module.c` 里的 `RM_*` 是给外部模块用的 API，不是引擎内部实现。

    实测：问"跳表怎么插入节点"命中 `RM_DigestAddStringBuffer`、
    问"内存分配在哪实现"命中 `RM_Calloc`——都是模块 API 抢占了核心实现的位置。
    """
    return chunk.path.endswith("module.c") and (chunk.symbol or "").startswith("RM_")


def asks_about_modules(query: str) -> bool:
    lowered = (query or "").lower()
    return any(hint in lowered for hint in _MODULE_HINTS)


@dataclass(slots=True)
class RetrievedChunk:
    chunk: Chunk
    score: float
    rank: int
    source: str  # "rerank" / "fusion"
    rerank_score: float | None = None


@dataclass(slots=True)
class RetrievalResult:
    query: str
    hits: list[RetrievedChunk]
    fusion: FusionResult | None = None
    rerank: RerankResult | None = None
    degraded: list[str] = field(default_factory=list)
    timings_ms: dict[str, float] = field(default_factory=dict)
    # 仅用于桩实现/评测回放：没有真实 rerank 时手工指定相关性特征
    manual_top_score: float | None = None

    @property
    def is_degraded(self) -> bool:
        return bool(self.degraded)

    @property
    def top_rerank_score(self) -> float | None:
        if self.rerank is not None and self.rerank.top_score is not None:
            return self.rerank.top_score
        return self.manual_top_score

    def note(self) -> str:
        return degrade_note(self.degraded)

    def chunk_ids(self) -> list[str]:
        return [hit.chunk.chunk_id for hit in self.hits]


class HybridRetriever:
    def __init__(
        self,
        settings: Settings,
        *,
        dense: DenseIndex,
        bm25: BM25Index,
        chunks: dict[str, Chunk],
        embedder: EmbeddingProvider,
        reranker: Reranker,
    ) -> None:
        self.settings = settings
        self.dense = dense
        self.bm25 = bm25
        self.chunks = chunks
        self.embedder = embedder
        self.reranker = reranker

    async def retrieve(self, query: str, *, top_k: int | None = None) -> RetrievalResult:
        cfg = self.settings
        top_k = top_k or cfg.top_k
        timings: dict[str, float] = {}
        degraded: list[str] = []

        # --- 1. 双路召回（并行） ---
        started = time.perf_counter()
        dense_hits: list[ScoredDoc] | None = None
        bm25_hits: list[ScoredDoc] | None = None

        async def run_dense() -> list[ScoredDoc] | None:
            try:
                vector = (await self.embedder.embed([query]))[0]
            except Exception:  # noqa: BLE001 - 任何 embedding 失败都降级到纯 BM25
                nonlocal degraded
                degraded.append("embed_unavailable")
                return None
            try:
                return await asyncio.to_thread(self.dense.search, vector, cfg.dense_limit)
            except Exception:  # noqa: BLE001
                degraded.append("dense_unavailable")
                return None

        async def run_bm25() -> list[ScoredDoc] | None:
            try:
                return await asyncio.to_thread(self.bm25.search, query, cfg.bm25_limit)
            except Exception:  # noqa: BLE001
                degraded.append("bm25_unavailable")
                return None

        dense_hits, bm25_hits = await asyncio.gather(run_dense(), run_bm25())
        timings["recall"] = (time.perf_counter() - started) * 1000

        # --- 2. 融合 ---
        started = time.perf_counter()
        fusion = weighted_rrf(
            dense_hits,
            bm25_hits,
            w_dense=cfg.w_dense,
            w_bm25=cfg.w_bm25,
            k=cfg.rrf_k,
        )
        degraded.extend(fusion.degraded)
        timings["fuse"] = (time.perf_counter() - started) * 1000

        if not fusion.hits:
            return RetrievalResult(
                query=query,
                hits=[],
                fusion=fusion,
                degraded=_dedupe(degraded),
                timings_ms=timings,
            )

        # --- 3. 重排 ---
        window = self._build_window(query, fusion.hits)
        pinned = self._pinned_ids(query, window)
        candidates = [(hit.chunk_id, self._rerank_text(hit.chunk_id)) for hit in window]
        candidates = [(cid, text) for cid, text in candidates if text]

        started = time.perf_counter()
        rerank_result = await rerank_candidates(self.reranker, query, candidates, top_n=top_k)
        timings["rerank"] = (time.perf_counter() - started) * 1000
        degraded.extend(rerank_result.degraded)

        # --- 4. 组装 ---
        hits: list[RetrievedChunk] = []
        for rank, (chunk_id, score) in enumerate(
            zip(rerank_result.chunk_ids, rerank_result.scores, strict=False), start=1
        ):
            chunk = self.chunks.get(chunk_id)
            if chunk is None:
                continue
            hits.append(
                RetrievedChunk(
                    chunk=chunk,
                    score=score,
                    rank=rank,
                    source="rerank" if not rerank_result.is_degraded else "fusion",
                    rerank_score=score if not rerank_result.is_degraded else None,
                )
            )

        if not hits:
            # 重排后一条都没匹配上（provider 返回脏数据），退回融合序
            degraded.append("rerank_fallback")
            for rank, hit in enumerate(window[:top_k], start=1):
                chunk = self.chunks.get(hit.chunk_id)
                if chunk is not None:
                    hits.append(
                        RetrievedChunk(chunk=chunk, score=hit.score, rank=rank, source="fusion")
                    )

        hits = self._pin_symbol_hits(hits, pinned, top_k)

        return RetrievalResult(
            query=query,
            hits=hits,
            fusion=fusion,
            rerank=rerank_result,
            degraded=_dedupe(degraded),
            timings_ms=timings,
        )

    def _build_window(self, query: str, fused: list) -> list:
        """构造 rerank 候选窗口，剔除会抢占核心实现的模块 API。

        只剔除"没在问模块"时的 `RM_*`。问"怎么写 Redis 模块"时必须保留，
        否则会把用户真正想要的答案删掉。

        试过再剔除 `macro_const`（单行常量宏）：它能把 q025 的 top1
        从 `ACTIVE_EXPIRE_CYCLE_KEYS_PER_LOOP` 修成 `activeExpireCycleTryExpire`，
        但该问题仍因分数低被拒，同时让 6 条该拒的样本分数升高（Refusal F1 0.741→0.692）。
        **净效果为负，已回滚**。见 ISSUE-028。
        """
        keep_module = asks_about_modules(query)
        window = []
        for hit in fused:
            chunk = self.chunks.get(hit.chunk_id)
            if chunk is None:
                continue
            if not keep_module and is_module_api(chunk):
                continue
            window.append(hit)
            if len(window) >= self.settings.rerank_window:
                break
        return window

    def _pinned_ids(self, query: str, window: list) -> list[str]:
        """查询里出现完整标识符时，把 symbol 与之完全相等的 chunk 钉在最前。

        实测：问 `zmalloc_used_memory` 时 rerank 把 `zmalloc_usable_size` 排第一、
        问"内存分配在哪实现"时把 `RM_Calloc` 排第一。rerank 只看字面相似度，
        分不清"名字像"和"就是它"。用户写出一个完整标识符时，意图是明确的，
        这条确定性规则比模型打分更可靠。
        """
        identifiers = extract_identifiers(query)
        if not identifiers:
            return []
        return [
            hit.chunk_id
            for hit in window
            if (self.chunks.get(hit.chunk_id).symbol or "") in identifiers
        ]

    def _pin_symbol_hits(
        self, hits: list[RetrievedChunk], pinned: list[str], top_k: int
    ) -> list[RetrievedChunk]:
        """把钉住的符号提到最前，其余保持原顺序；顺便重排 rank。

        `score` 一律记 0.0 且 `rerank_score=None`：置顶来自**确定性规则**而不是模型打分，
        给它编一个 1.0 会让下游（拒答门、前端展示）误以为"模型给了满分"。
        """
        if not pinned:
            return hits
        by_id = {h.chunk.chunk_id: h for h in hits}
        ordered: list[RetrievedChunk] = []
        for chunk_id in pinned:
            hit = by_id.pop(chunk_id, None)
            if hit is not None:
                ordered.append(hit)
            else:
                chunk = self.chunks.get(chunk_id)
                if chunk is not None:
                    ordered.append(
                        RetrievedChunk(
                            chunk=chunk, score=0.0, rank=0, source="symbol_pin", rerank_score=None
                        )
                    )
        ordered.extend(h for h in hits if h.chunk.chunk_id in by_id)
        ordered = ordered[:top_k]
        for index, hit in enumerate(ordered, start=1):
            hit.rank = index
        return ordered

    def _rerank_text(self, chunk_id: str) -> str:
        """送给 reranker 的文本：签名 + 代码，不带 context_header。

        header 里的路径与行号对相关性判断没有帮助，反而会稀释代码本身的信号。
        """
        chunk = self.chunks.get(chunk_id)
        if chunk is None:
            return ""
        prefix = f"{chunk.signature}\n" if chunk.signature else ""
        return prefix + chunk.code


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
