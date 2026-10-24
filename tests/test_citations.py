"""引用校验的 12+ 个 case（P6 验收项）。

这些 case 来自"模型实际会怎么胡说"，不是凭空造的边界：
编造行号、抄错文件名、引用看都没看过的区间、把行号写反、区间过宽。
每个 case 必须被**正确归类**——失败码错了，定向重写的 prompt 就会给错提示。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from codeatlas.generation.citations import (
    Citation,
    CitationValidator,
    FileMeta,
    extract_citations,
    parse_citation_token,
    strip_invalid_claims,
)
from codeatlas.indexing.types import Chunk, ChunkKind


def make_chunk(path: str, start: int, end: int, text: str = "code") -> Chunk:
    return Chunk(
        chunk_id=f"{path}-{start}-{end}",
        path=path,
        start_line=start,
        end_line=end,
        body_start_line=start,
        node_type=ChunkKind.FUNCTION,
        code=text,
        context_header="// header",
        file_sha="sha",
    )


@pytest.fixture
def repo(tmp_path: Path):
    """两个文件：server.c 100 行，expire.c 50 行。"""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "server.c").write_text(
        "\n".join(f"// line {i}" for i in range(1, 101)) + "\n"
    )
    (tmp_path / "src" / "expire.c").write_text(
        "\n".join(f"// line {i}" for i in range(1, 51)) + "\n"
    )
    return tmp_path


def validator_for(repo: Path, evidence: list[Chunk], chunks: list[Chunk] | None = None):
    chunks = chunks or evidence
    return CitationValidator.from_chunks(
        repo_root=repo, all_chunks=chunks, evidence=evidence, max_span_lines=200
    )


# ---------- 抽取 ----------


def test_extract_plain():
    cites = extract_citations("见 src/server.c:10-20 的实现")
    assert [(c.path, c.start_line, c.end_line) for c in cites] == [("src/server.c", 10, 20)]


def test_extract_bracketed_and_backticked():
    text = "参考 [src/server.c:10-20] 和 `src/expire.c:5`"
    cites = extract_citations(text)
    assert len(cites) == 2
    assert cites[1].start_line == 5 and cites[1].end_line == 5


def test_extract_dedupes_identical():
    cites = extract_citations("src/server.c:10-20 ... src/server.c:10-20")
    assert len(cites) == 1


def test_extract_ignores_non_code_paths():
    assert extract_citations("见 https://example.com/a.py:10 和 README.md:3") == []


def test_extract_windows_backslash():
    cites = extract_citations(r"见 src\server.c:10-20")
    assert cites[0].path == "src/server.c"


def test_extract_tolerates_spaces_around_dash():
    cites = extract_citations("src/server.c:10 - 20")
    assert (cites[0].start_line, cites[0].end_line) == (10, 20)


def test_parse_token_rejects_incomplete():
    assert parse_citation_token("src/server.c") is None
    assert parse_citation_token("src/server.c:99").start_line == 99


# ---------- L1 ----------


def test_valid_citation_passes(repo):
    ev = [make_chunk("src/server.c", 5, 30)]
    report = validator_for(repo, ev).validate("答案见 src/server.c:10-15")

    assert report.ok
    assert report.valid_ratio == 1.0
    assert len(report.valid) == 1


def test_made_up_file_fails(repo):
    ev = [make_chunk("src/server.c", 5, 30)]
    report = validator_for(repo, ev).validate("见 src/nosuch.c:1-5")

    assert not report.ok
    assert report.failure_codes() == ["range_out_of_file"]


def test_line_beyond_eof_fails(repo):
    ev = [make_chunk("src/server.c", 5, 30)]
    report = validator_for(repo, ev).validate("见 src/server.c:95-120")

    assert report.failure_codes() == ["range_out_of_file"]
    assert "100 行" in report.failures[0].detail


def test_reversed_range_fails(repo):
    ev = [make_chunk("src/server.c", 5, 30)]
    report = validator_for(repo, ev).validate("见 src/server.c:30-10")

    assert report.failure_codes() == ["range_out_of_file"]
    assert "非法" in report.failures[0].detail


def test_zero_line_fails(repo):
    ev = [make_chunk("src/server.c", 5, 30)]
    report = validator_for(repo, ev).validate("见 src/server.c:0-5")

    assert report.failure_codes() == ["range_out_of_file"]


def test_span_too_wide_fails(repo):
    (repo / "src" / "big.c").write_text("\n".join("x" for _ in range(500)) + "\n")
    ev = [make_chunk("src/big.c", 1, 500)]
    validator = validator_for(repo, ev)
    report = validator.validate("见 src/big.c:1-400")

    assert report.failure_codes() == ["span_too_wide"]


# ---------- L2（关键） ----------


def test_line_exists_but_not_in_evidence_fails(repo):
    """行号真实存在、但模型没看过这些行——这是最危险的一类，必须拦住。"""
    ev = [make_chunk("src/server.c", 10, 20)]
    report = validator_for(repo, ev).validate("见 src/server.c:60-70")

    assert not report.ok
    assert report.failure_codes() == ["not_grounded"]


def test_partially_outside_evidence_fails(repo):
    ev = [make_chunk("src/server.c", 10, 20)]
    report = validator_for(repo, ev).validate("见 src/server.c:18-25")

    assert report.failure_codes() == ["not_grounded"]


def test_citation_inside_large_evidence_passes(repo):
    ev = [make_chunk("src/server.c", 1, 90)]
    report = validator_for(repo, ev).validate("见 src/server.c:40-45")

    assert report.ok


def test_evidence_from_another_file_does_not_ground(repo):
    """expire.c 在索引里（行号合法），但本次没把它送给 LLM —— 引了就是没依据。"""
    ev = [make_chunk("src/server.c", 10, 20)]
    all_chunks = ev + [make_chunk("src/expire.c", 1, 50)]
    report = validator_for(repo, ev, all_chunks).validate("见 src/expire.c:5-10")

    assert report.failure_codes() == ["not_grounded"]


def test_multiple_evidence_spans_all_grounded(repo):
    ev = [make_chunk("src/server.c", 1, 10), make_chunk("src/server.c", 50, 60)]
    report = validator_for(repo, ev).validate("见 src/server.c:5-8 与 src/server.c:55-58")

    assert report.ok
    assert len(report.valid) == 2


# ---------- 组合与重写 ----------


def test_mixed_valid_and_invalid(repo):
    ev = [make_chunk("src/server.c", 10, 20)]
    report = validator_for(repo, ev).validate(
        "结论 A 见 src/server.c:12-18，结论 B 见 src/server.c:80-90"
    )

    assert len(report.valid) == 1
    assert len(report.failures) == 1
    assert not report.ok
    assert "1/2" in report.summary()


def test_no_citations_is_not_a_failure(repo):
    """不含引用的答案由上层决定怎么处理，校验层不该替它判死。"""
    report = validator_for(repo, [make_chunk("src/server.c", 1, 10)]).validate(
        "这是个不含引用的回答"
    )

    assert report.ok
    assert report.total == 0


def test_strip_removes_offending_lines_only(repo):
    ev = [make_chunk("src/server.c", 10, 20)]
    validator = validator_for(repo, ev)
    answer = "第一句结论见 src/server.c:12-18\n第二句瞎编见 src/server.c:80-90\n第三句无引用"
    report = validator.validate(answer)

    cleaned = strip_invalid_claims(answer, report)

    assert "第一句" in cleaned
    assert "第二句" not in cleaned
    assert "第三句" in cleaned


def test_strip_is_noop_when_all_valid(repo):
    ev = [make_chunk("src/server.c", 10, 20)]
    validator = validator_for(repo, ev)
    answer = "见 src/server.c:12-18"
    report = validator.validate(answer)

    assert strip_invalid_claims(answer, report) == answer


def test_allowed_ranges_render(repo):
    ev = [make_chunk("src/server.c", 10, 20)]
    validator = validator_for(repo, ev)
    report = validator.validate("见 src/server.c:12-18")

    rendered = report.render_allowed_ranges()
    assert "src/server.c:12-18" in rendered


# ---------- L3 ----------


def test_stale_file_is_warned_not_failed(repo):
    """文件被改过：行号是"建索引时正确"的位置，判失败会让所有答案在代码更新后失效。"""
    ev = [make_chunk("src/server.c", 10, 20)]
    validator = validator_for(repo, ev)
    (repo / "src" / "server.c").write_text("changed\n" * 120)

    report = validator.validate("见 src/server.c:12-18")

    assert report.ok, "stale 不应判失败"
    assert len(report.stale) == 1


def test_fresh_file_is_not_stale(repo):
    ev = [make_chunk("src/server.c", 10, 20)]
    report = validator_for(repo, ev).validate("见 src/server.c:12-18")

    assert report.stale == []


def test_from_chunks_computes_real_line_counts(repo):
    ev = [make_chunk("src/server.c", 1, 10)]
    validator = validator_for(repo, ev)

    assert validator.files["src/server.c"].n_lines == 100
    assert (
        validator.files["src/server.c"].sha256
        == hashlib.sha256((repo / "src" / "server.c").read_bytes()).hexdigest()
    )


# ---------- 手工构造（不走 from_chunks） ----------


def test_direct_construction_with_meta():
    validator = CitationValidator(
        repo_root=Path("."),
        files={"src/x.c": FileMeta(n_lines=10, sha256="abc")},
        evidence=[make_chunk("src/x.c", 1, 10)],
    )

    assert validator.validate("见 src/x.c:3-5").ok


def test_citation_span_helper():
    assert Citation("r", "a.c", 10, 20).span == 11
    assert Citation("r", "a.c", 10, 10).span == 1
    assert Citation("r", "a.c", 10, 10).render() == "a.c:10"
    assert Citation("r", "a.c", 10, 20).render() == "a.c:10-20"
