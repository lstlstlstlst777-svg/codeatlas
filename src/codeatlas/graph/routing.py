"""条件边：图的全部"决策"都在这里，节点本身不藏分支。

这么拆的原因：分支逻辑是这个系统最需要被单独测试的部分
（拒答该不该触发、重写该不该再来一次、何时放弃），
藏在节点内部就只能靠跑全图来验证，慢且难定位。
"""

from __future__ import annotations

from codeatlas.graph.state import QAState

MAX_ATTEMPTS = 2


def route_after_gate(state: QAState) -> str:
    """拒答门后：直接拒答，还是进生成。"""
    if state.get("refused"):
        return "refuse"
    if not (state.get("evidence_ids") or []):
        return "refuse"
    return "generate"


def route_after_validate(state: QAState) -> str:
    """校验后：通过就收尾；失败且还有重试预算就重写；预算用完就降级收尾。"""
    failed = int(state.get("citation_total") or 0) - int(state.get("citation_valid") or 0)
    if failed <= 0:
        return "finalize"

    attempts = int(state.get("attempts") or 0)
    if attempts >= MAX_ATTEMPTS:
        return "finalize"  # 带着失败进来，finalize 会删掉无效句子

    return "repair"


def route_after_generate(state: QAState) -> str:
    """模型自己说 [REFUSE] 就不必再校验。"""
    if state.get("refused"):
        return "refuse"
    return "validate"
