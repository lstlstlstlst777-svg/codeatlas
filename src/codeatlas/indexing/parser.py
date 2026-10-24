"""tree-sitter 封装：这是全项目唯一直接接触 tree-sitter 的模块。

行号语义（0.26 实测）：
- `Point.row` / `Point.column` 都是 0-based，column 按字节计；
- `end_point` 指向最后一个字节**之后**，所以当它落在行首（column==0）时必须回退一行。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import tree_sitter_c
from tree_sitter import Language, Node, Parser, Tree

from codeatlas.indexing.types import Span

LANGUAGE = Language(tree_sitter_c.language())


@dataclass(slots=True)
class ParsedFile:
    path: str  # 相对仓库根，正斜杠
    source: bytes
    lines: list[bytes]  # 不含行尾换行符
    tree: Tree

    @property
    def n_lines(self) -> int:
        return len(self.lines)

    def text(self, start_byte: int, end_byte: int) -> str:
        return self.source[start_byte:end_byte].decode("utf-8", "replace")

    def line_text(self, line_no: int) -> str:
        return self.lines[line_no - 1].decode("utf-8", "replace")

    def line_offset(self, row: int) -> int:
        """0-based 行号 → 该行首字节偏移。

        splitter 只按行做决策，字节偏移统一由这里算。两边各自算字节是行号错位的
        经典来源——切分器以为的"第 N 行"和 chunker 以为的不是同一个位置。
        """
        if row <= 0:
            return 0
        return sum(len(line) + 1 for line in self.lines[:row])

    def line_end_offset(self, row: int) -> int:
        """0-based 行号的**排他**结束偏移（最后一个有效字节之后）。

        与 tree-sitter 的 `end_byte` 同语义，可直接当 `source[start:end]` 的上界用。
        早期版本这里是"最后一个字节的下标"，差一位导致每个切分段都丢掉最后一行——
        收尾的 `}` 消失，片段读起来像残缺代码。
        """
        if not self.lines:
            return 0
        row = min(max(row, 0), len(self.lines) - 1)
        content = self.lines[row].rstrip(b"\r")
        return self.line_offset(row) + len(content)


def split_lines(source: bytes) -> list[bytes]:
    lines = source.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    return lines


def node_span(node: Node) -> Span:
    start_line = node.start_point.row + 1
    end_line = node.end_point.row + 1
    if node.end_point.column == 0 and end_line > start_line:
        end_line -= 1  # end_point 落在下一行行首
    return Span(
        start_byte=node.start_byte,
        end_byte=node.end_byte,
        start_line=start_line,
        end_line=end_line,
    )


def span_from_bytes(
    start_byte: int, end_byte: int, start_line: int, end_line: int, end_column: int
) -> Span:
    if end_column == 0 and end_line > start_line:
        end_line -= 1
    return Span(
        start_byte=start_byte,
        end_byte=end_byte,
        start_line=start_line,
        end_line=end_line,
    )


_PARSER = Parser(LANGUAGE)


def parse_file(abs_path: Path, repo_root: Path) -> ParsedFile:
    source = abs_path.read_bytes()
    tree = _PARSER.parse(source)
    rel = abs_path.relative_to(repo_root).as_posix()
    return ParsedFile(path=rel, source=source, lines=split_lines(source), tree=tree)


def parse_source(source: bytes, path: str = "<memory>") -> ParsedFile:
    tree = _PARSER.parse(source)
    return ParsedFile(path=path, source=source, lines=split_lines(source), tree=tree)
