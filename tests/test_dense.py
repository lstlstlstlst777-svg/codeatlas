"""Dense 索引：用本地嵌入式模式跑，不依赖 Qdrant 服务端。

覆盖三件在真实运行里会咬人的事：
1. 换 embedding 模型后维度不符 —— 必须报错，不能静默写坏数据；
2. 按文件删除 —— 增量更新的正确性完全依赖它；
3. payload 里不存 code —— 定位信息必须能还原 path:start-end。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codeatlas.indexing.types import Chunk, ChunkKind
from codeatlas.retrieval.dense import DenseIndex, DenseIndexError, point_id


def make_chunk(
    chunk_id: str,
    path: str = "src/a.c",
    sha: str = "sha1",
    *,
    kind: ChunkKind = ChunkKind.FUNCTION,
    symbol: str = "f",
    start: int = 1,
    end: int = 3,
) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        path=path,
        start_line=start,
        end_line=end,
        body_start_line=start,
        node_type=kind,
        symbol=symbol,
        code="int f(void) { return 0; }",
        context_header="// header",
        file_sha=sha,
    )


def make_vector(seed: float) -> list[float]:
    return [seed, seed + 0.1, seed + 0.2, seed + 0.3]


@pytest.fixture
def index(tmp_path: Path):
    idx = DenseIndex.open(str(tmp_path / "qdrant"), "test_collection", dim=4)
    idx.ensure_collection()
    yield idx
    idx.close()


def test_point_id_is_uint64():
    """Qdrant 只接受 uint64 或 UUID；chunk_id 是 16 位 hex，正好 64 bit。"""
    value = point_id("4d74bea80a5b2044")
    assert 0 <= value < 2**64
    assert value == int("4d74bea80a5b2044", 16)


def test_ensure_collection_is_idempotent(index: DenseIndex):
    assert index.exists()
    assert index.ensure_collection() is False  # 已存在，不重建


def test_upsert_and_count(index: DenseIndex):
    chunks = [make_chunk("a" * 16), make_chunk("b" * 16)]
    added = index.upsert(chunks, [make_vector(0.1), make_vector(0.9)])

    assert added == 2
    assert index.count() == 2


def test_upsert_is_idempotent_by_chunk_id(index: DenseIndex):
    chunk = make_chunk("a" * 16)
    index.upsert([chunk], [make_vector(0.1)])
    index.upsert([chunk], [make_vector(0.2)])

    assert index.count() == 1


def test_vector_count_mismatch_is_fatal(index: DenseIndex):
    with pytest.raises(DenseIndexError, match="数量不一致"):
        index.upsert([make_chunk("a" * 16)], [])


def test_dimension_mismatch_on_reopen(tmp_path: Path):
    idx = DenseIndex.open(str(tmp_path / "q"), "c", dim=4)
    idx.ensure_collection()
    idx.close()

    other = DenseIndex.open(str(tmp_path / "q"), "c", dim=8)
    with pytest.raises(DenseIndexError, match="维度"):
        other.ensure_collection()
    other.close()


def test_delete_by_path(index: DenseIndex):
    index.upsert(
        [make_chunk("a" * 16, "src/a.c"), make_chunk("b" * 16, "src/a.c")],
        [make_vector(0.1), make_vector(0.2)],
    )
    index.upsert([make_chunk("c" * 16, "src/b.c")], [make_vector(0.3)])

    index.delete_paths(["src/a.c"])

    assert index.count() == 1
    states = index.file_states()
    assert set(states) == {"src/b.c"}


def test_file_states_aggregates_points_per_path(index: DenseIndex):
    index.upsert(
        [
            make_chunk("a" * 16, "src/a.c", "shaA"),
            make_chunk("b" * 16, "src/a.c", "shaA"),
            make_chunk("c" * 16, "src/b.c", "shaB"),
        ],
        [make_vector(0.1), make_vector(0.2), make_vector(0.3)],
    )

    states = index.file_states()

    assert states["src/a.c"].points == 2
    assert states["src/a.c"].file_sha == "shaA"
    assert states["src/b.c"].points == 1


def test_search_returns_ranked_chunk_ids(index: DenseIndex):
    index.upsert(
        [make_chunk("a" * 16), make_chunk("b" * 16)],
        [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]],
    )

    hits = index.search([1.0, 0.0, 0.0, 0.0], limit=2)

    assert hits[0].chunk_id == "a" * 16
    assert hits[0].rank == 1
    assert hits[0].score > hits[1].score


def test_search_with_payload_has_location(index: DenseIndex):
    index.upsert([make_chunk("a" * 16, symbol="initServer", start=10, end=20)], [make_vector(0.5)])

    payload = index.search_with_payload(make_vector(0.5), limit=1)[0]

    assert payload["path"] == "src/a.c"
    assert payload["start_line"] == 10
    assert payload["end_line"] == 20
    assert payload["symbol"] == "initServer"
    assert "code" not in payload, "payload 不该存 code，文本一律回 chunk store 查"


def test_macro_kind_lands_in_payload(index: DenseIndex):
    chunk = make_chunk("a" * 16, kind=ChunkKind.MACRO, symbol="C_OK")
    chunk.fields = {"macro_kind": "macro_const"}

    index.upsert([chunk], [make_vector(0.1)])
    payload = index.search_with_payload(make_vector(0.1), limit=1)[0]

    assert payload["macro_kind"] == "macro_const"
    assert payload["node_type"] == "macro"


def test_empty_search_and_upsert_are_safe(index: DenseIndex):
    assert index.search(make_vector(0.1), limit=0) == []
    assert index.upsert([], []) == 0
    assert index.delete_paths([]) == 0


def test_persistence_across_reopen(tmp_path: Path):
    """本地模式的数据要落盘：关掉再开，点还在。"""
    target = str(tmp_path / "q")
    idx = DenseIndex.open(target, "c", dim=4)
    idx.ensure_collection()
    idx.upsert([make_chunk("a" * 16)], [make_vector(0.4)])
    idx.close()

    reopened = DenseIndex.open(target, "c", dim=4)
    assert reopened.count() == 1
    reopened.close()
