"""macro 噪声与符号加权的交叉分析（ISSUE-008 / ISSUE-018）。

两个问题绑在一起：为了修"精确标识符被高频调用点压过"，给符号名加了 extra tf；
但 macro chunk 的 symbol 是宏名，加权后可能让一批无关宏挤进 top-5。

这里在真实索引上换不同 `symbol_boost` 重跑同一组查询，同时统计
top-5 里的 kind 分布与 macro_const 占比，为 P7 的决策留下基线数字。

用法：
    python scripts/macro_analysis.py
"""

from __future__ import annotations

from pathlib import Path

from codeatlas.config import load_config
from codeatlas.indexing.store import load_chunks
from codeatlas.retrieval.bm25 import BM25Index, BM25Params

QUERIES = [
    "zmalloc_used_memory",
    "activeExpireCycle",
    "how does Redis expire keys",
    "replication handshake with master",
    "where is the event loop created",
    "command table lookup by name",
    "how to parse an integer from string",
    "list type push and pop implementation",
    "acl user permission check",
    "how does rdb save a string object",
]

BOOSTS = (0, 3, 5, 8)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    settings = load_config(root=root)
    chunks = load_chunks(root / "data" / "chunks.jsonl.gz")
    by_id = {c.chunk_id: c for c in chunks}

    print(f"chunks={len(chunks)}  queries={len(QUERIES)}  top_k={settings.top_k}")
    print(
        f"macro_const total={sum(1 for c in chunks if c.fields.get('macro_kind') == 'macro_const')}"
    )

    rows = []
    for boost in BOOSTS:
        index = BM25Index(BM25Params(k1=settings.bm25_k1, b=settings.bm25_b, symbol_boost=boost))
        index.add_many(
            (
                c.chunk_id,
                f"{c.signature or ''}\n{c.code}",
                f"{c.symbol or ''} {c.parent_symbol or ''}",
            )
            for c in chunks
        )

        kind_counts: dict[str, int] = {}
        macro_const = 0
        exact_first = 0
        exact_checked = 0

        for query in QUERIES:
            hits = index.search(query, limit=settings.top_k)
            for hit in hits:
                chunk = by_id[hit.chunk_id]
                kind = chunk.node_type.value
                kind_counts[kind] = kind_counts.get(kind, 0) + 1
                if chunk.fields.get("macro_kind") == "macro_const":
                    macro_const += 1
            # 查询词恰好是某个 chunk 的符号名时，检查是否排第一
            if hits:
                top = by_id[hits[0].chunk_id]
                if top.symbol and top.symbol.lower() == query.lower():
                    exact_first += 1
            if any(
                by_id[h.chunk_id].symbol and by_id[h.chunk_id].symbol.lower() == query.lower()
                for h in hits
            ):
                exact_checked += 1

        total = len(QUERIES) * settings.top_k
        rows.append(
            {
                "boost": boost,
                "macro_const": macro_const,
                "macro_const_pct": macro_const / total * 100,
                "kinds": kind_counts,
                "exact_first": exact_first,
                "exact_checked": exact_checked,
            }
        )

    print(f"\n{'boost':>5} {'macro_const':>12} {'占比':>8} {'精确命中首位':>14}")
    for r in rows:
        print(
            f"{r['boost']:>5} {r['macro_const']:>12} {r['macro_const_pct']:>7.1f}% "
            f"{r['exact_first']:>10}/{r['exact_checked']:<3}"
        )

    print("\nkind 分布（boost=5）:")
    for row in rows:
        if row["boost"] == 5:
            for kind, count in sorted(row["kinds"].items(), key=lambda kv: -kv[1]):
                print(f"  {kind:<10} {count:>4}")

    print(
        "\n判读方式：\n"
        "  - macro_const 占比高（>15%）说明单行宏确实在挤占 top-k → P7 考虑降权或过滤；\n"
        "  - 精确命中首位随 boost 上升而改善，说明符号加权有效；\n"
        "  - 若 boost 增大后 macro_const 占比同步飙升，说明加权被宏名带偏，需要按 kind 差异化加权。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
