from __future__ import annotations

from codeatlas.indexing.tokenizer import tokenize


def test_snake_case_identifier_keeps_full_and_parts() -> None:
    tokens = tokenize("zmalloc_used_memory")
    assert tokens.count("zmalloc_used_memory") == 2  # 完整标识符权重加倍
    assert {"zmalloc", "used", "memory"} <= set(tokens)


def test_camel_case_identifier_is_split() -> None:
    tokens = tokenize("getIteratorFromNode")
    assert tokens.count("getiteratorfromnode") == 2
    assert {"get", "iterator", "from", "node"} <= set(tokens)


def test_general_source_line() -> None:
    tokens = tokenize("int addNumbers(int a, int b) { return a + b; }")
    assert "addnumbers" in tokens
    assert "int" in tokens  # 类型名保留，靠 IDF 自然降权
    assert "return" not in tokens  # 控制流停用词剔除


def test_numbers_are_indexed_for_line_hints() -> None:
    assert "6379" in tokenize("server.port = 6379;")
