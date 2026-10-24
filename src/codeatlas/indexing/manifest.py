"""索引产物的一致性清单（ISSUE-017）。

问题：`data/chunks.jsonl.gz` 与 `data/bm25.json.gz` 是两份独立文件。
只重建其中一个（比如改了切分参数却只跑了 bm25-build 的一半，或者手工替换了文件），
检索仍然能出结果，但 chunk_id 还原出的 `path:line` 来自旧切分——
表现为"引用行号轻微偏移"，这种错误不会抛异常，只会让引用校验莫名其妙地失败。

做法：两份产物各写一个 manifest，记录
- 内容指纹（chunker 参数 + 索引参数）
- 产出的条目数
- 生成时间与仓库路径
加载时先比对 manifest，不一致直接报错，要求显式重建。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

MANIFEST_SUFFIX = ".manifest.json"
MANIFEST_VERSION = 1


class IndexMismatchError(RuntimeError):
    """两份索引产物不是同一次构建出来的，继续用会得到错误的行号。"""


@dataclass(frozen=True, slots=True)
class IndexManifest:
    kind: str
    version: int
    fingerprint: str
    entries: int
    repo: str
    chunker_version: str
    created_at: str

    @classmethod
    def build(
        cls,
        *,
        kind: str,
        fingerprint: str,
        entries: int,
        repo: str,
        chunker_version: str,
        created_at: str,
    ) -> IndexManifest:
        return cls(
            kind=kind,
            version=MANIFEST_VERSION,
            fingerprint=fingerprint,
            entries=entries,
            repo=repo,
            chunker_version=chunker_version,
            created_at=created_at,
        )

    def save(self, artifact: str | Path) -> Path:
        target = Path(str(artifact) + MANIFEST_SUFFIX)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        return target

    @classmethod
    def load(cls, artifact: str | Path) -> IndexManifest:
        path = Path(str(artifact) + MANIFEST_SUFFIX)
        if not path.is_file():
            raise IndexMismatchError(
                f"缺少 manifest：{path}。索引产物不完整，请重新运行 `codeatlas bm25-build`。"
            )
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != MANIFEST_VERSION:
            raise IndexMismatchError(
                f"manifest 版本 {data.get('version')} 不受支持（期望 {MANIFEST_VERSION}）"
            )
        return cls(**data)


def verify_pair(
    chunks_artifact: str | Path,
    index_artifact: str | Path,
    expected_fingerprint: str | None = None,
) -> tuple[IndexManifest, IndexManifest]:
    """校验 chunk store 与倒排索引属于同一次构建。"""
    chunks_meta = IndexManifest.load(chunks_artifact)
    index_meta = IndexManifest.load(index_artifact)

    if chunks_meta.entries != index_meta.entries:
        raise IndexMismatchError(
            "chunk store 与索引条目数不一致："
            f"chunks={chunks_meta.entries} ({chunks_meta.created_at}), "
            f"index={index_meta.entries} ({index_meta.created_at})。"
            "两者不是同一次构建的产物，行号会错位——请重新运行 `codeatlas bm25-build`。"
        )

    if chunks_meta.fingerprint != index_meta.fingerprint:
        raise IndexMismatchError(
            "chunk store 与索引的配置指纹不一致："
            f"{chunks_meta.fingerprint} vs {index_meta.fingerprint}。"
            "配置改过但只重建了一个产物——请重新运行 `codeatlas bm25-build`。"
        )

    if expected_fingerprint and chunks_meta.fingerprint != expected_fingerprint:
        raise IndexMismatchError(
            f"索引指纹 {chunks_meta.fingerprint} 与当前配置 {expected_fingerprint} 不一致。"
            "当前 config 与建索引时不同，检索结果不能与配置对应——请重建索引。"
        )

    return chunks_meta, index_meta
