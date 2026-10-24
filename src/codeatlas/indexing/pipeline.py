"""仓库级切分：单文件失败降级为按行切，绝不中断全量索引。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from codeatlas.indexing.chunker import CChunker
from codeatlas.indexing.parser import parse_file
from codeatlas.indexing.scanner import scan_repo
from codeatlas.indexing.types import Chunk


@dataclass(slots=True)
class ChunkStats:
    files: int = 0
    chunks: int = 0
    splits: int = 0
    by_kind: dict[str, int] = field(default_factory=dict)
    errors: list[tuple[str, str]] = field(default_factory=list)
    per_file: list[tuple[str, int]] = field(default_factory=list)


def chunk_repo(
    repo_root: Path,
    chunker: CChunker | None = None,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
) -> tuple[list[Chunk], ChunkStats]:
    chunker = chunker or CChunker()
    stats = ChunkStats()
    all_chunks: list[Chunk] = []

    for path in scan_repo(repo_root, include=include, exclude=exclude):
        stats.files += 1
        try:
            pf = parse_file(path, repo_root)
            chunks = chunker.chunk(pf)
        except Exception as exc:  # noqa: BLE001 - 任何解析/切分异常都降级，不能中断全量
            stats.errors.append((path.name, f"{type(exc).__name__}: {exc}"[:200]))
            pf = parse_file(path, repo_root)
            chunks = chunker.file_chunks(pf)

        stats.per_file.append((pf.path, len(chunks)))
        stats.chunks += len(chunks)
        stats.splits += sum(1 for chunk in chunks if chunk.is_split)
        for chunk in chunks:
            key = chunk.node_type.value
            stats.by_kind[key] = stats.by_kind.get(key, 0) + 1
        all_chunks.extend(chunks)

    return all_chunks, stats
