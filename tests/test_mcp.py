"""MCP server 的行为测试（全离线，不碰网络）。

重点不是"能返回字符串"，而是几条容易错的行为：
- 同名多定义要**全给**并提示，不能只返回第一条（`aeApiCreate` 有 4 份）；
- 长函数被切段要按行号排序并标注序号（`RM_Call` 有 21 段，遍历顺序是乱的）；
- 查不到时给相近符号，而不是一句"未找到"；
- 前导注释不能把代码挤出预算（`expireIfNeeded` 有 100 多行注释）。
"""

from __future__ import annotations

import pytest

from codeatlas.indexing.types import Chunk, ChunkKind
from codeatlas.mcp import server as mcp_server
from codeatlas.retrieval.service import RetrievalResult, RetrievedChunk


def make_chunk(
    chunk_id: str,
    path: str,
    start: int,
    end: int,
    symbol: str | None,
    kind: ChunkKind = ChunkKind.FUNCTION,
    *,
    split_index: int | None = None,
    split_total: int | None = None,
    comment: str | None = None,
    signature: str | None = None,
) -> Chunk:
    body = f"int {symbol or 'anon'}(void) {{ return {start}; }}"
    code = f"{comment}\n{body}" if comment else body
    return Chunk(
        chunk_id=chunk_id,
        path=path,
        start_line=start,
        end_line=end,
        body_start_line=start + (comment.count("\n") + 1 if comment else 0),
        node_type=kind,
        symbol=symbol,
        signature=signature or (f"int {symbol}(void)" if symbol else None),
        code=code,
        context_header=f"// {path}",
        leading_comment=comment,
        is_split=split_total is not None,
        split_index=split_index,
        split_total=split_total,
        file_sha="sha",
    )


@pytest.fixture
def stub_service(monkeypatch):
    """把 _Service 置为就绪，注入固定 chunk，跳过真实索引加载。"""

    def install(chunks: list[Chunk], retriever=None):
        service = mcp_server._service
        monkeypatch.setattr(service, "_ready", True, raising=False)
        monkeypatch.setattr(service, "chunks", {c.chunk_id: c for c in chunks}, raising=False)
        symbols: dict[str, list[str]] = {}
        by_lower: dict[str, list[str]] = {}
        for chunk in chunks:
            if not chunk.symbol:
                continue
            symbols.setdefault(chunk.symbol, []).append(chunk.chunk_id)
            by_lower.setdefault(chunk.symbol.lower(), []).append(chunk.chunk_id)
        for ids in [*symbols.values(), *by_lower.values()]:
            ids.sort(key=lambda cid: (service.chunks[cid].path, service.chunks[cid].start_line))
        monkeypatch.setattr(service, "symbols", symbols, raising=False)
        monkeypatch.setattr(service, "by_lower", by_lower, raising=False)
        monkeypatch.setattr(service, "retriever", retriever, raising=False)
        monkeypatch.setattr(service, "degraded", [], raising=False)
        return service

    return install


# ---------- 查找策略 ----------


async def test_exact_lookup_wins_over_near_names(stub_service):
    stub_service(
        [
            make_chunk("a" * 16, "src/zmalloc.c", 410, 414, "zmalloc_used_memory"),
            make_chunk("b" * 16, "src/zmalloc.c", 380, 395, "zmalloc_usable_size"),
        ]
    )

    out = await mcp_server.get_symbol("zmalloc_used_memory")

    assert "精确匹配" in out
    assert "src/zmalloc.c:410-414" in out
    assert "zmalloc_usable_size" not in out


async def test_lookup_is_case_insensitive_when_no_exact(stub_service):
    stub_service([make_chunk("a" * 16, "src/x.c", 1, 5, "zmalloc_used_memory")])

    out = await mcp_server.get_symbol("ZMALLOC_USED_MEMORY")

    assert "忽略大小写匹配" in out
    assert "zmalloc_used_memory" in out


async def test_prefix_lookup_explains_it_is_not_exact(stub_service):
    stub_service(
        [
            make_chunk("a" * 16, "src/x.c", 1, 5, "zmalloc_used_memory"),
            make_chunk("b" * 16, "src/y.c", 9, 12, "zmalloc_usable"),
        ]
    )

    out = await mcp_server.get_symbol("zmalloc_use")

    assert "前缀匹配" in out
    # 必须明确"不是同名"，否则模型会以为只找到一个
    assert "没有同名符号" in out


async def test_unknown_symbol_suggests_near_misses(stub_service):
    stub_service([make_chunk("a" * 16, "src/x.c", 1, 5, "zmalloc_used_memory")])

    out = await mcp_server.get_symbol("zmalloc_used_memroy")

    assert "未找到符号" in out
    assert "相近符号" in out
    assert "zmalloc_used_memory" in out
    # 拼错时不要误导去走语义检索之外的路
    assert "search_code" in out


async def test_empty_name_is_handled(stub_service):
    stub_service([make_chunk("a" * 16, "src/x.c", 1, 5, "f")])

    assert "未找到符号" in await mcp_server.get_symbol("   ")


# ---------- 多重定义与分段 ----------


async def test_same_symbol_in_many_files_is_reported(stub_service):
    stub_service(
        [
            make_chunk("a" * 16, "src/ae_epoll.c", 39, 57, "aeApiCreate"),
            make_chunk("b" * 16, "src/ae_kqueue.c", 45, 60, "aeApiCreate"),
            make_chunk("c" * 16, "src/ae_select.c", 30, 44, "aeApiCreate"),
            make_chunk("d" * 16, "src/ae_evport.c", 74, 95, "aeApiCreate"),
        ]
    )

    out = await mcp_server.get_symbol("aeApiCreate")

    assert "同名定义 4 处" in out
    for path in ("ae_epoll.c", "ae_kqueue.c", "ae_select.c", "ae_evport.c"):
        assert path in out


async def test_split_parts_are_ordered_by_line_not_insertion(stub_service):
    """切分产物的遍历顺序是乱的，必须按行号排，否则 agent 拼出来的原文是错位的。"""
    stub_service(
        [
            make_chunk(
                "c" * 16, "src/rdb.c", 300, 340, "rdbLoadObject", split_index=3, split_total=3
            ),
            make_chunk(
                "a" * 16, "src/rdb.c", 100, 150, "rdbLoadObject", split_index=1, split_total=3
            ),
            make_chunk(
                "b" * 16, "src/rdb.c", 160, 290, "rdbLoadObject", split_index=2, split_total=3
            ),
        ]
    )

    out = await mcp_server.get_symbol("rdbLoadObject", max_chars=80)

    assert "切分为 3 段" in out
    positions = [out.index(f"[{i}/3]") for i in (1, 2, 3)]
    assert positions == sorted(positions)
    # 行号顺序也要对
    assert out.index("100-150") < out.index("160-290") < out.index("300-340")


async def test_long_leading_comment_does_not_hide_code(stub_service):
    """`expireIfNeeded` 有 100 多行文档注释，直接截断会只剩注释、零行代码。"""
    comment = "/*\n" + "\n".join(f" * line {i}" for i in range(120)) + "\n */"
    stub_service([make_chunk("a" * 16, "src/db.c", 1746, 1818, "expireIfNeeded", comment=comment)])

    out = await mcp_server.get_symbol("expireIfNeeded", max_chars=600)

    assert "文档注释已省略" in out
    # 关键：代码必须在预算内出现
    assert "int expireIfNeeded(void)" in out


# ---------- search_code ----------


class StubRetriever:
    def __init__(self, chunks: list[Chunk], degraded: list[str] | None = None) -> None:
        self.chunks = chunks
        self.degraded = degraded or []
        self.queries: list[str] = []

    async def retrieve(self, query: str, *, top_k: int | None = None) -> RetrievalResult:
        self.queries.append(query)
        return RetrievalResult(
            query=query,
            hits=[
                RetrievedChunk(chunk=c, score=0.5, rank=i + 1, source="rerank")
                for i, c in enumerate(self.chunks[: top_k or 5])
            ],
            degraded=list(self.degraded),
            timings_ms={"recall": 1.0},
        )


async def test_search_code_returns_locatable_headers(stub_service):
    retriever = StubRetriever([make_chunk("a" * 16, "src/expire.c", 35, 67, "expireIfNeeded")])
    stub_service([], retriever)

    out = await mcp_server.search_code("how does expire work", top_k=3)

    assert "src/expire.c:35-67" in out
    assert "```c" in out
    assert "score=" in out


async def test_search_code_warns_degraded_at_top(stub_service):
    """降级提示必须在顶部：放末尾时模型已经写完成结论了。"""
    retriever = StubRetriever(
        [make_chunk("a" * 16, "src/x.c", 1, 5, "f")], degraded=["dense_unavailable"]
    )
    stub_service([], retriever)

    out = await mcp_server.search_code("anything")
    lines = out.splitlines()

    assert "降级检索" in lines[1]
    assert "dense_unavailable" in lines[1]


async def test_search_code_clamps_top_k(stub_service):
    retriever = StubRetriever(
        [make_chunk(f"{i}" * 16, "src/x.c", i * 10, i * 10 + 5, f"f{i}") for i in range(1, 6)]
    )
    stub_service([], retriever)

    await mcp_server.search_code("q", top_k=999)

    # 上限 20，这里只有 5 条，验证不会抛错且全部返回
    assert len(retriever.queries) == 1


async def test_search_code_empty_result_gives_next_step(stub_service):
    stub_service([], StubRetriever([]))

    out = await mcp_server.search_code("nothing here")

    assert "未找到相关片段" in out
    assert "get_symbol" in out
