"""多轮上下文：把"那它的复杂度是多少"这类追问还原成可检索的完整问题。

为什么不直接上 LLM 改写：
- 每次改写多一次模型调用（实测 30B 在 700ms~1s），而绝大多数问题本身就是完整的；
- 追问的特征很明确（以代词开头、或极短），用确定性规则先筛一遍，
  只有真的需要时才拼上一轮问题。这样**零额外延迟、零额外成本**，
  也不会因为改写模型抽风把好问题改坏。

代价是规则只能处理"指代上一轮"这一种最常见的情况（连续追问三层以上会失真），
对代码问答这个场景够用。真需要更强改写时，替换 `needs_context` 即可，
调用方不用改。
"""

from __future__ import annotations

import re

# 句首出现即视为指代上文（只检查开头 4 个字符，避免"这个项目里 ACL 怎么实现"
# 这类本身完整的问题被误判——它虽然含"这个"，但主语是 ACL）
_ANAPHORA_HEAD = (
    "它",
    "他",
    "她",
    "这个",
    "那个",
    "这些",
    "那些",
    "这",
    "那",
    "该",
    "此",
    "其",
    "上面",
    "上述",
    "前面",
    "刚才",
    "之前",
    "这里",
    "那里",
    "继续",
    "还有",
)

_ANAPHORA_EN = frozenset(
    {"it", "its", "they", "them", "this", "that", "these", "those", "same", "both"}
)

# 英文追问常见 "what about its X" / "how about that"：首词是疑问/连接词，
# 真正的指代在第 2~3 个词。只看首词会漏掉这类。
_FOLLOW_UP_OPENERS_EN = frozenset(
    {"what", "how", "why", "and", "but", "so", "then", "also", "about"}
)

# 少于这个长度基本不可能是独立问题（"为什么" "展开说说"）
_MIN_STANDALONE_CHARS = 10


def needs_context(question: str) -> bool:
    """这个问题离开上文还能不能独立成立。"""
    q = (question or "").strip()
    if not q:
        return False
    if len(q) <= _MIN_STANDALONE_CHARS:
        return True
    if q.startswith(_ANAPHORA_HEAD):
        return True
    words = re.findall(r"[a-z']+", q.lower())
    if not words:
        return False
    if words[0] in _ANAPHORA_EN:
        return True
    # "what about its complexity"：首词是疑问/连接词时，看前三个词里有没有指代
    return words[0] in _FOLLOW_UP_OPENERS_EN and any(w in _ANAPHORA_EN for w in words[1:3])


def contextualize(question: str, history: list[dict[str, str]] | None) -> str:
    """把追问拼成一个完整查询，用于检索。

    只拼**上一个问题**，不拼答案：答案里全是函数名和行号，
    拼进去会让 BM25 被这些词主导，反而找不到新一轮要的东西。
    """
    q = (question or "").strip()
    if not history or not needs_context(q):
        return q
    previous = (history[-1].get("question") or "").strip()
    if not previous or previous == q:
        return q
    return f"{previous} {q}"


def dialog_messages(history: list[dict[str, str]] | None, limit: int = 2) -> list[dict[str, str]]:
    """把历史轮次转成 chat messages。

    答案截断到 400 字符：历史只用来理解"它指什么"，不需要完整答案，
    带上全文会让每轮 prompt 线性膨胀。
    """
    if not history:
        return []
    messages: list[dict[str, str]] = []
    for turn in history[-limit:]:
        asked = (turn.get("question") or "").strip()
        answered = (turn.get("answer") or "").strip()
        if not asked:
            continue
        messages.append({"role": "user", "content": asked})
        if answered:
            messages.append({"role": "assistant", "content": answered[:400]})
    return messages
