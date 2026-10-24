"""依赖版本一致性检查（ISSUE-002 的守卫）。

背景：pyproject 之前只写版本范围，容器装到 qdrant-client 1.20.0 / fastapi 0.161.1，
宿主却是 1.19.1 / 0.157.0。P4 要把 7019 个向量写进 Qdrant，客户端版本不一致会让
collection 参数与 payload 的解释出现差异——这类问题不会报错，只会让检索结果悄悄变差。

判定分两档：
- **运行时依赖**（`dependencies`）必须严格一致，缺一个或不一致都算失败——镜像里没有 dev 工具是设计，
  但运行时依赖少一个就是坏镜像。
- **开发依赖**（`dev` extra）只做"装了就必须版本对"的软检查。宿主要跑测试才需要它，
  容器不装是正常的，据此报失败只会训练人忽略报警。
"""

from __future__ import annotations

import re
import sys
import tomllib
from importlib.metadata import PackageNotFoundError, requires, version
from pathlib import Path

# 关键包：这三个直接决定"写进向量库的数据长什么样"和"HTTP 语义"。
# 任何一个不一致都必须停下来，不能靠"应该差不多"糊过去。
CRITICAL = ("qdrant-client", "fastapi", "pydantic")

_PIN = re.compile(r"^\s*([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?\s*==\s*([^\s,;]+)\s*$")


def _normalise(name: str) -> str:
    """PEP 503 归一化：Qdrant_Client / qdrant-client 视为同一个包。"""
    return re.sub(r"[-_.]+", "-", name).lower()


def _pins(specs: list[str]) -> dict[str, str]:
    pins: dict[str, str] = {}
    for spec in specs:
        match = _PIN.match(spec)
        if match:
            pins[_normalise(match.group(1))] = match.group(2)
    return pins


def parse_pins(pyproject: Path) -> tuple[dict[str, str], dict[str, str]]:
    """返回 (运行时 pins, dev pins)。两组分开，因为容器不装 dev。"""
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    project = data.get("project", {})
    runtime = _pins(list(project.get("dependencies", [])))

    dev_specs: list[str] = []
    for group in project.get("optional-dependencies", {}).values():
        dev_specs.extend(group)
    return runtime, _pins(dev_specs)


def find_pyproject(start: Path) -> Path | None:
    """从 cwd 与包路径向上找本项目（name == codeatlas）的 pyproject.toml。"""
    for anchor in (Path.cwd(), start):
        for candidate in [anchor, *anchor.parents]:
            path = candidate / "pyproject.toml"
            if path.is_file() and _is_our_project(path):
                return path
    return None


def _is_our_project(path: Path) -> bool:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return False
    return data.get("project", {}).get("name") == "codeatlas"


def pins_from_metadata() -> tuple[dict[str, str], dict[str, str]]:
    """没有 pyproject 时，从已安装包的 Requires-Dist 反推（容器兜底）。"""
    try:
        specs = requires("codeatlas") or []
    except PackageNotFoundError:
        return {}, {}

    runtime_specs: list[str] = []
    dev_specs: list[str] = []
    for spec in specs:
        base, _, marker = spec.partition(";")
        target = dev_specs if "extra ==" in marker else runtime_specs
        target.append(base.strip())
    return _pins(runtime_specs), _pins(dev_specs)


def check(start: Path | None = None) -> int:
    pyproject = find_pyproject(start or Path(__file__).resolve())
    if pyproject is not None:
        runtime, dev = parse_pins(pyproject)
        source = str(pyproject)
        unpinned = _find_unpinned(pyproject)
    else:
        runtime, dev = pins_from_metadata()
        source = "installed package metadata (codeatlas)"
        unpinned = []

    if not runtime:
        print("no pinned runtime dependencies found (neither pyproject nor metadata)")
        return 2

    runtime_drift, runtime_missing = _compare(runtime)
    dev_drift, dev_missing = _compare(dev)

    print(f"source    : {source}")
    print(f"python    : {sys.version.split()[0]}  ({sys.executable})")
    print(f"pinned    : {len(runtime)} runtime" + (f" + {len(dev)} dev" if dev else ""))

    if unpinned:
        print(f"\nunpinned in pyproject (ISSUE-002): {len(unpinned)}")
        for spec in unpinned:
            print(f"  ! {spec}")

    if runtime_missing:
        print(f"\nmissing runtime ({len(runtime_missing)}):")
        for name in runtime_missing:
            print(f"  ? {name} — not installed, image is broken")

    if runtime_drift:
        print(f"\ndrift runtime ({len(runtime_drift)}):")
        for name, expected, actual in runtime_drift:
            mark = "CRITICAL" if name in CRITICAL else "warn"
            print(f"  x {name}: expected {expected}, installed {actual}  [{mark}]")

    if dev_drift:
        print(f"\ndrift dev ({len(dev_drift)}), non-fatal:")
        for name, expected, actual in dev_drift:
            print(f"  x {name}: expected {expected}, installed {actual}")

    if dev_missing:
        print(f"\ndev tooling absent ({len(dev_missing)}): {', '.join(sorted(dev_missing))}")
        print("  (normal inside the runtime image — only `make install` provides these)")

    critical_drift = [d for d in runtime_drift if d[0] in CRITICAL]
    if unpinned or runtime_missing or runtime_drift:
        if critical_drift:
            print("\nFAILED — critical package drift; rebuild the image before indexing")
        else:
            print("\nFAILED — runtime dependencies out of sync")
        return 2

    if dev_missing and not dev_drift:
        print("\nPASS — all runtime pins match (dev tooling absent, expected in image)")
    elif dev_drift:
        print("\nPASS — all runtime pins match (dev versions drifted, non-fatal)")
    else:
        print("\nPASS — all runtime pins match (dev in sync)")
    return 0


def _compare(pins: dict[str, str]) -> tuple[list[tuple[str, str, str]], list[str]]:
    drift: list[tuple[str, str, str]] = []
    missing: list[str] = []
    for name, expected in sorted(pins.items()):
        try:
            actual = version(name)
        except PackageNotFoundError:
            missing.append(name)
            continue
        if actual != expected:
            drift.append((name, expected, actual))
    return drift, missing


def _find_unpinned(pyproject: Path) -> list[str]:
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    project = data.get("project", {})
    raw: list[str] = list(project.get("dependencies", []))
    for group in project.get("optional-dependencies", {}).values():
        raw.extend(group)
    return [spec for spec in raw if not _PIN.match(spec)]


def main(argv: list[str] | None = None) -> int:
    return check()
