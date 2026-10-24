"""多轮上下文：指代还原 + 历史注入。

两块都要测，因为它们解决的是不同环节的问题：
- `contextualize` 决定**检索**能不能找到东西（追问本身没有检索价值）；
- `history` 决定**生成**能不能理解"它"指什么（以及不能顺手复用上一轮的引用）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codeatlas.generation.prompts import build_messages
from codeatlas.generation.query import contextualize, dialog_messages, needs_context
from codeatlas.graph.build import build_graph, run_graph
from codeatlas.graph.nodes import GraphDeps
from codeatlas.indexing.types import Chunk, ChunkKind
from codeatlas.providers.fake import FakeLLM
from codeatlas.retrieval.service import RetrievalResult, RetrievedChunk
from codeatlas.settings import Settings

HISTORY = [
    {
        "question": "how does Redis expire keys",
        "answer": "`expireIfNeeded` 在访问时惰性删除 (src/expire.c:35-67)。",
    }
]


# ---------- 指代判定 ----------


@pytest.mark.parametrize(
    "question",
    [
        "那它的时间复杂度是多少",
        "这个函数在哪调用",
        "为什么",
        "继续",
        "what about its complexity",
        "and that?",
        "它",
    ],
)
def test_anaphoric_questions_need_context(question: str) -> None:
    assert needs_context(question)


@pytest.mark.parametrize(
    "question",
    [
        "how does Redis expire keys",
        "zmalloc_used_memory",
        "ACL 权限检查怎么实现",
        "listTypePush 的返回值是什么",
        "rdb 是怎么保存字符串对象的",
    ],
)
def test_standalone_questions_do_not_need_context(question: str) -> None:
    assert not needs_context(question)


def test_short_question_is_treated_as_follow_up() -> None:
    """极短的问题几乎不可能是完整提问，宁可拼上上文也不要不拼。"""
    assert needs_context("为什么")
    assert not needs_context("为什么 listTypePush 会修改 quicklist 的长度字段")


# ---------- 查询还原 ----------


def test_contextualize_prepends_previous_question() -> None:
    query = contextualize("那它的时间复杂度是多少", HISTORY)

    assert query.startswith("how does Redis expire keys")
    assert "时间复杂度" in query


def test_contextualize_keeps_standalone_question_untouched() -> None:
    question = "how does Redis expire keys"
    assert contextualize(question, HISTORY) == question


def test_contextualize_without_history_returns_question() -> None:
    assert contextualize("那它的复杂度呢", []) == "那它的复杂度呢"
    assert contextualize("那它的复杂度呢", None) == "那它的复杂度呢"


def test_contextualize_does_not_pull_in_previous_answer() -> None:
    """答案里全是函数名和行号，拼进查询会让 BM25 被这些词主导。"""
    query = contextualize("那它的复杂度呢", HISTORY)

    assert "expireIfNeeded" not in query
    assert "35-67" not in query


# ---------- 历史注入 ----------


def test_dialog_messages_caps_answer_length() -> None:
    long_answer = "x" * 5000
    messages = dialog_messages([{"question": "q", "answer": long_answer}])

    assert len(messages) == 2
    assert len(messages[1]["content"]) == 400


def test_dialog_messages_limits_turns() -> None:
    history = [{"question": f"q{i}", "answer": "a"} for i in range(5)]
    messages = dialog_messages(history, limit=2)

    assert [m["content"] for m in messages if m["role"] == "user"] == ["q3", "q4"]


def test_dialog_messages_empty_when_no_history() -> None:
    assert dialog_messages(None) == []
    assert dialog_messages([]) == []


def test_build_messages_includes_history_and_warns_about_citations() -> None:
    chunk = Chunk(
        chunk_id="a" * 16,
        path="src/expire.c",
        start_line=35,
        end_line=67,
        body_start_line=35,
        node_type=ChunkKind.FUNCTION,
        symbol="expireIfNeeded",
        code="int expireIfNeeded(...) {}",
        context_header="// expire.c",
        file_sha="sha",
    )

    messages = build_messages("那它的复杂度呢", [chunk], HISTORY)

    roles = [m["role"] for m in messages]
    assert roles == ["system", "user", "assistant", "user"]
    assert "how does Redis expire keys" in messages[1]["content"]
    # 不复述历史引用：上一轮的证据不在本轮里，复用会导致校验全挂
    assert "只能来自上面的证据" in messages[-1]["content"]


def test_build_messages_without_history_stays_two_messages() -> None:
    chunk = Chunk(
        chunk_id="b" * 16,
        path="src/x.c",
        start_line=1,
        end_line=2,
        body_start_line=1,
        node_type=ChunkKind.FUNCTION,
        symbol="f",
        code="void f(void) {}",
        context_header="// x.c",
        file_sha="sha",
    )

    messages = build_messages("f 做什么", [chunk])

    assert [m["role"] for m in messages] == ["system", "user"]


# ---------- 图里真的用上了 ----------


class RecordingRetriever:
    def __init__(self, hits: list[Chunk]) -> None:
        self.hits = hits
        self.queries: list[str] = []

    async def retrieve(self, query: str, top_k: int | None = None) -> RetrievalResult:
        self.queries.append(query)
        return RetrievalResult(
            query=query,
            hits=[
                RetrievedChunk(chunk=c, score=0.5, rank=i + 1, source="rerank")
                for i, c in enumerate(self.hits)
            ],
            degraded=[],
            timings_ms={"recall": 1.0, "rerank": 1.0},
            manual_top_score=0.9,
        )


@pytest.fixture
def follow_up_graph(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "expire.c").write_text(
        "\n".join(f"// line {i}" for i in range(1, 201)) + "\n", encoding="utf-8"
    )

    chunk = Chunk(
        chunk_id="c" * 16,
        path="src/expire.c",
        start_line=35,
        end_line=67,
        body_start_line=35,
        node_type=ChunkKind.FUNCTION,
        symbol="expireIfNeeded",
        code="int expireIfNeeded(redisDb *db, robj *key) { return 0; }",
        context_header="// expire.c",
        file_sha="sha",
    )
    retriever = RecordingRetriever([chunk])
    deps = GraphDeps(
        settings=Settings(provider_mode="fake", refuse_tau=-2.0),
        retriever=retriever,
        llm=FakeLLM(["惰性删除，均摊 O(1) (src/expire.c:35-67)。"]),
        chunks={chunk.chunk_id: chunk},
        repo_root=repo,
    )
    return build_graph(deps), retriever


async def test_graph_retrieves_with_contextualized_query(follow_up_graph) -> None:
    graph, retriever = follow_up_graph

    await run_graph(graph, "那它的时间复杂度是多少", history=HISTORY)

    assert retriever.queries == ["how does Redis expire keys 那它的时间复杂度是多少"]


async def test_graph_keeps_question_verbatim_for_generation(follow_up_graph) -> None:
    """检索用还原后的查询，但**生成要拿原话**——改写后的句子读起来别扭。"""
    graph, _ = follow_up_graph

    final = await run_graph(graph, "那它的时间复杂度是多少", history=HISTORY)

    assert final["question"] == "那它的时间复杂度是多少"
    assert final["rewritten"] == "how does Redis expire keys 那它的时间复杂度是多少"


async def test_graph_without_history_passes_question_through(follow_up_graph) -> None:
    graph, retriever = follow_up_graph

    await run_graph(graph, "how does Redis expire keys")

    assert retriever.queries == ["how does Redis expire keys"]
