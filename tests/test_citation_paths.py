"""引用路径归一化：模型写法的变体都要能落到同一个 key。

这些 case 全部来自"实际可能发生"的假设，不是凭空造的边界：
Windows 反斜杠、绝对路径、大小写、`./` 前缀、同名文件歧义。
"""

from __future__ import annotations

import pytest

from codeatlas.citation_paths import (
    format_citation,
    lookup_path,
    normalize_path,
    parse_citation,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("src/server.c", "src/server.c"),
        ("src\\server.c", "src/server.c"),
        ("./src/server.c", "src/server.c"),
        ("src//server.c", "src/server.c"),
        ("  src/server.c  ", "src/server.c"),
        ('"src/server.c"', "src/server.c"),
        ("`src/server.c`", "src/server.c"),
    ],
)
def test_normalize_variants(raw, expected):
    assert normalize_path(raw) == expected


def test_normalize_strips_repo_prefix():
    assert (
        normalize_path(
            "E:\\CodeAtlas\\vendor\\redis-7.2.5\\src\\server.c",
            repo_root="E:/CodeAtlas/vendor/redis-7.2.5",
        )
        == "src/server.c"
    )


def test_normalize_strips_repo_prefix_case_insensitive():
    """Windows 盘符与目录大小写不固定，不能因此判失败。"""
    assert (
        normalize_path(
            "e:/codeatlas/Vendor/Redis-7.2.5/src/server.c",
            repo_root="E:/CodeAtlas/vendor/redis-7.2.5",
        )
        == "src/server.c"
    )


def test_normalize_keeps_unrelated_absolute_path():
    """不在仓库下的绝对路径不该被瞎剥，原样保留（后续 lookup 会失败，这是对的）。"""
    got = normalize_path("/usr/include/stdio.h", repo_root="E:/CodeAtlas/vendor/redis-7.2.5")
    assert got == "usr/include/stdio.h"


def test_parse_citation_with_range():
    path, start, end = parse_citation("src/server.c:1234-1256")
    assert (path, start, end) == ("src/server.c", 1234, 1256)


def test_parse_citation_single_line():
    path, start, end = parse_citation("src/server.c:42")
    assert (path, start, end) == ("src/server.c", 42, None)


def test_parse_citation_without_line():
    path, start, end = parse_citation("src\\server.c")
    assert (path, start, end) == ("src/server.c", None, None)


def test_parse_citation_tolerates_spaces_around_dash():
    assert parse_citation("src/server.c:10 - 20") == ("src/server.c", 10, 20)


def test_lookup_exact():
    known = {"src/server.c", "src/networking.c"}
    assert lookup_path("src/server.c", known) == "src/server.c"


def test_lookup_case_insensitive():
    known = {"src/server.c"}
    assert lookup_path("SRC/Server.C", known) == "src/server.c"


def test_lookup_suffix_unique():
    known = {"vendor/redis/src/server.c", "src/networking.c"}
    assert lookup_path("server.c", known) == "vendor/redis/src/server.c"


def test_lookup_suffix_ambiguous_returns_none():
    """两个文件同名时宁可失败：引错文件比引用失败更难察觉。"""
    known = {"a/server.c", "b/server.c"}
    assert lookup_path("server.c", known) is None


def test_lookup_missing_returns_none():
    assert lookup_path("src/nope.c", {"src/server.c"}) is None


def test_lookup_does_not_match_partial_segment():
    """`erver.c` 不能匹配 `server.c`——后缀必须落在 `/` 边界上。"""
    known = {"src/server.c"}
    assert lookup_path("erver.c", known) is None


def test_format_citation_single_line_collapses():
    assert format_citation("src/server.c", 42, 42) == "src/server.c:42"


def test_format_citation_range():
    assert format_citation("src\\server.c", 10, 20) == "src/server.c:10-20"
