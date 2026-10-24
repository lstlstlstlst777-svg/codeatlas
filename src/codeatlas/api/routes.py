"""SSE 问答端点。

**断线可恢复**的实现方式（这是 P7 的核心验收项）：

LangGraph 的 checkpointer 在**每个节点完成后**落盘。客户端断开时 uvicorn 会取消请求 task，
图也随之取消——但已完成的节点状态已经在 SQLite 里。所以：

1. 断开后 `GET /ask/state/{thread_id}` 能看到 `next_nodes=["validate"]` 之类的断点；
2. `POST /ask/resume/{thread_id}` 用 `astream(None, config)` 从断点继续，
   不会重跑已经完成的 retrieve（省掉一次 embedding + rerank 的钱和时间）；
3. 唯一会重跑的是**断开时正在执行的那个节点**——它没有落盘，只能重来。

为什么不用后台任务把图跑完：那需要额外的任务队列与进程管理，
而"节点级恢复"已经能满足"不重复烧钱、不丢进度"的实际需求。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from codeatlas.api.models import (
    AskRequest,
    AskResponse,
    CodeResponse,
    StateResponse,
    to_response,
)
from codeatlas.graph.state import new_state

router = APIRouter()


def _runtime(request: Request):
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="runtime 未就绪")
    return runtime


async def _load_history(runtime: Any, thread_id: str) -> list[dict[str, str]]:
    """从上一轮的 checkpoint 取多轮上下文。

    为什么不靠 LangGraph 的 reducer 累积：一轮里 `answer` 会被写多次
    （generate 写一次、repair 后再写一次），reducer 会把中间态也留下。
    每轮开始时显式从最终态里取一次，语义清楚、也便于设上限。
    """
    try:
        snapshot = await runtime.graph.aget_state(_config(thread_id))
    except Exception:  # noqa: BLE001 - 历史只是锦上添花，取不到就当新会话
        return []
    values = dict(getattr(snapshot, "values", None) or {})
    if not values.get("question"):
        return []

    history = list(values.get("history") or [])
    answer = values.get("answer")
    # 只有"答完了并且没拒答"的轮次才进历史：半截答案会让下一轮误解上下文
    if answer and not values.get("refused"):
        history.append({"question": values["question"], "answer": answer})
    return history[-3:]


def _config(thread_id: str, recursion_limit: int = 12) -> dict[str, Any]:
    return {"recursion_limit": recursion_limit, "configurable": {"thread_id": thread_id}}


def _sse(event: str, payload: dict[str, Any]) -> dict[str, str]:
    return {"event": event, "data": json.dumps(payload, ensure_ascii=False)}


async def _stream_events(
    runtime,
    thread_id: str,
    question: str | None,
    *,
    resume: bool,
    history: list[dict[str, str]] | None = None,
) -> AsyncIterator[dict[str, str]]:
    graph = runtime.graph
    config = _config(thread_id)

    # resume 时传 None：LangGraph 从 checkpointer 里的断点继续
    payload: Any = None if resume else new_state(question or "", thread_id, history=history)

    try:
        async for mode, item in graph.astream(
            payload, config=config, stream_mode=["custom", "updates"]
        ):
            if mode == "custom":
                if isinstance(item, dict) and item.get("type") == "token":
                    yield _sse("token", {"text": item.get("text", "")})
                continue

            # mode == "updates"：{node_name: partial_state}
            for node_name, update in (item or {}).items():
                yield _sse(
                    "stage",
                    {
                        "node": node_name,
                        "stages": (update or {}).get("stages", []),
                        "timings": (update or {}).get("timings", {}),
                    },
                )

        snapshot = await graph.aget_state(config)
        final = dict(snapshot.values or {})
        yield _sse("done", to_response(thread_id, final).model_dump())
    except asyncio.CancelledError:
        # 客户端断开：状态已由 checkpointer 保存，重新抛出以便 uvicorn 正常收尾
        raise
    except Exception as exc:  # noqa: BLE001 - 任何异常都要让前端收到可读事件
        yield _sse("error", {"message": str(exc), "code": exc.__class__.__name__})


@router.post("/ask", response_model=AskResponse)
async def ask(req: AskRequest, request: Request) -> AskResponse:
    runtime = _runtime(request)
    thread_id = req.thread_id or uuid.uuid4().hex

    history = await _load_history(runtime, thread_id)
    result = await runtime.graph.ainvoke(
        new_state(req.question, thread_id, req.top_k, history),
        config=_config(thread_id),
    )
    return to_response(thread_id, dict(result))


@router.post("/ask/stream")
async def ask_stream(req: AskRequest, request: Request) -> EventSourceResponse:
    runtime = _runtime(request)
    thread_id = req.thread_id or uuid.uuid4().hex
    # 流式端点必须走逐 token 生成
    history = await _load_history(runtime, thread_id)
    runtime.with_streaming(True)

    return EventSourceResponse(
        _stream_events(runtime, thread_id, req.question, resume=False, history=history),
        headers={"X-Thread-Id": thread_id},
    )


@router.get("/code", response_model=CodeResponse)
async def get_code(path: str, start: int, end: int, request: Request) -> CodeResponse:
    """按引用区间返回源码。

    用户点引用的真实诉求是"看那段代码"，只给 `path:start-end` 等于让他自己去仓库里翻。
    这里直接读文件返回，比查 chunk store 更准——引用可能落在 chunk 边界之外。
    """
    runtime = _runtime(request)
    root = runtime.repo_root.resolve()
    target = (root / path).resolve()

    # 路径来自模型输出，必须挡住 `../` 之类的穿越。
    # 用 is_relative_to 而不是字符串前缀比较：前缀比较会把 /app/vendor-evil 当成
    # /app/vendor 的子路径（"vendor" 是 "vendor-evil" 的前缀）。
    if not target.is_relative_to(root):
        raise HTTPException(status_code=400, detail="路径越界")
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"文件不存在：{path}")
    if start < 1 or end < start:
        raise HTTPException(status_code=400, detail="行号非法")

    try:
        all_lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"读取失败：{exc}") from exc

    if start > len(all_lines):
        raise HTTPException(status_code=400, detail=f"起始行超出文件（共 {len(all_lines)} 行）")

    end = min(end, len(all_lines))
    excerpt = all_lines[start - 1 : end]
    return CodeResponse(
        path=path,
        start=start,
        end=end,
        code="\n".join(excerpt),
        line_count=len(excerpt),
        file_lines=len(all_lines),
    )


@router.get("/ask/state/{thread_id}", response_model=StateResponse)
async def ask_state(thread_id: str, request: Request) -> StateResponse:
    runtime = _runtime(request)
    snapshot = await runtime.graph.aget_state(_config(thread_id))
    values = dict(snapshot.values or {})
    next_nodes = list(snapshot.next or ())

    return StateResponse(
        thread_id=thread_id,
        exists=bool(values),
        finished=bool(values) and not next_nodes,
        next_nodes=next_nodes,
        stages=list(values.get("stages") or []),
        answer=values.get("answer"),
        refused=values.get("refused"),
    )


@router.post("/ask/resume/{thread_id}")
async def ask_resume(thread_id: str, request: Request) -> EventSourceResponse:
    runtime = _runtime(request)
    snapshot = await runtime.graph.aget_state(_config(thread_id))
    if not snapshot.values:
        raise HTTPException(status_code=404, detail=f"未知 thread_id: {thread_id}")
    if not snapshot.next:
        raise HTTPException(status_code=409, detail="该会话已完成，无需恢复")

    runtime.with_streaming(True)
    return EventSourceResponse(
        _stream_events(runtime, thread_id, None, resume=True),
        headers={"X-Thread-Id": thread_id},
    )
