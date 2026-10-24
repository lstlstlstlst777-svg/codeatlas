"""API 层：路由、SSE 事件协议、断点恢复。

用 stub Runtime 替换真实装配（真实装配要 Qdrant + 7000 条索引，不适合单测），
但图本身是**真的**——用 FakeProvider + 临时 repo 跑完整流程。
这样测的是协议与恢复逻辑，而不是"能不能连上 Qdrant"。

SSE 事件名是对外契约，改名会打断前端，所以这里逐条断言。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from codeatlas.api.app import create_app
from codeatlas.api.runtime import Runtime
from codeatlas.graph.build import build_graph
from codeatlas.graph.nodes import GraphDeps
from codeatlas.indexing.types import Chunk, ChunkKind
from codeatlas.providers.fake import FakeLLM
from codeatlas.retrieval.service import RetrievalResult, RetrievedChunk
from codeatlas.settings import Settings

SERVER_C = "\n".join(f"// line {i}" for i in range(1, 201)) + "\n"


def make_chunk(chunk_id: str, path: str, symbol: str, start: int = 10, end: int = 40) -> Chunk:
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
        context_header=f"// {path}",
        file_sha="sha",
    )


class StubRetriever:
    def __init__(self, hits: list[Chunk], top_score: float = 0.5) -> None:
        self.hits = hits
        self.top_score = top_score
        self.settings = Settings(provider_mode="fake")
        # 多轮测试要看"第二轮到底拿什么去检索"
        self.queries: list[str] = []

    async def retrieve(self, query: str, top_k: int | None = None) -> RetrievalResult:
        self.queries.append(query)
        return RetrievalResult(
            query=query,
            hits=[
                RetrievedChunk(chunk=c, score=0.5, rank=i + 1, source="rerank")
                for i, c in enumerate(self.hits)
            ],
            timings_ms={"recall": 1.0},
            manual_top_score=self.top_score,
        )


class StubRuntime:
    """只保留 routes 真正用到的几个成员。"""

    def __init__(self, repo: Path, hits: list[Chunk], responses: list[str]) -> None:
        self.settings = Settings(provider_mode="fake", refuse_tau=-2.0)
        self.chunks = {c.chunk_id: c for c in hits}
        self.retriever = StubRetriever(hits)
        self.deps = type("D", (), {"aclose": _noop_async})()
        self.dense = type("Q", (), {"count": lambda self: len(hits), "close": lambda self: None})()
        self.bm25 = type("B", (), {"num_docs": len(hits)})()
        self.graph_deps = GraphDeps(
            settings=self.settings,
            retriever=self.retriever,
            llm=FakeLLM(responses),
            chunks=self.chunks,
            repo_root=repo,
            allow_uncalibrated_gate=True,
        )
        # 恢复逻辑依赖 checkpointer，stub 也必须带一个（内存版即可）
        from langgraph.checkpoint.memory import InMemorySaver

        self.repo_root = repo
        self.saver = InMemorySaver()
        self.graph = build_graph(self.graph_deps, checkpointer=self.saver)

    def with_streaming(self, enabled: bool):
        if self.graph_deps.stream_tokens == enabled:
            return self.graph
        self.graph_deps.stream_tokens = enabled
        self.graph = build_graph(self.graph_deps, checkpointer=self.saver)
        return self.graph

    def stats(self) -> dict[str, object]:
        return {
            "chunks": len(self.chunks),
            "bm25_docs": len(self.chunks),
            "dense_points": len(self.chunks),
            "index_fingerprint": "test123",
            "llm": "fake/llm",
            "refuse_tau": -2.0,
        }

    async def aclose(self) -> None:
        return None


async def _noop_async() -> None:
    return None


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "server.c").write_text(SERVER_C, encoding="utf-8")
    return tmp_path


@pytest.fixture
def make_client(repo: Path, monkeypatch):
    """把 Runtime.create 换掉，避免测试依赖 Qdrant 与真实索引。"""

    def factory(hits: list[Chunk] | None = None, responses: list[str] | None = None):
        # 不能用 `hits or default`：空列表是合法输入（用于测拒答），会被 falsy 吞掉
        stub = StubRuntime(
            repo,
            hits if hits is not None else [make_chunk("a" * 16, "src/server.c", "initServer")],
            responses
            if responses is not None
            else ["`initServer` 初始化服务器 (src/server.c:12-20)。"],
        )

        async def fake_create(cls, settings, **kwargs):
            return stub

        # app.py 在模块顶层 import 了 Runtime，必须替换同一个对象上的类方法
        monkeypatch.setattr(Runtime, "create", classmethod(fake_create))
        return stub

    return factory


async def _client(app):
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def test_healthz_reports_index_state(make_client):
    make_client()
    app = create_app(Settings(provider_mode="fake"))

    async with app.router.lifespan_context(app), await _client(app) as client:
        resp = await client.get("/healthz")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["chunks"] == 1
    assert body["index_fingerprint"] == "test123"


async def test_ask_returns_structured_answer(make_client):
    make_client()
    app = create_app(Settings(provider_mode="fake"))

    async with app.router.lifespan_context(app), await _client(app) as client:
        resp = await client.post("/ask", json={"question": "initServer 做什么"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"]
    assert body["refused"] is False
    assert body["thread_id"]
    assert "src/server.c:12-20" in body["citations"]


async def test_ask_rejects_empty_question(make_client):
    make_client()
    app = create_app(Settings(provider_mode="fake"))

    async with app.router.lifespan_context(app), await _client(app) as client:
        resp = await client.post("/ask", json={"question": ""})

    assert resp.status_code == 422


async def test_ask_uses_provided_thread_id(make_client):
    make_client()
    app = create_app(Settings(provider_mode="fake"))

    async with app.router.lifespan_context(app), await _client(app) as client:
        resp = await client.post("/ask", json={"question": "initServer", "thread_id": "my-thread"})

    assert resp.json()["thread_id"] == "my-thread"


async def test_state_reports_finished_after_ask(make_client):
    make_client()
    app = create_app(Settings(provider_mode="fake"))

    async with app.router.lifespan_context(app), await _client(app) as client:
        await client.post("/ask", json={"question": "initServer", "thread_id": "t-done"})
        resp = await client.get("/ask/state/t-done")

    body = resp.json()
    assert body["exists"] is True
    assert body["finished"] is True
    assert body["next_nodes"] == []
    assert "finalize" in body["stages"][-1]


async def test_state_unknown_thread(make_client):
    make_client()
    app = create_app(Settings(provider_mode="fake"))

    async with app.router.lifespan_context(app), await _client(app) as client:
        resp = await client.get("/ask/state/nope")

    assert resp.status_code == 200
    assert resp.json()["exists"] is False


async def test_resume_on_finished_thread_is_409(make_client):
    make_client()
    app = create_app(Settings(provider_mode="fake"))

    async with app.router.lifespan_context(app), await _client(app) as client:
        await client.post("/ask", json={"question": "initServer", "thread_id": "t2"})
        resp = await client.post("/ask/resume/t2")

    assert resp.status_code == 409


async def test_resume_unknown_thread_is_404(make_client):
    make_client()
    app = create_app(Settings(provider_mode="fake"))

    async with app.router.lifespan_context(app), await _client(app) as client:
        resp = await client.post("/ask/resume/never-existed")

    assert resp.status_code == 404


async def test_stream_emits_stage_then_token_then_done(make_client):
    make_client()
    app = create_app(Settings(provider_mode="fake"))

    events: list[str] = []
    async with (
        app.router.lifespan_context(app),
        await _client(app) as client,
        client.stream("POST", "/ask/stream", json={"question": "initServer 做什么"}) as resp,
    ):
        assert resp.status_code == 200
        async for line in resp.aiter_lines():
            if line.startswith("event:"):
                events.append(line.split(":", 1)[1].strip())

    assert "stage" in events
    assert "token" in events
    assert events[-1] == "done", f"最后一个事件必须是 done，实际 {events[-3:]}"
    # 顺序：stage 先于 token
    assert events.index("stage") < events.index("token")


async def test_stream_done_payload_has_citations(make_client):
    make_client()
    app = create_app(Settings(provider_mode="fake"))

    payloads: list[str] = []
    async with (
        app.router.lifespan_context(app),
        await _client(app) as client,
        client.stream("POST", "/ask/stream", json={"question": "initServer 做什么"}) as resp,
    ):
        current = None
        async for line in resp.aiter_lines():
            if line.startswith("event:"):
                current = line.split(":", 1)[1].strip()
            elif line.startswith("data:") and current == "done":
                payloads.append(line.split(":", 1)[1].strip())

    import json

    done = json.loads(payloads[-1])
    assert done["answer"]
    assert done["citations"]


async def test_stream_uses_crlf_separators(make_client):
    """SSE 事件分隔符是 \\r\\n\\r\\n（sse-starlette 的行为）。

    前端曾按 "\\n\\n" 切分，结果一个事件都解析不出来——表现是"点了提问毫无反应"，
    而服务端一切正常。这条断言把分隔符钉住，前端也必须兼容两种。
    """
    make_client()
    app = create_app(Settings(provider_mode="fake"))

    raw = ""
    async with (
        app.router.lifespan_context(app),
        await _client(app) as client,
        client.stream("POST", "/ask/stream", json={"question": "initServer 做什么"}) as resp,
    ):
        async for chunk in resp.aiter_text():
            raw += chunk

    assert "\r\n" in raw, "SSE 应使用 CRLF 分隔，前端解析必须兼容"
    assert "event: done" in raw


async def test_same_thread_carries_history_into_retrieval(make_client):
    """同一个 thread_id 的追问，检索查询应带上上一轮问题。

    这是"多轮对话"的关键行为：`那它的复杂度` 单独拿去做检索是零信息量，
    实测被拒答（相关度 -2.03）。
    """
    stub = make_client()
    app = create_app(Settings(provider_mode="fake"))

    async with app.router.lifespan_context(app), await _client(app) as client:
        first = await client.post(
            "/ask",
            json={"question": "how does initServer work", "thread_id": "t-multi"},
        )
        second = await client.post(
            "/ask",
            json={"question": "那它的复杂度呢", "thread_id": "t-multi"},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert stub.retriever.queries[0] == "how does initServer work"
    # 追问被还原成"上一轮问题 + 本轮问题"
    assert stub.retriever.queries[1] == "how does initServer work 那它的复杂度呢"
    assert second.json()["question"] == "那它的复杂度呢"


async def test_new_thread_starts_fresh(make_client):
    """不同 thread_id 之间不能串上下文——否则并发用户会互相污染。"""
    stub = make_client()
    app = create_app(Settings(provider_mode="fake"))

    async with app.router.lifespan_context(app), await _client(app) as client:
        await client.post("/ask", json={"question": "how does initServer work", "thread_id": "a"})
        await client.post("/ask", json={"question": "那它的复杂度呢", "thread_id": "b"})

    assert stub.retriever.queries[1] == "那它的复杂度呢"


async def test_refused_turn_is_not_added_to_history(make_client):
    """拒答的轮次不进历史：一个没答案的问题当下文，只会把下一轮检索带偏。"""
    stub = make_client(hits=[])
    app = create_app(Settings(provider_mode="fake"))

    async with app.router.lifespan_context(app), await _client(app) as client:
        first = await client.post(
            "/ask", json={"question": "how does initServer work", "thread_id": "r"}
        )
        await client.post("/ask", json={"question": "那它的复杂度呢", "thread_id": "r"})

    assert first.json()["refused"] is True
    # 上一轮被拒 → 没有可用的上文 → 追问原样检索
    assert stub.retriever.queries[1] == "那它的复杂度呢"


async def test_code_endpoint_returns_source(make_client, repo):
    make_client()
    app = create_app(Settings(provider_mode="fake"))

    async with app.router.lifespan_context(app), await _client(app) as client:
        resp = await client.get("/code", params={"path": "src/server.c", "start": 10, "end": 12})

    assert resp.status_code == 200
    body = resp.json()
    assert body["line_count"] == 3
    assert body["file_lines"] == 200
    assert body["code"].startswith("// line 10")


async def test_code_endpoint_blocks_path_traversal(make_client):
    make_client()
    app = create_app(Settings(provider_mode="fake"))

    async with app.router.lifespan_context(app), await _client(app) as client:
        resp = await client.get("/code", params={"path": "../../secret.txt", "start": 1, "end": 2})

    assert resp.status_code == 400
    assert "越界" in resp.json()["detail"]


async def test_code_endpoint_404_is_json_not_html(make_client):
    """API 的 404 必须回 JSON：前端拿去 JSON.parse，给一整页 HTML 会直接崩。"""
    make_client()
    app = create_app(Settings(provider_mode="fake"))

    async with app.router.lifespan_context(app), await _client(app) as client:
        resp = await client.get("/code", params={"path": "src/nope.c", "start": 1, "end": 2})

    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")
    assert "不存在" in resp.json()["detail"]


async def test_code_endpoint_clamps_end_beyond_eof(make_client):
    make_client()
    app = create_app(Settings(provider_mode="fake"))

    async with app.router.lifespan_context(app), await _client(app) as client:
        resp = await client.get("/code", params={"path": "src/server.c", "start": 198, "end": 999})

    assert resp.status_code == 200
    assert resp.json()["end"] == 200


async def test_code_endpoint_rejects_bad_range(make_client):
    make_client()
    app = create_app(Settings(provider_mode="fake"))

    async with app.router.lifespan_context(app), await _client(app) as client:
        resp = await client.get("/code", params={"path": "src/server.c", "start": 20, "end": 10})

    assert resp.status_code == 400


async def test_stream_refusal_still_emits_done(make_client):
    """拒答也要有 done 事件，否则前端永远等不到结束。"""
    make_client(hits=[], responses=["不该被调用"])
    app = create_app(Settings(provider_mode="fake"))

    events: list[str] = []
    async with (
        app.router.lifespan_context(app),
        await _client(app) as client,
        client.stream("POST", "/ask/stream", json={"question": "无关问题"}) as resp,
    ):
        async for line in resp.aiter_lines():
            if line.startswith("event:"):
                events.append(line.split(":", 1)[1].strip())

    assert events[-1] == "done"
    assert "token" not in events
