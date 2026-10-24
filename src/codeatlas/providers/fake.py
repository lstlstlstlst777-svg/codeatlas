"""确定性假实现：CI 与离线单测不依赖外网、不花钱、结果可复现。"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import AsyncIterator, Sequence

from codeatlas.providers.base import RerankHit

_TOKEN_RE = re.compile(r"[a-z0-9_]+")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def deterministic_vector(text: str, dim: int) -> list[float]:
    """把文本哈希铺开成 dim 维单位向量：同输入永远同输出。"""
    raw = bytearray()
    counter = 0
    while len(raw) < dim:
        raw.extend(hashlib.sha256(f"{counter}:{text}".encode()).digest())
        counter += 1
    values = [(byte / 127.5) - 1.0 for byte in raw[:dim]]
    norm = math.sqrt(sum(v * v for v in values)) or 1.0
    return [v / norm for v in values]


class FakeEmbedding:
    def __init__(self, dim: int = 1024) -> None:
        self.model_id = "fake/embedding"
        self.dim = dim

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [deterministic_vector(text, self.dim) for text in texts]

    async def aclose(self) -> None:
        return None


class FakeReranker:
    def __init__(self) -> None:
        self.model_id = "fake/reranker"

    async def rerank(self, query: str, documents: Sequence[str], top_n: int) -> list[RerankHit]:
        query_tokens = _tokens(query)
        hits: list[RerankHit] = []
        for index, doc in enumerate(documents):
            doc_tokens = _tokens(doc)
            overlap = len(query_tokens & doc_tokens)
            score = overlap / (len(query_tokens) or 1)
            hits.append(RerankHit(index=index, score=score))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:top_n]

    async def aclose(self) -> None:
        return None


class FakeLLM:
    """按脚本顺序返回；耗尽后回落固定文本，保证测试不受时序影响。"""

    def __init__(self, responses: list[str] | None = None) -> None:
        self.model_id = "fake/llm"
        self._responses = list(responses or ["OK"])
        self._cursor = 0

    def _next(self) -> str:
        if self._cursor < len(self._responses):
            text = self._responses[self._cursor]
            self._cursor += 1
            return text
        return self._responses[-1] if self._responses else "OK"

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.1,
        max_tokens: int = 1200,
    ) -> str:
        return self._next()

    async def stream(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.1,
        max_tokens: int = 1200,
    ) -> AsyncIterator[str]:
        text = self._next()
        for piece in text.split(" "):
            yield piece + " "

    async def aclose(self) -> None:
        return None
