"""量化对比：本系统 vs "agent 直接 grep+read 代码" 的信息摄入量。

不是比谁答得好（这需要人工评分），而是比**回答同一个问题要读多少代码**——
这是成本、延迟、上下文窗口占用的共同分子。

两种访问方式：
- 本系统：检索 → 重排 → 只把 top-k 证据送进模型（题面 + 证据，一次调用）；
- grep agent：先猜关键词 → grep → 每条命中读上下文（±40 行）→ 再决定要不要继续搜。

对 agent 的估算刻意**取最乐观值**：只读命中点附近 40 行，不读整文件、不做二次确认。
真实 agent 通常读得更多。
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
from pathlib import Path

ROOT = Path("vendor/redis-7.2.5")
SRC = ROOT / "src"
CONTEXT_LINES = 40  # agent 读命中点上下各 40 行

# 从 dev 集里取该答的查询，覆盖"符号名 / 机制 / 定位"三类
QUERIES = [
    "zmalloc_used_memory",
    "how does Redis expire keys",
    "where is the event loop created",
    "list type push and pop",
    "how to parse long long from string",
    "acl user permission check",
    "command table lookup by name",
    "replication handshake with master",
]

_IDENT = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b|\b[a-z]+[A-Z][A-Za-z0-9]*\b")
_STOP = {
    "how",
    "where",
    "what",
    "does",
    "the",
    "and",
    "with",
    "from",
    "type",
    "redis",
    "is",
    "of",
    "to",
    "in",
    "a",
    "by",
    "for",
}


def agent_keywords(query: str) -> list[str]:
    """agent 会怎么挑搜索词：标识符优先，其次是有信息量的实词。"""
    words = _IDENT.findall(query)
    if words:
        return words[:2]
    plain = [w for w in re.findall(r"[a-zA-Z_]{3,}", query.lower()) if w not in _STOP]
    return plain[:2]


def grep_hits(pattern: str) -> list[tuple[str, int]]:
    try:
        out = subprocess.run(
            ["grep", "-rn", "--include=*.c", "--include=*.h", "-w", pattern, str(SRC)],
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    hits: list[tuple[str, int]] = []
    for line in out.splitlines():
        parts = line.split(":", 2)
        if len(parts) < 2:
            continue
        try:
            hits.append((parts[0], int(parts[1])))
        except ValueError:
            continue
    return hits


def agent_intake(query: str, max_hits: int = 20) -> dict[str, object]:
    """估算 agent 读多少字符：命中数 × (2*40 行 + 1) × 平均行长。"""
    keywords = agent_keywords(query)
    if not keywords:
        return {"keywords": [], "hits": 0, "files": 0, "chars": 0}

    files: set[str] = set()
    hit_count = 0
    chars = 0
    for kw in keywords:
        hits = grep_hits(kw)
        hit_count += len(hits)
        for path, _line in hits[:max_hits]:
            files.add(path)
            try:
                raw = Path(path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            lines = raw.splitlines()
            avg_len = (len(raw) / max(len(lines), 1)) if lines else 0
            chars += int((CONTEXT_LINES * 2 + 1) * avg_len)
    return {
        "keywords": keywords,
        "hits": hit_count,
        "files": len(files),
        "chars": chars,
    }


async def our_intake(query: str) -> dict[str, object]:
    """本系统真正送进模型的字符数（题面 + 证据 + 系统提示）。"""
    from codeatlas.config import load_config
    from codeatlas.generation.prompts import build_messages
    from codeatlas.indexing.store import load_chunks
    from codeatlas.providers.registry import build_deps
    from codeatlas.retrieval.bm25 import BM25Index
    from codeatlas.retrieval.dense import DenseIndex
    from codeatlas.retrieval.service import HybridRetriever

    settings = load_config()
    chunks = load_chunks("data/chunks.jsonl.gz")
    chunk_map = {c.chunk_id: c for c in chunks}
    bm25 = BM25Index.load("data/bm25.json.gz")
    dense = DenseIndex.open(settings.qdrant_url, settings.qdrant_collection, settings.emb_dim)
    deps = build_deps(settings, use_cache=False)
    retriever = HybridRetriever(
        settings,
        dense=dense,
        bm25=bm25,
        chunks=chunk_map,
        embedder=deps.embed,
        reranker=deps.rerank,
    )
    try:
        result = await retriever.retrieve(query)
        evidence = [chunk_map[cid] for cid in result.chunk_ids() if cid in chunk_map]
        messages = build_messages(query, evidence)
        chars = sum(len(m["content"]) for m in messages)
        return {"evidence": len(evidence), "chars": chars, "files": len({e.path for e in evidence})}
    finally:
        await deps.aclose()
        dense.close()


async def main() -> None:
    print(f"{'query':<34} {'本系统':>10} {'grep agent':>12} {'倍数':>7}")
    print("-" * 68)
    rows = []
    for query in QUERIES:
        ours = await our_intake(query)
        theirs = agent_intake(query)
        ratio = theirs["chars"] / max(ours["chars"], 1)
        rows.append({"query": query, "ours": ours, "agent": theirs, "ratio": ratio})
        print(f"{query[:33]:<34} {ours['chars']:>10,} {theirs['chars']:>12,} {ratio:>6.1f}x")

    Path("eval/intake_compare.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    total_ours = sum(r["ours"]["chars"] for r in rows)
    total_agent = sum(r["agent"]["chars"] for r in rows)
    print("-" * 68)
    print(f"{'合计':<34} {total_ours:>10,} {total_agent:>12,} {total_agent / total_ours:>6.1f}x")


if __name__ == "__main__":
    asyncio.run(main())
