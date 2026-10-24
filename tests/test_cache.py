"""缓存的行为边界（P7 的基础件）。

缓存最容易出的两种错：
1. **该 miss 却 hit**——候选集变了却复用了旧排序，结果静默错位；
2. **缓存挂了变成故障**——Redis 连不上就整个问答失败。

这里用内存 Fake 后端把两条都钉住，不需要真 Redis。
"""

from __future__ import annotations

import pytest

from codeatlas.providers.base import RerankHit
from codeatlas.providers.fake import FakeEmbedding, FakeReranker
from codeatlas.retrieval.cache import (
    CachedEmbedding,
    CachedReranker,
    NullCache,
    pack_vector,
    text_key,
    unpack_vector,
)


class FakeCache:
    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}

    async def get(self, key: str) -> bytes | None:
        return self.data.get(key)

    async def set(self, key: str, value: bytes, ttl: int) -> None:
        self.data[key] = value

    async def get_many(self, keys: list[str]) -> list[bytes | None]:
        return [self.data.get(k) for k in keys]

    async def set_many(self, items: dict[str, bytes], ttl: int) -> None:
        self.data.update(items)

    async def close(self) -> None:
        return None


class CountingEmbedding(FakeEmbedding):
    def __init__(self, dim: int = 4) -> None:
        super().__init__(dim=dim)
        self.calls = 0

    async def embed(self, texts):
        self.calls += 1
        return await super().embed(texts)


class CountingReranker(FakeReranker):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def rerank(self, query, documents, top_n):
        self.calls += 1
        return await super().rerank(query, documents, top_n)


# ---------- 向量编码 ----------


def test_pack_unpack_roundtrip():
    vector = [0.5, -0.25, 1.0, 0.0]
    assert unpack_vector(pack_vector(vector)) == pytest.approx(vector)


def test_packed_size_is_4_bytes_per_dim():
    assert len(pack_vector([0.1] * 1024)) == 4096


def test_text_key_is_stable_and_distinct():
    assert text_key("emb", "fp1", "hello") == text_key("emb", "fp1", "hello")
    assert text_key("emb", "fp1", "hello") != text_key("emb", "fp1", "world")
    # 指纹变了必须换 key，否则改配置后会读到旧结果
    assert text_key("emb", "fp1", "hello") != text_key("emb", "fp2", "hello")


# ---------- embedding 缓存 ----------


async def test_embedding_cache_hits_on_second_call():
    cache = FakeCache()
    inner = CountingEmbedding(dim=4)
    cached = CachedEmbedding(inner, cache, "fp1")

    first = await cached.embed(["a", "b"])
    second = await cached.embed(["a", "b"])

    assert inner.calls == 1, "第二次不应再请求 embedding"
    # 缓存用 float32 存储，与 Qdrant 内部精度一致；比较必须用近似而非 ==
    for got, want in zip(second, first, strict=True):
        assert got == pytest.approx(want, rel=1e-6)


async def test_embedding_cache_partial_hit():
    """只补 miss 的那几条，已缓存的不要重算。"""
    cache = FakeCache()
    inner = CountingEmbedding(dim=4)
    cached = CachedEmbedding(inner, cache, "fp1")

    await cached.embed(["a", "b"])
    result = await cached.embed(["a", "c"])

    assert inner.calls == 2
    assert len(result) == 2


async def test_embedding_preserves_order_after_partial_hit():
    cache = FakeCache()
    inner = CountingEmbedding(dim=4)
    cached = CachedEmbedding(inner, cache, "fp1")
    reference = await CachedEmbedding(FakeEmbedding(dim=4), FakeCache(), "x").embed(["a", "b", "c"])

    await cached.embed(["b"])
    mixed = await cached.embed(["a", "b", "c"])

    assert len(mixed) == len(reference)
    for got, want in zip(mixed, reference, strict=True):
        assert got == pytest.approx(want, rel=1e-6), "混合命中/未命中时顺序不能乱"


async def test_embedding_empty_input():
    cached = CachedEmbedding(CountingEmbedding(dim=4), FakeCache(), "fp1")
    assert await cached.embed([]) == []


async def test_embedding_cache_stats():
    cached = CachedEmbedding(CountingEmbedding(dim=4), FakeCache(), "fp1")
    await cached.embed(["a"])
    await cached.embed(["a"])
    assert "hit=1/2" in cached.stats()


# ---------- rerank 缓存 ----------


async def test_rerank_cache_hits_on_identical_input():
    cache = FakeCache()
    inner = CountingReranker()
    cached = CachedReranker(inner, cache, "fp1")

    first = await cached.rerank("q", ["doc1", "doc2"], 2)
    second = await cached.rerank("q", ["doc1", "doc2"], 2)

    assert inner.calls == 1
    assert [(h.index, h.score) for h in first] == [(h.index, h.score) for h in second]


async def test_rerank_cache_misses_when_candidates_change():
    """候选集变了必须 miss——否则会把别的候选的分数套上来。"""
    cache = FakeCache()
    inner = CountingReranker()
    cached = CachedReranker(inner, cache, "fp1")

    await cached.rerank("q", ["doc1", "doc2"], 2)
    await cached.rerank("q", ["doc1", "doc3"], 2)

    assert inner.calls == 2


async def test_rerank_cache_misses_when_query_changes():
    cache = FakeCache()
    inner = CountingReranker()
    cached = CachedReranker(inner, cache, "fp1")

    await cached.rerank("q1", ["doc1"], 1)
    await cached.rerank("q2", ["doc1"], 1)

    assert inner.calls == 2


async def test_rerank_cache_misses_when_top_n_changes():
    cache = FakeCache()
    inner = CountingReranker()
    cached = CachedReranker(inner, cache, "fp1")

    await cached.rerank("q", ["d1", "d2", "d3"], 2)
    await cached.rerank("q", ["d1", "d2", "d3"], 3)

    assert inner.calls == 2


async def test_rerank_cache_empty_documents():
    cached = CachedReranker(CountingReranker(), FakeCache(), "fp1")
    assert await cached.rerank("q", [], 5) == []


async def test_rerank_cached_hits_are_rerankhit_objects():
    cached = CachedReranker(CountingReranker(), FakeCache(), "fp1")
    await cached.rerank("q", ["a", "b"], 2)

    hits = await cached.rerank("q", ["a", "b"], 2)

    assert all(isinstance(h, RerankHit) for h in hits)
    assert all(0 <= h.index < 2 for h in hits)


# ---------- 降级 ----------


async def test_null_cache_never_breaks_flow():
    inner = CountingEmbedding(dim=4)
    cached = CachedEmbedding(inner, NullCache(), "fp1")

    assert await cached.embed(["a"]) == await CachedEmbedding(
        FakeEmbedding(dim=4), NullCache(), "x"
    ).embed(["a"])
    assert inner.calls == 1


async def test_broken_redis_falls_back_to_direct_calls():
    """Redis 连不上时，缓存层必须透明降级，不能让问答整体失败。"""
    from codeatlas.retrieval.cache import RedisCache

    broken = RedisCache("redis://127.0.0.1:6390/0")  # 故意用错端口
    inner = CountingEmbedding(dim=4)
    cached = CachedEmbedding(inner, broken, "fp1")

    result = await cached.embed(["a", "b"])

    assert len(result) == 2
    assert inner.calls == 1
    await broken.close()
