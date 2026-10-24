"""复测 LLM 延迟（ISSUE-019）：判断 22.7s 是抖动还是稳定劣化。

免费档排队严重时，单次 20s+ 会让 SSE 首 token 体验崩掉。
这里对两个候选模型各打 10 次，比较 p50/p95，并记录 token 数（判断是不是输出变长了）。

用法：
    python scripts/llm_bench.py                # 两个模型各 10 次
    python scripts/llm_bench.py --n 5 --model Qwen/Qwen3-8B
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time

from codeatlas.config import load_config
from codeatlas.providers.http import ProviderError
from codeatlas.providers.openai_compat import OpenAICompatibleLLM

PROMPT = [{"role": "user", "content": "用一句话说明 Redis 的 zmalloc_used_memory 做什么。"}]

MODELS = ("Qwen/Qwen3-8B", "Qwen/Qwen3-Coder-30B-A3B-Instruct")


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(p / 100 * (len(ordered) - 1))))
    return ordered[idx]


async def bench_model(settings, model: str, runs: int) -> dict:
    llm = OpenAICompatibleLLM(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model_id=model,
        timeout=120.0,
    )
    latencies: list[float] = []
    chars: list[int] = []
    failures: list[str] = []
    try:
        # 预热一次，排除建连成本；TPM 未恢复时可能 429，不让它中断整个测试
        t0 = time.perf_counter()
        try:
            await llm.complete(PROMPT, max_tokens=64)
            print(f"  warmup {(time.perf_counter() - t0) * 1000:.0f}ms")
        except ProviderError as exc:
            print(f"  warmup FAILED: code={exc.code} — {exc.message[:100]}")
            print("  （若为 50602，说明 TPM 已耗尽，等约 60s 再测）")

        for i in range(runs):
            t0 = time.perf_counter()
            try:
                text = await llm.complete(PROMPT, max_tokens=64)
            except ProviderError as exc:
                failures.append(f"code={exc.code}")
                print(f"  run {i + 1}: FAIL {exc.code} {exc.message[:80]}")
                continue
            ms = (time.perf_counter() - t0) * 1000
            latencies.append(ms)
            chars.append(len(text))
            print(f"  run {i + 1}: {ms:6.0f}ms  {len(text)} chars  {text[:50]!r}")
    finally:
        await llm.aclose()

    return {
        "model": model,
        "runs": len(latencies),
        "p50": statistics.median(latencies) if latencies else 0.0,
        "p95": pct(latencies, 95),
        "min": min(latencies) if latencies else 0.0,
        "max": max(latencies) if latencies else 0.0,
        "avg_chars": statistics.mean(chars) if chars else 0.0,
        "failures": failures,
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=10)
    parser.add_argument("--model", default=None, help="只测指定模型")
    args = parser.parse_args()

    settings = load_config()
    models = (args.model,) if args.model else MODELS

    results = []
    for model in models:
        print(f"\n[{model}]")
        results.append(await bench_model(settings, model, args.n))

    print("\n=== summary ===")
    print(f"{'model':<38} {'runs':>5} {'p50':>9} {'p95':>9} {'max':>9} {'chars':>6}")
    for r in results:
        print(
            f"{r['model']:<38} {r['runs']:>5} {r['p50'] / 1000:>8.1f}s "
            f"{r['p95'] / 1000:>8.1f}s {r['max'] / 1000:>8.1f}s {r['avg_chars']:>6.0f}"
        )
        if r["failures"]:
            print(f"  failures: {r['failures']}")

    free = next((r for r in results if "8B" in r["model"]), None)
    if free:
        if free["p50"] > 10000:
            print("\n免费档 p50 > 10s：端到端体验不可接受，建议主跑改付费档或换模型。")
        else:
            print(f"\n免费档 p50={free['p50'] / 1000:.1f}s，在可接受范围内。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
