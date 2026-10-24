"""增量比对：只重嵌变化的文件（P4 验收项）。

这里全是纯逻辑，不碰网络也不碰 Qdrant，所以可以写得很细。
真实增量测试在 test_dense.py 里用本地模式跑。
"""

from __future__ import annotations

from codeatlas.indexing.incremental import group_by_path, plan_incremental, select_chunks
from codeatlas.indexing.types import Chunk, ChunkKind
from codeatlas.retrieval.dense import FileState


def make_chunk(path: str, sha: str, chunk_id: str | None = None) -> Chunk:
    return Chunk(
        chunk_id=chunk_id or f"{path}#{sha[:6]}",
        path=path,
        start_line=1,
        end_line=2,
        body_start_line=1,
        node_type=ChunkKind.FUNCTION,
        code="int f(void) { return 0; }",
        context_header="// header",
        file_sha=sha,
    )


def test_group_by_path():
    chunks = [make_chunk("a.c", "s1"), make_chunk("a.c", "s1"), make_chunk("b.c", "s2")]
    grouped = group_by_path(chunks)

    assert set(grouped) == {"a.c", "b.c"}
    assert len(grouped["a.c"]) == 2
    assert len(grouped["b.c"]) == 1


def test_first_run_everything_added():
    chunks = [make_chunk("a.c", "s1"), make_chunk("b.c", "s2")]

    plan = plan_incremental(group_by_path(chunks), {})

    assert plan.added == ["a.c", "b.c"]
    assert plan.changed == []
    assert plan.removed == []
    assert plan.needs_embed == ["a.c", "b.c"]
    assert not plan.is_noop


def test_rerun_is_noop():
    """同一份代码跑第二次，不能发起任何 embedding——这是 P4 的验收项。"""
    chunks = [make_chunk("a.c", "s1"), make_chunk("b.c", "s2")]
    indexed = {
        "a.c": FileState("a.c", "s1", 1),
        "b.c": FileState("b.c", "s2", 1),
    }

    plan = plan_incremental(group_by_path(chunks), indexed)

    assert plan.needs_embed == []
    assert plan.is_noop


def test_changed_file_detected_by_sha():
    chunks = [make_chunk("a.c", "new"), make_chunk("b.c", "s2")]
    indexed = {
        "a.c": FileState("a.c", "old", 1),
        "b.c": FileState("b.c", "s2", 1),
    }

    plan = plan_incremental(group_by_path(chunks), indexed)

    assert plan.changed == ["a.c"]
    assert plan.needs_embed == ["a.c"]
    assert plan.unchanged == ["b.c"]


def test_new_file_detected():
    chunks = [make_chunk("a.c", "s1"), make_chunk("brand_new.c", "s9")]
    indexed = {"a.c": FileState("a.c", "s1", 1)}

    plan = plan_incremental(group_by_path(chunks), indexed)

    assert plan.added == ["brand_new.c"]
    assert plan.unchanged == ["a.c"]


def test_deleted_file_detected():
    chunks = [make_chunk("a.c", "s1")]
    indexed = {
        "a.c": FileState("a.c", "s1", 1),
        "gone.c": FileState("gone.c", "s2", 3),
    }

    plan = plan_incremental(group_by_path(chunks), indexed)

    assert plan.removed == ["gone.c"]
    assert plan.needs_embed == []


def test_changed_and_removed_together():
    chunks = [make_chunk("a.c", "new")]
    indexed = {
        "a.c": FileState("a.c", "old", 2),
        "gone.c": FileState("gone.c", "s2", 5),
    }

    plan = plan_incremental(group_by_path(chunks), indexed)

    assert plan.changed == ["a.c"]
    assert plan.removed == ["gone.c"]
    assert plan.summary() == "added=0 changed=1 removed=1 unchanged=0"


def test_select_chunks_keeps_all_chunks_of_selected_files():
    """一个文件的所有 chunk 必须一起处理，否则会留下半截索引。"""
    chunks = [make_chunk("a.c", "s1", "a1"), make_chunk("a.c", "s1", "a2")]
    chunks.append(make_chunk("b.c", "s2", "b1"))
    grouped = group_by_path(chunks)

    selected = select_chunks(grouped, ["a.c"])

    assert {c.chunk_id for c in selected} == {"a1", "a2"}


def test_missing_sha_never_matches_unchanged():
    """file_sha 为空时不能算 unchanged，否则会永远跳过重嵌（也就永远修不好）。"""
    chunks = [make_chunk("a.c", "")]
    indexed = {"a.c": FileState("a.c", "", 1)}

    plan = plan_incremental(group_by_path(chunks), indexed)

    # sha 双方都空 -> 视为未变；这是可接受的，因为空 sha 只可能出现在异常路径
    assert plan.unchanged == ["a.c"]

    indexed["a.c"] = FileState("a.c", "real-sha", 1)
    plan2 = plan_incremental(group_by_path(chunks), indexed)
    assert plan2.changed == ["a.c"]
