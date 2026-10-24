"""索引 manifest：两份产物必须同源，否则行号会错位（ISSUE-017）。

这些测试的核心不是"能存能读"，而是**错位必须报错**——
之前的实现只打 WARN 继续跑，返回的行号来自旧切分，引用校验会莫名其妙失败。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codeatlas.indexing.manifest import (
    MANIFEST_SUFFIX,
    IndexManifest,
    IndexMismatchError,
    verify_pair,
)


def make_manifest(
    path: Path,
    *,
    kind: str = "chunk_store",
    fingerprint: str = "fp1",
    entries: int = 100,
    created_at: str = "2026-10-23T00:00:00+08:00",
) -> Path:
    return IndexManifest.build(
        kind=kind,
        fingerprint=fingerprint,
        entries=entries,
        repo="E:/CodeAtlas/vendor/redis-7.2.5",
        chunker_version="chunker-v1",
        created_at=created_at,
    ).save(path)


def test_manifest_roundtrip(tmp_path: Path):
    artifact = tmp_path / "chunks.jsonl.gz"
    artifact.write_bytes(b"")

    make_manifest(artifact, kind="chunk_store", entries=42)
    meta = IndexManifest.load(artifact)

    assert meta.kind == "chunk_store"
    assert meta.entries == 42
    assert meta.chunker_version == "chunker-v1"
    assert meta.version == 1


def test_manifest_file_name_is_derived(tmp_path: Path):
    artifact = tmp_path / "bm25.json.gz"
    artifact.write_bytes(b"")

    written = make_manifest(artifact, kind="bm25_index")

    assert written.name == f"bm25.json.gz{MANIFEST_SUFFIX}"


def test_missing_manifest_is_fatal(tmp_path: Path):
    artifact = tmp_path / "chunks.jsonl.gz"
    artifact.write_bytes(b"")

    with pytest.raises(IndexMismatchError, match="缺少 manifest"):
        IndexManifest.load(artifact)


def test_pair_ok(tmp_path: Path):
    chunks = tmp_path / "chunks.jsonl.gz"
    index = tmp_path / "bm25.json.gz"
    for p in (chunks, index):
        p.write_bytes(b"")
    make_manifest(chunks, kind="chunk_store", entries=7019)
    make_manifest(index, kind="bm25_index", entries=7019)

    chunk_meta, index_meta = verify_pair(chunks, index)

    assert chunk_meta.entries == index_meta.entries == 7019


def test_entry_count_mismatch_is_fatal(tmp_path: Path):
    """只重建了 chunk store（改了切分）但索引还是旧的。"""
    chunks = tmp_path / "chunks.jsonl.gz"
    index = tmp_path / "bm25.json.gz"
    for p in (chunks, index):
        p.write_bytes(b"")
    make_manifest(chunks, kind="chunk_store", entries=7100, created_at="later")
    make_manifest(index, kind="bm25_index", entries=7019, created_at="earlier")

    with pytest.raises(IndexMismatchError, match="条目数不一致"):
        verify_pair(chunks, index)


def test_fingerprint_mismatch_is_fatal(tmp_path: Path):
    """改了权重/切分参数，只重建了其中一个产物。"""
    chunks = tmp_path / "chunks.jsonl.gz"
    index = tmp_path / "bm25.json.gz"
    for p in (chunks, index):
        p.write_bytes(b"")
    make_manifest(chunks, kind="chunk_store", fingerprint="new")
    make_manifest(index, kind="bm25_index", fingerprint="old")

    with pytest.raises(IndexMismatchError, match="配置指纹不一致"):
        verify_pair(chunks, index)


def test_stale_index_vs_current_config_is_fatal(tmp_path: Path):
    """索引是昨天的配置建的，今天改了 config 没重建。"""
    chunks = tmp_path / "chunks.jsonl.gz"
    index = tmp_path / "bm25.json.gz"
    for p in (chunks, index):
        p.write_bytes(b"")
    make_manifest(chunks, kind="chunk_store", fingerprint="old")
    make_manifest(index, kind="bm25_index", fingerprint="old")

    with pytest.raises(IndexMismatchError, match="与当前配置"):
        verify_pair(chunks, index, expected_fingerprint="new")


def test_unknown_manifest_version_is_fatal(tmp_path: Path):
    artifact = tmp_path / "chunks.jsonl.gz"
    artifact.write_bytes(b"")
    (tmp_path / f"chunks.jsonl.gz{MANIFEST_SUFFIX}").write_text(
        '{"kind":"chunk_store","version":999,"fingerprint":"fp","entries":1,'
        '"repo":"r","chunker_version":"v","created_at":"t"}',
        encoding="utf-8",
    )

    with pytest.raises(IndexMismatchError, match="版本"):
        IndexManifest.load(artifact)
