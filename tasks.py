"""任务入口：不依赖 make，任何装了 Python 的机器都能跑。

为什么要有这个文件：本机（Git Bash）没有 `make`，Makefile 实际上跑不起来，
而 README 又写着 `make xxx`——文档与现实的差距会让人以为环境坏了。
这里用纯 Python 复刻同一套命令，并能自动定位 conda 解释器。

用法：
    python tasks.py                # 列出所有任务
    python tasks.py test
    python tasks.py search "zmalloc_used_memory" --limit 3
    python tasks.py check-deps --in-docker
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_PY = r"D:\miniconda3\envs\codeatlas\python.exe"
DEFAULT_IMAGE = "codeatlas-api:dev"


def python_exe() -> str:
    """优先用 conda 环境的解释器；找不到就退回当前解释器并提示。"""
    override = os.environ.get("CODEATLAS_PY")
    if override and Path(override).is_file():
        return override
    if Path(DEFAULT_PY).is_file():
        return DEFAULT_PY
    print(f"[warn] {DEFAULT_PY} 不存在，改用当前解释器 {sys.executable}")
    return sys.executable


def run(cmd: list[str], *, env: dict[str, str] | None = None) -> int:
    merged = os.environ.copy()
    merged["PIP_CONFIG_FILE"] = str(ROOT / "pip.ini")
    if env:
        merged.update(env)
    print(f"$ {' '.join(cmd)}")
    return subprocess.call(cmd, cwd=ROOT, env=merged)


def task_install() -> int:
    return run([python_exe(), "-m", "pip", "install", "-e", ".[dev]"])


def task_lint() -> int:
    code = run([python_exe(), "-m", "ruff", "check", "."])
    if code != 0:
        return code
    return run([python_exe(), "-m", "ruff", "format", "--check", "."])


def task_fmt() -> int:
    code = run([python_exe(), "-m", "ruff", "format", "."])
    if code != 0:
        return code
    return run([python_exe(), "-m", "ruff", "check", "--fix", "."])


def task_test() -> int:
    return run([python_exe(), "-m", "pytest", "-q"])


def task_check_models() -> int:
    return run([python_exe(), "-m", "codeatlas.cli", "check-models"])


def task_check_deps(in_docker: bool) -> int:
    if in_docker:
        return run(
            ["docker", "run", "--rm", DEFAULT_IMAGE, "python", "-m", "codeatlas.cli", "check-deps"]
        )
    return run([python_exe(), "-m", "codeatlas.cli", "check-deps"])


def task_chunk(top: int) -> int:
    return run([python_exe(), "-m", "codeatlas.cli", "chunk", "--top", str(top)])


def task_bm25() -> int:
    return run([python_exe(), "-m", "codeatlas.cli", "bm25-build"])


def task_index(full: bool, recreate: bool) -> int:
    cmd = [python_exe(), "-m", "codeatlas.cli", "index"]
    if full:
        cmd.append("--full")
    if recreate:
        cmd.append("--recreate")
    return run(cmd)


def task_ask(
    question: str,
    experiment: str | None,
    allow_uncalibrated: bool,
    no_cache: bool,
    continue_thread: bool = False,
) -> int:
    cmd = [python_exe(), "-m", "codeatlas.cli", "ask", question]
    if experiment:
        cmd.extend(["--experiment", experiment])
    if allow_uncalibrated:
        cmd.append("--allow-uncalibrated")
    if no_cache:
        cmd.append("--no-cache")
    if continue_thread:
        cmd.append("--continue")
    return run(cmd)


def task_compose(action: str) -> int:
    if action not in {"up", "down", "ps", "logs"}:
        print(f"compose 只支持 up/down/ps/logs，收到 {action}")
        return 2
    cmd = ["docker", "compose", action]
    if action == "up":
        cmd.append("-d")
    return run(cmd)


def task_search(query: str, limit: int, snippets: int) -> int:
    return run(
        [
            python_exe(),
            "-m",
            "codeatlas.cli",
            "bm25-search",
            query,
            "--limit",
            str(limit),
            "--snippets",
            str(snippets),
        ]
    )


def task_calibrate(no_cache: bool) -> int:
    cmd = [python_exe(), "scripts/calibrate_gate.py"]
    if no_cache:
        cmd.append("--no-cache")
    return run(cmd)


def task_eval(limit: int, no_cache: bool) -> int:
    cmd = [python_exe(), "scripts/eval_full.py"]
    if limit:
        cmd.extend(["--limit", str(limit)])
    if no_cache:
        cmd.append("--no-cache")
    return run(cmd)


def task_serve(port: int) -> int:
    return run(
        [
            python_exe(),
            "-m",
            "uvicorn",
            "codeatlas.api.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ]
    )


def task_bench_embed(n: int) -> int:
    return run([python_exe(), "scripts/embed_bench.py", "--n", str(n)])


def task_bench_llm(n: int) -> int:
    return run([python_exe(), "scripts/llm_bench.py", "--n", str(n)])


TASKS = {
    "install": "安装依赖（阿里云源）",
    "lint": "ruff check + format --check",
    "fmt": "ruff format + 自动修",
    "test": "pytest",
    "check-models": "三槽位连通性检查",
    "check-deps": "依赖版本一致性（--in-docker 查容器）",
    "chunk": "结构感知切分统计（--top N）",
    "bm25": "只重建 BM25 索引 + chunk store + manifest",
    "index": "全量/增量建索引：chunk store + BM25 + dense（--full / --recreate）",
    "search": "BM25 查询（--limit N --snippets N）",
    "ask": "端到端问答（--continue 接着上一轮 / --no-cache）",
    "compose": "docker compose up|down|ps|logs",
    "calibrate": "在 eval/dev.jsonl 上标定拒答阈值 τ（--no-cache 冷启动）",
    "eval": "跑 E1/E3/E4/E5 评测并生成 docs/EVAL.md（--limit N）",
    "serve": "启动 HTTP 服务（--port 8000）",
    "bench-embed": "embedding 吞吐实测（--n 320）",
    "bench-llm": "LLM 延迟实测（--n 5）",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="CodeAtlas 任务入口（不依赖 make）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="\n".join(f"  {name:<14} {desc}" for name, desc in TASKS.items()),
    )
    parser.add_argument("task", nargs="?", help="任务名；省略则列出全部")
    parser.add_argument("--top", type=int, default=8, help="chunk 任务：打印前 N 个文件")
    parser.add_argument("--limit", type=int, default=10, help="search：返回条数")
    parser.add_argument("--snippets", type=int, default=0, help="search：前 N 条打印代码")
    parser.add_argument("--n", type=int, default=320, help="bench 任务：样本文本数")
    parser.add_argument("--in-docker", action="store_true", help="check-deps：在容器内执行")
    parser.add_argument("--full", action="store_true", help="index：忽略已有索引全部重嵌")
    parser.add_argument("--recreate", action="store_true", help="index：删除并重建 collection")
    parser.add_argument("--experiment", default=None, help="ask：使用实验配置（如 llm_coder）")
    parser.add_argument(
        "--allow-uncalibrated",
        action="store_true",
        help="ask：refuse_tau 未标定时仍放行（评测用）",
    )
    parser.add_argument("--no-cache", action="store_true", help="ask/eval：禁用 Redis 缓存")
    parser.add_argument(
        "--continue", dest="continue_thread", action="store_true", help="ask：接着上一轮问"
    )
    parser.add_argument("--port", type=int, default=8000, help="serve：监听端口")
    parser.add_argument("extra", nargs="*", help="search / ask 的自然语言参数（可省略引号）")
    args = parser.parse_args(argv)

    if not args.task:
        print("可用任务：\n")
        for name, desc in TASKS.items():
            print(f"  {name:<14} {desc}")
        print("\n例：python tasks.py ask how does redis expire keys --experiment llm_coder")
        return 0

    task = args.task
    if task == "install":
        return task_install()
    if task == "lint":
        return task_lint()
    if task == "fmt":
        return task_fmt()
    if task == "test":
        return task_test()
    if task == "check-models":
        return task_check_models()
    if task == "check-deps":
        return task_check_deps(args.in_docker)
    if task == "chunk":
        return task_chunk(args.top)
    if task == "bm25":
        return task_bm25()
    if task == "index":
        return task_index(args.full, args.recreate)
    if task == "search":
        query = " ".join(args.extra)
        if not query:
            parser.error("search 需要查询词：python tasks.py search <query>")
        return task_search(query, args.limit, args.snippets)
    if task == "ask":
        question = " ".join(args.extra)
        if not question:
            parser.error("ask 需要问题：python tasks.py ask <question>")
        return task_ask(
            question, args.experiment, args.allow_uncalibrated, args.no_cache, args.continue_thread
        )
    if task == "compose":
        action = args.extra[0] if args.extra else "ps"
        return task_compose(action)
    if task == "calibrate":
        return task_calibrate(args.no_cache)
    if task == "eval":
        return task_eval(args.limit if hasattr(args, "limit") else 0, args.no_cache)
    if task == "serve":
        return task_serve(args.port)
    if task == "bench-embed":
        return task_bench_embed(args.n)
    if task == "bench-llm":
        return task_bench_llm(args.n)

    parser.error(f"未知任务：{task}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
