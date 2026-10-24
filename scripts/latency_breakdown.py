"""延迟分解（P5）：把端到端拆到每个阶段，找出真正的时间花在哪。

验收线写的是"检索 p95 < 300ms（不含 LLM）"，但 embedding 与 rerank 都是跨公网
API 调用。不拆开看就没法判断：是检索太慢，还是"远程模型调用"本来就占大头。
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
from codeatlas.retrieval.fusion import weighted_rrf
from codeatlas.retrieval.rerank import rerank_candidates

QUERIES = [
    "zmalloc_used_memory",
    "where is the event loop created for the server",
    "how does Redis expire keys",
    "replication handshake with master",
    "command table lookup by name",
]


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(p / 100 * (len(ordered) - 1))))
    return ordered[idx]


def report(name: str, values: list[float]) -> None:
    print(
        f"  {name:<22} p50={statistics.median(values):7.1f}ms  "
        f"p95={pct(values, 95):7.1f}ms  max={max(values):7.1f}ms"
    )


async def main() -> int:
    root = Path(__file__).resolve().parents[1]
    settings = load_config(root=root)
    chunks = load_chunks(root / "data" / "chunks.jsonl.gz")
    chunk_map = {c.chunk_id: c for c in chunks}

    bm25 = BM25Index.load(root / "data" / "bm25.json.gz")
    dense = DenseIndex.open(settings.qdrant_url, settings.qdrant_collection, settings.emb_dim)
    deps = build_deps(settings)

    stages = {k: [] for k in ("embed", "dense", "bm25", "fuse", "rerank", "total")}

    try:
        # 预热一次，排除建连
        await deps.embed.embed(["warmup"])

        for query in QUERIES:
            started = time.perf_counter()

            t0 = time.perf_counter()
            vector = (await deps.embed.embed([query]))[0]
            stages["embed"].append((time.perf_counter() - t0) * 1000)

            t0 = time.perf_counter()
            dense_hits = await asyncio.to_thread(dense.search, vector, settings.dense_limit)
            stages["dense"].append((time.perf_counter() - t0) * 1000)

            t0 = time.perf_counter()
            bm25_hits = await asyncio.to_thread(bm25.search, query, settings.bm25_limit)
            stages["bm25"].append((time.perf_counter() - t0) * 1000)

            t0 = time.perf_counter()
            fusion = weighted_rrf(
                dense_hits,
                bm25_hits,
                w_dense=settings.w_dense,
                w_bm25=settings.w_bm25,
                k=settings.rrf_k,
            )
            stages["fuse"].append((time.perf_counter() - t0) * 1000)

            window = fusion.hits[: settings.rerank_window]
            pairs = [
                (
                    h.chunk_id,
                    f"{chunk_map[h.chunk_id].signature or ''}\n{chunk_map[h.chunk_id].code}",
                )
                for h in window
                if h.chunk_id in chunk_map
            ]
            t0 = time.perf_counter()
            await rerank_candidates(deps.rerank, query, pairs, top_n=settings.top_k)
            stages["rerank"].append((time.perf_counter() - t0) * 1000)

            stages["total"].append((time.perf_counter() - started) * 1000)
    finally:
        await deps.aclose()
        dense.close()

    print(f"n={len(QUERIES)} queries, dim={settings.emb_dim}")
    print("\n=== per-stage latency ===")
    for name in ("embed", "dense", "bm25", "fuse", "rerank", "total"):
        report(name, stages[name])

    local = [
        d + b + f for d, b, f in zip(stages["dense"], stages["bm25"], stages["fuse"], strict=True)
    ]
    print("\n=== local-only (dense + bm25 + fuse, no network) ===")
    report("local_retrieval", local)
    print("\n判读：如果 local_retrieval 的 p95 远小于 300ms，说明超时来自远程模型调用，")
    print("      而不是检索实现本身。优化方向是缓存/本地模型，不是改索引。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
