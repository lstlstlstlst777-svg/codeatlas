"""配置加载：yaml 必须是真源（ISSUE-011 的回归测试）。

这些测试存在的意义不是"跑通"，而是锁住三条容易退化的语义：
1. yaml 赢过环境变量——否则 sweep 改 yaml 但被 .env 覆盖，评测数字与参数对不上；
2. 未知键必须报错——否则拼错的键静默失效，人以为改了其实没改；
3. 切分参数进指纹——否则换切法后旧缓存污染新索引。
"""

from __future__ import annotations

import pytest

from codeatlas.config import ConfigError, load_config
from codeatlas.settings import Settings

BASE_YAML = """
indexing:
  repo: vendor/redis-7.2.5
  include: ["src/*.c"]
  exclude: ["deps/"]
  chunker_version: chunker-v1
  max_chunk_lines: 120
  max_split_depth: 2

retrieval:
  dense_limit: 50
  bm25_limit: 50
  dense_weight: 1.0
  bm25_weight: 0.75
  rrf_k: 60
  rerank_window: 30
  top_k: 5

bm25:
  k1: 1.2
  b: 0.5

gate:
  refuse_tau: null
  max_repair_attempts: 2

models:
  llm: Qwen/Qwen3-8B
  embedding: BAAI/bge-m3
  rerank: BAAI/bge-reranker-v2-m3
"""


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "default.yaml").write_text(BASE_YAML, encoding="utf-8")
    return tmp_path


def test_yaml_overrides_env(monkeypatch, repo):
    """yaml 是唯一真源：环境变量不该偷偷改掉已写进 yaml 的参数。"""
    monkeypatch.setenv("W_BM25", "0.1")
    monkeypatch.setenv("RERANK_WINDOW", "99")

    settings = load_config(root=repo)

    assert settings.w_bm25 == 0.75
    assert settings.rerank_window == 30


def test_yaml_can_express_null(repo):
    """`refuse_tau: null` 必须变成本真的 None，不能被默认值填成 0.02。
    未标定就是未标定，不许假装有值。"""
    settings = load_config(root=repo)
    assert settings.refuse_tau is None


def test_unknown_key_is_fatal(repo):
    (repo / "config" / "default.yaml").write_text(
        BASE_YAML + "\nretrieval:\n  bm25_weigth: 0.5\n", encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="bm25_weigth"):
        load_config(root=repo)


def test_experiment_layer_overrides_default(repo):
    (repo / "config" / "experiments").mkdir()
    (repo / "config" / "experiments" / "e2_hybrid.yaml").write_text(
        "retrieval:\n  bm25_weight: 1.25\n", encoding="utf-8"
    )

    settings = load_config(experiment="e2_hybrid", root=repo)

    assert settings.w_bm25 == 1.25
    # 未覆盖的项保持 default 的值
    assert settings.w_dense == 1.0


def test_missing_experiment_is_fatal(repo):
    with pytest.raises(ConfigError, match="实验配置不存在"):
        load_config(experiment="nope", root=repo)


def test_fingerprint_tracks_chunking(repo):
    """切法一变 chunk_id 语义就变，指纹必须跟着变，否则缓存会串。"""
    before = load_config(root=repo).config_fingerprint()

    text = BASE_YAML.replace("max_chunk_lines: 120", "max_chunk_lines: 80")
    (repo / "config" / "default.yaml").write_text(text, encoding="utf-8")

    assert load_config(root=repo).config_fingerprint() != before


def test_fingerprint_tracks_retrieval_weights(repo):
    before = load_config(root=repo).config_fingerprint()

    text = BASE_YAML.replace("bm25_weight: 0.75", "bm25_weight: 0.5")
    (repo / "config" / "default.yaml").write_text(text, encoding="utf-8")

    assert load_config(root=repo).config_fingerprint() != before


def test_repo_default_config_is_loadable():
    """仓库里真实的 default.yaml 必须能被加载——防止改了 yaml 忘了改映射表。"""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    settings = load_config(root=root)

    assert isinstance(settings, Settings)
    assert settings.rrf_k == 60
    assert settings.max_chunk_lines > 0
