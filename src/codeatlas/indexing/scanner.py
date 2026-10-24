"""仓库扫描：按 `indexing.include` / `indexing.exclude` 收集文件。

这两组 glob 来自 `config/default.yaml`（唯一真源）。之前是硬编码 `src/` + `.c/.h`，
yaml 里写的 exclude 根本没生效——等于把"配置项"当装饰品，这类假配置比没有配置更危险。

exclude 支持两种写法：
- `"deps/"`（尾斜杠）→ 目录前缀，任意层级命中即排除；
- `"src/*.test.c"` → 相对仓库根的 glob。
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

SOURCE_SUFFIXES = frozenset({".c", ".h"})

DEFAULT_INCLUDE = ("src/*.c", "src/*.h")
DEFAULT_EXCLUDE = ("deps/", "tests/", "modules/", "src/*.test.c")


def scan_repo(
    repo_root: Path,
    include: list[str] | tuple[str, ...] | None = None,
    exclude: list[str] | tuple[str, ...] | None = None,
) -> list[Path]:
    repo_root = Path(repo_root)
    if not repo_root.is_dir():
        raise FileNotFoundError(f"repo not found: {repo_root}")

    include_globs = list(include) if include else list(DEFAULT_INCLUDE)
    exclude_globs = list(exclude) if exclude is not None else list(DEFAULT_EXCLUDE)

    found: dict[str, Path] = {}
    for pattern in include_globs:
        for path in repo_root.glob(pattern):
            if not path.is_file() or path.suffix not in SOURCE_SUFFIXES:
                continue
            rel = path.relative_to(repo_root).as_posix()
            if _is_excluded(rel, exclude_globs):
                continue
            found[rel] = path

    if not found:
        raise FileNotFoundError(
            f"no C source matched {include_globs} under {repo_root}（exclude={exclude_globs}）"
        )
    return [found[rel] for rel in sorted(found)]


def _is_excluded(rel: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if not pattern:
            continue
        if pattern.endswith("/"):
            if f"/{pattern}" in f"/{rel}":
                return True
        elif PurePosixPath(rel).match(pattern):
            return True
    return False
