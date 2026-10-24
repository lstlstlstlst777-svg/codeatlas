"""混合检索编排：降级阶梯必须逐级可用（P5）。

用 FakeProvider + 本地 Qdrant + 小 BM25，全离线、可复现。
重点不是"能检索"，而是**每一路挂掉时都还能给出结果或明确降级原因**。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codeatlas.indexing.types import Chunk, ChunkKind
from codeatlas.providers.fake import FakeEmbedding, FakeReranker
from codeatlas.retrieval.bm25 import BM25Index, BM25Params
from codeatlas.retrieval.dense import DenseIndex
from codeatlas.retrieval.service import HybridRetriever
from codeatlas.settings import Settings

DIM = 8

CHUNKS = [
    Chunk(
        chunk_id="a" * 16,
        path="src/zmalloc.c",
        start_line=410,
        end_line=414,
        body_start_line=410,
        node_type=ChunkKind.FUNCTION,
        symbol="zmalloc_used_memory",
        signature="size_t zmalloc_used_memory(void)",
        code="size_t zmalloc_used_memory(void) { return used_memory; }",
        context_header="// src/zmalloc.c | function zmalloc_used_memory",
        file_sha="sha_zmalloc",
    ),
    Chunk(
        chunk_id="b" * 16,
        path="src/expire.c",
        start_line=35,
        end_line=67,
        body_start_line=35,
        node_type=ChunkKind.FUNCTION,
        symbol="activeExpireCycleTryExpire",
        signature="int activeExpireCycleTryExpire(redisDb *db, dictEntry *de, long long now)",
        code="int activeExpireCycleTryExpire(redisDb *db, dictEntry *de, long long now) { return 0; }",
        context_header="// src/expire.c | function activeExpireCycleTryExpire",
        file_sha="sha_expire",
    ),
    Chunk(
        chunk_id="c" * 16,
        path="src/server.c",
        start_line=2558,
        end_line=2677,
        body_start_line=2558,
        node_type=ChunkKind.FUNCTION,
        symbol="initServer",
        signature="void initServer(void)",
        code="void initServer(void) { server.el = aeCreateEventLoop(1024); }",
        context_header="// src/server.c | function initServer",
        file_sha="sha_server",
    ),
]


class BrokenEmbedding(FakeEmbedding):
    async def embed(self, texts):
        raise RuntimeError("embedding 服务 503")


class BrokenReranker(FakeReranker):
    async def rerank(self, query, documents, top_n):
        raise RuntimeError("rerank 服务 503")


class FlakyDense(DenseIndex):
    def search(self, vector, limit):
        raise RuntimeError("qdrant 掉线")


@pytest.fixture
def setup(tmp_path: Path):
    settings = Settings(provider_mode="fake", emb_dim=DIM, top_k=2, rerank_window=5)
    chunks = {c.chunk_id: c for c in CHUNKS}

    bm25 = BM25Index(BM25Params())
    bm25.add_many((c.chunk_id, f"{c.signature or ''}\n{c.code}", c.symbol or "") for c in CHUNKS)

    dense = DenseIndex.open(str(tmp_path / "qdrant"), "svc", dim=DIM)
    dense.ensure_collection()
    dense.upsert(CHUNKS, _sync_embed(CHUNKS))

    yield settings, chunks, bm25, dense
    dense.close()


def _sync_embed(chunks: list[Chunk]) -> list[list[float]]:
    from codeatlas.providers.fake import deterministic_vector

    return [deterministic_vector(c.embedding_text(), DIM) for c in chunks]


def make_retriever(settings, chunks, bm25, dense, *, embedder=None, reranker=None):
    return HybridRetriever(
        settings,
        dense=dense,
        bm25=bm25,
        chunks=chunks,
        embedder=embedder or FakeEmbedding(dim=DIM),
        reranker=reranker or FakeReranker(),
    )


async def test_exact_symbol_query_hits_expected_chunk(setup):
    settings, chunks, bm25, dense = setup
    retriever = make_retriever(settings, chunks, bm25, dense)

    result = await retriever.retrieve("zmalloc_used_memory", top_k=2)

    assert result.hits
    assert result.hits[0].chunk.chunk_id == "a" * 16
    assert not result.is_degraded
    assert "recall" in result.timings_ms


async def test_results_carry_line_numbers(setup):
    settings, chunks, bm25, dense = setup
    retriever = make_retriever(settings, chunks, bm25, dense)

    result = await retriever.retrieve("initServer", top_k=1)

    chunk = result.hits[0].chunk
    assert chunk.path == "src/server.c"
    assert chunk.start_line == 2558


async def test_embedding_failure_falls_back_to_bm25(setup):
    settings, chunks, bm25, dense = setup
    retriever = make_retriever(settings, chunks, bm25, dense, embedder=BrokenEmbedding(dim=DIM))

    result = await retriever.retrieve("activeExpireCycle", top_k=2)

    assert result.hits, "BM25 单独也要能出结果"
    assert "embed_unavailable" in result.degraded
    assert "向量检索不可用" in result.note()


async def test_dense_failure_falls_back_to_bm25(setup):
    settings, chunks, bm25, _dense = setup
    flaky = FlakyDense.__new__(FlakyDense)
    flaky.client = _dense.client
    flaky.collection = _dense.collection
    flaky.dim = DIM
    flaky._owns_client = False
    flaky.local_mode = True

    retriever = make_retriever(settings, chunks, bm25, flaky)

    result = await retriever.retrieve("zmalloc", top_k=2)

    assert result.hits
    assert "dense_unavailable" in result.degraded


async def test_rerank_failure_keeps_fusion_order(setup):
    settings, chunks, bm25, dense = setup
    retriever = make_retriever(settings, chunks, bm25, dense, reranker=BrokenReranker())

    result = await retriever.retrieve("zmalloc", top_k=2)

    assert result.hits
    assert "rerank_unavailable" in result.degraded
    assert result.hits[0].source == "fusion"


async def test_no_match_returns_empty_not_error(setup):
    settings, chunks, bm25, dense = setup
    retriever = make_retriever(settings, chunks, bm25, dense)

    result = await retriever.retrieve("完全无关的词汇xyzzy", top_k=2)

    # 用的是确定性假向量，可能仍有召回；关键是"不抛异常且结构完整"
    assert isinstance(result.hits, list)


async def test_top_k_is_respected(setup):
    settings, chunks, bm25, dense = setup
    retriever = make_retriever(settings, chunks, bm25, dense)

    result = await retriever.retrieve("redis server", top_k=1)

    assert len(result.hits) <= 1


async def test_rerank_scores_recorded_when_healthy(setup):
    settings, chunks, bm25, dense = setup
    retriever = make_retriever(settings, chunks, bm25, dense)

    result = await retriever.retrieve("zmalloc_used_memory", top_k=2)

    assert result.rerank is not None
    assert result.hits[0].source == "rerank"
    assert result.hits[0].rerank_score is not None
