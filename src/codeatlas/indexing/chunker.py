"""结构感知切分：函数 / 结构体 / 枚举 / typedef / 宏 / 巨型命令表。

设计取舍：
- 不用固定窗口，chunk 边界来自 AST，行号与源文件逐字节对齐；
- 超长函数二次切分，但每段都带签名与父级作用域（写在 context_header 里，不重复代码）；
- `#ifdef` 递归下钻，否则 Redis 里大量条件编译包裹的函数会被整块吞掉；
- `redisCommandTable` 这类巨型表按元素切，一条命令一个 chunk，检索"某命令在哪实现"才准。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from tree_sitter import Node

from codeatlas.indexing.parser import ParsedFile, node_span
from codeatlas.indexing.splitter import split_oversized
from codeatlas.indexing.types import (
    Chunk,
    ChunkKind,
    Span,
    file_sha256,
    make_chunk_id,
)
from codeatlas.settings import CHUNKER_VERSION

_KIND_BY_TYPE: dict[str, ChunkKind] = {
    "function_definition": ChunkKind.FUNCTION,
    "struct_specifier": ChunkKind.STRUCT,
    "enum_specifier": ChunkKind.ENUM,
    "type_definition": ChunkKind.TYPEDEF,
    "preproc_def": ChunkKind.MACRO,
    "preproc_function_def": ChunkKind.MACRO,
}

_COMMAND_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*Command)\b")


@dataclass(slots=True)
class ChunkerConfig:
    max_chunk_lines: int = 120
    max_split_depth: int = 2
    max_signature_chars: int = 200
    table_min_entries: int = 8
    max_leading_comments: int = 3
    # 前导注释可能很长，预算不能被压到无法切分
    min_split_lines: int = 20
    # 超长注释只保留尾部这么多行（整整 100+ 行的文档注释会让 chunk 超限）
    max_comment_lines: int = 30
    chunker_version: str = CHUNKER_VERSION


class CChunker:
    def __init__(self, config: ChunkerConfig | None = None) -> None:
        self.cfg = config or ChunkerConfig()
        self._pf: ParsedFile | None = None
        self._sha = ""
        self._chunks: list[Chunk] = []

    def chunk(self, pf: ParsedFile) -> list[Chunk]:
        self._pf = pf
        self._sha = file_sha256(pf.source)
        self._chunks = []
        self._visit(pf.tree.root_node, None)
        return self._chunks

    # ---------- 遍历 ----------

    def _visit(self, node: Node, parent_symbol: str | None) -> None:
        for child in node.named_children:
            kind = _KIND_BY_TYPE.get(child.type)
            if child.type == "comment":
                continue
            if child.type == "declaration" and self._emit_table(child, parent_symbol):
                continue
            if kind is None:
                self._visit(child, parent_symbol)
                continue
            if kind in (ChunkKind.FUNCTION, ChunkKind.STRUCT, ChunkKind.ENUM) and (
                child.child_by_field_name("body") is None
            ):
                continue  # 纯声明 / 前向声明，没有可检索的实现
            self._emit_node(child, kind, parent_symbol)

    # ---------- 普通节点 ----------

    def _emit_node(self, node: Node, kind: ChunkKind, parent_symbol: str | None) -> None:
        pf = self._require_pf()
        symbol, signature = self._symbol_and_signature(node, kind)
        body_start_line = node.start_point.row + 1
        span = node_span(node)
        first_comment = self._leading_comment(node)
        leading_comment: str | None = None
        start_byte, start_line = span.start_byte, span.start_line
        if first_comment is not None:
            comment_start_row, comment_start_byte = self._comment_start(node, first_comment)
            leading_comment = pf.text(comment_start_byte, node.start_byte).rstrip()
            start_byte = comment_start_byte
            start_line = comment_start_row + 1
        header_span = Span(
            start_byte=start_byte,
            end_byte=span.end_byte,
            start_line=start_line,
            end_line=span.end_line,
        )

        if (
            kind in (ChunkKind.FUNCTION, ChunkKind.STRUCT, ChunkKind.ENUM)
            and header_span.end_line - header_span.start_line + 1 > self.cfg.max_chunk_lines
        ):
            parts = split_oversized(
                node, self._line_budget(node, first_comment), self.cfg.max_split_depth, depth=0
            )
            total = len(parts)
            for index, part in enumerate(parts):
                # splitter 只给行范围，字节一律由 ParsedFile 换算（唯一的真源）
                part_start_row = part.start_row
                part_end_row = min(part.end_row, pf.n_lines - 1)
                # 末行是空行时，按行尾切会得不到内容，导致 code 比声明的行区间少一行。
                # 回退到最后一个非空行——这只会让段更短，不违反 max_lines。
                while part_end_row > part_start_row and not pf.lines[part_end_row].strip():
                    part_end_row -= 1
                part_start_byte = pf.line_offset(part_start_row)
                part_end_byte = pf.line_end_offset(part_end_row)
                part_start_line = part_start_row + 1
                part_end_line = part_end_row + 1
                if index == 0 and first_comment is not None:
                    part_start_byte = start_byte
                    part_start_line = start_line
                self._append(
                    span=Span(
                        start_byte=part_start_byte,
                        end_byte=part_end_byte,
                        start_line=part_start_line,
                        end_line=part_end_line,
                    ),
                    kind=kind,
                    symbol=symbol,
                    parent_symbol=parent_symbol,
                    signature=signature,
                    leading_comment=leading_comment if index == 0 else None,
                    body_start_line=part_start_line,
                    split_index=index + 1,
                    split_total=total,
                )
            return

        self._append(
            span=header_span,
            kind=kind,
            symbol=symbol,
            parent_symbol=parent_symbol,
            signature=signature,
            leading_comment=leading_comment,
            body_start_line=body_start_line,
            extra_fields=self._extra_fields(node, kind),
        )

    def _line_budget(self, node: Node, first_comment: Node | None) -> int:
        """切分预算 = 总上限 - 前导注释占用的行数。

        不扣掉注释会出现"函数体 100 行 + 注释 30 行 = 131 行"这种超限 chunk：
        函数本身没超预算所以不切，但最终 chunk 仍然超标（实测 42 个）。
        注释算在 chunk 里，就必须算在预算里。
        """
        if first_comment is None:
            return self.cfg.max_chunk_lines
        comment_rows = node.start_point.row - first_comment.start_point.row
        # 留一个下限：注释本身极长时不能把预算压到没法切
        return max(self.cfg.max_chunk_lines - comment_rows, self.cfg.min_split_lines)

    # ---------- 分类标签 ----------

    def _extra_fields(self, node: Node, kind: ChunkKind) -> dict[str, str] | None:
        """给 chunk 打细分类标签，供检索层按需降权（ISSUE-008）。

        macro 占全量 28.7%，其中大量是 `#define C_OK 0` 这类单行常量：
        它们对自然语言提问几乎没有价值，却会挤进 top-k。
        但直接过滤会丢掉"某宏在哪定义"这类真实需求，所以先打标签、不动检索，
        等 P7 用评测数据决定降权还是过滤。
        """
        if kind is not ChunkKind.MACRO:
            return None
        if node.type == "preproc_function_def":
            return {"macro_kind": "macro_func"}
        # 必须用 node_span：preproc_def 的 end_point 会把行尾换行也算进去，
        # 直接用 row 差值会把所有单行宏误判成跨两行（实测 2016 个全进了 macro_block）。
        span = node_span(node)
        if span.end_line == span.start_line:
            return {"macro_kind": "macro_const"}
        return {"macro_kind": "macro_block"}

    # ---------- 巨型表 ----------

    def _emit_table(self, decl: Node, parent_symbol: str | None) -> bool:
        """把 `struct redisCommand redisCommandTable[] = {...}` 按元素切开。"""
        entries: list[Node] | None = None
        table_name: str | None = None
        for child in decl.named_children:
            if child.type != "init_declarator":
                continue
            declarator = child.child_by_field_name("declarator")
            value = child.child_by_field_name("value")
            if declarator is None or value is None or declarator.type != "array_declarator":
                continue
            candidates = [c for c in value.named_children if c.type == "initializer_list"]
            if len(candidates) < self.cfg.table_min_entries:
                continue
            entries = candidates
            table_name = _declarator_name(declarator) or "<table>"
            break

        if not entries or table_name is None:
            return False

        for index, entry in enumerate(entries):
            span = node_span(entry)
            text = self._require_pf().text(entry.start_byte, entry.end_byte)
            matches = _COMMAND_RE.findall(text)
            symbol = matches[-1] if matches else f"{table_name}[{index}]"
            self._append(
                span=span,
                kind=ChunkKind.TABLE,
                symbol=symbol,
                parent_symbol=table_name,
                signature=f"{table_name}[{index}]",
                leading_comment=None,
                body_start_line=span.start_line,
                extra_fields={"table": table_name, "entry_index": str(index)},
            )
        return True

    # ---------- 兜底 ----------

    def file_chunks(self, pf: ParsedFile) -> list[Chunk]:
        """解析失败时的降级：整文件按行窗口切，保证索引不中断。"""
        self._pf = pf
        self._sha = file_sha256(pf.source)
        self._chunks = []
        window = self.cfg.max_chunk_lines
        start = 1
        while start <= pf.n_lines:
            end = min(start + window - 1, pf.n_lines)
            self._append(
                span=Span(
                    start_byte=self._line_start_byte(pf, start),
                    end_byte=self._line_end_byte(pf, end),
                    start_line=start,
                    end_line=end,
                ),
                kind=ChunkKind.FILE,
                symbol=pf.path.rsplit("/", 1)[-1],
                parent_symbol=None,
                signature=f"{pf.path} L{start}-{end} (fallback line window)",
                leading_comment=None,
                body_start_line=start,
                extra_fields={"fallback": "line_window"},
            )
            start = end + 1
        return self._chunks

    @staticmethod
    def _line_start_byte(pf: ParsedFile, line_no: int) -> int:
        return sum(len(line) + 1 for line in pf.lines[: line_no - 1])

    @staticmethod
    def _line_end_byte(pf: ParsedFile, line_no: int) -> int:
        return sum(len(line) + 1 for line in pf.lines[:line_no]) - 1

    # ---------- 组装 ----------

    def _append(
        self,
        *,
        span: Span,
        kind: ChunkKind,
        symbol: str | None,
        parent_symbol: str | None,
        signature: str | None,
        leading_comment: str | None,
        body_start_line: int,
        split_index: int | None = None,
        split_total: int | None = None,
        extra_fields: dict[str, str] | None = None,
    ) -> None:
        pf = self._require_pf()
        code = pf.text(span.start_byte, span.end_byte)
        if code.endswith("\n"):
            # 宏/语句节点常把行尾换行也包进 end_byte，去掉它才能与行号区间严格对应
            code = code[:-1]
        if not code.strip():
            return
        header = self._context_header(
            pf.path, kind, symbol, span, split_index, split_total, parent_symbol, signature
        )
        self._chunks.append(
            Chunk(
                chunk_id=make_chunk_id(
                    pf.path, span.start_line, span.end_line, self.cfg.chunker_version
                ),
                path=pf.path,
                start_line=span.start_line,
                end_line=span.end_line,
                body_start_line=body_start_line,
                node_type=kind,
                symbol=symbol,
                parent_symbol=parent_symbol,
                signature=signature,
                code=code,
                context_header=header,
                leading_comment=leading_comment,
                is_split=split_total is not None,
                split_index=split_index,
                split_total=split_total,
                tokens_est=max(1, len(code) // 4),
                file_sha=self._sha,
                fields=extra_fields or {},
            )
        )

    def _context_header(
        self,
        path: str,
        kind: ChunkKind,
        symbol: str | None,
        span: Span,
        split_index: int | None,
        split_total: int | None,
        parent_symbol: str | None,
        signature: str | None,
    ) -> str:
        head = f"// {path} | {kind} {symbol or '<anonymous>'} | L{span.start_line}-{span.end_line}"
        if split_total:
            head += f" | part {split_index}/{split_total}"
        if parent_symbol and kind is ChunkKind.TABLE:
            head += f" | table {parent_symbol}"
        lines = [head]
        if signature:
            lines.append(f"// signature: {signature}")
        return "\n".join(lines)

    # ---------- 符号与注释 ----------

    def _symbol_and_signature(self, node: Node, kind: ChunkKind) -> tuple[str | None, str | None]:
        pf = self._require_pf()
        if kind is ChunkKind.FUNCTION:
            body = node.child_by_field_name("body")
            raw = (
                pf.text(node.start_byte, body.start_byte)
                if body
                else pf.text(node.start_byte, node.end_byte)
            )
            signature = " ".join(raw.split())[: self.cfg.max_signature_chars]
            return _declarator_name(node.child_by_field_name("declarator")), signature

        if kind in (ChunkKind.STRUCT, ChunkKind.ENUM):
            name_node = node.child_by_field_name("name")
            symbol = name_node.text.decode() if name_node else None
            keyword = "struct" if kind is ChunkKind.STRUCT else "enum"
            return symbol, f"{keyword} {symbol or '<anonymous>'}"

        if kind is ChunkKind.TYPEDEF:
            inner = next(
                (
                    c
                    for c in node.named_children
                    if c.type in {"struct_specifier", "enum_specifier"}
                ),
                None,
            )
            tag = inner.child_by_field_name("name") if inner is not None else None
            symbol = tag.text.decode() if tag is not None else None
            if symbol is None:
                symbol = _declarator_name(node.child_by_field_name("declarator"))
            return symbol, f"typedef {symbol or '<anonymous>'}"

        name_node = node.child_by_field_name("name")
        symbol = name_node.text.decode() if name_node else None
        first_line = pf.line_text(node.start_point.row + 1).strip()
        return symbol, first_line[: self.cfg.max_signature_chars]

    def _leading_comment(self, node: Node) -> Node | None:
        pf = self._require_pf()
        collected: list[Node] = []
        cursor = node
        while len(collected) < self.cfg.max_leading_comments:
            previous = cursor.prev_named_sibling
            if previous is None or previous.type != "comment":
                break
            gap = pf.source[previous.end_byte : cursor.start_byte]
            if gap.strip() or gap.count(b"\n") > 2:
                break
            collected.append(previous)
            cursor = previous
        return collected[-1] if collected else None

    def _comment_start(self, node: Node, comment: Node) -> tuple[int, int]:
        """返回 (comment_start_row, comment_start_byte)，超长注释只保留尾部。

        Redis 里有 100+ 行的文档注释（例如 `RM_SetCommandInfo`）。整块挂到 chunk 上
        会让 chunk 远超 max_chunk_lines，而切分逻辑对"注释"无能为力——它是一整块
        字符串，没有可切的语句边界。实测这类 chunk 有 8 个超过 120 行。
        保留尾部是因为紧贴函数的那几行（通常是参数说明与 `*/`）信息密度最高。
        """
        pf = self._require_pf()
        start_row = comment.start_point.row
        comment_lines = node.start_point.row - start_row
        if comment_lines <= self.cfg.max_comment_lines:
            return start_row, comment.start_byte
        trimmed_row = node.start_point.row - self.cfg.max_comment_lines
        return trimmed_row, pf.line_offset(trimmed_row)

    def _require_pf(self) -> ParsedFile:
        if self._pf is None:
            raise RuntimeError("chunk() must be called before emitting chunks")
        return self._pf


def _declarator_name(node: Node | None) -> str | None:
    """穿透 pointer/function/array/parenthesized declarator，取最内层标识符。"""
    guard = 0
    while node is not None and guard < 16:
        guard += 1
        if node.type in {"identifier", "field_identifier", "type_identifier"}:
            return node.text.decode()
        nxt = node.child_by_field_name("declarator")
        if nxt is None:
            nxt = next(
                (
                    c
                    for c in node.named_children
                    if c.type
                    in {
                        "identifier",
                        "pointer_declarator",
                        "function_declarator",
                        "parenthesized_declarator",
                        "array_declarator",
                        "attributed_declarator",
                    }
                ),
                None,
            )
        node = nxt
    return None
