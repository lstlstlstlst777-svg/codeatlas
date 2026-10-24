"""配置自检：缺东西时说人话，而不是甩一段 pydantic 栈。

新机器 clone 下来最常踩的三个坑：
1. 忘了 `cp .env.example .env`；
2. 复制了但没填 key（还是 `replace-me`）；
3. key 填了但用了 `.com` 域名（返回 30014 Token is invalid）。

这些都不该让人对着 `ValidationError: 1 validation error for Settings` 猜。
"""

from __future__ import annotations

from pathlib import Path

PLACEHOLDERS = frozenset({"replace-me", "your-key", "sk-xxx", "", "changeme"})

ENV_HINT = """\
缺少配置。请按下面三步：

  1. cp .env.example .env
  2. 编辑 .env，把 LLM_API_KEY / EMB_API_KEY / RR_API_KEY 填成真实的 SiliconFlow key
  3. 确认 BASE_URL 是 https://api.siliconflow.cn/v1（.com 域名会返回 30014 Token is invalid）

当前工作目录：{cwd}
"""


class ConfigSetupError(RuntimeError):
    """配置缺失或明显填错，附可操作提示。"""


def check_env_file(cwd: Path | None = None) -> None:
    """只做"有没有"的检查，不读 key 内容（避免把密钥带进异常信息）。"""
    base = Path(cwd) if cwd else Path.cwd()
    env_file = base / ".env"
    if env_file.is_file():
        return
    example = base / ".env.example"
    message = ENV_HINT.format(cwd=base)
    if example.is_file():
        message += f"\n（{example.name} 存在，直接复制即可）"
    raise ConfigSetupError(message)


def looks_like_placeholder(value: str) -> bool:
    return value.strip().lower() in PLACEHOLDERS


def check_api_keys(settings, *, allow_placeholder: bool = False) -> list[str]:
    """返回问题列表；空列表表示没问题。

    `provider_mode=fake` 时不做检查——CI 与单测本来就不联网。
    """
    if getattr(settings, "provider_mode", "live") == "fake":
        return []

    problems: list[str] = []
    slots = (
        ("LLM", settings.llm_api_key, settings.llm_base_url),
        ("EMB", settings.emb_api_key, settings.emb_base_url),
        ("RR", settings.rr_api_key, settings.rr_base_url),
    )
    for name, key, base_url in slots:
        if not key:
            problems.append(f"{name}_API_KEY 为空")
        elif not allow_placeholder and looks_like_placeholder(key):
            problems.append(f"{name}_API_KEY 仍是占位符（{key[:12]}…）")
        if ".com" in (base_url or "") and "siliconflow" in (base_url or ""):
            problems.append(
                f"{name}_BASE_URL 用了 {base_url}，国内站应为 https://api.siliconflow.cn/v1"
            )
    return problems


def require_usable(settings) -> None:
    """需要在真实调用前调用一次；不满足就抛带提示的异常。"""
    problems = check_api_keys(settings)
    if problems:
        raise ConfigSetupError(
            "配置不完整：\n  - "
            + "\n  - ".join(problems)
            + "\n\n"
            + ENV_HINT.format(cwd=Path.cwd())
        )


def env_status(cwd: Path | None = None) -> dict[str, object]:
    """给 check-models 用的轻量状态，不打印 key 本身。"""
    base = Path(cwd) if cwd else Path.cwd()
    env_file = base / ".env"
    return {
        "env_file_exists": env_file.is_file(),
        "env_file_path": str(env_file),
        "env_file_size": env_file.stat().st_size if env_file.is_file() else 0,
    }
