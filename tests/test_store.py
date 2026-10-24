"""chunk 落盘：round-trip 必须逐字节还原 code，否则引用校验会拿错行。"""

from __future__ import annotations

from pathlib import Path

import pytest

from codeatlas.indexing.store import (
    iter_chunks,
    load_chunk_map,
    load_chunks,
    save_chunks,
    store_stats,
)
from codeatlas.indexing.types import Chunk, ChunkKind


def make_chunk(chunk_id: str, code: str, **overrides) -> Chunk:
    payload = {
        "chunk_id": chunk_id,
        "path": "src/server.c",
        "start_line": 10,
        "end_line": 12,
        "body_start_line": 10,
        "node_type": ChunkKind.FUNCTION,
        "symbol": "initServer",
        "signature": "void initServer(void)",
        "code": code,
        "context_header": "// src/server.c | function initServer | L10-12",
        "file_sha": "abc123",
    }
    payload.update(overrides)
    return Chunk(**payload)


def test_roundtrip_preserves_code_exactly(tmp_path: Path):
    code = 'void f(void) {\n    printf("中文 \\n 引号");\n}'
    path = save_chunks([make_chunk("c1", code)], tmp_path / "chunks.jsonl.gz")

    restored = load_chunks(path)

    assert len(restored) == 1
    assert restored[0].code == code
    assert restored[0].symbol == "initServer"
    assert restored[0].node_type is ChunkKind.FUNCTION


def test_roundtrip_keeps_order_and_ids(tmp_path: Path):
    chunks = [make_chunk(f"c{i}", f"int v{i};") for i in range(50)]
    path = save_chunks(chunks, tmp_path / "chunks.jsonl.gz")

    restored = load_chunks(path)

    assert [c.chunk_id for c in restored] == [f"c{i}" for i in range(50)]


def test_chunk_map_indexes_by_id(tmp_path: Path):
    path = save_chunks(
        [make_chunk("a", "int a;"), make_chunk("b", "int b;")], tmp_path / "c.jsonl.gz"
    )

    mapping = load_chunk_map(path)

    assert set(mapping) == {"a", "b"}
    assert mapping["b"].code == "int b;"


def test_iter_chunks_is_lazy(tmp_path: Path):
    path = save_chunks([make_chunk(f"c{i}", "x" * 10) for i in range(100)], tmp_path / "c.jsonl.gz")

    first = next(iter_chunks(path))

    assert first.chunk_id == "c0"


def test_refuses_empty_store(tmp_path: Path):
    with pytest.raises(ValueError, match="empty"):
        save_chunks([], tmp_path / "c.jsonl.gz")


def test_store_stats_counts_by_kind(tmp_path: Path):
    path = save_chunks(
        [
            make_chunk("a", "int a;"),
            make_chunk("b", "#define X 1", node_type=ChunkKind.MACRO),
            make_chunk("c", "#define Y 2", node_type=ChunkKind.MACRO),
        ],
        tmp_path / "c.jsonl.gz",
    )

    stats = store_stats(path)

    assert stats["chunks"] == 3
    assert stats["macro"] == 2
    assert stats["function"] == 1


def test_gzip_is_actually_smaller(tmp_path: Path):
    """接近真实的重复代码，压缩后应显著小于原文。"""
    chunks = [make_chunk(f"c{i}", "int compute(void) { return 42; }" * 20) for i in range(200)]
    path = save_chunks(chunks, tmp_path / "c.jsonl.gz")

    raw_size = sum(len(c.model_dump_json()) for c in chunks)

    assert path.stat().st_size < raw_size / 3
