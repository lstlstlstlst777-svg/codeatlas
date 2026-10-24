"""提示词：引用格式契约与重写指令。

设计约束（都来自"程序要能机械校验"这个前提）：
- 引用必须写成 `path:start-end`，且**只能引用给定证据里出现过的行**；
- 没有依据时必须输出 `[REFUSE]`，不能编；
- 重写时把**允许区间清单**直接注入，比"请你重新核对一下"有效得多——
  后者模型只会换一种说法继续编。
"""

from __future__ import annotations

from codeatlas.generation.query import dialog_messages
from codeatlas.indexing.types import Chunk

PROMPT_VERSION = "prompt-v1"

SYSTEM_PROMPT = """\
你是一个 C 代码库检索助手。用户会问关于一个 C 项目的问题，你会拿到若干代码片段作为证据。

铁律（违反即为错误回答）：
1. 只能基于给定的证据回答，不要使用你对该项目的一般印象。
2. 每一个事实性结论后面都必须紧跟引用，格式严格为 `路径:起始行-结束行`，
   例如 `src/server.c:1234-1256`。路径与行号必须与证据里的完全一致。
3. 引用区间必须落在某一条证据的区间之内，不能超出，也不要跨越两条证据拼接。
4. 如果证据不足以回答问题，只输出 `[REFUSE]` 加一句简短原因，不要猜测、不要给通用建议。
5. 用中文回答，简洁，直接说结论与位置；不要复述整段代码。

回答格式示例：
`initServer` 创建事件循环并初始化服务器状态 (src/server.c:2558-2677)。
它先调用 `aeCreateEventLoop` 建立事件驱动层 (src/server.c:2580-2589)。
"""

REFUSE_TOKEN = "[REFUSE]"
REFUSE_ANSWER = "该仓库中未找到相关实现。"


def render_evidence(evidence: list[Chunk], *, max_chars: int = 4000) -> str:
    """把证据渲染成带行号锚点的文本块。

    每条证据前标注 `path:start-end`，与其内部代码的相对行号对应关系写清楚，
    这样模型引用时能直接对上，而不是自己数行。
    """
    blocks: list[str] = []
    for index, chunk in enumerate(evidence, start=1):
        body = chunk.code
        if len(body) > max_chars:
            body = body[:max_chars] + "\n... (truncated)"
        symbol = f" {chunk.symbol}" if chunk.symbol else ""
        blocks.append(
            f"[证据 {index}] {chunk.path}:{chunk.start_line}-{chunk.end_line}"
            f"（{chunk.node_type.value}{symbol}）\n```c\n{body}\n```"
        )
    return "\n\n".join(blocks)


def build_messages(
    question: str,
    evidence: list[Chunk],
    history: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    user = (
        f"问题：{question}\n\n"
        f"以下是从代码库检索到的证据（共 {len(evidence)} 条）：\n\n"
        f"{render_evidence(evidence)}\n\n"
        "请基于这些证据回答，每个结论都要带 `路径:行号-行号` 引用。"
        "证据不足时只输出 [REFUSE]。"
    )
    dialog = dialog_messages(history)
    if dialog:
        # 有历史时必须加这条：模型很容易顺手复用上一轮的引用，
        # 而那一轮的证据不在本轮里，校验会全部失败（表现为无谓的重写）。
        user += "\n\n注意：历史对话仅供理解指代（如「它」指什么），本轮的引用只能来自上面的证据。"
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        *dialog,
        {"role": "user", "content": user},
    ]


def build_repair_messages(
    question: str,
    evidence: list[Chunk],
    previous_answer: str,
    problems: list[str],
    allowed_ranges: list[str],
) -> list[dict[str, str]]:
    """定向重写：把"错在哪"和"只能引用这些区间"都摆出来。"""
    problem_text = "\n".join(f"- {item}" for item in problems) or "- 引用无法校验"
    range_text = "\n".join(f"- {item}" for item in allowed_ranges) or "- （无可用区间）"
    user = (
        f"问题：{question}\n\n"
        f"你上一版回答的引用校验没通过：\n{problem_text}\n\n"
        f"上一版回答：\n{previous_answer}\n\n"
        f"**只能**引用下面这些区间（它们来自本次证据，已逐行核对）：\n{range_text}\n\n"
        "请重写回答。要求：\n"
        "1. 每条结论的引用必须严格落在上面任一区间内；\n"
        "2. 如果某条结论无法用上述区间支撑，直接删掉这句话，不要换个说法保留；\n"
        "3. 全部结论都无法支撑时，只输出 [REFUSE]。"
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]
