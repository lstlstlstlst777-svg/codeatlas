"""OpenAI 兼容接入：chat/completions 与 embeddings。

硅基流动、DeepSeek、DashScope 兼容模式都走这套协议，所以这里只依赖 base_url + model，
换供应商只需改 .env 三行。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence

import httpx

from codeatlas.providers.http import ProviderError, post_json


class OpenAICompatibleEmbedding:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model_id: str,
        dim: int,
        timeout: float = 30.0,
        max_concurrency: int = 8,
        max_batch: int = 32,
    ) -> None:
        self.model_id = model_id
        self.dim = dim
        self._max_batch = max_batch
        self._sem = asyncio.Semaphore(max_concurrency)
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            timeout=httpx.Timeout(timeout, connect=10.0),
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
        )

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._max_batch):
            batch = list(texts[start : start + self._max_batch])
            async with self._sem:
                body = await post_json(
                    self._client,
                    "embeddings",
                    {"model": self.model_id, "input": batch, "encoding_format": "float"},
                    slot="embedding",
                )
            data = body.get("data") or []
            if len(data) != len(batch):
                raise ProviderError(
                    "embedding", "bad_count", f"expected {len(batch)} vectors, got {len(data)}"
                )
            for item in sorted(data, key=lambda d: d["index"]):
                vector = item["embedding"]
                if len(vector) != self.dim:
                    raise ProviderError(
                        "embedding",
                        "dim_mismatch",
                        f"model returned dim={len(vector)}, config expects {self.dim}",
                    )
                vectors.append(vector)
        return vectors

    async def aclose(self) -> None:
        await self._client.aclose()


class OpenAICompatibleLLM:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model_id: str,
        timeout: float = 90.0,
        max_concurrency: int = 4,
    ) -> None:
        self.model_id = model_id
        self._sem = asyncio.Semaphore(max_concurrency)
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            timeout=httpx.Timeout(timeout, connect=10.0),
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
        )

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.1,
        max_tokens: int = 1200,
    ) -> str:
        async with self._sem:
            body = await post_json(
                self._client,
                "chat/completions",
                {
                    "model": self.model_id,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "stream": False,
                },
                slot="llm",
            )
        choices = body.get("choices") or []
        if not choices:
            raise ProviderError("llm", "empty_choices", json.dumps(body)[:300])
        return choices[0]["message"].get("content") or ""

    async def stream(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.1,
        max_tokens: int = 1200,
    ) -> AsyncIterator[str]:
        payload = {
            "model": self.model_id,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        async with (
            self._sem,
            self._client.stream("POST", "chat/completions", json=payload) as response,
        ):
            if response.status_code >= 400:
                raw = (await response.aread()).decode("utf-8", "replace")
                code: object = response.status_code
                message = raw[:300]
                try:
                    body = json.loads(raw)
                except ValueError:
                    body = None
                if isinstance(body, dict):
                    code = body.get("code", code)
                    message = str(body.get("message", message))
                raise ProviderError("llm", code, message, response.status_code)

            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                chunk = line[len("data:") :].strip()
                if not chunk or chunk == "[DONE]":
                    if chunk == "[DONE]":
                        break
                    continue
                try:
                    event = json.loads(chunk)
                except ValueError:
                    continue
                for choice in event.get("choices") or []:
                    delta = choice.get("delta") or {}
                    text = delta.get("content")
                    if text:
                        yield text

    async def aclose(self) -> None:
        await self._client.aclose()
