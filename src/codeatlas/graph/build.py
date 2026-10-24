"""图装配与编译。

拓扑：

    retrieve → gate ─┬─→ generate → validate ─┬─→ finalize → END
                     │                        └─→ repair ──┐
                     └─→ refuse → END                      │
                                                           │
                     repair ─────────────────────────────→ validate

`recursion_limit=12` 是硬护栏：任何路由写错都不该让一次请求无限跑下去。
按最坏路径数一遍（retrieve 1 + gate 1 + generate 1 + validate/repair 各 5 次 + finalize 1）
12 步够用且留有余量。
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from codeatlas.graph.nodes import GraphDeps, QANodes
from codeatlas.graph.routing import route_after_gate, route_after_generate, route_after_validate
from codeatlas.graph.state import QAState

RECURSION_LIMIT = 12


def build_graph(deps: GraphDeps, checkpointer: Any = None):
    nodes = QANodes(deps)

    graph = StateGraph(QAState)
    graph.add_node("retrieve", nodes.retrieve)
    graph.add_node("gate", nodes.gate)
    graph.add_node("generate", nodes.generate)
    graph.add_node("validate", nodes.validate)
    graph.add_node("repair", nodes.repair)
    graph.add_node("finalize", nodes.finalize)
    graph.add_node("refuse", nodes.refuse)

    graph.add_edge(START, "retrieve")
    graph.add_edge("retrieve", "gate")

    graph.add_conditional_edges(
        "gate",
        route_after_gate,
        {"generate": "generate", "refuse": "refuse"},
    )
    graph.add_conditional_edges(
        "generate",
        route_after_generate,
        {"validate": "validate", "refuse": "refuse"},
    )
    graph.add_conditional_edges(
        "validate",
        route_after_validate,
        {"finalize": "finalize", "repair": "repair"},
    )
    graph.add_edge("repair", "validate")
    graph.add_edge("finalize", END)
    graph.add_edge("refuse", END)

    return graph.compile(checkpointer=checkpointer)


async def run_graph(
    compiled,
    question: str,
    *,
    request_id: str = "local",
    thread_id: str | None = None,
    history: list[dict[str, str]] | None = None,
) -> QAState:
    from codeatlas.graph.state import new_state

    config = {
        "recursion_limit": RECURSION_LIMIT,
        "configurable": {"thread_id": thread_id or request_id},
    }
    result = await compiled.ainvoke(new_state(question, request_id, history=history), config=config)
    return result  # type: ignore[return-value]
