"""API 数据契约。

SSE 事件是对外协议，改动会破坏前端，所以集中定义在这里。

| event | data | 时机 |
| --- | --- | --- |
| `stage` | `{"node": "retrieve", "stages": [...], "timings": {...}}` | 每个节点完成 |
| `token` | `{"text": "Redis "}` | 生成阶段逐 token |
| `done` | 完整 `AskResponse` | 正常结束 |
| `error` | `{"message": "...", "code": "..."}` | 异常 |

**不推 `citation` 事件**：引用校验必须等整段答案生成完才能做，
中途推"某条引用有效"会误导前端把它当成最终结论。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    thread_id: str | None = Field(
        default=None,
        description="会话 id。同一 id 可续接、可恢复中断的请求；不传则自动生成。",
    )
    top_k: int | None = Field(default=None, ge=1, le=20)


class AskResponse(BaseModel):
    thread_id: str
    question: str
    answer: str
    refused: bool
    refuse_reason: str | None = None
    citations: list[str] = Field(default_factory=list)
    citation_total: int = 0
    citation_valid: int = 0
    attempts: int = 0
    degraded: list[str] = Field(default_factory=list)
    stages: list[str] = Field(default_factory=list)
    timings_ms: dict[str, float] = Field(default_factory=dict)
    hit_ids: list[str] = Field(default_factory=list)


class StateResponse(BaseModel):
    thread_id: str
    exists: bool
    # 已完成 = 图跑到了 END；未完成时 `next_nodes` 给出待执行的节点
    finished: bool
    next_nodes: list[str] = Field(default_factory=list)
    stages: list[str] = Field(default_factory=list)
    answer: str | None = None
    refused: bool | None = None


class CodeResponse(BaseModel):
    path: str
    start: int
    end: int
    code: str
    line_count: int
    file_lines: int


class HealthResponse(BaseModel):
    status: str
    version: str
    mode: str
    index_fingerprint: str
    chunks: int
    bm25_docs: int
    dense_points: int
    llm: str
    refuse_tau: float | None


def to_response(thread_id: str, state: dict) -> AskResponse:
    return AskResponse(
        thread_id=thread_id,
        question=state.get("question", ""),
        answer=state.get("answer", "") or "",
        refused=bool(state.get("refused")),
        refuse_reason=state.get("refuse_reason"),
        citations=list(state.get("citations") or []),
        citation_total=int(state.get("citation_total") or 0),
        citation_valid=int(state.get("citation_valid") or 0),
        attempts=int(state.get("attempts") or 0),
        degraded=list(state.get("degraded") or []),
        stages=list(state.get("stages") or []),
        timings_ms=dict(state.get("timings") or {}),
        hit_ids=list(state.get("hit_ids") or []),
    )
