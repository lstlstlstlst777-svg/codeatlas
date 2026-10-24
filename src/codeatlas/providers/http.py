"""共用 HTTP 工具：限流、退避重试、错误归一化。

硅基流动的错误码会直接决定排查方向（30001 余额不足 / 30014 token 无效 / 429 限流），
所以这里必须把 code 与 message 原样带出来，不能只抛一个 HTTPStatusError。
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

# 免费档的两类 429：
# - 普通的并发/请求数超限，等几秒就好；
# - `50602 TPM limit reached` 是**每分钟 token 配额**，窗口是 60 秒，
#   退避必须足够长，否则 4 次重试全落在同一个窗口里，等于白等。
TPM_CODES = frozenset({50602})
TPM_BACKOFF_START = 12.0
TPM_BACKOFF_MAX = 65.0


class ProviderError(RuntimeError):
    def __init__(self, slot: str, code: object, message: str, status: int | None = None) -> None:
        self.slot = slot
        self.code = code
        self.message = message
        self.status = status
        super().__init__(f"[{slot}] code={code} status={status} message={message}")

    @property
    def is_rate_limit(self) -> bool:
        return self.status == 429

    @property
    def is_tpm_limit(self) -> bool:
        return self.status == 429 and (
            str(self.code) in {str(c) for c in TPM_CODES} or "TPM" in self.message.upper()
        )

    @property
    def is_fatal(self) -> bool:
        """重试没有意义的错误：密钥、余额、参数。"""
        return self.status in {400, 401, 403, 404, 422} and not self.is_rate_limit


def _backoff_seconds(status: int, body: dict[str, Any] | None, attempt: int) -> float:
    """按错误类型给出等待时长。attempt 从 0 开始。"""
    code = body.get("code") if isinstance(body, dict) else None
    message = str(body.get("message", "")) if isinstance(body, dict) else ""
    is_tpm = str(code) in {str(c) for c in TPM_CODES} or "TPM" in message.upper()

    if status == 429 and is_tpm:
        return min(TPM_BACKOFF_START * (2**attempt), TPM_BACKOFF_MAX)

    retry_after = body.get("retry_after") if isinstance(body, dict) else None
    if isinstance(retry_after, int | float) and retry_after > 0:
        return min(float(retry_after), 30.0)

    return min(0.6 * (2**attempt), 8.0)


async def post_json(
    client: httpx.AsyncClient,
    path: str,
    payload: dict[str, Any],
    *,
    slot: str,
    attempts: int = 4,
    base_delay: float = 0.6,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """带退避的 POST，仅对 429/5xx/网络异常重试。

    `sleep` 可注入，测试里传一个记录器即可验证"等了多久"而不用真等。
    """
    wait = sleep or asyncio.sleep
    last_error: Exception | None = None

    for attempt in range(attempts):
        try:
            response = await client.post(path, json=payload)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            last_error = exc
            if attempt == attempts - 1:
                raise ProviderError(slot, "network", str(exc)) from exc
            await wait(min(base_delay * (2**attempt), 8.0) + random.random() * 0.2)
            continue

        if response.status_code >= 400:
            code: object = response.status_code
            message = response.text[:400]
            body: dict[str, Any] | None = None
            try:
                parsed = response.json()
            except ValueError:
                parsed = None
            if isinstance(parsed, dict):
                body = parsed
                code = body.get("code", code)
                message = str(body.get("message", message))

            if response.status_code in RETRYABLE_STATUS and attempt < attempts - 1:
                await wait(
                    _backoff_seconds(response.status_code, body, attempt) + random.random() * 0.2
                )
                continue

            raise ProviderError(slot, code, message, response.status_code)

        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError(slot, "bad_json", response.text[:200]) from exc

    raise ProviderError(slot, "retry_exhausted", str(last_error))
