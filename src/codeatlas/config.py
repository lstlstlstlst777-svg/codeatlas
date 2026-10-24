"""配置加载：`config/default.yaml` 是检索参数的唯一真源（ISSUE-011）。

为什么不能只靠 pydantic-settings：

    pydantic-settings 的优先级是 init kwargs > 环境变量 > .env > 字段默认值。
    如果参数散落在 `.env` 和 `settings.py` 默认值里，sweep 时改 yaml 不会生效，
    评测跑出来的数字与报告里写的参数对不上——这是评测类项目最致命的错误，
    而且不会报错，只会让人对着表格怀疑人生。

约定（写死在这里，不要再改）：

    1. 切分 / 检索 / 融合 / 阈值 / 模型 ID → yaml，进版本控制，sweep 只改 yaml。
    2. 密钥、base_url、Qdrant/Redis 地址 → `.env`，因为换机器就要变。
    3. yaml 里没写的字段才由 `.env` 填；两者都有时 **yaml 赢**（init kwargs 优先级最高）。
    4. yaml 里出现映射表之外的键 → 直接报错。沉默地忽略一个拼错的键，
       等于让人以为参数改了其实没改。

`include` / `exclude` 是 glob 列表，交给 scanner 用，不参与指纹（它们决定索引范围，
改了必须重建索引，而不是复用缓存）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from codeatlas.settings import Settings

DEFAULT_CONFIG = "config/default.yaml"
EXPERIMENT_DIR = "config/experiments"

# yaml 路径 → Settings 字段。显式列出来，一是不让拼错的键静默失效，
# 二是这份表本身就是"哪些参数可调"的清单。
_SCALARS: dict[str, str] = {
    "indexing.repo": "repo",
    "indexing.chunker_version": "chunker_version",
    "indexing.max_chunk_lines": "max_chunk_lines",
    "indexing.max_split_depth": "max_split_depth",
    "retrieval.dense_limit": "dense_limit",
    "retrieval.bm25_limit": "bm25_limit",
    "retrieval.dense_weight": "w_dense",
    "retrieval.bm25_weight": "w_bm25",
    "retrieval.rrf_k": "rrf_k",
    "retrieval.rerank_window": "rerank_window",
    "retrieval.rerank_max_doc_chars": "rerank_max_doc_chars",
    "retrieval.rerank_timeout": "rerank_timeout",
    "retrieval.top_k": "top_k",
    "bm25.k1": "bm25_k1",
    "bm25.b": "bm25_b",
    "bm25.symbol_boost": "bm25_symbol_boost",
    "gate.refuse_tau": "refuse_tau",
    "gate.max_repair_attempts": "max_repair_attempts",
    "models.llm": "llm_model",
    "models.embedding": "emb_model",
    "models.rerank": "rr_model",
}

_STR_LISTS: dict[str, str] = {
    "indexing.include": "include_globs",
    "indexing.exclude": "exclude_globs",
}


class ConfigError(RuntimeError):
    """配置不合法：未知键、类型不符、实验文件缺失。"""


def load_config(
    config_path: str | Path | None = None,
    experiment: str | Path | None = None,
    root: str | Path | None = None,
) -> Settings:
    """读 default.yaml（可选叠加 experiment），返回 Settings。

    `experiment` 给了文件名（如 `e2_hybrid`）或路径时，其值覆盖 default；
    实验文件只需写要改的项，不用完整复制一份。
    """
    base = Path(root) if root else Path.cwd()
    default_file = Path(config_path) if config_path else base / DEFAULT_CONFIG

    merged: dict[str, Any] = {}
    for path in _resolve_layers(default_file, experiment, base):
        flat = _flatten(_read_yaml(path), path)
        for dotted in flat:
            if dotted not in _SCALARS and dotted not in _STR_LISTS:
                raise ConfigError(
                    f"{path}: 未知配置项 '{dotted}'。"
                    f"可用的键见 codeatlas/config.py 的 _SCALARS / _STR_LISTS。"
                )
        merged.update(flat)

    kwargs: dict[str, Any] = {}
    for dotted, field in _SCALARS.items():
        if dotted in merged:
            kwargs[field] = merged[dotted]
    for dotted, field in _STR_LISTS.items():
        if dotted in merged:
            kwargs[field] = merged[dotted]

    # init kwargs 优先级高于环境变量与 .env，所以这里一旦传了值，.env 就覆盖不了。
    return Settings(**kwargs)


def _resolve_layers(default_file: Path, experiment: str | Path | None, base: Path) -> list[Path]:
    if not default_file.is_file():
        raise ConfigError(f"缺少默认配置：{default_file.resolve()}")
    layers = [default_file]
    if experiment is None:
        return layers

    candidate = Path(experiment)
    if not candidate.is_file():
        candidate = base / EXPERIMENT_DIR / f"{experiment}.yaml"
    if not candidate.is_file():
        candidate = base / EXPERIMENT_DIR / str(experiment)
    if not candidate.is_file():
        raise ConfigError(f"实验配置不存在：{experiment}（找过 {EXPERIMENT_DIR}/）")
    layers.append(candidate)
    return layers


def _read_yaml(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: 顶层必须是映射（mapping）")
    return raw


def _flatten(data: dict[str, Any], path: Path) -> dict[str, Any]:
    """两层就够了（section.key），多余层级视为配置写错。"""
    flat: dict[str, Any] = {}
    for section, values in data.items():
        if not isinstance(values, dict):
            raise ConfigError(f"{path}: '{section}' 必须是映射，实际是 {type(values).__name__}")
        for key, value in values.items():
            flat[f"{section}.{key}"] = value
    return flat


def resolve_repo(settings: Settings, root: str | Path | None = None) -> Path:
    """把 yaml 里的相对 repo 路径解析成绝对路径。"""
    base = Path(root) if root else Path.cwd()
    repo = Path(settings.repo)
    return repo if repo.is_absolute() else base / repo
