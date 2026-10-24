"""引用路径归一化：不管模型怎么胡说，都要能落到 manifest 里的同一个 key。

模型输出的引用长这样：`src/server.c:1234-1256`。但实际可能出现的变体：
- `src\\server.c`（Windows 反斜杠）
- `./src/server.c`（相对当前目录前缀）
- `E:\\CodeAtlas\\vendor\\redis-7.2.5\\src\\server.c`（绝对路径）
- `src//server.c`、`SRC/Server.C`（多余分隔符、大小写）

L1 校验如果直接字符串比较，这些都会被判 `range_out_of_file`——
而模型其实引对了，只是写法不同。误杀引用会让重写风暴白白烧掉预算。

这里的策略是**宽容归一化 + 严格判定**：路径尽量还原，但还原不出来就明确失败，
不做"猜一个最像的"这种模糊匹配（那会把错误引用放过去，比误杀更糟）。
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

# `src/server.c:1234-1256`、`src/server.c:1234`、`src/server.c` 都接受
CITATION_RE = re.compile(r"^(?P<path>.+?)(?::(?P<start>\d+)(?:\s*-\s*(?P<end>\d+))?)?$")


def normalize_path(raw: str, repo_root: str | Path | None = None) -> str:
    """把各种写法的路径压成 POSIX 风格相对路径。

    不是"尽力而为"：调用方拿到的必须是一个确定的 key。
    无法判断相对谁时，就只做分隔符与冗余段的清理。
    """
    text = (raw or "").strip().strip("\"'`")
    if not text:
        return ""

    text = text.replace("\\", "/")
    text = re.sub(r"/+", "/", text)

    if repo_root is not None:
        root = PurePosixPath(str(repo_root).replace("\\", "/").rstrip("/"))
        candidate = PurePosixPath(text)
        # 绝对路径：尝试剥掉仓库前缀（大小写不敏感，Windows 盘符大小写不一致很常见）
        root_parts = [p.lower() for p in root.parts]
        cand_parts = [p.lower() for p in candidate.parts]
        for i in range(len(cand_parts) - len(root_parts) + 1):
            if cand_parts[i : i + len(root_parts)] == root_parts:
                text = "/".join(candidate.parts[i + len(root_parts) :])
                break

    # 去掉 ./ 与 ../ 造成的冗余前导段，以及 PurePosixPath 对根目录给出的 "/"
    parts = [p for p in PurePosixPath(text).parts if p not in ("", ".", "/")]
    text = "/".join(parts)
    while text.startswith("../"):
        text = text[3:]
    return text


def parse_citation(raw: str) -> tuple[str, int | None, int | None]:
    """拆 `path:start-end`。返回的路径已归一化，行号为 1-based，缺失则为 None。"""
    match = CITATION_RE.match((raw or "").strip().strip("\"'`"))
    if not match:
        return normalize_path(raw), None, None
    path = normalize_path(match.group("path"))
    start = int(match.group("start")) if match.group("start") else None
    end = int(match.group("end")) if match.group("end") else None
    return path, start, end


def lookup_path(
    normalized: str,
    known: set[str] | dict[str, object],
) -> str | None:
    """在 manifest 的已知路径集合里找归一化后的 key。

    逐级放宽：精确 → 大小写不敏感 → 以 `/` 边界的后缀匹配（唯一命中才算）。
    后缀匹配只允许唯一解：多个文件都叫 `server.c` 时宁可失败，
    因为"引用到了错的那个文件"比"引用失败"更难发现。
    """
    if normalized in known:
        return normalized

    lowered = {str(k).lower(): str(k) for k in known}
    if normalized.lower() in lowered:
        return lowered[normalized.lower()]

    suffix = f"/{normalized.lower()}"
    hits = [original for low, original in lowered.items() if low.endswith(suffix)]
    if len(hits) == 1:
        return hits[0]
    return None


def format_citation(path: str, start_line: int, end_line: int) -> str:
    """回显给用户的标准写法。"""
    if start_line == end_line:
        return f"{normalize_path(path)}:{start_line}"
    return f"{normalize_path(path)}:{start_line}-{end_line}"
