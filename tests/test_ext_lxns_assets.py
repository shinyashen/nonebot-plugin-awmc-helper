"""core/ext/lxns_assets 直连层测试（respx mock）。"""

import respx
import pytest

BASE = "https://assets2.lxns.net"


@pytest.fixture
def lxns_assets():
    from nonebot_plugin_awmc_helper.core.ext import lxns_assets as mod

    return mod


@pytest.mark.asyncio
async def test_fetch_chart_dx_uses_offset_id(lxns_assets):
    """DX 谱面：官方 id = 根 id+10000（BLACK ROSE 1001 → 11001）。"""
    with respx.mock(assert_all_called=False) as m:
        m.get(f"{BASE}/maimai/chart/11001.txt").respond(text="&title=BLACK ROSE [DX]\n")
        text, official = await lxns_assets.fetch_chart_text(1001, dx=True)
    assert "BLACK ROSE" in text
    assert official == 11001


@pytest.mark.asyncio
async def test_fetch_chart_sd_direct_hit(lxns_assets):
    """SD 谱面：根 id 直接命中（True Love Song = 8）。"""
    with respx.mock(assert_all_called=False) as m:
        m.get(f"{BASE}/maimai/chart/8.txt").respond(text="&music_id=8\n")
        _text, official = await lxns_assets.fetch_chart_text(8, dx=False)
    assert official == 8


@pytest.mark.asyncio
async def test_fetch_chart_sd_fallback_offset(lxns_assets):
    """SD 根 id 404 时回退 +10000 兜底探一次。"""
    with respx.mock(assert_all_called=False) as m:
        m.get(f"{BASE}/maimai/chart/1001.txt").respond(status_code=404)
        m.get(f"{BASE}/maimai/chart/11001.txt").respond(text="ok-sd-fallback")
        text, official = await lxns_assets.fetch_chart_text(1001, dx=False)
        assert text == "ok-sd-fallback"
        assert official == 11001


@pytest.mark.asyncio
async def test_fetch_chart_missing(lxns_assets):
    """双探均 404 → ChartNotAvailableError（国服先行曲口径）。"""
    with respx.mock(assert_all_called=False) as m:
        m.get(url__regex=r".*/chart/\d+\.txt").respond(status_code=404)
        with pytest.raises(lxns_assets.ChartNotAvailableError):
            await lxns_assets.fetch_chart_text(2025, dx=False)


@pytest.mark.asyncio
async def test_fetch_music_root_id(lxns_assets):
    """音频按 %10000 取根（11001 → music/1001.mp3）。"""
    with respx.mock(assert_all_called=False) as m:
        m.get(f"{BASE}/maimai/music/1001.mp3").respond(content=b"id3-audio")
        data = await lxns_assets.fetch_music(11001)
    assert data == b"id3-audio"


@pytest.mark.asyncio
async def test_fetch_music_utage_reuses_base(lxns_assets):
    """宴谱音频按 %10000 取根复用原曲（111355 → music/1355.mp3）。"""
    with respx.mock(assert_all_called=False) as m:
        m.get(f"{BASE}/maimai/music/1355.mp3").respond(content=b"utage-audio")
        data = await lxns_assets.fetch_music(111355)
    assert data == b"utage-audio"


@pytest.mark.asyncio
async def test_fetch_music_missing(lxns_assets):
    with respx.mock(assert_all_called=False) as m:
        m.get(url__regex=r".*/music/\d+\.mp3").respond(status_code=404)
        with pytest.raises(lxns_assets.ChartNotAvailableError):
            await lxns_assets.fetch_music(12345)
