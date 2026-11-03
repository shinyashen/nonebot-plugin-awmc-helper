"""core/ext/moant 直连层测试（respx mock）。"""

import httpx
import respx
import pytest

ROUTE = "https://dw.moant.cn:34225/api/chartFile"


@pytest.fixture
def moant():
    from nonebot_plugin_awmc_helper.core.ext import ExtError, ExtNetworkError, moant

    # 错误类挂到模块对象上，断言处就近取用
    moant.ExtError = ExtError
    moant.ExtNetworkError = ExtNetworkError
    return moant


def _chart_route(m, music_id: str):
    """按官方谱面 id（query 参数）精确匹配的兜底路由。"""
    return m.get(ROUTE, params={"musicId": music_id, "file": "maidata.txt"})


@pytest.mark.asyncio
async def test_fetch_chart_dx_uses_offset_id(moant):
    """DX 谱面：官方 id = 根 id+10000（BLACK ROSE 1001 → musicId=11001）。"""
    with respx.mock(assert_all_called=False) as m:
        route = _chart_route(m, "11001").respond(text="&title=BLACK ROSE\n&inote_2=\n")
        text, official = await moant.fetch_chart_text(1001, dx=True)
    assert route.called
    assert "BLACK ROSE" in text
    assert official == 11001


@pytest.mark.asyncio
async def test_fetch_chart_sd_direct_hit(moant):
    """SD 谱面：根 id 直接命中（True Love Song = 8）；防御性去 BOM。"""
    with respx.mock(assert_all_called=False) as m:
        _chart_route(m, "8").respond(text="\ufeff&title=TLS\n&inote_2=\n")
        text, official = await moant.fetch_chart_text(8, dx=False)
    assert text.startswith("&title=")
    assert official == 8


@pytest.mark.asyncio
async def test_fetch_chart_sd_fallback_offset(moant):
    """SD 根 id 404 时 +10000 兜底探一次（同落雪双探口径）。"""
    with respx.mock(assert_all_called=False) as m:
        _chart_route(m, "1001").respond(
            status_code=404, json={"message": "not found", "success": False}
        )
        _chart_route(m, "11001").respond(text="&music_id=11001\n&inote_2=\n")
        text, official = await moant.fetch_chart_text(1001, dx=False)
    assert text.startswith("&music_id=")
    assert official == 11001


@pytest.mark.asyncio
async def test_fetch_chart_missing_returns_none(moant):
    """双探均 404 → None（无谱面语义留给调用方）。"""
    with respx.mock(assert_all_called=False) as m:
        m.get(ROUTE).respond(
            status_code=404, json={"message": "not found", "success": False}
        )
        assert await moant.fetch_chart_text(2025, dx=False) is None


@pytest.mark.asyncio
async def test_fetch_chart_placeholder_body_returns_none(moant):
    """200 但正文无 &inote_（占位头）→ 视为无谱面返回 None。"""
    with respx.mock(assert_all_called=False) as m:
        _chart_route(m, "8").respond(text="&title=占位\n")
        _chart_route(m, "10008").respond(status_code=404)  # +10000 第二探
        assert await moant.fetch_chart_text(8, dx=False) is None


@pytest.mark.asyncio
async def test_fetch_chart_http_500_raises(moant):
    """非 200/404（如 5xx）→ ExtError（不静默当无谱面）。"""
    with respx.mock(assert_all_called=False) as m:
        m.get(ROUTE).respond(status_code=500)
        with pytest.raises(moant.ExtError, match="HTTP 500"):
            await moant.fetch_chart_text(8, dx=False)


@pytest.mark.asyncio
async def test_fetch_chart_network_error_raises(moant):
    """网络异常（重试 1 次后）→ ExtNetworkError。"""
    with respx.mock(assert_all_called=False) as m:
        m.get(ROUTE).side_effect = httpx.ConnectError("connection refused")
        with pytest.raises(moant.ExtNetworkError):
            await moant.fetch_chart_text(8, dx=False)


@pytest.mark.asyncio
async def test_fetch_chart_text_by_id_utage(moant):
    """宴谱按官方 diff_id 直取（[協]ラグトレイン = 111355）。"""
    with respx.mock(assert_all_called=False) as m:
        route = _chart_route(m, "111355").respond(
            text="&title=[協]ラグトレイン\n&inote_2=\n"
        )
        text = await moant.fetch_chart_text_by_id(111355)
    assert route.called
    assert "[協]" in text
