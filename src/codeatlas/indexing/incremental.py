"""增量索引：只重嵌变化的文件（P4 验收项之一）。

判定单位是**文件**而不是 chunk：一个 .c 改了一行，它的 chunk 边界可能全变，
按 chunk 做 diff 会把"移动了几行"误判成"新增 N 个 chunk"，白白多嵌。
用 file_sha 比对，粒度和成本都合适。

执行顺序是刻意的：
    1. 先 embedding（最容易失败的一步：网络、限流、余额）
    2. 再删旧点
    3. 再插新点
    4. 最后写 manifest

1 失败时索引完全没动，重跑即可；2/3 是本地操作，失败概率低。
如果反过来先删再嵌，一次限流就会留下"文件被删了一半"的索引。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from codeatlas.indexing.types import Chunk
from codeatlas.retrieval.dense import FileState


@dataclass(slots=True)
class IncrementalPlan:
    added: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)

    @property
    def needs_embed(self) -> list[str]:
        return sorted(self.added + self.changed)

    @property
    def is_noop(self) -> bool:
        return not self.added and not self.changed and not self.removed

    def summary(self) -> str:
        return (
            f"added={len(self.added)} changed={len(self.changed)} "
            f"removed={len(self.removed)} unchanged={len(self.unchanged)}"
        )


def group_by_path(chunks: list[Chunk]) -> dict[str, list[Chunk]]:
    grouped: dict[str, list[Chunk]] = {}
    for chunk in chunks:
        grouped.setdefault(chunk.path, []).append(chunk)
    return grouped


def plan_incremental(
    chunks_by_path: dict[str, list[Chunk]],
    indexed: dict[str, FileState],
) -> IncrementalPlan:
    plan = IncrementalPlan()

    for path, chunks in chunks_by_path.items():
        current_sha = next((c.file_sha for c in chunks if c.file_sha), "")
        state = indexed.get(path)
        if state is None:
            plan.added.append(path)
        elif state.file_sha != current_sha:
            plan.changed.append(path)
        else:
            plan.unchanged.append(path)

    plan.removed = [path for path in indexed if path not in chunks_by_path]
    plan.added.sort()
    plan.changed.sort()
    plan.removed.sort()
    plan.unchanged.sort()
    return plan


def select_chunks(chunks_by_path: dict[str, list[Chunk]], paths: list[str]) -> list[Chunk]:
    selected: list[Chunk] = []
    for path in paths:
        selected.extend(chunks_by_path.get(path, []))
    return selected
