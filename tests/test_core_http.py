"""core/http：智能代理路由测试（transport 打桩，无真实网络）。

nonebug 在用例阶段才完成 nonebot 初始化，插件模块一律函数内延迟导入。
"""

import ssl

import httpx
import respx
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


@pytest.fixture
def fresh_cache(monkeypatch):
    """清空进程级 transport 缓存并在用例后还原，避免用例间串味。"""
    from nonebot_plugin_awmc_helper.core import http

    monkeypatch.setattr(http, "_transport_cache", {})


def test_build_transport_none_without_proxy(monkeypatch):
    from nonebot_plugin_awmc_helper.config import plugin_config
    from nonebot_plugin_awmc_helper.core.http import build_smart_transport

    monkeypatch.setattr(plugin_config, "awmc_proxy", None)
    assert build_smart_transport() is None


def test_build_transport_merges_foreign_hosts(monkeypatch, fresh_cache):
    from nonebot_plugin_awmc_helper.config import plugin_config
    from nonebot_plugin_awmc_helper.core.http import (
        SmartProxyTransport,
        build_smart_transport,
    )

    monkeypatch.setattr(plugin_config, "awmc_proxy", "http://127.0.0.1:7796")
    monkeypatch.setattr(plugin_config, "awmc_foreign_hosts", ["sub.example.org"])
    transport = build_smart_transport()
    inner = transport._inner
    assert isinstance(inner, SmartProxyTransport)
    assert "sub.example.org" in inner._foreign_hosts
    assert "github.com" in inner._foreign_hosts


@pytest.mark.asyncio
async def test_shared_transport_delegates_and_survives_client_close(
    monkeypatch, fresh_cache
):
    """薄壳透传请求；client.aclose 只关壳，共享底层连接池不受影响。"""
    from nonebot_plugin_awmc_helper.config import plugin_config
    from nonebot_plugin_awmc_helper.core.http import build_smart_transport

    monkeypatch.setattr(plugin_config, "awmc_proxy", "http://127.0.0.1:7796")
    monkeypatch.setattr(plugin_config, "awmc_foreign_hosts", [])

    transport = build_smart_transport()
    async with httpx.AsyncClient(transport=transport) as client:
        with respx.mock(assert_all_called=False) as mock:
            mock.get("https://domestic.cn/x").respond(json={"ok": True})
            resp = await client.get("https://domestic.cn/x")
            assert resp.json() == {"ok": True}

    inner = transport._inner
    # 第二个客户端复用同一底层 transport，且仍可用（修复前会被首个 client 关闭）
    async with httpx.AsyncClient(transport=build_smart_transport()) as client2:
        with respx.mock(assert_all_called=False) as mock2:
            mock2.get("https://domestic.cn/y").respond(json={"ok": 2})
            resp2 = await client2.get("https://domestic.cn/y")
            assert resp2.json() == {"ok": 2}
    assert build_smart_transport()._inner is inner


def test_build_transport_cached_by_verify(monkeypatch, fresh_cache):
    from nonebot_plugin_awmc_helper.config import plugin_config
    from nonebot_plugin_awmc_helper.core.http import build_smart_transport

    monkeypatch.setattr(plugin_config, "awmc_proxy", "http://127.0.0.1:7796")
    monkeypatch.setattr(plugin_config, "awmc_foreign_hosts", [])

    t1 = build_smart_transport()
    t2 = build_smart_transport()
    assert t1 is not t2  # 每客户端独立薄壳
    assert t1._inner is t2._inner  # 底层连接池共享

    ctx = ssl.create_default_context()
    t3 = build_smart_transport(verify=ctx)
    assert t3._inner is not t1._inner  # 不同 verify 各建一套通道
