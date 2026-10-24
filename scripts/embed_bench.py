"""实测 embedding 吞吐（ISSUE-009）：决定 7019 个 chunk 怎么入库。

冷启动测出的 12.5s/2 条 不能用来外推——那里面大半是 TLS 握手。
这里在热态下对比三种批大小，并记录是否出现 429（免费档限流的直接证据）。

用法：
    python scripts/embed_bench.py            # 默认 640 条文本
    python scripts/embed_bench.py --n 320
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time
from pathlib import Path

from codeatlas.config import load_config
from codeatlas.indexing.chunker import CChunker, ChunkerConfig
from codeatlas.indexing.store import load_chunks
from codeatlas.providers.http import ProviderError
from codeatlas.providers.openai_compat import OpenAICompatibleEmbedding

TOTAL_CHUNKS = 7019


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(p / 100 * (len(ordered) - 1))))
    return ordered[idx]


def load_texts(n: int) -> list[str]:
    root = Path(__file__).resolve().parents[1]
    store = root / "data" / "chunks.jsonl.gz"
    if store.is_file():
        chunks = load_chunks(store)
        print(f"loaded {len(chunks)} chunks from {store.name}")
        return [c.embedding_text() for c in chunks[:n]]

    # 没建过索引就直接切
    settings = load_config(root=root)
    repo = root / settings.repo
    chunker = CChunker(
        ChunkerConfig(
            max_chunk_lines=settings.max_chunk_lines,
            max_split_depth=settings.max_split_depth,
            chunker_version=settings.chunker_version,
        )
    )
    from codeatlas.indexing.pipeline import chunk_repo

    chunks, _ = chunk_repo(repo, chunker=chunker)
    print(f"chunked {len(chunks)} chunks on the fly")
    return [c.embedding_text() for c in chunks[:n]]


async def run_case(
    provider: OpenAICompatibleEmbedding,
    texts: list[str],
    batch: int,
    label: str,
) -> dict:
    """一批一批地打，记录每次调用的延迟。"""
    latencies: list[float] = []
    errors: list[str] = []
    done = 0

    # 预热：第一次调用包含连接建立，单独标记，不计入统计
    t0 = time.perf_counter()
    await provider.embed(texts[:1])
    warm_ms = (time.perf_counter() - t0) * 1000
    print(f"  warmup {warm_ms:.0f}ms")

    started = time.perf_counter()
    for start in range(0, len(texts), batch):
        window = texts[start : start + batch]
        t0 = time.perf_counter()
        try:
            await provider.embed(window)
        except ProviderError as exc:
            errors.append(f"code={exc.code}")
            if str(exc.code) == "429":
                print(f"  429 at offset {start} — 免费档限流已触发")
                break
            raise
        latencies.append((time.perf_counter() - t0) * 1000)
        done += len(window)

    elapsed = time.perf_counter() - started
    return {
        "label": label,
        "batch": batch,
        "texts": done,
        "calls": len(latencies),
        "elapsed_s": elapsed,
        "p50_ms": statistics.median(latencies) if latencies else 0.0,
        "p95_ms": pct(latencies, 95),
        "per_text_ms": elapsed * 1000 / done if done else 0.0,
        "texts_per_s": done / elapsed if elapsed else 0.0,
        "errors": errors,
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=640, help="样本文本数")
    parser.add_argument("--runs", type=int, default=2, help="每种批大小重复次数（取最好）")
    args = parser.parse_args()

    settings = load_config()
    texts = load_texts(args.n)
    print(f"sample: {len(texts)} texts, avg {sum(map(len, texts)) / len(texts):.0f} chars")

    provider = OpenAICompatibleEmbedding(
        base_url=settings.emb_base_url,
        api_key=settings.emb_api_key,
        model_id=settings.emb_model,
        dim=settings.emb_dim,
        max_batch=32,
    )

    results = []
    try:
        for batch, label in ((1, "single"), (8, "batch8"), (32, "batch32")):
            best = None
            for run in range(args.runs):
                print(f"\n[{label}] run {run + 1}/{args.runs}")
                stats = await run_case(provider, texts, batch, label)
                print(
                    f"  {stats['calls']} calls  {stats['elapsed_s']:.1f}s  "
                    f"p50={stats['p50_ms']:.0f}ms p95={stats['p95_ms']:.0f}ms  "
                    f"{stats['texts_per_s']:.1f} texts/s"
                )
                if stats["errors"]:
                    print(f"  errors: {stats['errors'][:3]}")
                if best is None or stats["texts_per_s"] > best["texts_per_s"]:
                    best = stats
            results.append(best)
    finally:
        await provider.aclose()

    print("\n=== summary ===")
    print(f"{'case':<10} {'calls':>6} {'p50':>8} {'p95':>8} {'texts/s':>9} {'7019 预估':>12}")
    for r in results:
        est_min = TOTAL_CHUNKS / r["texts_per_s"] / 60 if r["texts_per_s"] else 0
        print(
            f"{r['label']:<10} {r['calls']:>6} {r['p50_ms']:>7.0f}ms {r['p95_ms']:>7.0f}ms "
            f"{r['texts_per_s']:>9.1f} {est_min:>10.1f}min"
        )

    if any(r["errors"] for r in results):
        print("\n注意：出现 429，说明当前并发/批次会触发免费档限流，入库必须降速。")
    else:
        print("\n样本内未见 429。全量入库仍需串行分批，且遇到 429 要能退避。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
