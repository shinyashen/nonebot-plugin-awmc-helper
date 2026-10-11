"""core/ext/yuzu.get_plate_list 直连测试（respx mock）：柚子逐牌曲单拉取。"""

import httpx
import respx
import pytest

BASE = "https://www.yuzuchan.moe/api/v2"
PLATE_URL = f"{BASE}/maimaidx/music/get_plate"

SAMPLE = {"真": [8, 44], "熊&华": [10191, 11001], "舞ReMASTER": [17, 22]}


@pytest.fixture
def yuzu_mock():
    with respx.mock(assert_all_called=False) as mock:
        yield mock


@pytest.fixture
def yuzu_ext():
    from nonebot_plugin_awmc_helper.core.ext import yuzu as mod

    return mod


@pytest.mark.asyncio
async def test_get_plate_list_ok(yuzu_mock, yuzu_ext):
    yuzu_mock.get(PLATE_URL).respond(json=SAMPLE)
    data = await yuzu_ext.get_plate_list()
    assert data == SAMPLE


@pytest.mark.asyncio
async def test_get_plate_list_bad_shape(yuzu_mock, yuzu_ext):
    # 值非 int 列表 / 顶层非 dict / 空结构 → 业务错误（上层降级不过筛）
    yuzu_mock.get(PLATE_URL).respond(json=[1, 2])
    with pytest.raises(yuzu_ext.ExtError):
        await yuzu_ext.get_plate_list()
    yuzu_mock.get(PLATE_URL).respond(json={"真": ["8", 44]})
    with pytest.raises(yuzu_ext.ExtError):
        await yuzu_ext.get_plate_list()
    yuzu_mock.get(PLATE_URL).respond(json={})
    with pytest.raises(yuzu_ext.ExtError):
        await yuzu_ext.get_plate_list()


@pytest.mark.asyncio
async def test_get_plate_list_network_error(yuzu_mock, yuzu_ext):
    from nonebot_plugin_awmc_helper.core.ext import ExtNetworkError

    yuzu_mock.get(PLATE_URL).mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(ExtNetworkError):
        await yuzu_ext.get_plate_list()
