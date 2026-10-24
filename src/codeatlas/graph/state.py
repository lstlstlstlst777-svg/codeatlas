"""图状态：LangGraph 的 checkpoint 要求可序列化，所以只放基本类型。

不直接放 `Chunk` 对象：SQLite checkpoint 会把状态 JSON 化，pydantic 模型能存但
恢复后是 dict，节点里再 `isinstance` 判断就会出错。统一用 dict + id 引用，
需要原文时从 chunk store 查。
"""

from __future__ import annotations

from typing import Any, TypedDict


class QAState(TypedDict, total=False):
    # 输入
    request_id: str
    question: str
    # 最近几轮 [{"question": ..., "answer": ...}]，由 API/CLI 从上一轮的
    # checkpoint 里读出来注入。**不靠 LangGraph reducer 累积**：
    # 一次请求只带一个 answer，reducer 会把同一轮的半成品也加进去。
    history: list[dict[str, str]]

    # 检索
    rewritten: str
    top_k: int | None
    hit_ids: list[str]
    evidence_ids: list[str]
    degraded: list[str]
    timings: dict[str, float]
    top_rerank_score: float | None

    # 生成与校验
    answer: str
    attempts: int
    citation_total: int
    citation_valid: int
    citations: list[str]
    failure_codes: list[str]

    # 终态
    refused: bool
    refuse_reason: str
    error: str | None
    stages: list[str]


def new_state(
    question: str,
    request_id: str,
    top_k: int | None = None,
    history: list[dict[str, str]] | None = None,
) -> QAState:
    return QAState(
        request_id=request_id,
        question=question.strip(),
        top_k=top_k,
        history=list(history or []),
        attempts=0,
        refused=False,
        stages=[],
    )


def append_stage(state: Any, stage: str) -> list[str]:
    """LangGraph 的 reducer 需要返回新列表而不是就地修改。"""
    return [*(state.get("stages") or []), stage]
