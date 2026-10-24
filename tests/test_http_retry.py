"""HTTP 层：错误分类与重试边界。

这是全项目最容易被"顺手优化"破坏的地方：
- 把 30001（余额不足）也重试，只会让日志变脏、让 P4 的入库卡在无意义的退避里；
- 把 429 当成普通错误抛出，P4 跑 7019 条时必然中途崩。
两类错误必须走不同路径，所以要有测试盯着。
"""

from __future__ import annotations

import httpx
import pytest
import respx

from codeatlas.providers.http import RETRYABLE_STATUS, ProviderError, post_json


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url="https://api.example.com/v1/")


@respx.mock
async def test_success_returns_body():
    respx.post("https://api.example.com/v1/embeddings").mock(
        return_value=httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1]}]})
    )

    async with make_client() as client:
        body = await post_json(client, "embeddings", {"model": "m"}, slot="embedding")

    assert body["data"][0]["embedding"] == [0.1]


@respx.mock
async def test_insufficient_balance_is_not_retried():
    """30001 是账号问题，重试一万次也不会好。必须一次就抛，且带出原始 code。"""
    route = respx.post("https://api.example.com/v1/embeddings").mock(
        return_value=httpx.Response(
            403, json={"code": 30001, "message": "Sorry, your account balance is insufficient"}
        )
    )

    async with make_client() as client:
        with pytest.raises(ProviderError) as excinfo:
            await post_json(client, "embeddings", {"model": "m"}, slot="embedding", attempts=4)

    assert str(excinfo.value.code) == "30001"
    assert excinfo.value.slot == "embedding"
    assert route.call_count == 1, "30001 must not be retried"


@respx.mock
async def test_invalid_token_is_not_retried():
    """30014 是 key 错了（常见于用了 .com 域名），重试无意义。"""
    route = respx.post("https://api.example.com/v1/chat/completions").mock(
        return_value=httpx.Response(401, json={"code": 30014, "message": "Token is invalid"})
    )

    async with make_client() as client:
        with pytest.raises(ProviderError) as excinfo:
            await post_json(client, "chat/completions", {}, slot="llm", attempts=3)

    assert str(excinfo.value.code) == "30014"
    assert route.call_count == 1


@respx.mock
async def test_rate_limit_is_retried_then_succeeds():
    """429 属于可恢复错误：退避后应当成功，而不是直接失败。"""
    route = respx.post("https://api.example.com/v1/embeddings")
    route.side_effect = [
        httpx.Response(429, json={"code": 429, "message": "rate limited"}),
        httpx.Response(200, json={"data": []}),
    ]

    async with make_client() as client:
        body = await post_json(
            client, "embeddings", {"model": "m"}, slot="embedding", attempts=3, base_delay=0.0
        )

    assert body == {"data": []}
    assert route.call_count == 2


@respx.mock
async def test_rate_limit_exhausts_attempts():
    respx.post("https://api.example.com/v1/embeddings").mock(
        return_value=httpx.Response(429, json={"code": 429, "message": "rate limited"})
    )

    async with make_client() as client:
        with pytest.raises(ProviderError) as excinfo:
            await post_json(client, "embeddings", {}, slot="embedding", attempts=3, base_delay=0.0)

    assert excinfo.value.status == 429


@respx.mock
async def test_server_error_is_retried():
    route = respx.post("https://api.example.com/v1/embeddings")
    route.side_effect = [
        httpx.Response(503),
        httpx.Response(200, json={"ok": True}),
    ]

    async with make_client() as client:
        body = await post_json(
            client, "embeddings", {}, slot="embedding", attempts=3, base_delay=0.0
        )

    assert body == {"ok": True}
    assert route.call_count == 2


@respx.mock
async def test_client_error_other_than_429_fails_immediately():
    """400 参数错误：重试不会让请求变合法。"""
    route = respx.post("https://api.example.com/v1/embeddings").mock(
        return_value=httpx.Response(400, json={"code": 30011, "message": "invalid model"})
    )

    async with make_client() as client:
        with pytest.raises(ProviderError) as excinfo:
            await post_json(client, "embeddings", {}, slot="embedding", attempts=4)

    assert str(excinfo.value.code) == "30011"
    assert route.call_count == 1


@respx.mock
async def test_network_error_is_retried_then_reported():
    route = respx.post("https://api.example.com/v1/embeddings")
    route.side_effect = httpx.ConnectTimeout("boom")

    async with make_client() as client:
        with pytest.raises(ProviderError) as excinfo:
            await post_json(client, "embeddings", {}, slot="embedding", attempts=2, base_delay=0.0)

    assert excinfo.value.code == "network"
    assert route.call_count == 2


@respx.mock
async def test_bad_json_is_reported_not_retried():
    route = respx.post("https://api.example.com/v1/embeddings").mock(
        return_value=httpx.Response(200, text="<html>gateway</html>")
    )

    async with make_client() as client:
        with pytest.raises(ProviderError) as excinfo:
            await post_json(client, "embeddings", {}, slot="embedding", attempts=3)

    assert excinfo.value.code == "bad_json"
    assert route.call_count == 1


def test_retryable_status_set_is_explicit():
    """可重试集合必须显式：不要哪天有人把 4xx 全塞进去。"""
    assert {429, 500, 502, 503, 504} == RETRYABLE_STATUS
    for code in (400, 401, 403, 404, 422):
        assert code not in RETRYABLE_STATUS


@respx.mock
async def test_tpm_limit_waits_much_longer_than_plain_429():
    """TPM 窗口是 60 秒。普通 429 的 0.6s/1.2s 退避会全落在同一窗口里，等于白等。"""
    route = respx.post("https://api.example.com/v1/chat/completions")
    route.side_effect = [
        httpx.Response(429, json={"code": 50602, "message": "TPM limit reached"}),
        httpx.Response(200, json={"ok": True}),
    ]

    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    async with make_client() as client:
        await post_json(
            client,
            "chat/completions",
            {},
            slot="llm",
            attempts=3,
            sleep=fake_sleep,
        )

    assert route.call_count == 2
    assert waits and waits[0] >= 10.0, f"TPM backoff too short: {waits}"


@respx.mock
async def test_plain_429_waits_briefly():
    route = respx.post("https://api.example.com/v1/embeddings")
    route.side_effect = [
        httpx.Response(429, json={"code": 429, "message": "too many requests"}),
        httpx.Response(200, json={"ok": True}),
    ]

    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    async with make_client() as client:
        await post_json(client, "embeddings", {}, slot="embedding", attempts=3, sleep=fake_sleep)

    assert waits and waits[0] < 2.0


def test_provider_error_classifies_tpm():
    tpm = ProviderError("llm", 50602, "TPM limit reached", status=429)
    assert tpm.is_rate_limit
    assert tpm.is_tpm_limit
    assert not tpm.is_fatal

    balance = ProviderError("embedding", 30001, "insufficient balance", status=403)
    assert not balance.is_rate_limit
    assert balance.is_fatal
