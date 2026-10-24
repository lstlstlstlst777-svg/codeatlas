from __future__ import annotations

from pathlib import Path

import pytest

from codeatlas.indexing import CChunker, Chunk, ParsedFile, parse_file, scan_repo

FIXTURES = Path(__file__).parent / "fixtures" / "c"
REPO_ROOT = Path(__file__).resolve().parents[1]
REDIS_ROOT = REPO_ROOT / "vendor" / "redis-7.2.5"


def assert_alignment(pf: ParsedFile, chunk: Chunk) -> None:
    """行号不变式。

    chunk 允许从行中开始/结束（表项不含行尾逗号、切分段落在语句边界），
    所以首尾行做子串校验，中间行必须逐字相等，且行数必须一致。
    """
    code_lines = chunk.code.split("\n")
    span = chunk.end_line - chunk.start_line + 1
    assert len(code_lines) == span, f"{chunk.chunk_id}: {len(code_lines)} != {span}"

    first_source = pf.lines[chunk.start_line - 1].decode()
    last_source = pf.lines[chunk.end_line - 1].decode()
    assert code_lines[0] in first_source, f"{chunk.chunk_id}: first line mismatch"
    assert code_lines[-1] in last_source, f"{chunk.chunk_id}: last line mismatch"

    if span > 2:
        middle = [line.decode() for line in pf.lines[chunk.start_line : chunk.end_line - 1]]
        assert code_lines[1:-1] == middle, f"{chunk.chunk_id}: middle lines mismatch"

    assert 1 <= chunk.start_line <= chunk.body_start_line <= chunk.end_line <= pf.n_lines


@pytest.mark.parametrize("name", ["simple.c", "giant_func.c", "ifdef.c", "table.c"])
def test_fixture_alignment(name: str) -> None:
    pf = parse_file(FIXTURES / name, FIXTURES)
    chunks = CChunker().chunk(pf)
    assert chunks, "should produce chunks"
    for chunk in chunks:
        assert_alignment(pf, chunk)
        assert chunk.code.encode("utf-8") in pf.source


def test_no_chunk_overlaps() -> None:
    """同文件内非切分 chunk 不应互相覆盖，否则引用会出现歧义。"""
    pf = parse_file(FIXTURES / "simple.c", FIXTURES)
    spans = sorted(
        (c.start_line, c.end_line)
        for c in CChunker().chunk(pf)
        if not c.is_split and c.node_type.value != "table"
    )
    for (start_a, end_a), (start_b, end_b) in zip(spans, spans[1:], strict=False):
        assert end_a < start_b, f"overlap: [{start_a},{end_a}] vs [{start_b},{end_b}]"


@pytest.mark.skipif(not (REDIS_ROOT / "src").is_dir(), reason="vendor/redis-7.2.5 not present")
def test_redis_full_src_no_crash_and_sample_alignment() -> None:
    files = scan_repo(REDIS_ROOT)
    assert len(files) > 100

    chunker = CChunker()
    total = 0
    checked = 0
    for path in files:
        pf = parse_file(path, REDIS_ROOT)
        for chunk in chunker.chunk(pf):
            total += 1
            if total % 41 == 0 and checked < 200:
                assert_alignment(pf, chunk)
                checked += 1

    assert total > 1000, f"unexpected chunk count: {total}"
    assert checked >= 100


# --- ISSUE-012：大文件回归 ---
# ISSUE-001 的 ABI 错位在 200+ 顶层节点后才出现，小 fixture 测不出来。
# 这个文件由 scripts/gen_fixture.py 生成（260 函数 + 40 宏 + 20 结构体）。

BIG_FIXTURE = FIXTURES / "many_nodes.c"


def test_big_fixture_node_rows_are_sane() -> None:
    """节点行号必须落在文件范围内。0x40000000+n 这类指针值会立刻被这条抓住。"""
    pf = parse_file(BIG_FIXTURE, FIXTURES)

    rows = [node.start_point.row for node in pf.tree.root_node.named_children]

    assert len(rows) > 200, f"fixture 太小，复现不了 ISSUE-001（只有 {len(rows)} 个顶层节点）"
    assert all(0 <= row < pf.n_lines for row in rows), (
        f"节点行号越界，疑似 tree-sitter 版本不兼容（ISSUE-001）：max={max(rows)}, "
        f"n_lines={pf.n_lines}"
    )
    # 行号必须单调递增：错位时会出现乱序或重复
    assert rows == sorted(rows), "顶层节点行号非单调，解析结果不可信"


def test_big_fixture_alignment_covers_late_nodes() -> None:
    """重点看靠后的节点：错位从第 52 个节点开始，越往后越离谱。"""
    pf = parse_file(BIG_FIXTURE, FIXTURES)
    chunks = CChunker().chunk(pf)

    assert len(chunks) >= 260
    for chunk in chunks:
        assert_alignment(pf, chunk)

    late = [c for c in chunks if c.start_line > pf.n_lines * 0.8]
    assert late, "靠后 20% 的行没有产出 chunk，切分可能提前中断"
    for chunk in late:
        assert chunk.code.encode("utf-8") in pf.source


def test_big_fixture_symbols_match_line_numbers() -> None:
    """函数名里的序号必须与所在行号对应。

    这是比"行号在范围内"更强的约束：行号整体偏移时前者仍可能通过，这条会失败。
    注意用 `body_start_line` 而不是 `start_line`：chunk 会把紧邻的前导注释一起收进来，
    所以 start_line 指向注释首行，声明本身在 body_start_line。
    """
    pf = parse_file(BIG_FIXTURE, FIXTURES)
    chunks = CChunker().chunk(pf)

    checked = 0
    for chunk in chunks:
        if not chunk.symbol or not chunk.symbol.startswith("many_func_"):
            continue
        index = int(chunk.symbol.rsplit("_", 1)[1])
        source_line = pf.lines[chunk.body_start_line - 1].decode()
        assert f"many_func_{index:03d}" in source_line, (
            f"{chunk.symbol} 声明在第 {chunk.body_start_line} 行，但该行是 {source_line.strip()!r}"
        )
        # 前导注释确实被包含进来，否则 start_line 应等于 body_start_line
        assert chunk.start_line <= chunk.body_start_line
        checked += 1

    assert checked >= 250, f"只校验到 {checked} 个函数，覆盖不足"
