"""一次性验证脚本：在 Redis 全量 chunk 上建 BM25，检查命中质量、体积与耗时。

跑完就删也行，但它记录的基线数字（体积/耗时/top-5）是 P5 融合权重的参照，
所以留在 scripts/ 下当可复现的观测工具。
"""

from __future__ import annotations

import time
from pathlib import Path

from codeatlas.config import load_config, resolve_repo
from codeatlas.indexing.chunker import CChunker, ChunkerConfig
from codeatlas.indexing.pipeline import chunk_repo
from codeatlas.retrieval.bm25 import BM25Index, BM25Params

QUERIES = [
    "zmalloc_used_memory",
    "where is the event loop created for the server",
    "how does Redis expire keys",
    "replication handshake with master",
    "command table lookup by name",
]


def main() -> int:
    settings = load_config()
    root = Path(__file__).resolve().parents[1]
    repo = resolve_repo(settings, root)

    chunker = CChunker(
        ChunkerConfig(
            max_chunk_lines=settings.max_chunk_lines,
            max_split_depth=settings.max_split_depth,
            chunker_version=settings.chunker_version,
        )
    )

    t0 = time.perf_counter()
    chunks, stats = chunk_repo(repo, chunker=chunker)
    t_chunk = time.perf_counter() - t0
    print(f"chunks={len(chunks)} files={stats.files} split={stats.splits} {t_chunk:.2f}s")

    params = BM25Params(k1=settings.bm25_k1, b=settings.bm25_b)
    index = BM25Index(params=params)

    t0 = time.perf_counter()
    # BM25 索引纯代码 + 签名 + 符号名，不用 context_header：
    # header 里的路径/行号是给人看的，进词表只会稀释 idf。
    index.add_many(
        (
            chunk.chunk_id,
            f"{chunk.signature or ''}\n{chunk.code}",
            f"{chunk.symbol or ''} {chunk.parent_symbol or ''}",
        )
        for chunk in chunks
    )
    t_index = time.perf_counter() - t0
    print(f"index {t_index:.2f}s  {index.stats()}")

    # 独立文件名：这个脚本不写 manifest，若覆盖 data/bm25.json.gz 会让
    # verify_pair() 判定产物不同源而报错（ISSUE-017）。观测脚本不该污染正式产物。
    out = root / "data" / "bm25_probe.json.gz"
    index.save(out)
    size_mb = out.stat().st_size / 1024 / 1024
    print(f"saved {out.name}  {size_mb:.1f} MB  (limit 50 MB)")

    by_id = {c.chunk_id: c for c in chunks}
    for query in QUERIES:
        print(f"\n[query] {query}")
        t0 = time.perf_counter()
        hits = index.search(query, limit=5)
        ms = (time.perf_counter() - t0) * 1000
        for hit in hits:
            chunk = by_id[hit.chunk_id]
            print(
                f"  {hit.rank}. {hit.score:7.3f}  {chunk.path}:{chunk.start_line}-"
                f"{chunk.end_line}  [{chunk.node_type}] {chunk.symbol or ''}"
            )
        print(f"  {ms:.1f}ms")

    # 精确标识符命中：必须排第一
    exact = index.search("zmalloc_used_memory", limit=1)
    if exact:
        top = by_id[exact[0].chunk_id]
        print(f"\nexact hit -> {top.path}:{top.start_line} symbol={top.symbol}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
