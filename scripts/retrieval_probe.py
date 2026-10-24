"""端到端检索探针（P5）：dense + BM25 + RRF + rerank 在真实代码上跑。

报告每路召回、融合、重排后的 top-k，并逐条打印延迟。
用于回答三个问题：
1. hybrid 是否比单路好？（看 BM25 独有 vs dense 独有的命中）
2. rerank 有没有改变顺序？（对比融合序与重排序）
3. 延迟分布如何？（不含 LLM，验收线 p95 < 300ms）

用法：
    python scripts/retrieval_probe.py
"""

from __future__ import annotations

import asyncio
import statistics
import time
from pathlib import Path

from codeatlas.config import load_config
from codeatlas.indexing.store import load_chunks
from codeatlas.providers.registry import build_deps
from codeatlas.retrieval.bm25 import BM25Index
from codeatlas.retrieval.dense import DenseIndex
from codeatlas.retrieval.service import HybridRetriever

QUERIES = [
    "zmalloc_used_memory",
    "where is the event loop created for the server",
    "how does Redis expire keys",
    "replication handshake with master",
    "command table lookup by name",
    "how to parse long long from string",
    "list type push and pop",
    "acl user permission check",
    "rdb save string object",
    "cluster slot migration",
]


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(p / 100 * (len(ordered) - 1))))
    return ordered[idx]


async def main() -> int:
    root = Path(__file__).resolve().parents[1]
    settings = load_config(root=root)

    chunks = load_chunks(root / "data" / "chunks.jsonl.gz")
    chunk_map = {c.chunk_id: c for c in chunks}

    bm25 = BM25Index.load(
        root / "data" / "bm25.json.gz",
    )
    dense = DenseIndex.open(settings.qdrant_url, settings.qdrant_collection, settings.emb_dim)
    deps = build_deps(settings)

    retriever = HybridRetriever(
        settings,
        dense=dense,
        bm25=bm25,
        chunks=chunk_map,
        embedder=deps.embed,
        reranker=deps.rerank,
    )

    print(f"chunks={len(chunks)}  top_k={settings.top_k}  window={settings.rerank_window}")
    print(f"weights dense={settings.w_dense} bm25={settings.w_bm25} k={settings.rrf_k}\n")

    timings: list[float] = []
    rerank_moved = 0
    try:
        for query in QUERIES:
            started = time.perf_counter()
            result = await retriever.retrieve(query)
            total_ms = (time.perf_counter() - started) * 1000
            timings.append(total_ms)

            fused_order = [h.chunk_id for h in (result.fusion.hits if result.fusion else [])][
                : settings.top_k
            ]
            final_order = result.chunk_ids()
            moved = fused_order[: len(final_order)] != final_order
            rerank_moved += 1 if moved else 0

            flags = "".join(
                [
                    "D"
                    if any(h.dense_rank for h in (result.fusion.hits[:1] if result.fusion else []))
                    else "-",
                    "B"
                    if any(h.bm25_rank for h in (result.fusion.hits[:1] if result.fusion else []))
                    else "-",
                ]
            )
            print(f"[{query}]")
            print(
                f"  {total_ms:6.1f}ms  recall={result.timings_ms.get('recall', 0):.0f} "
                f"rerank={result.timings_ms.get('rerank', 0):.0f}  "
                f"rerank_moved={'yes' if moved else 'no'}  {flags}"
                + (f"  degraded={result.degraded}" if result.degraded else "")
            )
            for hit in result.hits:
                c = hit.chunk
                score = (
                    f"{hit.rerank_score:.4f}"
                    if hit.rerank_score is not None
                    else f"{hit.score:.5f}"
                )
                print(
                    f"    {hit.rank}. {score:>8}  {c.path}:{c.start_line}-{c.end_line}  "
                    f"[{c.node_type}] {c.symbol or '-'}"
                )
            print()
    finally:
        await deps.aclose()
        dense.close()

    print("=== latency (end-to-end, no LLM) ===")
    print(
        f"  n={len(timings)}  p50={statistics.median(timings):.0f}ms  "
        f"p95={pct(timings, 95):.0f}ms  max={max(timings):.0f}ms"
    )
    print(f"  rerank changed order in {rerank_moved}/{len(QUERIES)} queries")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
