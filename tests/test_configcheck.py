"""配置自检：错误信息必须能指导动作，而不是甩栈。

考察的是"新机器上第一次跑"的体验——这正是最容易让人放弃的时刻。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codeatlas.configcheck import (
    ConfigSetupError,
    check_api_keys,
    check_env_file,
    env_status,
    looks_like_placeholder,
    require_usable,
)
from codeatlas.settings import Settings


def test_missing_env_file_gives_actionable_error(tmp_path: Path):
    with pytest.raises(ConfigSetupError) as excinfo:
        check_env_file(tmp_path)

    message = str(excinfo.value)
    assert "cp .env.example .env" in message
    assert str(tmp_path) in message


def test_missing_env_file_mentions_example_when_present(tmp_path: Path):
    (tmp_path / ".env.example").write_text("LLM_API_KEY=", encoding="utf-8")

    with pytest.raises(ConfigSetupError, match=r"\.env\.example 存在"):
        check_env_file(tmp_path)


def test_existing_env_file_passes(tmp_path: Path):
    (tmp_path / ".env").write_text("LLM_API_KEY=sk-real", encoding="utf-8")
    check_env_file(tmp_path)  # 不抛即通过


def test_fake_mode_skips_key_checks():
    settings = Settings(provider_mode="fake", llm_api_key="", emb_api_key="", rr_api_key="")
    assert check_api_keys(settings) == []


def test_empty_keys_are_reported():
    settings = Settings(provider_mode="live", llm_api_key="", emb_api_key="", rr_api_key="")

    problems = check_api_keys(settings)

    assert len(problems) == 3
    assert all("为空" in p for p in problems)


def test_placeholder_keys_are_reported():
    settings = Settings(
        provider_mode="live",
        llm_api_key="replace-me",
        emb_api_key="sk-real",
        rr_api_key="sk-real",
    )

    problems = check_api_keys(settings)

    assert len(problems) == 1
    assert "占位符" in problems[0]


def test_dot_com_base_url_is_flagged():
    """`.com` 是国内站的经典坑，返回 30014，值得在启动前就拦住。"""
    settings = Settings(
        provider_mode="live",
        llm_api_key="sk-real",
        emb_api_key="sk-real",
        rr_api_key="sk-real",
        llm_base_url="https://api.siliconflow.com/v1",
    )

    problems = check_api_keys(settings)

    assert any("api.siliconflow.cn" in p for p in problems)


def test_require_usable_raises_with_all_problems():
    settings = Settings(provider_mode="live", llm_api_key="", emb_api_key="", rr_api_key="")

    with pytest.raises(ConfigSetupError) as excinfo:
        require_usable(settings)

    message = str(excinfo.value)
    assert message.count("为空") == 3
    assert "cp .env.example .env" in message


def test_require_usable_passes_for_good_config():
    settings = Settings(
        provider_mode="live",
        llm_api_key="sk-aaaaaaaaaaaa",
        emb_api_key="sk-aaaaaaaaaaaa",
        rr_api_key="sk-aaaaaaaaaaaa",
    )
    require_usable(settings)


@pytest.mark.parametrize("value", ["replace-me", "REPLACE-ME", " your-key ", "", "sk-xxx"])
def test_placeholder_detection(value):
    assert looks_like_placeholder(value)


def test_real_key_is_not_placeholder():
    assert not looks_like_placeholder("sk-ljtnotvkgluinlxpvmid")


def test_env_status_does_not_leak_secrets(tmp_path: Path):
    (tmp_path / ".env").write_text("LLM_API_KEY=sk-secret-value\n", encoding="utf-8")

    status = env_status(tmp_path)

    assert status["env_file_exists"] is True
    assert "sk-secret-value" not in str(status)
