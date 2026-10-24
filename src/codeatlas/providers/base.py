"""Provider 协议：上层只依赖这些接口，换供应商不改业务代码。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Protocol, runtime_checkable

from pydantic import BaseModel


class RerankHit(BaseModel):
    """index 是原始候选下标，不是排序后的位置——回填 chunk_id 必须用它。"""

    index: int
    score: float


@runtime_checkable
class EmbeddingProvider(Protocol):
    model_id: str
    dim: int

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def aclose(self) -> None: ...


@runtime_checkable
class Reranker(Protocol):
    model_id: str

    async def rerank(self, query: str, documents: Sequence[str], top_n: int) -> list[RerankHit]: ...

    async def aclose(self) -> None: ...


@runtime_checkable
class LLMProvider(Protocol):
    model_id: str

    async def stream(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.1,
        max_tokens: int = 1200,
    ) -> AsyncIterator[str]: ...

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.1,
        max_tokens: int = 1200,
    ) -> str: ...

    async def aclose(self) -> None: ...
