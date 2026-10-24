"""代码感知分词：BM25 对 C 代码有效的前提。

`zmalloc_used_memory` 会产出完整标识符（计两次，强化精确匹配）+ 三个子词；
`zmallocUsedMemory` 额外做 camelCase 拆分。停用词只去控制流与英文虚词，
类型名（int/void/struct/static）保留——它们的 IDF 自然趋零，删掉反而丢信号。
"""

from __future__ import annotations

import re

IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
CAMEL_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[0-9]+")
NUMBER_RE = re.compile(r"\b\d+\b")

STOPWORDS = frozenset(
    {
        "if",
        "else",
        "for",
        "while",
        "return",
        "switch",
        "case",
        "break",
        "continue",
        "goto",
        "do",
        "default",
        "sizeof",
        "the",
        "a",
        "an",
        "of",
        "to",
        "in",
        "on",
        "is",
        "are",
        "how",
        "what",
        "where",
        "which",
        "does",
        "and",
        "or",
        "with",
        "by",
    }
)


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for match in IDENT_RE.finditer(text):
        word = match.group(0)
        lowered = word.lower()
        if lowered not in STOPWORDS:
            tokens.append(lowered)
            tokens.append(lowered)  # 完整标识符权重加倍
        camel_parts = CAMEL_RE.findall(word)
        if len(camel_parts) > 1:
            for part in camel_parts:
                piece = part.lower()
                if piece != lowered and piece not in STOPWORDS:
                    tokens.append(piece)
        if "_" in word:
            for part in word.split("_"):
                piece = part.lower()
                if piece and piece != lowered and piece not in STOPWORDS:
                    tokens.append(piece)
    tokens.extend(NUMBER_RE.findall(text))
    return tokens
