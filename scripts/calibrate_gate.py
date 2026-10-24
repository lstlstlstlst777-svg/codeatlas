"""dev 集标定（ISSUE-007）。

要解决的是一个必须用数据回答的问题：**拒答阈值 τ 取多少**。

特征取 `log10(rerank_top1)`：实测 rerank 分数跨 4 个数量级（相关 0.1~0.99，
不相关 1e-5），线性尺度下任何阈值都会退化成"全拒"或"全过"。

评价口径（正类 = "该拒"）：
- TP 该拒且拒了、FP **该答却拒了**（最痛，用户体验直接崩）、
  FN 该拒没拒（给了错误答案）、TN 该答且答了；
- 约束 `FalseRefusalRate = FP/(FP+TN) <= 0.10`，在此之下最大化 Refusal F1。

为什么约束优先于 F1：误拒一个能答的问题，比偶尔答一个其实该拒的问题更伤。
用户可以自己判断"这答案靠不靠谱"，但没机会知道"系统其实知道答案却不说"。

顺带产出 Recall@k（用 gold_symbols 匹配），这就是 ISSUE-025 的观测手段。

用法：
    python scripts/calibrate_gate.py            # 用缓存（快）
    python scripts/calibrate_gate.py --no-cache # 冷启动（慢，但数字更真实）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
from pathlib import Path

from codeatlas.config import load_config
from codeatlas.indexing.store import load_chunks
from codeatlas.providers.registry import build_deps
from codeatlas.retrieval.bm25 import BM25Index
from codeatlas.retrieval.dense import DenseIndex
from codeatlas.retrieval.service import HybridRetriever

ROOT = Path(__file__).resolve().parents[1]
DEV = ROOT / "eval" / "dev.jsonl"
MAX_FALSE_REFUSAL = 0.10


def load_dev() -> list[dict]:
    rows = []
    for line in DEV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def feature_of(top_score: float | None) -> float | None:
    if top_score is None or top_score <= 0:
        return None
    return math.log10(max(top_score, 1e-12))


def score_feature(scores: list[float], mode: str) -> float | None:
    """把 top-k 的重排分聚合成一个"能否回答"的特征。

    top1 有个内在缺陷：locate 类问题（"在哪触发"）的答案往往分散在多个位置，
    没有任何一条单独打分很高，但系统其实完全能答。只看 top1 会把这类问题误拒。
    """
    vals = [s for s in scores if s and s > 0]
    if not vals:
        return None
    if mode == "top1":
        agg = vals[0]
    elif mode == "mean3":
        agg = sum(vals[:3]) / len(vals[:3])
    elif mode == "sum5":
        agg = sum(vals[:5])
    else:
        raise ValueError(mode)
    return math.log10(max(agg, 1e-12))


def hit_at_k(hits, gold_symbols: list[str], gold_files: list[str], k: int) -> bool:
    """top-k 里是否出现了 gold 符号（或 gold 文件）。

    优先用符号匹配：同一个符号可能被多个文件引用，但"定义"才是答案。
    没有 gold_symbols 时退回文件匹配。
    """
    window = hits[:k]
    if gold_symbols:
        symbols = {h.chunk.symbol for h in window if h.chunk.symbol}
        if symbols & set(gold_symbols):
            return True
    if gold_files:
        files = {h.chunk.path for h in window}
        if files & set(gold_files):
            return True
    return False


async def collect(rows: list[dict], use_cache: bool) -> list[dict]:
    settings = load_config(root=ROOT)
    chunks = load_chunks(ROOT / "data" / "chunks.jsonl.gz")
    chunk_map = {c.chunk_id: c for c in chunks}
    bm25 = BM25Index.load(ROOT / "data" / "bm25.json.gz")
    dense = DenseIndex.open(settings.qdrant_url, settings.qdrant_collection, settings.emb_dim)
    deps = build_deps(settings, use_cache=use_cache)
    retriever = HybridRetriever(
        settings,
        dense=dense,
        bm25=bm25,
        chunks=chunk_map,
        embedder=deps.embed,
        reranker=deps.rerank,
    )

    out: list[dict] = []
    try:
        for i, row in enumerate(rows, start=1):
            result = await retriever.retrieve(row["question"])
            out.append(
                {
                    **row,
                    "top_score": result.top_rerank_score,
                    "top_scores": [h.rerank_score for h in result.hits if h.rerank_score],
                    "feature": feature_of(result.top_rerank_score),
                    "top1_symbol": result.hits[0].chunk.symbol if result.hits else None,
                    "top1_path": result.hits[0].chunk.path if result.hits else None,
                    "recall1": hit_at_k(
                        result.hits, row.get("gold_symbols") or [], row.get("gold_files") or [], 1
                    ),
                    "recall5": hit_at_k(
                        result.hits, row.get("gold_symbols") or [], row.get("gold_files") or [], 5
                    ),
                    "degraded": result.degraded,
                }
            )
            if i % 10 == 0:
                print(f"  {i}/{len(rows)}", flush=True)
    finally:
        await deps.aclose()
        dense.close()
    return out


def sweep(rows: list[dict]) -> list[dict]:
    """在 log10 空间扫阈值，返回每档的指标。"""
    features = [r["feature"] for r in rows if r["feature"] is not None]
    if not features:
        return []
    lo, hi = math.floor(min(features) * 2) / 2 - 0.5, 0.0

    answerable = [r for r in rows if not r["should_refuse"]]
    refusable = [r for r in rows if r["should_refuse"]]

    grid: list[dict] = []
    steps = int((hi - lo) / 0.1) + 1
    for step in range(steps):
        tau = round(lo + step * 0.1, 2)
        tp = fp = fn = tn = 0
        for r in answerable:
            f = r["feature"]
            refused = f is None or f < tau
            if refused:
                fp += 1
            else:
                tn += 1
        for r in refusable:
            f = r["feature"]
            refused = f is None or f < tau
            if refused:
                tp += 1
            else:
                fn += 1

        ffr = fp / (fp + tn) if (fp + tn) else 0.0
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        grid.append(
            {
                "tau": tau,
                "tp": tp,
                "fp": fp,
                "fn": fn,
                "tn": tn,
                "ffr": ffr,
                "precision": precision,
                "recall": recall,
                "f1": f1,
            }
        )
    return grid


def pick(grid: list[dict]) -> dict | None:
    feasible = [g for g in grid if g["ffr"] <= MAX_FALSE_REFUSAL]
    if not feasible:
        return None
    # F1 优先；F1 相同时取更宽松（tau 更小）的那档，宁可少拒
    best = max(feasible, key=lambda g: (round(g["f1"], 4), -g["tau"]))
    return best


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()

    rows = load_dev()
    n_answer = sum(1 for r in rows if not r["should_refuse"])
    n_refuse = sum(1 for r in rows if r["should_refuse"])
    print(f"dev set: {len(rows)} 条（该答 {n_answer} / 该拒 {n_refuse}）")

    print("collecting retrieval signals...")
    enriched = await collect(rows, use_cache=not args.no_cache)

    missing = [r["id"] for r in enriched if r["feature"] is None]
    if missing:
        print(f"警告：{len(missing)} 条没有 rerank 分数（降级）：{missing}")

    # --- 检索质量 ---
    answerable = [r for r in enriched if not r["should_refuse"]]
    r1 = sum(1 for r in answerable if r["recall1"]) / len(answerable) if answerable else 0
    r5 = sum(1 for r in answerable if r["recall5"]) / len(answerable) if answerable else 0
    print(f"\n=== 检索质量（{len(answerable)} 条该答）===")
    print(f"  Recall@1 = {r1 * 100:.1f}%   Recall@5 = {r5 * 100:.1f}%")

    misses = [r for r in answerable if not r["recall5"]]
    if misses:
        print(f"  未命中 {len(misses)} 条：")
        for r in misses:
            print(f"    {r['id']} {r['question'][:40]!r} -> top1={r['top1_symbol']}")

    # --- 分数分布 ---
    print("\n=== 分数分布（log10 rerank_top1）===")
    for label, group in (
        ("该答", answerable),
        ("该拒", [r for r in enriched if r["should_refuse"]]),
    ):
        vals = sorted(r["feature"] for r in group if r["feature"] is not None)
        if not vals:
            continue
        p10 = vals[max(0, int(len(vals) * 0.1) - 1)]
        print(
            f"  {label}: min={vals[0]:.2f} p10={p10:.2f} "
            f"median={vals[len(vals) // 2]:.2f} max={vals[-1]:.2f}"
        )

    # --- 阈值扫描 ---
    grid = sweep(enriched)
    print(f"\n=== τ 扫描（约束 FalseRefusalRate <= {MAX_FALSE_REFUSAL:.0%}）===")
    print(
        f"{'tau':>6} {'TP':>3} {'FP':>3} {'FN':>3} {'TN':>3} {'FFR':>6} {'P':>6} {'R':>6} {'F1':>6}"
    )
    for g in grid:
        if g["tau"] % 0.5 != 0 and abs(g["tau"] - round(g["tau"])) > 1e-9:
            continue  # 只打印 0.5 的整数倍，避免刷屏
        mark = " <=" if g["ffr"] <= MAX_FALSE_REFUSAL else ""
        print(
            f"{g['tau']:>6.2f} {g['tp']:>3} {g['fp']:>3} {g['fn']:>3} {g['tn']:>3} "
            f"{g['ffr']:>6.1%} {g['precision']:>6.2f} {g['recall']:>6.2f} {g['f1']:>6.2f}{mark}"
        )

    best = pick(grid)
    print()
    if best is None:
        print(f"没有可行阈值：在所有 τ 上 FalseRefusalRate 都超过 {MAX_FALSE_REFUSAL:.0%}。")
        print("说明该答与该拒的分数分布重叠严重，需要先提升检索质量或补充特征。")
        return 1

    print(f"推荐 τ = {best['tau']:.2f}（log10 空间）")
    print(
        f"  对应 Refusal F1={best['f1']:.3f} P={best['precision']:.2f} R={best['recall']:.2f}"
        f"  FalseRefusalRate={best['ffr']:.1%}（{best['fp']}/{best['fp'] + best['tn']} 条该答被误拒）"
    )
    print(f"\n写入 config/default.yaml：gate.refuse_tau: {best['tau']:.2f}")

    # 落一份明细，方便人工复核哪几条被误判
    out = ROOT / "eval" / "gate_calibration.json"
    out.write_text(
        json.dumps(
            {
                "tau": best["tau"],
                "false_refusal_rate": best["ffr"],
                "f1": best["f1"],
                "recall_at_1": r1,
                "recall_at_5": r5,
                "rows": enriched,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"明细 -> {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
