"""集中配置：三槽位（LLM / embedding / rerank）各自独立，便于单独换供应商。"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# 版本号参与缓存指纹：切分策略 / 分词器 / prompt 任一变动都必须让缓存失效。
# chunker-v2 (2026-10-24)：修了三个会导致 chunk 超长或丢行的问题——
#   1. split_oversized 对 if-else 链失效（一个 1004 行的 if 分支切不动，
#      随后 embedding 因超过 8192 token 直接 400）；
#   2. 前导注释不计入切分预算（42 个 chunk 超过 max_chunk_lines）；
#   3. 100+ 行的文档注释整块挂载（chunk 行数失控）。
# 升级到 v2 后 chunk_id 全变，旧索引必须重建（跑 `codeatlas index --full`）。
CHUNKER_VERSION = "chunker-v2"
TOKENIZER_VERSION = "tokenizer-v1"
PROMPT_VERSION = "prompt-v1"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    provider_mode: Literal["live", "fake"] = "live"

    # --- 索引范围（由 config/default.yaml 覆盖，见 codeatlas/config.py） ---
    repo: str = "vendor/redis-7.2.5"
    include_globs: list[str] = ["src/*.c", "src/*.h"]
    exclude_globs: list[str] = ["deps/", "tests/", "modules/", "src/*.test.c"]
    chunker_version: str = CHUNKER_VERSION
    max_chunk_lines: int = 120
    max_split_depth: int = 2

    # --- 槽位 1：生成 ---
    llm_base_url: str = "https://api.siliconflow.cn/v1"
    llm_api_key: str = ""
    llm_model: str = "Qwen/Qwen3-8B"

    # --- 槽位 2：dense embedding ---
    emb_base_url: str = "https://api.siliconflow.cn/v1"
    emb_api_key: str = ""
    emb_model: str = "BAAI/bge-m3"
    emb_dim: int = 1024

    # --- 槽位 3：rerank ---
    rr_base_url: str = "https://api.siliconflow.cn/v1"
    rr_api_key: str = ""
    rr_model: str = "BAAI/bge-reranker-v2-m3"

    # --- 检索参数（参与指纹） ---
    dense_limit: int = 50
    bm25_limit: int = 50
    rrf_k: int = 60
    w_dense: float = 1.0
    w_bm25: float = 0.75
    # 30→24 与 8000→3000 是实测决定（scripts/rerank_tuning.py）：
    # 输入从 44K 字符降到 30K，p50 从 2714ms 降到 537ms，
    # 而 top-5 与 baseline 重合 94%、top-1 完全一致（10/10）。
    rerank_window: int = 24
    rerank_max_doc_chars: int = 3000
    # 正常 p50 500-800ms，8s 是 10 倍余量。原来 20s 会让"超时+重试"合计 22.7s，
    # 一次抖动就能把单次请求拖到 20 秒以上，比快速降级差得多。
    rerank_timeout: float = 8.0
    top_k: int = 5
    bm25_k1: float = 1.2
    bm25_b: float = 0.5
    bm25_symbol_boost: int = 5
    max_span_lines: int = 200
    max_repair_attempts: int = 2

    # --- 拒答阈值：实测 rerank 分数跨 4 个数量级，必须由 dev 集标定，禁止手填直觉值 ---
    # None = 尚未标定。P6 的拒答门在这里显式报错，而不是悄悄用一个"看起来合理"的数字。
    refuse_tau: float | None = None

    # --- 基础设施 ---
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "redis_src_v1"
    redis_url: str = "redis://localhost:6379/0"

    # --- 观测 ---
    langfuse_enabled: bool = False
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_base_url: str = "https://cloud.langfuse.com"

    def index_fingerprint(self) -> str:
        """只包含**决定索引内容**的参数。

        索引是不是还能用，取决于"chunk 边界"和"向量本身"，不取决于检索时怎么打分。
        早先把 `rerank_window` / `top_k` 也塞进来，导致改一个打分参数就被要求重建
        8 分钟的索引——那种校验很快会被当成噪音绕过，反而更危险。

        必须在内：
        - chunker 三个参数：切法变了，chunk_id 的语义就变了；
        - tokenizer：决定 BM25 词表；
        - bm25_symbol_boost：它参与 tf 计算，改了就得重建倒排表；
        - emb_model / emb_dim：换模型必须重嵌。
        """
        payload = {
            "chunker": self.chunker_version,
            "max_chunk_lines": self.max_chunk_lines,
            "max_split_depth": self.max_split_depth,
            "tokenizer": TOKENIZER_VERSION,
            "bm25_symbol_boost": self.bm25_symbol_boost,
            "emb": self.emb_model,
            "emb_dim": self.emb_dim,
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]

    def config_fingerprint(self) -> str:
        """检索链路全参数指纹，用于隔离缓存命名空间。

        包含 `index_fingerprint` 的全部内容（索引变了缓存必然失效），
        外加所有影响"同一 query 得到什么结果"的检索/生成参数。
        """
        payload = {
            "index": self.index_fingerprint(),
            "prompt": PROMPT_VERSION,
            "llm": self.llm_model,
            "rerank": self.rr_model,
            "dense_limit": self.dense_limit,
            "bm25_limit": self.bm25_limit,
            "rrf_k": self.rrf_k,
            "w_dense": self.w_dense,
            "w_bm25": self.w_bm25,
            "rerank_window": self.rerank_window,
            "rerank_max_doc_chars": self.rerank_max_doc_chars,
            "top_k": self.top_k,
            "bm25_k1": self.bm25_k1,
            "bm25_b": self.bm25_b,
            "refuse_tau": self.refuse_tau,
            "max_repair_attempts": self.max_repair_attempts,
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]
