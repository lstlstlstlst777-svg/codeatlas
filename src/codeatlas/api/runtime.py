"""API 运行时：把索引、provider、图装配成一份可复用的单例。

为什么集中在一个 Runtime 而不是每个请求现建：
- 加载 chunk store 要 1.7MB 解压 + 7143 个对象，BM25 索引 0.95MB，每请求重建等于自杀；
- Qdrant 客户端和 HTTP 连接池要复用，否则每次问答都重新 TLS 握手；
- SQLite checkpointer 是**单连接**，必须全局唯一（多连接写同一个库会锁冲突）。

生命周期由 FastAPI 的 lifespan 管，进程退出时一并关闭。
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiosqlite

from codeatlas.config import resolve_repo
from codeatlas.graph.build import build_graph
from codeatlas.graph.nodes import GraphDeps
from codeatlas.indexing.store import load_chunks
from codeatlas.providers.registry import Deps, build_deps
from codeatlas.retrieval.bm25 import BM25Index
from codeatlas.retrieval.dense import DenseIndex
from codeatlas.retrieval.service import HybridRetriever
from codeatlas.settings import Settings

DEFAULT_CHECKPOINT_DB = "data/checkpoints.sqlite"


def project_root() -> Path:
    """定位项目根。

    **不能用 `Path(__file__).parents[N]`**：宿主上是 `src/codeatlas/api/`，
    容器里却是 `site-packages/codeatlas/api/`——同一个 N 指到两个地方，
    表现是容器启动即 `FileNotFoundError: /usr/local/lib/python3.11/data/chunks.jsonl.gz`。
    统一用 cwd（容器 WORKDIR=/app），需要时用 CODEATLAS_ROOT 覆盖，
    与 `load_config()` / `resolve_repo()` 的口径保持一致。
    """
    return Path(os.environ.get("CODEATLAS_ROOT") or Path.cwd())


def checkpoint_path(base: Path, override: str | None = None) -> Path:
    """checkpointer 的落盘位置。

    **不能放在 Windows 的 bind mount 上**：SQLite 的 WAL 模式依赖共享内存（-shm），
    而 Docker Desktop 把 Windows 目录挂进容器后不支持这套语义，直接 `disk I/O error`。
    容器里用命名卷（Linux 文件系统），宿主本地跑仍用 data/ 下的默认路径。
    """
    raw = override or os.environ.get("CODEATLAS_CHECKPOINT_DB") or DEFAULT_CHECKPOINT_DB
    path = Path(raw)
    return path if path.is_absolute() else base / path


@dataclass
class Runtime:
    settings: Settings
    chunks: dict[str, Any]
    bm25: BM25Index
    dense: DenseIndex
    deps: Deps
    retriever: HybridRetriever
    graph: Any
    graph_deps: GraphDeps
    saver: Any
    repo_root: Path
    _conn: Any = None

    @classmethod
    async def create(
        cls,
        settings: Settings,
        *,
        root: Path | None = None,
        use_cache: bool = True,
        checkpoint_db: str | None = None,
    ) -> Runtime:
        base = Path(root) if root else project_root()
        repo_root = resolve_repo(settings)

        chunks = await asyncio.to_thread(load_chunks, base / "data" / "chunks.jsonl.gz")
        chunk_map = {c.chunk_id: c for c in chunks}
        bm25 = await asyncio.to_thread(BM25Index.load, base / "data" / "bm25.json.gz")
        dense = DenseIndex.open(settings.qdrant_url, settings.qdrant_collection, settings.emb_dim)
        deps = build_deps(settings, use_cache=use_cache)

        retriever = HybridRetriever(
            settings,
            dense=dense,
            bm25=bm25,
            chunks=chunk_map,
            embedder=deps.embed,
            reranker=deps.rerank,
        )

        db_path = checkpoint_path(base, checkpoint_db)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(str(db_path))
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        saver = AsyncSqliteSaver(conn)
        await saver.setup()

        graph_deps = GraphDeps(
            settings=settings,
            retriever=retriever,
            llm=deps.llm,
            chunks=chunk_map,
            repo_root=repo_root,
            allow_uncalibrated_gate=False,
        )
        graph = build_graph(graph_deps, checkpointer=saver)

        return cls(
            settings=settings,
            chunks=chunk_map,
            bm25=bm25,
            dense=dense,
            deps=deps,
            retriever=retriever,
            graph=graph,
            graph_deps=graph_deps,
            saver=saver,
            repo_root=repo_root,
            _conn=conn,
        )

    def with_streaming(self, enabled: bool):
        """按需切换"逐 token 生成"。`stream_tokens` 是编译时的字段，只能重建图。

        重建的是编译产物，底层资源（chunks / qdrant / provider / saver）全部共用，
        开销只有 `compile()` 本身（微秒级），不必担心。
        """
        if self.graph_deps.stream_tokens == enabled:
            return self.graph
        self.graph_deps.stream_tokens = enabled
        self.graph = build_graph(self.graph_deps, checkpointer=self.saver)
        return self.graph

    async def aclose(self) -> None:
        await self.deps.aclose()
        self.dense.close()
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    def stats(self) -> dict[str, object]:
        return {
            "chunks": len(self.chunks),
            "bm25_docs": self.bm25.num_docs,
            "dense_points": self.dense.count(),
            "index_fingerprint": self.settings.index_fingerprint(),
            "llm": self.settings.llm_model,
            "refuse_tau": self.settings.refuse_tau,
        }
