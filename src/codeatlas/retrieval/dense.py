"""Dense 索引：Qdrant 封装 + payload 契约。

两种运行模式（同一份代码，靠 `qdrant_url` 区分）：
- `http://...` → 连服务端，多进程可共享，P7/P8 用；
- 其他值（如 `data/qdrant`）→ 本地嵌入式，单进程独占，拉不到镜像时的降级路径。

payload 里**不存 code**。理由：
- code 平均 750 字符，7019 条就是 5MB，存两份没有意义；
- 更重要是避免"两处副本不一致"——chunk store 是代码文本的唯一真源，
  payload 只放定位信息，取文本一律回查 store（哪次查不到就是 manifest 没校验好）。
"""

from __future__ import annotations

import warnings
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient, models

from codeatlas.indexing.types import Chunk
from codeatlas.retrieval.bm25 import ScoredDoc

# payload 字段：定位所需的最小集合
PAYLOAD_FIELDS = (
    "chunk_id",
    "path",
    "start_line",
    "end_line",
    "body_start_line",
    "node_type",
    "symbol",
    "parent_symbol",
    "file_sha",
    "macro_kind",
)


class DenseIndexError(RuntimeError):
    """collection 不存在、维度不匹配、模式不支持。"""


@dataclass(frozen=True, slots=True)
class FileState:
    """一个文件在索引里的现状，用于增量比对。"""

    path: str
    file_sha: str
    points: int


def open_client(target: str) -> QdrantClient:
    """按 target 形态选模式。`http(s)://` 走服务端，其余当本地路径。"""
    if target.startswith(("http://", "https://")):
        return QdrantClient(url=target, timeout=30)
    if target == ":memory:":
        return QdrantClient(location=":memory:")
    Path(target).mkdir(parents=True, exist_ok=True)
    with warnings.catch_warnings():
        # 本地模式在客户端内部会发 DeprecationWarning，这里明确接受它作为离线降级
        warnings.simplefilter("ignore", DeprecationWarning)
        return QdrantClient(path=target)


def point_id(chunk_id: str) -> int:
    """chunk_id 是 16 位 hex（sha1 前 16 位），正好落在 uint64 里。"""
    return int(chunk_id, 16)


class DenseIndex:
    def __init__(
        self,
        client: QdrantClient,
        collection: str,
        dim: int,
        *,
        owns_client: bool = False,
        local_mode: bool = False,
    ) -> None:
        self.client = client
        self.collection = collection
        self.dim = dim
        self._owns_client = owns_client
        # 本地嵌入式模式不支持 payload 索引（会发 UserWarning），建了也没用
        self.local_mode = local_mode

    @classmethod
    def open(cls, target: str, collection: str, dim: int) -> DenseIndex:
        is_local = not target.startswith(("http://", "https://"))
        return cls(
            open_client(target),
            collection,
            dim,
            owns_client=True,
            local_mode=is_local,
        )

    # ---------- schema ----------

    def exists(self) -> bool:
        return self.client.collection_exists(self.collection)

    def ensure_collection(self, *, recreate: bool = False) -> bool:
        """返回 True 表示新建了 collection。维度不一致必须报错，不能静默用错的。"""
        if self.exists():
            if recreate:
                self.client.delete_collection(self.collection)
            else:
                self._assert_compatible()
                return False

        self.client.create_collection(
            collection_name=self.collection,
            vectors_config=models.VectorParams(size=self.dim, distance=models.Distance.COSINE),
        )
        # path 上建索引：增量比对要按 path 过滤，没有索引会全表扫
        if not self.local_mode:
            self.client.create_payload_index(
                collection_name=self.collection,
                field_name="path",
                field_schema=models.PayloadSchemaType.KEYWORD,
            )
        return True

    def _assert_compatible(self) -> None:
        info = self.client.get_collection(self.collection)
        vectors = info.config.params.vectors
        if isinstance(vectors, dict):
            raise DenseIndexError(f"{self.collection}: 意外的命名向量配置 {list(vectors)}")
        if vectors.size != self.dim:
            raise DenseIndexError(
                f"{self.collection} 现有维度 {vectors.size} 与配置 {self.dim} 不一致。"
                "换 embedding 模型必须重建 collection（--recreate）。"
            )
        if vectors.distance != models.Distance.COSINE:
            raise DenseIndexError(f"{self.collection} 距离函数是 {vectors.distance}，期望 COSINE")

    # ---------- 写入 ----------

    @staticmethod
    def _payload(chunk: Chunk) -> dict[str, Any]:
        return {
            "chunk_id": chunk.chunk_id,
            "path": chunk.path,
            "start_line": chunk.start_line,
            "end_line": chunk.end_line,
            "body_start_line": chunk.body_start_line,
            "node_type": chunk.node_type.value,
            "symbol": chunk.symbol,
            "parent_symbol": chunk.parent_symbol,
            "file_sha": chunk.file_sha,
            "macro_kind": chunk.fields.get("macro_kind"),
        }

    def upsert(self, chunks: Sequence[Chunk], vectors: Sequence[Sequence[float]]) -> int:
        if len(chunks) != len(vectors):
            raise DenseIndexError(f"chunks={len(chunks)} 与 vectors={len(vectors)} 数量不一致")
        if not chunks:
            return 0

        points = [
            models.PointStruct(
                id=point_id(chunk.chunk_id),
                vector=list(vector),
                payload=self._payload(chunk),
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
        self.client.upsert(collection_name=self.collection, points=points, wait=True)
        return len(points)

    def delete_paths(self, paths: Iterable[str]) -> int:
        """按文件删点：增量更新时先清掉旧版本，再插入新版本。"""
        targets = [p for p in paths if p]
        if not targets:
            return 0
        self.client.delete(
            collection_name=self.collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="path",
                            match=models.MatchAny(any=targets),
                        )
                    ]
                )
            ),
            wait=True,
        )
        return len(targets)

    # ---------- 查询 ----------

    def search(self, vector: Sequence[float], limit: int) -> list[ScoredDoc]:
        if limit <= 0:
            return []
        response = self.client.query_points(
            collection_name=self.collection,
            query=list(vector),
            limit=limit,
            with_payload=True,
        )
        return [
            ScoredDoc(
                chunk_id=str((point.payload or {}).get("chunk_id", "")),
                score=float(point.score),
                rank=rank,
            )
            for rank, point in enumerate(response.points, start=1)
        ]

    def search_with_payload(self, vector: Sequence[float], limit: int) -> list[dict[str, Any]]:
        """检索并带回 payload，供 P5 融合和 P6 引用校验直接用。"""
        if limit <= 0:
            return []
        response = self.client.query_points(
            collection_name=self.collection,
            query=list(vector),
            limit=limit,
            with_payload=True,
        )
        results = []
        for rank, point in enumerate(response.points, start=1):
            payload = dict(point.payload or {})
            payload["score"] = float(point.score)
            payload["rank"] = rank
            results.append(payload)
        return results

    # ---------- 统计与增量 ----------

    def count(self) -> int:
        return int(self.client.count(self.collection, exact=True).count)

    def file_states(self) -> dict[str, FileState]:
        """扫出索引里每个文件的现状（path → sha + 点数），用于增量比对。

        7019 个点的 payload 全量 scroll 大约 0.2s，比"按文件逐个 count"快得多。
        """
        states: dict[str, FileState] = {}
        offset: Any = None
        while True:
            records, offset = self.client.scroll(
                collection_name=self.collection,
                limit=1024,
                offset=offset,
                with_payload=["path", "file_sha"],
                with_vectors=False,
            )
            for record in records:
                payload = record.payload or {}
                path = str(payload.get("path", ""))
                sha = str(payload.get("file_sha", ""))
                if not path:
                    continue
                prev = states.get(path)
                states[path] = FileState(
                    path=path,
                    file_sha=sha,
                    points=(prev.points if prev else 0) + 1,
                )
            if offset is None:
                break
        return states

    def close(self) -> None:
        if self._owns_client:
            self.client.close()
