"""Rerank 接入：/rerank 不属于 OpenAI 协议，单独实现。

注意响应里的 index 是**原始候选下标**，不是排序位置；上层必须按下标回填 chunk_id，
否则重排后就对错了源。
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

import httpx

from codeatlas.providers.base import RerankHit
from codeatlas.providers.http import ProviderError, post_json


class SiliconFlowReranker:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model_id: str,
        timeout: float = 20.0,
        max_concurrency: int = 4,
        max_doc_chars: int = 8000,
    ) -> None:
        self.model_id = model_id
        self._max_doc_chars = max_doc_chars
        self._sem = asyncio.Semaphore(max_concurrency)
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            timeout=httpx.Timeout(timeout, connect=10.0),
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
        )

    async def rerank(self, query: str, documents: Sequence[str], top_n: int) -> list[RerankHit]:
        if not documents:
            return []
        clipped = [doc[: self._max_doc_chars] for doc in documents]
        async with self._sem:
            body = await post_json(
                self._client,
                "rerank",
                {
                    "model": self.model_id,
                    "query": query,
                    "documents": clipped,
                    "top_n": min(top_n, len(clipped)),
                    "return_documents": False,
                },
                slot="rerank",
            )
        results = body.get("results") or []
        hits = [
            RerankHit(index=int(item["index"]), score=float(item["relevance_score"]))
            for item in results
        ]
        if not hits:
            raise ProviderError("rerank", "empty_results", "rerank returned no results")
        return sorted(hits, key=lambda h: h.score, reverse=True)

    async def aclose(self) -> None:
        await self._client.aclose()
