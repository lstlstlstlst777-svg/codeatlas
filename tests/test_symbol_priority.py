"""两条确定性排序规则（ISSUE-025 的缓解措施）。

它们不是调参，是**规则**，所以可以写死断言：
1. 用户写出完整标识符时，该标识符的定义必须排第一（rerank 分不清"名字像"和"就是它"）；
2. 没在问模块时，`RM_*` 这类对外 API 包装不应抢占核心实现的位置。

这两条都来自 dev 集上的真实失败（q001 / q016 / q021），不是臆想的需求。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codeatlas.indexing.types import Chunk, ChunkKind
from codeatlas.providers.fake import FakeEmbedding, FakeReranker
from codeatlas.retrieval.bm25 import BM25Index, BM25Params
from codeatlas.retrieval.dense import DenseIndex
from codeatlas.retrieval.service import (
    HybridRetriever,
    asks_about_modules,
    extract_identifiers,
    is_module_api,
)
from codeatlas.settings import Settings

DIM = 8


def make_chunk(chunk_id: str, path: str, symbol: str, start: int = 1) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        path=path,
        start_line=start,
        end_line=start + 4,
        body_start_line=start,
        node_type=ChunkKind.FUNCTION,
        symbol=symbol,
        signature=f"void {symbol}(void)",
        code=f"void {symbol}(void) {{ /* {symbol} */ }}",
        context_header=f"// {path} | {symbol}",
        file_sha="sha",
    )


# ---------- 标识符抽取 ----------


@pytest.mark.parametrize(
    "query,expected",
    [
        ("zmalloc_used_memory 这个函数做什么", {"zmalloc_used_memory"}),
        ("aeCreateEventLoop 在哪里定义", {"aeCreateEventLoop"}),
        ("看一下 listTypePush 和 listTypePop", {"listTypePush", "listTypePop"}),
        ("how does Redis expire keys", set()),
        ("主从复制的握手流程是怎样的", set()),
        ("", set()),
    ],
)
def test_extract_identifiers(query, expected):
    assert extract_identifiers(query) == expected


def test_extract_identifiers_ignores_plain_words():
    """普通英文单词不该被当成符号去置顶。"""
    assert extract_identifiers("what is the server doing") == set()


# ---------- 模块 API 判定 ----------


def test_module_api_detected():
    assert is_module_api(make_chunk("a" * 16, "src/module.c", "RM_Calloc"))


def test_core_function_is_not_module_api():
    assert not is_module_api(make_chunk("a" * 16, "src/zmalloc.c", "zmalloc"))


def test_rm_prefixed_outside_module_c_is_not_module_api():
    """只有 module.c 里的 RM_* 才是对外 API 包装。"""
    assert not is_module_api(make_chunk("a" * 16, "src/cluster.c", "RM_Something"))


@pytest.mark.parametrize(
    "query",
    [
        "how to write a Redis module",
        "如何开发一个模块",
        "RM_CreateCommand 怎么用",
        "redis module api",
    ],
)
def test_asks_about_modules_true(query):
    assert asks_about_modules(query)


@pytest.mark.parametrize(
    "query",
    [
        "内存分配是在哪里实现的",
        "how does Redis expire keys",
        "跳表怎么插入节点",
    ],
)
def test_asks_about_modules_false(query):
    assert not asks_about_modules(query)


# ---------- 集成：置顶与过滤 ----------


@pytest.fixture
def retriever_setup(tmp_path: Path):
    chunks = [
        make_chunk("1" * 16, "src/zmalloc.c", "zmalloc_used_memory"),
        make_chunk("2" * 16, "src/zmalloc.h", "zmalloc_usable_size"),
        make_chunk("3" * 16, "src/module.c", "RM_MallocUsableSize"),
        make_chunk("4" * 16, "src/server.c", "initServer"),
    ]
    chunk_map = {c.chunk_id: c for c in chunks}
    settings = Settings(provider_mode="fake", emb_dim=DIM, top_k=3, rerank_window=10)

    bm25 = BM25Index(BM25Params())
    bm25.add_many((c.chunk_id, f"{c.signature}\n{c.code}", c.symbol or "") for c in chunks)

    dense = DenseIndex.open(str(tmp_path / "q"), "svc", dim=DIM)
    dense.ensure_collection()
    from codeatlas.providers.fake import deterministic_vector

    dense.upsert(chunks, [deterministic_vector(c.embedding_text(), DIM) for c in chunks])

    retriever = HybridRetriever(
        settings,
        dense=dense,
        bm25=bm25,
        chunks=chunk_map,
        embedder=FakeEmbedding(dim=DIM),
        reranker=FakeReranker(),
    )
    yield retriever
    dense.close()


async def test_exact_symbol_is_pinned_first(retriever_setup):
    """写全 `zmalloc_used_memory` 时，它的定义必须排第一，哪怕 rerank 更喜欢 usable_size。"""
    result = await retriever_setup.retrieve("zmalloc_used_memory", top_k=3)

    assert result.hits[0].chunk.symbol == "zmalloc_used_memory"
    assert result.hits[0].source in {"symbol_pin", "rerank"}


async def test_pinned_hit_does_not_fake_a_score(retriever_setup):
    """规则置顶不能编造 rerank 分数，否则拒答门会被骗。"""
    result = await retriever_setup.retrieve("zmalloc_used_memory", top_k=3)

    pinned = [h for h in result.hits if h.source == "symbol_pin"]
    for hit in pinned:
        assert hit.score == 0.0
        assert hit.rerank_score is None


async def test_module_api_filtered_when_not_asked(retriever_setup):
    result = await retriever_setup.retrieve("内存分配在哪里实现", top_k=3)

    symbols = [h.chunk.symbol for h in result.hits]
    assert "RM_MallocUsableSize" not in symbols


async def test_module_api_kept_when_asked(retriever_setup):
    """问模块 API 时不该被过滤，且写出完整符号时应被置顶。

    这条同时验证两件事：过滤规则有例外，例外本身也受符号置顶保护。
    """
    result = await retriever_setup.retrieve("RM_MallocUsableSize 是什么", top_k=3)

    assert result.hits, "问模块 API 时不应该查不到东西"
    assert result.hits[0].chunk.symbol == "RM_MallocUsableSize"


async def test_pin_keeps_remaining_order(retriever_setup):
    result = await retriever_setup.retrieve("zmalloc_used_memory", top_k=3)

    ranks = [h.rank for h in result.hits]
    assert ranks == list(range(1, len(ranks) + 1)), "rank 必须连续且从 1 开始"
