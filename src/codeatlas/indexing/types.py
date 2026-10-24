"""切分产物的数据契约。

约定：
- `code` 逐字节取自源文件，是唯一真值；任何附加信息只能进 `context_header`。
- 行号 1-based、闭区间；`body_start_line` 是去掉前导注释后的代码首行。
"""

from __future__ import annotations

import hashlib
from enum import StrEnum

from pydantic import BaseModel, Field


class ChunkKind(StrEnum):
    FUNCTION = "function"
    STRUCT = "struct"
    ENUM = "enum"
    TYPEDEF = "typedef"
    MACRO = "macro"
    TABLE = "table"
    FILE = "file"


class Span(BaseModel):
    """字节区间 + 行区间。end 已按 tree-sitter 语义修正到最后一个实际行。"""

    start_byte: int
    end_byte: int
    start_line: int
    end_line: int


class Chunk(BaseModel):
    chunk_id: str
    path: str
    start_line: int
    end_line: int
    body_start_line: int
    node_type: ChunkKind
    symbol: str | None = None
    parent_symbol: str | None = None
    signature: str | None = None
    code: str
    context_header: str
    leading_comment: str | None = None
    is_split: bool = False
    split_index: int | None = None
    split_total: int | None = None
    tokens_est: int = 0
    file_sha: str = ""
    commit: str | None = None
    fields: dict[str, str] = Field(default_factory=dict)

    def embedding_text(self) -> str:
        """送给 embedding 的文本：头部元信息 + 原文，避免片段脱离上下文。"""
        return f"{self.context_header}\n{self.code}"

    @property
    def span_len(self) -> int:
        return self.end_line - self.start_line + 1


def make_chunk_id(path: str, start_line: int, end_line: int, chunker_version: str) -> str:
    raw = f"{path}:{start_line}:{end_line}:{chunker_version}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def file_sha256(source: bytes) -> str:
    return hashlib.sha256(source).hexdigest()
