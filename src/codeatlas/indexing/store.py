"""chunk 落盘：索引与检索之间唯一的元数据来源。

为什么必须持久化而不是每次重算切分：
- `bm25-search` / `dense-search` 要把 chunk_id 还原成 `path:start-end` 才能看；
- 引用校验 L1 需要 `n_lines` 与 `file_sha`，L2 需要本次 evidence 的区间；
- 每次查询都重切 164 个文件（约 1 秒）在 P7 的 p95<300ms 目标下不可接受。

格式选 gzip JSONL 而不是 pickle：容器与宿主都会读这份文件，反序列化不该有执行代码能力；
逐行写也便于后续做流式加载与大仓库分片。
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Iterable, Iterator
from pathlib import Path

from codeatlas.indexing.types import Chunk

DEFAULT_CHUNK_STORE = "data/chunks.jsonl.gz"


def save_chunks(chunks: Iterable[Chunk], path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with gzip.open(target, "wt", encoding="utf-8", compresslevel=6) as fh:
        for chunk in chunks:
            fh.write(chunk.model_dump_json())
            fh.write("\n")
            count += 1
    if count == 0:
        raise ValueError(f"refusing to write empty chunk store: {target}")
    return target


def iter_chunks(path: str | Path) -> Iterator[Chunk]:
    with gzip.open(Path(path), "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield Chunk.model_validate(json.loads(line))


def load_chunks(path: str | Path) -> list[Chunk]:
    return list(iter_chunks(path))


def load_chunk_map(path: str | Path) -> dict[str, Chunk]:
    return {chunk.chunk_id: chunk for chunk in iter_chunks(path)}


def store_stats(path: str | Path) -> dict[str, int | float]:
    path = Path(path)
    by_kind: dict[str, int] = {}
    total = 0
    for chunk in iter_chunks(path):
        total += 1
        key = chunk.node_type.value
        by_kind[key] = by_kind.get(key, 0) + 1
    return {
        "chunks": total,
        "size_mb": round(path.stat().st_size / 1024 / 1024, 2),
        **by_kind,
    }
