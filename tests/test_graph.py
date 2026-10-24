"""图编排的 5 个分支（P6 验收项）：正常 / 拒答 / 重写成功 / 重写用尽 / 递归护栏。

用 stub retriever + FakeLLM，全离线可复现。
这些测试要证明的不是"能跑通"，而是**每个分支都走到该走的终点**——
比如"重写两次仍失败"必须收尾成删句而不是无限重试。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codeatlas.config import ConfigError
from codeatlas.graph.build import RECURSION_LIMIT, build_graph, run_graph
from codeatlas.graph.nodes import GraphDeps
from codeatlas.graph.routing import route_after_validate
from codeatlas.indexing.types import Chunk, ChunkKind
from codeatlas.providers.fake import FakeLLM
from codeatlas.retrieval.service import RetrievalResult, RetrievedChunk
from codeatlas.settings import Settings

SERVER_C = "\n".join(f"// line {i}" for i in range(1, 201)) + "\n"


def make_chunk(chunk_id: str, path: str, start: int, end: int, symbol: str) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        path=path,
        start_line=start,
        end_line=end,
        body_start_line=start,
        node_type=ChunkKind.FUNCTION,
        symbol=symbol,
        signature=f"void {symbol}(void)",
        code=f"void {symbol}(void) {{}}",
        context_header=f"// {path} | function {symbol}",
        file_sha="sha",
    )


class StubRetriever:
    def __init__(self, hits: list[Chunk], top_score: float | None = 0.5) -> None:
        self.hits = hits
        self.top_score = top_score
        self.calls = 0

    async def retrieve(self, query: str, top_k: int | None = None) -> RetrievalResult:
        self.calls += 1
        return RetrievalResult(
            query=query,
            hits=[
                RetrievedChunk(chunk=c, score=0.5, rank=i + 1, source="rerank")
                for i, c in enumerate(self.hits)
            ],
            degraded=[],
            timings_ms={"recall": 1.0, "rerank": 1.0},
            manual_top_score=self.top_score,
        )


@pytest.fixture
def repo(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "server.c").write_text(SERVER_C, encoding="utf-8")
    return tmp_path


def build(repo: Path, hits: list[Chunk], llm: FakeLLM, *, tau=None, allow_uncalibrated=False):
    chunks = {c.chunk_id: c for c in hits}
    settings = Settings(provider_mode="fake", refuse_tau=tau, max_span_lines=200)
    deps = GraphDeps(
        settings=settings,
        retriever=StubRetriever(hits) if hits else StubRetriever([], top_score=None),
        llm=llm,
        chunks=chunks,
        repo_root=repo,
        allow_uncalibrated_gate=allow_uncalibrated,
    )
    return build_graph(deps)


# ---------- 1. 正常路径 ----------


async def test_happy_path_returns_answer_with_valid_citation(repo):
    hit = make_chunk("c1", "src/server.c", 10, 40, "initServer")
    llm = FakeLLM(["`initServer` 负责初始化服务器 (src/server.c:12-20)。"])
    graph = build(repo, [hit], llm, allow_uncalibrated=True)

    final = await run_graph(graph, "initServer 做什么")

    assert not final.get("refused")
    assert "src/server.c:12-20" in final.get("answer", "")
    assert final.get("attempts", 0) == 0
    assert "finalize" in (final.get("stages") or [])[-1]


# ---------- 2. 拒答 ----------


async def test_empty_hits_refuses_without_calling_llm(repo):
    llm = FakeLLM(["不该被调用"])
    graph = build(repo, [], llm, allow_uncalibrated=True)

    final = await run_graph(graph, "某个不存在的东西")

    assert final.get("refused")
    assert "没有检索到任何候选" in (final.get("refuse_reason") or "")
    assert llm._cursor == 0, "拒答路径不能烧 LLM 调用"


async def test_gate_threshold_triggers_refusal(repo):
    hit = make_chunk("c1", "src/server.c", 10, 40, "initServer")
    llm = FakeLLM(["不该被调用"])
    # rerank top1=0.5 -> log10=-0.301，阈值设成 -0.1 就会拒答
    graph = build(repo, [hit], llm, tau=-0.1)

    final = await run_graph(graph, "initServer 做什么")

    assert final.get("refused")
    assert "低于阈值" in (final.get("refuse_reason") or "")


async def test_gate_requires_calibration_by_default(repo):
    """τ 未标定且没显式放行时必须报错，不能静默用一个默认值。"""
    hit = make_chunk("c1", "src/server.c", 10, 40, "initServer")
    graph = build(repo, [hit], FakeLLM(["x"]), tau=None, allow_uncalibrated=False)

    with pytest.raises(ConfigError, match="refuse_tau 未标定"):
        await run_graph(graph, "initServer 做什么")


async def test_model_refuse_token_routes_to_refuse(repo):
    hit = make_chunk("c1", "src/server.c", 10, 40, "initServer")
    llm = FakeLLM(["[REFUSE] 证据没有提到这个问题"])
    graph = build(repo, [hit], llm, allow_uncalibrated=True)

    final = await run_graph(graph, "无关问题")

    assert final.get("refused")
    assert "模型判定证据不足" in (final.get("refuse_reason") or "")


# ---------- 3. 重写一次成功 ----------


async def test_repair_then_success(repo):
    hit = make_chunk("c1", "src/server.c", 10, 40, "initServer")
    llm = FakeLLM(
        [
            # 第一次：行号真实但不在证据内 -> not_grounded
            "`initServer` 初始化服务器 (src/server.c:150-160)。",
            # 重写后：落在证据区间内
            "`initServer` 初始化服务器 (src/server.c:12-20)。",
        ]
    )
    graph = build(repo, [hit], llm, allow_uncalibrated=True)

    final = await run_graph(graph, "initServer 做什么")

    assert final.get("attempts") == 1
    assert not final.get("refused")
    assert "src/server.c:12-20" in final.get("answer", "")
    assert "repair:1" in " ".join(final.get("stages") or [])


# ---------- 4. 重写用尽 ----------


async def test_repair_exhausted_strips_bad_claims(repo):
    hit = make_chunk("c1", "src/server.c", 10, 40, "initServer")
    bad = "`initServer` 初始化服务器 (src/server.c:150-160)。"
    good = "补充说明，没有引用。"
    llm = FakeLLM([bad, bad, bad])  # 三次都给同一个错误引用
    graph = build(repo, [hit], llm, allow_uncalibrated=True)

    final = await run_graph(graph, f"initServer 做什么 {good}")

    # 重写 2 次后放弃，finalize 删掉含坏引用的句子
    assert final.get("attempts") == 2
    assert "src/server.c:150-160" not in final.get("answer", "")


async def test_repair_exhausted_with_nothing_left_refuses(repo):
    hit = make_chunk("c1", "src/server.c", 10, 40, "initServer")
    bad = "`initServer` 初始化服务器 (src/server.c:150-160)。"
    graph = build(repo, [hit], FakeLLM([bad, bad, bad]), allow_uncalibrated=True)

    final = await run_graph(graph, "initServer 做什么")

    # 唯一一句含无效引用被删 -> 空答案 -> 拒答
    assert final.get("refused")
    assert "引用都无法校验" in (final.get("refuse_reason") or "")


def test_route_stops_repair_at_limit():
    state_at_limit = {"citation_total": 2, "citation_valid": 1, "attempts": 2}
    assert route_after_validate(state_at_limit) == "finalize"

    state_with_budget = {"citation_total": 2, "citation_valid": 1, "attempts": 1}
    assert route_after_validate(state_with_budget) == "repair"


# ---------- 5. 递归护栏 ----------


async def test_recursion_limit_is_finite(repo):
    hit = make_chunk("c1", "src/server.c", 10, 40, "initServer")
    # 永远返回坏的引用，验证不会无限重写
    graph = build(repo, [hit], FakeLLM(["见 src/server.c:150-160"]), allow_uncalibrated=True)

    final = await run_graph(graph, "initServer 做什么")

    repairs = [s for s in (final.get("stages") or []) if s.startswith("repair")]
    assert len(repairs) <= 2, f"重写次数超过上限：{repairs}"
    assert RECURSION_LIMIT <= 12


# ---------- 图结构 ----------


def test_graph_nodes_are_all_reachable(repo):
    hit = make_chunk("c1", "src/server.c", 10, 40, "initServer")
    graph = build(repo, [hit], FakeLLM(["x"]), allow_uncalibrated=True)

    nodes = set(graph.get_graph().nodes)
    for expected in ("retrieve", "gate", "generate", "validate", "repair", "finalize", "refuse"):
        assert expected in nodes, f"缺少节点 {expected}"
