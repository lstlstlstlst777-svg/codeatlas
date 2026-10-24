"""按配置组装三槽位；fake 模式下整条链路离线可跑。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from codeatlas.providers.base import EmbeddingProvider, LLMProvider, Reranker
from codeatlas.providers.fake import FakeEmbedding, FakeLLM, FakeReranker
from codeatlas.providers.openai_compat import OpenAICompatibleEmbedding, OpenAICompatibleLLM
from codeatlas.providers.siliconflow_rerank import SiliconFlowReranker
from codeatlas.retrieval.cache import CachedEmbedding, CachedReranker, RedisCache
from codeatlas.settings import Settings


@dataclass(slots=True)
class Deps:
    settings: Settings
    llm: LLMProvider
    embed: EmbeddingProvider
    rerank: Reranker
    cache: Any = None

    async def aclose(self) -> None:
        for provider in (self.llm, self.embed, self.rerank):
            await provider.aclose()
        if self.cache is not None:
            await self.cache.close()


def build_deps(settings: Settings | None = None, *, use_cache: bool = False) -> Deps:
    settings = settings or Settings()

    if settings.provider_mode == "fake":
        return Deps(
            settings=settings,
            llm=FakeLLM(),
            embed=FakeEmbedding(dim=settings.emb_dim),
            rerank=FakeReranker(),
        )

    embed: EmbeddingProvider = OpenAICompatibleEmbedding(
        base_url=settings.emb_base_url,
        api_key=settings.emb_api_key,
        model_id=settings.emb_model,
        dim=settings.emb_dim,
    )
    rerank: Reranker = SiliconFlowReranker(
        base_url=settings.rr_base_url,
        api_key=settings.rr_api_key,
        model_id=settings.rr_model,
        timeout=settings.rerank_timeout,
        max_doc_chars=settings.rerank_max_doc_chars,
    )

    cache = None
    if use_cache:
        # 缓存按 config 指纹分命名空间：改了参数就自动换一批 key，不会读到旧结果
        cache = RedisCache(settings.redis_url)
        fingerprint = settings.config_fingerprint()
        embed = CachedEmbedding(embed, cache, fingerprint)
        rerank = CachedReranker(rerank, cache, fingerprint)

    return Deps(
        settings=settings,
        llm=OpenAICompatibleLLM(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model_id=settings.llm_model,
        ),
        embed=embed,
        rerank=rerank,
        cache=cache,
    )
