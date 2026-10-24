"""超长节点的二次切分。

两层策略，缺一不可：

1. **结构化切分**：在 `compound_statement` 的直接子句边界切，尽量保持语义完整。
2. **语句边界兜底**：结构切分切不动时（典型是 `if/else if/else if` 链——AST 里
   else 分支嵌套了下一个 if，按直接子句只能切出 1 个巨型块），退回按**后代语句的
   起始行**贪心切。实测 `rdbLoadObject` 1004 行的函数体里有**一个 if 分支就占 1004 行**，
   只靠第 1 层会切出 `[18, 1004, 7]`——等于没切，随后 embedding 直接 400
   （超过 bge-m3 的 8192 token 上限）。

切分器只输出**行范围**，字节偏移由调用方按 `ParsedFile.line_offset()` 统一换算。
两边各自算字节是行号错位的经典来源，宁可多一层传递也不要两个真源。

约束：
- 只在语句边界断行，绝不在表达式中间劈开；
- 段间不重叠（overlap 会让同一行落进两个 chunk，引用校验与去重都会失真）；
- 首段保留签名与 `{`，末段保留 `}`。
"""

from __future__ import annotations

from dataclasses import dataclass

from tree_sitter import Node

_SPLITTABLE = frozenset(
    {
        "while_statement",
        "for_statement",
        "if_statement",
        "switch_statement",
        "do_statement",
    }
)

# 可以当作断点的节点类型：语句、声明、以及 struct/union/enum 的字段
_BOUNDARY_TYPES = frozenset(
    {
        "expression_statement",
        "declaration",
        "return_statement",
        "if_statement",
        "for_statement",
        "while_statement",
        "do_statement",
        "switch_statement",
        "break_statement",
        "continue_statement",
        "goto_statement",
        "labeled_statement",
        "compound_statement",
        "field_declaration",
        "preproc_ifdef",
        "preproc_if",
        "preproc_else",
        "preproc_elif",
    }
)


@dataclass(frozen=True, slots=True)
class Part:
    """纯行范围（0-based，闭区间）。"""

    start_row: int
    end_row: int

    @property
    def line_count(self) -> int:
        return self.end_row - self.start_row + 1


def split_oversized(node: Node, max_lines: int, max_depth: int, depth: int = 0) -> list[Part]:
    """把 node 切成若干段，保证每段不超过 max_lines。"""
    if max_lines <= 0:
        raise ValueError(f"max_lines must be > 0, got {max_lines}")

    structured = _split_by_units(node, max_lines, max_depth, depth)
    result: list[Part] = []
    for part in structured:
        if part.line_count > max_lines:
            result.extend(_split_by_boundaries(node, part, max_lines))
        else:
            result.append(part)

    if not result:
        return [_whole(node)]

    # 末段必须落在 node 真正的最后一行：收尾的 `}` 是"这段代码属于哪个函数"的
    # 唯一视觉线索，丢了它，检索到的片段读起来像是残缺的。
    last_row = _last_row(node)
    result[-1] = Part(result[-1].start_row, max(last_row, result[-1].start_row))
    return result


def _last_row(node: Node) -> int:
    """node 最后一个**实际内容**行（end_point 可能指向下一行行首）。"""
    end_row = node.end_point.row
    if node.end_point.column == 0 and end_row > node.start_point.row:
        end_row -= 1
    return end_row


# ---------- 第一层：按直接子句 ----------


def _split_by_units(node: Node, max_lines: int, max_depth: int, depth: int) -> list[Part]:
    body = _body_of(node)
    if body is None:
        return [_whole(node)]

    units = [child for child in body.named_children if child.type not in {"comment", ";"}]
    if not units:
        return [_whole(node)]

    parts: list[Part] = []
    pending: list[Node] = []
    pending_start_row = node.start_point.row

    def flush() -> None:
        if not pending:
            return
        last = pending[-1]
        parts.append(Part(pending_start_row, last.end_point.row))
        pending.clear()

    for unit in units:
        unit_lines = unit.end_point.row - unit.start_point.row + 1

        if unit_lines > max_lines and depth < max_depth and unit.type in _SPLITTABLE:
            nested = _body_of(unit)
            if nested is not None and nested.named_child_count > 0:
                flush()
                parts.extend(_split_by_units(unit, max_lines, max_depth, depth + 1))
                continue

        if not pending:
            pending_start_row = node.start_point.row if not parts else unit.start_point.row
        elif unit.end_point.row - pending_start_row + 1 > max_lines:
            flush()
            pending_start_row = unit.start_point.row

        pending.append(unit)

    flush()
    if not parts:
        return [_whole(node)]

    # 末段延伸到 node 末尾，保证收尾的 `}` 不丢
    parts[-1] = Part(parts[-1].start_row, node.end_point.row)
    return parts


def _body_of(node: Node) -> Node | None:
    """函数取 body，if/for/while 取 body，struct/union/enum 取字段列表。"""
    body = node.child_by_field_name("body")
    if body is not None:
        return body
    for child in node.named_children:
        if child.type in {"field_declaration_list", "enumerator_list", "compound_statement"}:
            return child
    return None


# ---------- 第二层：按语句边界兜底 ----------


def _split_by_boundaries(node: Node, part: Part, max_lines: int) -> list[Part]:
    """在一个超长段内按后代语句的起始行切。

    候选断点 = 所有语句/声明/字段的行首。贪心选择"不越过 max_lines 的最后一个候选"，
    这样既能切动 if-else 链，也不会把一行代码劈成两半。
    """
    boundaries = _collect_boundaries(node, part)
    parts: list[Part] = []
    cursor = part.start_row

    while cursor <= part.end_row:
        limit = cursor + max_lines - 1
        if limit >= part.end_row:
            parts.append(Part(cursor, part.end_row))
            break

        candidates = [row for row in boundaries if cursor < row <= limit]
        cut = max(candidates) if candidates else limit
        if cut <= cursor:
            cut = limit

        parts.append(Part(cursor, cut - 1))
        cursor = cut

    return parts or [part]


def _collect_boundaries(node: Node, part: Part) -> list[int]:
    """收集 part 范围内所有可断行的行号（0-based row）。"""
    rows: set[int] = set()

    def walk(current: Node, depth: int) -> None:
        if depth > 40:
            return
        for child in current.named_children:
            row = child.start_point.row
            if row > part.end_row:
                continue
            if part.start_row < row <= part.end_row and child.type in _BOUNDARY_TYPES:
                rows.add(row)
            walk(child, depth + 1)

    walk(node, 0)
    return sorted(rows)


def _whole(node: Node) -> Part:
    return Part(node.start_point.row, node.end_point.row)
