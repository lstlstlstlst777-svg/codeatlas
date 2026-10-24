"""rerank 参数实验：在延迟与质量之间找平衡点。

背景（ISSUE-024）：端到端 p95 = 1088ms，其中 rerank 独占 786ms。
rerank 的耗时与**送进去的总字符数**近似线性——当前 30 条候选 × 平均 750 字符
≈ 22K 字符。如果只送前 2000 字符就够判断相关性，延迟能砍掉一大半。

但不能凭感觉砍。这里对每组参数同时测两件事：
1. **延迟**（p50/p95）；
2. **质量**：top-5 与基线（window=30, 8000 字符）的重合率、top-1 是否一致。

重合率高说明截断没损失信息；掉到 4/5 以下就说明砍到肉了。

用法：
    python scripts/rerank_tuning.py
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
from codeatlas.retrieval.fusion import weighted_rrf

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

# (window, max_doc_chars, 标签)
CASES = [
    (30, 8000, "baseline"),
    (24, 4000, "w24_c4000"),
    (24, 3000, "w24_c3000"),
    (20, 2000, "w20_c2000"),
    (15, 1500, "w15_c1500"),
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

    bm25 = BM25Index.load(root / "data" / "bm25.json.gz")
    from codeatlas.retrieval.dense import DenseIndex

    dense = DenseIndex.open(settings.qdrant_url, settings.qdrant_collection, settings.emb_dim)
    deps = build_deps(settings)

    # 先算好每条查询的召回 + 融合，避免把 embed/dense 的时间混进 rerank 对比
    prepared: list[tuple[str, list[tuple[str, str]]]] = []
    try:
        await deps.embed.embed(["warmup"])
        for query in QUERIES:
            vector = (await deps.embed.embed([query]))[0]
            dense_hits = dense.search(vector, settings.dense_limit)
            bm25_hits = bm25.search(query, settings.bm25_limit)
            fusion = weighted_rrf(
                dense_hits,
                bm25_hits,
                w_dense=settings.w_dense,
                w_bm25=settings.w_bm25,
                k=settings.rrf_k,
            )
            pairs = []
            for hit in fusion.hits[: max(c[0] for c in CASES)]:
                chunk = chunk_map.get(hit.chunk_id)
                if chunk is None:
                    continue
                prefix = f"{chunk.signature}\n" if chunk.signature else ""
                pairs.append((hit.chunk_id, prefix + chunk.code))
            prepared.append((query, pairs))

        results: dict[str, dict] = {}
        for window, max_chars, label in CASES:
            latencies: list[float] = []
            tops: list[list[str]] = []
            for query, pairs in prepared:
                sub = pairs[:window]
                clipped = [(cid, text[:max_chars]) for cid, text in sub]
                t0 = time.perf_counter()
                hits = await deps.rerank.rerank(query, [t for _, t in clipped], top_n=5)
                latencies.append((time.perf_counter() - t0) * 1000)
                ordered = [clipped[h.index][0] for h in hits if 0 <= h.index < len(clipped)]
                tops.append(ordered[:5])
            total_chars = statistics.mean(
                sum(len(t[:max_chars]) for _, t in pairs[:window]) for _, pairs in prepared
            )
            results[label] = {
                "window": window,
                "max_chars": max_chars,
                "p50": statistics.median(latencies),
                "p95": pct(latencies, 95),
                "tops": tops,
                "avg_chars": total_chars,
            }

        base = results["baseline"]["tops"]
        print(
            f"{'case':<12} {'win':>4} {'chars':>6} {'avg输入':>8} "
            f"{'p50':>7} {'p95':>7} {'top5重合':>9} {'top1一致':>9}"
        )
        for label, r in results.items():
            overlap = sum(len(set(r["tops"][i]) & set(base[i])) for i in range(len(QUERIES))) / (
                len(QUERIES) * 5
            )
            top1_same = sum(
                1
                for i in range(len(QUERIES))
                if r["tops"][i] and base[i] and r["tops"][i][0] == base[i][0]
            )
            print(
                f"{label:<12} {r['window']:>4} {r['max_chars']:>6} "
                f"{r['avg_chars']:>8.0f} {r['p50']:>6.0f}ms {r['p95']:>6.0f}ms "
                f"{overlap * 100:>8.0f}% {top1_same:>6}/{len(QUERIES)}"
            )

        print("\n判读：top5 重合 >= 90% 且 top1 全一致，才认为该组参数可以替换 baseline。")
    finally:
        await deps.aclose()
        dense.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
