"""依赖钉版本的解析与比对（ISSUE-002 的守卫逻辑本身也要被测）。"""

from __future__ import annotations

from pathlib import Path

from codeatlas import deps


def test_parse_pins_splits_runtime_and_dev(tmp_path: Path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        """
[project]
dependencies = [
    "fastapi==0.157.0",
    "uvicorn[standard]==0.54.0",
    "qdrant-client==1.19.1",
    "some-pkg>=1.0,<2",
]

[project.optional-dependencies]
dev = ["pytest==8.4.2"]
""",
        encoding="utf-8",
    )

    runtime, dev = deps.parse_pins(pyproject)

    assert runtime["fastapi"] == "0.157.0"
    # extras 必须被剥掉，否则 version("uvicorn[standard]") 查不到
    assert runtime["uvicorn"] == "0.54.0"
    assert runtime["qdrant-client"] == "1.19.1"
    # 范围约束不算钉死
    assert "some-pkg" not in runtime
    # dev 单独一组：容器不装 dev，不能拿它判失败
    assert dev == {"pytest": "8.4.2"}


def test_find_unpinned_flags_ranges(tmp_path: Path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        """
[project]
dependencies = ["fastapi==0.157.0", "redis>=5.0,<7"]
""",
        encoding="utf-8",
    )

    assert deps._find_unpinned(pyproject) == ["redis>=5.0,<7"]


def test_normalise_is_pep503():
    assert deps._normalise("Qdrant_Client") == "qdrant-client"
    assert deps._normalise("tree.sitter") == "tree-sitter"
    assert deps._normalise("tree-sitter") == "tree-sitter"


def test_compare_reports_drift_and_missing():
    drift, missing = deps._compare(
        {"qdrant-client": "0.0.1", "definitely-not-installed-xyz": "1.0"}
    )

    assert drift and drift[0][0] == "qdrant-client"
    assert missing == ["definitely-not-installed-xyz"]


def test_repo_pyproject_is_fully_pinned():
    """真实仓库的 pyproject 不允许再出现范围约束——这条挂了说明有人把版本写法改回去了。"""
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    assert deps._find_unpinned(pyproject) == []

    runtime, dev = deps.parse_pins(pyproject)
    # 三个关键包决定入库数据与 HTTP 语义，缺一个就失去守卫意义
    for name in deps.CRITICAL:
        assert name in runtime, f"{name} must stay pinned"
    assert "pytest" in dev


def test_repo_deps_are_consistent_now():
    """宿主环境当前必须 PASS——如果这条挂了，说明 pyproject 改了但没 `make install`。"""
    assert deps.check() == 0
