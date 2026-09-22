"""core/http：智能代理路由测试（transport 打桩，无真实网络）。

nonebug 在用例阶段才完成 nonebot 初始化，插件模块一律函数内延迟导入。
"""

import httpx
import pytest


class _StubTransport(httpx.AsyncBaseTransport):
    """记录请求的打桩通道：fail=True 时抛连接阶段错误。"""

    def __init__(self, name: str, fail: bool = False):
        self.name = name
        self.fail = fail
        self.seen: list[str] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(str(request.url))
        if self.fail:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, json={"via": self.name})


def _make(
    foreign_fail: bool = False, direct_fail: bool = False
) -> tuple[httpx.AsyncBaseTransport, _StubTransport, _StubTransport]:
    from nonebot_plugin_awmc_helper.core.http import SmartProxyTransport

    proxied = _StubTransport("proxy", fail=foreign_fail)
    direct = _StubTransport("direct", fail=direct_fail)
    transport = SmartProxyTransport(
        "http://proxy:7890", ("foreign.dev",), direct=direct, proxied=proxied
    )
    return transport, direct, proxied


async def _get(transport, host: str) -> httpx.Response:
    return await transport.handle_async_request(
        httpx.Request("GET", f"https://{host}/x")
    )


@pytest.mark.asyncio
async def test_foreign_host_prefers_proxy():
    transport, direct, _ = _make()
    resp = await _get(transport, "api.foreign.dev")
    assert resp.json() == {"via": "proxy"}
    assert direct.seen == []


@pytest.mark.asyncio
async def test_foreign_falls_back_to_direct_on_connect_error():
    transport, direct, proxied = _make(foreign_fail=True)
    resp = await _get(transport, "api.foreign.dev")
    assert resp.json() == {"via": "direct"}
    assert proxied.seen
    assert direct.seen


@pytest.mark.asyncio
async def test_domestic_prefers_direct_and_falls_back_to_proxy():
    transport, direct, proxied = _make(direct_fail=True)
    resp = await _get(transport, "domestic.cn")
    assert resp.json() == {"via": "proxy"}
    assert direct.seen
    assert proxied.seen


@pytest.mark.asyncio
async def test_both_channels_fail_reraises_primary():
    transport, direct, proxied = _make(direct_fail=True, foreign_fail=True)
    with pytest.raises(httpx.ConnectError):
        await _get(transport, "domestic.cn")
    assert direct.seen
    assert proxied.seen


@pytest.mark.asyncio
async def test_read_timeout_not_retried_via_fallback():
    """读超时说明请求可能已送达对端，禁止换通道重放。"""
    transport, direct, proxied = _make()

    async def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    direct.handle_async_request = slow  # type: ignore[method-assign]
    with pytest.raises(httpx.ReadTimeout):
        await _get(transport, "domestic.cn")
    assert proxied.seen == []


def test_build_transport_none_without_proxy(monkeypatch):
    from nonebot_plugin_awmc_helper.config import plugin_config
    from nonebot_plugin_awmc_helper.core.http import build_smart_transport

    monkeypatch.setattr(plugin_config, "awmc_proxy", None)
    assert build_smart_transport() is None


def test_build_transport_merges_foreign_hosts(monkeypatch):
    from nonebot_plugin_awmc_helper.config import plugin_config
    from nonebot_plugin_awmc_helper.core.http import (
        SmartProxyTransport,
        build_smart_transport,
    )

    monkeypatch.setattr(plugin_config, "awmc_proxy", "http://127.0.0.1:7796")
    monkeypatch.setattr(plugin_config, "awmc_foreign_hosts", ["sub.example.org"])
    transport = build_smart_transport()
    assert isinstance(transport, SmartProxyTransport)
    assert "sub.example.org" in transport._foreign_hosts
    assert "github.com" in transport._foreign_hosts
