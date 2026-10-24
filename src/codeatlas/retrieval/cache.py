"""查询级缓存（Redis）：embedding 向量与 rerank 结果。

延迟的构成（实测）：embed 230ms + dense 30ms + bm25 1ms + rerank 480ms。
其中 710ms 是两次跨公网调用。**索引再优化也压不掉这部分**，能压掉它的只有缓存。

缓存粒度的选择：
- **embedding 按文本哈希**：同一句话在任何查询里都复用，命中率最高；
- **rerank 按 query + 候选集的联合哈希**：候选变了就不能复用（否则会拿到别的候选的分数）；
- 答案级缓存不放这里，它属于业务层（受 prompt/config 指纹约束，放在 graph 之外更安全）。

失败策略：Redis 挂了就当没有缓存。缓存是加速手段，不该成为新的故障点。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import struct
from collections.abc import Sequence
from typing import Any, Protocol

VECTOR_TTL = 7 * 24 * 3600  # 向量只随模型/文本变化，长 TTL 安全
RERANK_TTL = 24 * 3600


class CacheBackend(Protocol):
    async def get(self, key: str) -> bytes | None: ...
    async def set(self, key: str, value: bytes, ttl: int) -> None: ...
    async def get_many(self, keys: list[str]) -> list[bytes | None]: ...
    async def set_many(self, items: dict[str, bytes], ttl: int) -> None: ...
    async def close(self) -> None: ...


class NullCache:
    """禁用缓存时的空实现，让调用方不必到处判空。"""

    async def get(self, key: str) -> bytes | None:
        return None

    async def set(self, key: str, value: bytes, ttl: int) -> None:
        return None

    async def get_many(self, keys: list[str]) -> list[bytes | None]:
        return [None] * len(keys)

    async def set_many(self, items: dict[str, bytes], ttl: int) -> None:
        return None

    async def close(self) -> None:
        return None


class RedisCache:
    def __init__(self, url: str, *, prefix: str = "ca") -> None:
        self.url = url
        self.prefix = prefix
        self._client: Any = None
        self._broken = False

    async def _conn(self):
        if self._broken:
            return None
        if self._client is None:
            try:
                from redis.asyncio import Redis

                # 不要传 encoding=None：redis-py 6.x 会抛
                # TypeError("encode() argument 'encoding' must be str, not None")，
                # 而且异常被下面的 except 吞掉后表现为"缓存永远不命中"，极难发现。
                self._client = Redis.from_url(self.url, decode_responses=False, socket_timeout=1.0)
                await self._client.ping()
            except Exception as exc:  # noqa: BLE001 - 连不上就永久降级
                self._broken = True
                self._client = None
                import sys

                print(
                    f"[cache] Redis 不可用，已降级为直连（{exc.__class__.__name__}: {exc}）",
                    file=sys.stderr,
                )
        return self._client

    async def get(self, key: str) -> bytes | None:
        client = await self._conn()
        if client is None:
            return None
        try:
            return await client.get(key)
        except Exception:  # noqa: BLE001
            return None

    async def set(self, key: str, value: bytes, ttl: int) -> None:
        client = await self._conn()
        if client is None:
            return
        try:
            await client.set(key, value, ex=ttl)
        except Exception:  # noqa: BLE001
            return

    async def get_many(self, keys: list[str]) -> list[bytes | None]:
        client = await self._conn()
        if client is None or not keys:
            return [None] * len(keys)
        try:
            values = await client.mget(keys)
            return list(values)
        except Exception:  # noqa: BLE001
            return [None] * len(keys)

    async def set_many(self, items: dict[str, bytes], ttl: int) -> None:
        client = await self._conn()
        if client is None or not items:
            return
        try:
            pipe = client.pipeline(transaction=False)
            for key, value in items.items():
                pipe.set(key, value, ex=ttl)
            await pipe.execute()
        except Exception:  # noqa: BLE001
            return

    async def close(self) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):
                await self._client.aclose()


def text_key(prefix: str, fingerprint: str, text: str) -> str:
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}:{fingerprint}:{digest}"


def pack_vector(vector: Sequence[float]) -> bytes:
    """float32 定长打包：1024 维 = 4KB，比 JSON（约 10KB）省一半以上。"""
    return struct.pack(f"{len(vector)}f", *vector)


def unpack_vector(data: bytes) -> list[float]:
    count = len(data) // 4
    return list(struct.unpack(f"{count}f", data))


class CachedEmbedding:
    """按文本哈希缓存向量。

    只缓存**查询**用的短文本；入库时 7143 条全量 embedding 不走这里
    （每条只算一次，缓存只会拖慢首轮）。
    """

    def __init__(self, inner, cache: CacheBackend, fingerprint: str) -> None:
        self.inner = inner
        self.cache = cache
        self.fingerprint = fingerprint
        self.model_id = getattr(inner, "model_id", "unknown")
        self.dim = getattr(inner, "dim", 0)
        self.hits = 0
        self.misses = 0

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        keys = [text_key("emb", self.fingerprint, text) for text in texts]
        cached = await self.cache.get_many(keys)

        missing = [i for i, blob in enumerate(cached) if blob is None]
        if not missing:
            self.hits += len(texts)
            return [unpack_vector(blob) for blob in cached]  # type: ignore[arg-type]

        self.misses += len(missing)
        fresh = await self.inner.embed([texts[i] for i in missing])

        updates: dict[str, bytes] = {}
        # cached 里是 bytes，必须逐条解包；直接塞进结果会让调用方拿到 bytes 当向量用
        out: list[list[float] | None] = [
            unpack_vector(blob) if blob is not None else None for blob in cached
        ]
        for slot, index in enumerate(missing):
            vector = fresh[slot]
            out[index] = vector
            updates[keys[index]] = pack_vector(vector)
        if updates:
            await self.cache.set_many(updates, VECTOR_TTL)
        return [v for v in out if v is not None]

    async def aclose(self) -> None:
        await self.inner.aclose()

    def stats(self) -> str:
        total = self.hits + self.misses
        ratio = self.hits / total * 100 if total else 0.0
        return f"embed cache hit={self.hits}/{total} ({ratio:.0f}%)"


class CachedReranker:
    """按 query + 候选集联合哈希缓存重排结果。

    候选集变了必须 miss：否则会把"上一次候选的第 3 条"的分数套到这一次的第 3 条上，
    这种错误完全静默，只会让排序莫名其妙。
    """

    def __init__(self, inner, cache: CacheBackend, fingerprint: str) -> None:
        self.inner = inner
        self.cache = cache
        self.fingerprint = fingerprint
        self.model_id = getattr(inner, "model_id", "unknown")
        self.hits = 0
        self.misses = 0

    def _key(self, query: str, documents: Sequence[str], top_n: int) -> str:
        blob = json.dumps(
            [query, list(documents), top_n], ensure_ascii=False, separators=(",", ":")
        )
        digest = hashlib.sha1(blob.encode("utf-8")).hexdigest()[:24]
        return f"rr:{self.fingerprint}:{digest}"

    async def rerank(self, query: str, documents: Sequence[str], top_n: int):
        if not documents:
            return []
        key = self._key(query, documents, top_n)
        blob = await self.cache.get(key)
        if blob is not None:
            self.hits += 1
            from codeatlas.providers.base import RerankHit

            payload = json.loads(blob)
            return [RerankHit(index=item[0], score=item[1]) for item in payload]

        self.misses += 1
        hits = await self.inner.rerank(query, documents, top_n)
        await self.cache.set(
            key,
            json.dumps([[h.index, h.score] for h in hits]).encode("utf-8"),
            RERANK_TTL,
        )
        return hits

    async def aclose(self) -> None:
        await self.inner.aclose()

    def stats(self) -> str:
        total = self.hits + self.misses
        ratio = self.hits / total * 100 if total else 0.0
        return f"rerank cache hit={self.hits}/{total} ({ratio:.0f}%)"
