"""MCP server：只暴露**检索**能力，供 coding agent（Codex / Claude Code 等）调用。

为什么不提供 `ask_codebase` / `read_code`：
- 宿主本身就是 LLM，会归纳、会读文件、会写代码——再包一层"我帮你答"是重复劳动，
  答案过了两个模型，出错后也无法归因；
- 少两个工具 = 少两种误选。agent 的工具描述越少歧义，选错的概率越低；
- 副作用正合适：本进程**不需要 LLM key**，启动更快、失败面更小。

两个工具的分工是互补而非重叠：
- `search_code`：不知道叫什么，只知道"做什么" → 语义检索（BM25 + 向量 + 重排）；
- `get_symbol`：知道名字 → 精确查定义（含长函数多段、多文件同名）。

输出一律带 `path:start-end`——agent 拿到无法定位的代码等于没拿到。
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from codeatlas.config import load_config, resolve_repo
from codeatlas.indexing.store import DEFAULT_CHUNK_STORE, load_chunks
from codeatlas.providers.registry import build_deps
from codeatlas.retrieval.bm25 import BM25Index
from codeatlas.retrieval.dense import DenseIndex
from codeatlas.retrieval.service import HybridRetriever
from codeatlas.settings import Settings

DEFAULT_BM25_PATH = "data/bm25.json.gz"


def base_dir() -> Path:
    """项目根：优先 CODEATLAS_ROOT，否则用 cwd。

    **不能用 `Path(__file__)` 反推**：装成包之后 `__file__` 在 site-packages 里，
    推出来的是解释器目录（这个坑在 ISSUE-029 已经踩过一次）。
    宿主拉起 MCP 进程时的 cwd 也不一定是本项目——Codex 通常用「你正在编辑的项目」
    作为 cwd，所以接入时必须显式设 `CODEATLAS_ROOT`。
    """
    return Path(os.environ.get("CODEATLAS_ROOT") or Path.cwd())


MAX_CHARS_PER_HIT = 1500
MAX_TOTAL_CHARS = 12000
MAX_SYMBOL_HITS = 40

# log_level=WARNING：默认 INFO 会把每次工具调用打到 stderr，
# 混进宿主的日志里，排查真正的问题时全是噪声。
mcp = FastMCP("codeatlas", log_level="WARNING")


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "\n... (truncated)"


def _display_body(chunk: Any, limit: int) -> str:
    """展示用正文：压缩过长的前导注释，保证**代码本身**不被截断挤掉。

    实测 `expireIfNeeded` 前有 100 多行文档注释，直接截断 1500 字符的结果是
    "一整段注释、零行代码"——对 agent 完全无用。注释只留前两行作上下文，
    剩下的预算全给代码。
    """
    code = chunk.code
    comment = chunk.leading_comment
    if not comment or len(comment) <= 200:
        return _clip(code, limit)

    lines = code.splitlines()
    offset = max(0, chunk.body_start_line - chunk.start_line)
    if offset >= len(lines):
        return _clip(code, limit)
    merged = [*lines[:2], "/* ... 文档注释已省略 ... */", *lines[offset:]]
    return _clip("\n".join(merged), limit)


def _header(chunk: Any) -> str:
    symbol = f" {chunk.symbol}" if chunk.symbol else ""
    return f"{chunk.path}:{chunk.start_line}-{chunk.end_line}  ({chunk.node_type.value}{symbol})"


class _UnavailableDense:
    """qdrant 不可用时的占位对象：调用即抛，让 retriever 走已有的降级分支。"""

    def search(self, *args: Any, **kwargs: Any) -> list[Any]:
        raise RuntimeError("qdrant unavailable")

    def close(self) -> None:
        return None

    def count(self) -> int:
        return 0


class _Service:
    """进程级单例：索引只加载一次，后续工具调用复用。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._ready = False
        self.settings: Settings | None = None
        self.chunks: dict[str, Any] = {}
        # symbol -> chunk_id 列表：同名多定义、长函数多段都会落到同一个 key
        self.symbols: dict[str, list[str]] = {}
        self.by_lower: dict[str, list[str]] = {}
        self.bm25: Any = None
        self.dense: Any = None
        self.deps: Any = None
        self.retriever: Any = None
        self.repo_root: Any = None
        self.degraded: list[str] = []

    async def ensure(self) -> None:
        async with self._lock:
            if self._ready:
                return
            base = base_dir()
            settings = load_config(root=base)
            chunks = load_chunks(base / DEFAULT_CHUNK_STORE)
            chunk_map = {c.chunk_id: c for c in chunks}

            symbols: dict[str, list[str]] = {}
            by_lower: dict[str, list[str]] = {}
            for chunk in chunks:
                if not chunk.symbol:
                    continue
                symbols.setdefault(chunk.symbol, []).append(chunk.chunk_id)
                by_lower.setdefault(chunk.symbol.lower(), []).append(chunk.chunk_id)
            # 长函数被切成多段时遍历顺序不等于行号顺序，必须重排才能还原原文
            for ids in list(symbols.values()) + list(by_lower.values()):
                ids.sort(key=lambda cid: (chunk_map[cid].path, chunk_map[cid].start_line))

            try:
                dense: Any = DenseIndex.open(
                    settings.qdrant_url, settings.qdrant_collection, settings.emb_dim
                )
            except Exception:  # noqa: BLE001 - 降级成纯 BM25，不要让整个 server 起不来
                dense = _UnavailableDense()
                self.degraded.append("dense_unavailable")

            deps = build_deps(settings, use_cache=True)
            bm25 = BM25Index.load(base / DEFAULT_BM25_PATH)
            retriever = HybridRetriever(
                settings,
                dense=dense,
                bm25=bm25,
                chunks=chunk_map,
                embedder=deps.embed,
                reranker=deps.rerank,
            )

            self.settings = settings
            self.chunks = chunk_map
            self.symbols = symbols
            self.by_lower = by_lower
            self.bm25 = bm25
            self.dense = dense
            self.deps = deps
            self.retriever = retriever
            self.repo_root = resolve_repo(settings, root=base)
            self._ready = True

    def lookup(self, name: str) -> tuple[list[str], str]:
        """精确 → 忽略大小写 → 前缀 → 包含，依次降级。返回 (chunk_ids, 匹配方式)。"""
        query = name.strip()
        if not query:
            return [], "empty"

        if query in self.symbols:
            return list(self.symbols[query]), "exact"

        lowered = query.lower()
        if lowered in self.by_lower:
            return list(self.by_lower[lowered]), "case-insensitive"

        prefix = [
            cid for key, ids in self.by_lower.items() if key.startswith(lowered) for cid in ids
        ]
        if prefix:
            prefix.sort(key=lambda cid: (self.chunks[cid].path, self.chunks[cid].start_line))
            return prefix[:MAX_SYMBOL_HITS], "prefix"

        contains = [cid for key, ids in self.by_lower.items() if lowered in key for cid in ids]
        if contains:
            contains.sort(key=lambda cid: (self.chunks[cid].path, self.chunks[cid].start_line))
            return contains[:MAX_SYMBOL_HITS], "contains"

        return [], "none"


_service = _Service()


async def _ready_or_error() -> str | None:
    """初始化失败的提示要能直接照着做，否则 agent 只能看到一个 traceback。"""
    try:
        await _service.ensure()
    except Exception as exc:  # noqa: BLE001 - 启动期问题统一转成可读文本
        return (
            f"MCP 初始化失败：{exc}\n"
            f"检查项：1) CODEATLAS_ROOT 是否指向项目根（当前 {base_dir()}）；"
            "2) 是否已运行 `python tasks.py index`；3) qdrant/redis 是否已启动。"
        )
    return None


@mcp.tool()
async def search_code(query: str, top_k: int = 5, max_chars: int = MAX_CHARS_PER_HIT) -> str:
    """按语义在代码库里找实现，返回带行号的代码片段。

    用于"不知道它叫什么，只知道它做什么"：
    - "过期键是怎么触发的" → activeExpireCycle / expireIfNeeded
    - "命令行参数是怎么解析的" → 对应的解析函数

    知道名字时用 `get_symbol`（精确匹配，不依赖语义相似度）。
    需要穷举某个标识符的**所有**出现位置时也不要用它——它只返回 top-k，请直接用 grep。
    """
    if error := await _ready_or_error():
        return error
    assert _service.retriever is not None

    started = time.perf_counter()
    result = await _service.retriever.retrieve(query, top_k=max(1, min(top_k, 20)))
    elapsed = (time.perf_counter() - started) * 1000

    if not result.hits:
        return "未找到相关片段。可以换个说法，或改用 get_symbol 按符号名精确查找。"

    lines = [f"# 检索：{query}"]
    # 降级提示放**顶部**：放末尾时模型往往已经读完成绩单开始写结论了。
    # 只有 BM25 时召回明显变差（缺语义召回），模型需要知道这一点才能判断要不要再查一次。
    notes = sorted(set(result.degraded) | set(_service.degraded))
    if notes:
        lines.append("⚠️ 降级检索（" + ", ".join(notes) + "）：结果可能不完整，必要时换个说法再查")

    budget = MAX_TOTAL_CHARS
    for index, hit in enumerate(result.hits, start=1):
        chunk = hit.chunk
        lines.append(f"\n[{index}] {_header(chunk)}  score={hit.score:.4f}")
        if budget <= 0:
            lines.append("    (其余结果因输出预算省略；缩小 top_k 或问得更具体)")
            break
        body = _display_body(chunk, min(max(200, max_chars), budget))
        budget -= len(body)
        lines.append("```c")
        lines.append(body)
        lines.append("```")

    lines.append(f"> {elapsed:.0f}ms")
    return "\n".join(lines)


@mcp.tool()
async def get_symbol(name: str, max_chars: int = MAX_CHARS_PER_HIT) -> str:
    """按符号名精确查定义（函数 / 结构体 / 枚举 / 宏 / typedef），返回位置与源码。

    用于"我知道它叫什么"：`zmalloc_used_memory`、`listTypePush`、`clusterNode`。

    与 search_code 的区别：这里不走向量检索，符号名匹配是确定的，
    不会因为"名字相近"返回隔壁函数（`zmalloc_used_memory` 不会变成 `zmalloc_usable_size`）。

    两种情况会返回多条，均会明确标注：
    - 同名定义散在多个文件（例如每个平台一份 `aeApiCreate`）；
    - 长函数被切分成多段（每段带序号，按行号顺序拼接即原文）。
    """
    if error := await _ready_or_error():
        return error

    ids, how = _service.lookup(name)
    if not ids:
        text = f"未找到符号：`{name}`。"
        near = _near_miss(name)
        if near:
            text += "\n相近符号：" + "、".join(f"`{s}`" for s in near)
        text += "\n不确定名字时请用 search_code 按语义查找。"
        return text

    chunks = [_service.chunks[cid] for cid in ids]
    files: dict[str, list[Any]] = {}
    for chunk in chunks:
        files.setdefault(chunk.path, []).append(chunk)
    split_total = max((c.split_total or 1 for c in chunks), default=1)

    labels = {
        "exact": "精确匹配",
        "case-insensitive": "忽略大小写匹配",
        "prefix": "前缀匹配（没有同名符号）",
        "contains": "包含匹配（没有同名或前缀匹配）",
    }
    lines = [f"# {chunks[0].symbol or name}", labels.get(how, how)]

    if len(files) > 1:
        lines.append(
            f"⚠️ 同名定义 {len(files)} 处："
            + "、".join(sorted(files))
            + "（常见于平台适配或测试替身，按路径判断该用哪个）"
        )
    if split_total > 1:
        lines.append(f"⚠️ 该符号过长，已切分为 {split_total} 段；按序号顺序拼接即原文")

    budget = MAX_TOTAL_CHARS
    shown = 0
    for path in sorted(files):
        for order, chunk in enumerate(files[path], start=1):
            shown += 1
            tag = f" [{order}/{len(files[path])}]" if len(files[path]) > 1 else ""
            lines.append(f"\n## {_header(chunk)}{tag}")
            if chunk.signature:
                lines.append(f"签名：`{chunk.signature}`")
            if budget <= 0:
                lines.append("（输出预算用尽，后续片段省略；可按上面的行号直接读文件）")
                continue
            body = _display_body(chunk, min(max(200, max_chars), budget))
            budget -= len(body)
            lines.append("```c")
            lines.append(body)
            lines.append("```")

    if shown < len(chunks):
        lines.append(f"\n> 共 {len(chunks)} 个片段，已显示 {shown} 个")
    return "\n".join(lines)


def _near_miss(name: str) -> list[str]:
    """查不到时给几个相近符号——比一句"未找到"有用得多。"""
    lowered = name.strip().lower()
    if len(lowered) < 3:
        return []
    scored: list[tuple[int, str]] = []
    for symbol in _service.symbols:
        low = symbol.lower()
        # 公共前缀长度做廉价相似度，不额外引依赖
        common = 0
        for a, b in zip(low, lowered, strict=False):
            if a != b:
                break
            common += 1
        if common >= max(3, len(lowered) // 2):
            scored.append((-common, symbol))
    scored.sort()
    return [symbol for _, symbol in scored[:5]]


def main() -> None:
    """stdio 传输：由宿主（Codex / Claude Code）拉起本进程。"""
    mcp.run()


if __name__ == "__main__":
    main()
