"""评测：E1 检索召回 / E3 引用有效性 / E4 延迟 / E5 拒答。

只对**该答的 25 条**跑完整图（含 LLM 生成），因为只有它们会产生引用。
成本约 ¥0.05（付费档 ~¥0.0016/次），耗时约 2 分钟。

E2（Token F1）没有做：它需要人工写 gold answer，而 dev 集只有 gold symbol。
用"引用是否指向 gold 符号"替代更客观——这是这个项目真正想保证的东西
（可溯源的定位），而不是措辞与人工答案的字面重合度。缺什么写清楚，不假装有。

用法：
    python scripts/eval_full.py              # 用缓存
    python scripts/eval_full.py --limit 5    # 只跑前 5 条（调试用）
    python scripts/eval_full.py --no-cache
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path

from codeatlas.config import load_config, resolve_repo
from codeatlas.graph.build import build_graph
from codeatlas.graph.nodes import GraphDeps
from codeatlas.graph.state import new_state
from codeatlas.indexing.store import load_chunks
from codeatlas.providers.registry import build_deps
from codeatlas.retrieval.bm25 import BM25Index
from codeatlas.retrieval.dense import DenseIndex
from codeatlas.retrieval.service import HybridRetriever

ROOT = Path(__file__).resolve().parents[1]
DEV = ROOT / "eval" / "dev.jsonl"
OUT = ROOT / "docs" / "EVAL.md"


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(p / 100 * (len(ordered) - 1))))
    return ordered[idx]


def load_dev() -> list[dict]:
    return [
        json.loads(line) for line in DEV.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def citation_hits_gold(citations: list[str], gold_symbols: list[str], chunk_map) -> bool:
    """引用指向的行区间，是否落在某个 gold 符号的 chunk 内。

    这比"字符串里出现符号名"更严：它要求引用真的定位到了那段代码。
    """
    if not gold_symbols:
        return False
    for cite in citations:
        try:
            path, _, span = cite.partition(":")
            start, _, end = span.partition("-")
            lo = int(start)
            hi = int(end) if end else lo
        except ValueError:
            continue
        for chunk in chunk_map.values():
            if chunk.symbol not in gold_symbols:
                continue
            if chunk.path == path and chunk.start_line <= lo and hi <= chunk.end_line:
                return True
    return False


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()

    settings = load_config(root=ROOT)
    rows = [r for r in load_dev() if not r["should_refuse"]]
    if args.limit:
        rows = rows[: args.limit]

    chunks = load_chunks(ROOT / "data" / "chunks.jsonl.gz")
    chunk_map = {c.chunk_id: c for c in chunks}
    bm25 = BM25Index.load(ROOT / "data" / "bm25.json.gz")
    dense = DenseIndex.open(settings.qdrant_url, settings.qdrant_collection, settings.emb_dim)
    deps = build_deps(settings, use_cache=not args.no_cache)
    retriever = HybridRetriever(
        settings,
        dense=dense,
        bm25=bm25,
        chunks=chunk_map,
        embedder=deps.embed,
        reranker=deps.rerank,
    )
    graph = build_graph(
        GraphDeps(
            settings=settings,
            retriever=retriever,
            llm=deps.llm,
            chunks=chunk_map,
            repo_root=resolve_repo(settings),
        )
    )

    results = []
    print(f"evaluating {len(rows)} answerable questions...")
    try:
        for i, row in enumerate(rows, start=1):
            started = time.perf_counter()
            state = await graph.ainvoke(
                new_state(row["question"], f"eval-{row['id']}"),
                config={"recursion_limit": 12, "configurable": {"thread_id": f"eval-{row['id']}"}},
            )
            elapsed = (time.perf_counter() - started) * 1000
            total = int(state.get("citation_total") or 0)
            valid = int(state.get("citation_valid") or 0)
            cites = list(state.get("citations") or [])

            stages = list(state.get("stages") or [])
            # 拒答分两种：gate 层拦下的（阈值判断）和 LLM 自己说 [REFUSE] 的。
            # 混在一起统计会把"检索没找到"算成"拒答门太严"，两者的修法完全不同。
            gate_refused = any(s.startswith("gate:refuse") for s in stages)
            llm_refused = any(s.startswith("generate:refuse") for s in stages)

            results.append(
                {
                    "id": row["id"],
                    "question": row["question"],
                    "refused": bool(state.get("refused")),
                    "gate_refused": gate_refused,
                    "llm_refused": llm_refused,
                    "hit_ids": list(state.get("hit_ids") or []),
                    "citation_total": total,
                    "citation_valid": valid,
                    "citations": cites,
                    "cites_gold": citation_hits_gold(
                        cites, row.get("gold_symbols") or [], chunk_map
                    ),
                    "retrieval_hit": any(
                        chunk_map.get(cid) is not None
                        and chunk_map[cid].symbol in (row.get("gold_symbols") or [])
                        for cid in (state.get("hit_ids") or [])
                    ),
                    "attempts": int(state.get("attempts") or 0),
                    "elapsed_ms": elapsed,
                    "generate_ms": float((state.get("timings") or {}).get("generate") or 0),
                    "answer_chars": len(state.get("answer") or ""),
                }
            )
            flag = "OK " if cites and valid == total else "!! "
            print(f"  {flag}{i}/{len(rows)} {row['id']} {elapsed:.0f}ms 引用 {valid}/{total}")
    finally:
        await deps.aclose()
        dense.close()

    # --- 汇总 ---
    answered = [r for r in results if not r["refused"]]
    total_cites = sum(r["citation_total"] for r in results)
    valid_cites = sum(r["citation_valid"] for r in results)
    cite_ratio = valid_cites / total_cites if total_cites else 0.0
    cite_precision = (
        sum(1 for r in answered if r["citations"] and r["citation_valid"] == r["citation_total"])
        / len(answered)
        if answered
        else 0.0
    )
    gold_hit = sum(1 for r in answered if r["cites_gold"]) / len(answered) if answered else 0.0
    gate_out = [r for r in results if r["gate_refused"]]
    llm_out = [r for r in results if r["llm_refused"]]
    # 拒答时检索是否命中 gold：命中说明"其实能答"，没命中说明"检索本身失败"
    gate_out_missed = sum(1 for r in gate_out if not r["retrieval_hit"])
    llm_out_missed = sum(1 for r in llm_out if not r["retrieval_hit"])
    latencies = [r["elapsed_ms"] for r in results]
    rewritten = sum(1 for r in results if r["attempts"] > 0)

    lines = [
        "# 评测报告",
        "",
        f"> 自动生成于 `scripts/eval_full.py`，数据集 `eval/dev.jsonl`（{len(rows)} 条该答）。",
        "> 重新生成：`python scripts/eval_full.py`",
        "",
        f"模型：`{settings.llm_model}`　τ=`{settings.refuse_tau}`　"
        f"指纹 `{settings.config_fingerprint()}`",
        "",
        "## E3 引用有效性",
        "",
        "| 指标 | 值 |",
        "| --- | --- |",
        f"| 引用总数 | {total_cites} |",
        f"| 通过三层校验 | {valid_cites}（**{cite_ratio:.1%}**） |",
        f"| 全部引用均有效的回答 | {cite_precision:.1%} |",
        f"| 引用命中 gold 符号 | **{gold_hit:.1%}** |",
        f"| 因引用失败触发重写 | {rewritten}/{len(results)} |",
        "",
        "「引用命中 gold 符号」要求引用的行区间**落在 gold 符号的 chunk 内**，"
        "比字符串匹配更严——它要求真的定位到那段代码。",
        "",
        "## E4 端到端延迟（含 LLM 生成）",
        "",
        "| 指标 | 值 |",
        "| --- | --- |",
        f"| p50 | {statistics.median(latencies):.0f} ms |",
        f"| p95 | {pct(latencies, 95):.0f} ms |",
        f"| max | {max(latencies):.0f} ms |",
        "",
        "## E5 拒答（该答侧）",
        "",
        "| 指标 | 值 |",
        "| --- | --- |",
        f"| 被拒总数 | {len(results) - len(answered)}/{len(results)}（{(len(results) - len(answered)) / len(results):.1%}） |",
        f"| └ 阈值拦截（gate） | {len(gate_out)} 条，其中检索未命中 gold 的 {gate_out_missed} 条 |",
        f"| └ LLM 判定证据不足 | {len(llm_out)} 条，其中检索未命中 gold 的 {llm_out_missed} 条 |",
        "",
        "**这两种拒答必须分开看**：检索本来就没找到正确代码时的拒答是**正确行为**"
        "（比编一个答案好），只有「检索命中了却被拦下」才是拒答门的误判。"
        "混在一起统计会把检索问题算到阈值头上，修的方向就错了。",
        "",
        "> 该拒侧（15 条硬负样本）的指标见 `docs/ISSUES.md` ISSUE-007 的标定记录。",
        "",
        "## E1 检索召回",
        "",
        "见 ISSUE-007 标定记录：Recall@1 **52%**、Recall@5 **72%**（同一 dev 集）。",
        "",
        "## E2 未做（说明原因）",
        "",
        "Token F1 需要人工写 gold answer，dev 集只有 gold symbol。"
        "用「引用命中 gold 符号」替代更贴合本项目的目标——保证**引用可溯源**，"
        "而不是措辞与人工答案的字面重合。**不假装做了这一项。**",
        "",
        "## 逐条明细",
        "",
        "| id | 引用 | 命中gold | 重写 | 耗时 | 问题 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in results:
        mark = "✓" if r["cites_gold"] else "—"
        ref = "拒答" if r["refused"] else f"{r['citation_valid']}/{r['citation_total']}"
        lines.append(
            f"| {r['id']} | {ref} | {mark} | {r['attempts']} | {r['elapsed_ms']:.0f}ms | "
            f"{r['question'][:32]} |"
        )

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("\n=== summary ===")
    print(f"  引用有效 {valid_cites}/{total_cites} = {cite_ratio:.1%}")
    print(f"  引用命中 gold = {gold_hit:.1%}")
    print(f"  误拒 {len(results) - len(answered)}/{len(results)}")
    print(f"  p50 {statistics.median(latencies):.0f}ms  p95 {pct(latencies, 95):.0f}ms")
    print(f"\n报告 -> {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
