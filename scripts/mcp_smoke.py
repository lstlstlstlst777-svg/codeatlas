"""MCP 冒烟：用真实 stdio 客户端拉起 server，验证握手与两个工具。

不直接 import 函数——要证明的是**协议链路**通：宿主拉起进程、握手、调工具、
拿到 content 数组。直接调用会绕过序列化与传输层，而那正是最容易出问题的地方。
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def _text(result) -> str:
    return "\n".join(item.text for item in result.content if getattr(item, "text", None))


async def main() -> int:
    # 必须显式传完整 env：`env=None` 时 MCP SDK 只透传 HOME/PATH 等白名单变量，
    # CODEATLAS_ROOT 会被悄悄丢掉，表现是"从别的目录启动就找不到索引"。
    # Codex 是通过 config.toml 的 [mcp_servers.codeatlas.env] 显式设置的，行为一致。
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "codeatlas.mcp.server"],
        env={**os.environ},
    )

    failures = 0
    started = time.perf_counter()
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        print(f"handshake {time.perf_counter() - started:.1f}s")

        tools = await session.list_tools()
        names = sorted(t.name for t in tools.tools)
        print("tools:", ", ".join(names))
        if names != ["get_symbol", "search_code"]:
            print(f"FAIL: 工具集不符，实际 {names}")
            failures += 1

        async def call(tool: str, args: dict) -> tuple[str, float]:
            t0 = time.perf_counter()
            result = await session.call_tool(tool, args)
            return _text(result), (time.perf_counter() - t0) * 1000

        body, ms = await call("search_code", {"query": "how does Redis expire keys", "top_k": 3})
        ok = "src/" in body and "```c" in body and ms < 60000
        print(f"\nsearch_code  {ms:6.0f}ms {len(body):>6} chars  {'OK' if ok else 'FAIL'}")
        print("  " + next((ln for ln in body.splitlines() if ln.startswith("[1]")), "")[:96])
        failures += 0 if ok else 1

        body, ms = await call("get_symbol", {"name": "zmalloc_used_memory"})
        ok = "src/zmalloc.c" in body and "size_t zmalloc_used_memory(void)" in body
        print(f"\nget_symbol   {ms:6.0f}ms {len(body):>6} chars  {'OK' if ok else 'FAIL'}")
        failures += 0 if ok else 1

        body, _ = await call("get_symbol", {"name": "aeApiCreate"})
        ok = "同名定义 4 处" in body
        print(f"  同名多定义提示 {'':<8} {'OK' if ok else 'FAIL'}")
        failures += 0 if ok else 1

        body, _ = await call("get_symbol", {"name": "rdbLoadObject", "max_chars": 200})
        ok = "切分为 11 段" in body and "[1/11]" in body
        print(f"  长函数分段标注 {'':<8} {'OK' if ok else 'FAIL'}")
        failures += 0 if ok else 1

        body, _ = await call("get_symbol", {"name": "zmalloc_used_memroy"})
        ok = "相近符号" in body and "zmalloc_used_memory" in body
        print(f"  拼错给相近符号 {'':<8} {'OK' if ok else 'FAIL'}")
        failures += 0 if ok else 1

        body, _ = await call("search_code", {"query": "kubernetes pod resource limit"})
        ok = isinstance(body, str) and len(body) > 0
        print(f"  无关查询不崩 {'':<10} {'OK' if ok else 'FAIL'}")
        failures += 0 if ok else 1

    print(f"\n{'PASS' if failures == 0 else f'{failures} 项失败'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
