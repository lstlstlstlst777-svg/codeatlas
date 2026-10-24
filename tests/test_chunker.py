from __future__ import annotations

from pathlib import Path

from codeatlas.indexing import CChunker, ChunkerConfig, ChunkKind, parse_file

FIXTURES = Path(__file__).parent / "fixtures" / "c"


def _chunks(name: str, config: ChunkerConfig | None = None):
    pf = parse_file(FIXTURES / name, FIXTURES)
    return pf, CChunker(config).chunk(pf)


def test_symbols_and_kinds() -> None:
    _, chunks = _chunks("simple.c")
    by_kind: dict[ChunkKind, set[str]] = {}
    for chunk in chunks:
        by_kind.setdefault(chunk.node_type, set()).add(chunk.symbol or "")

    assert {"MAX_CLIENTS", "ZMALLOC_USED_MEMORY"} <= by_kind[ChunkKind.MACRO]
    assert "clientState" in by_kind[ChunkKind.TYPEDEF]
    assert "connType" in by_kind[ChunkKind.ENUM]
    assert {"initServer", "addNumbers"} <= by_kind[ChunkKind.FUNCTION]


def test_leading_comment_attached() -> None:
    pf, chunks = _chunks("simple.c")
    init = next(c for c in chunks if c.symbol == "initServer")
    assert init.leading_comment is not None
    assert "Initialize the server event loop" in init.leading_comment
    assert init.start_line < init.body_start_line
    assert init.code.lstrip().startswith("/* Initialize")
    assert pf.line_text(init.start_line).startswith("/* Initialize")


def test_signature_present_and_collapsed() -> None:
    _, chunks = _chunks("simple.c")
    init = next(c for c in chunks if c.symbol == "initServer")
    assert init.signature == "void initServer(void)"
    assert "signature: void initServer(void)" in init.context_header


def test_ifdef_drills_into_branches() -> None:
    _, chunks = _chunks("ifdef.c")
    handshakes = [c for c in chunks if c.symbol == "tlsHandshake"]
    assert len(handshakes) == 2, "两个 #ifdef 分支里的同名函数都应被切出来"
    assert {c.code.strip().splitlines()[-1] for c in handshakes} == {"}"}
    assert any(c.symbol == "TLS_ENABLED" for c in chunks)
    assert any(c.symbol == "plainHandshake" for c in chunks)


def test_giant_table_split_by_entry() -> None:
    _, chunks = _chunks("table.c")
    entries = [c for c in chunks if c.node_type is ChunkKind.TABLE]
    assert len(entries) == 12
    assert all(c.parent_symbol == "commandTable" for c in entries)
    assert entries[0].symbol == "getCommand"
    assert entries[-1].symbol == "hgetCommand"
    assert entries[0].fields == {"table": "commandTable", "entry_index": "0"}


def test_oversized_function_is_split_with_context() -> None:
    _, chunks = _chunks("giant_func.c", ChunkerConfig(max_chunk_lines=10))
    parts = [c for c in chunks if c.symbol == "oversized"]
    assert len(parts) >= 2
    assert all(c.is_split for c in parts)
    assert [c.split_index for c in parts] == list(range(1, len(parts) + 1))
    assert all(c.split_total == len(parts) for c in parts)

    for part in parts:
        assert part.span_len <= 10
        assert "signature: int oversized(int seed)" in part.context_header

    assert parts[0].start_line == 1
    assert "int oversized(int seed) {" in parts[0].code
    assert parts[-1].code.rstrip().endswith("}")
