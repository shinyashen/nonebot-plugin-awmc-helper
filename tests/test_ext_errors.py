"""ext 层错误三态收口：网络异常→ExtNetworkError、JSON 解析失败→ExtError。

上层（alias/matchers、bind）只 catch ExtError：裸 httpx 异常会被记成
未捕获而非友好提示，坏 JSON 会把原始报错文本透给用户（审查 F7）。
"""

import httpx
import respx
import pytest


@pytest.fixture
def ext_mock():
    with respx.mock(assert_all_called=False) as mock:
        yield mock


@pytest.mark.asyncio
async def test_fetch_json_invalid_json_is_exterror(ext_mock):
    from nonebot_plugin_awmc_helper.core.ext import ExtError, fetch_json

    ext_mock.get("https://example.com/data.json").respond(200, content=b"not json")
    with pytest.raises(ExtError, match="无效数据"):
        await fetch_json("https://example.com/data.json", name="测试数据")


@pytest.mark.asyncio
async def test_fetch_json_network_error_is_extnetworkerror(ext_mock):
    from nonebot_plugin_awmc_helper.core.ext import ExtNetworkError, fetch_json

    ext_mock.get("https://example.com/data.json").side_effect = httpx.ConnectError(
        "boom"
    )
    with pytest.raises(ExtNetworkError):
        await fetch_json("https://example.com/data.json", name="测试数据")


@pytest.mark.asyncio
async def test_yuzu_network_error_is_extnetworkerror(ext_mock):
    from nonebot_plugin_awmc_helper.core.ext import ExtNetworkError
    from nonebot_plugin_awmc_helper.core.ext.yuzu import yuzu_client

    ext_mock.get(
        "https://www.yuzuchan.moe/api/v2/aliases/maimaidx/votes"
    ).side_effect = httpx.ConnectError("boom")
    with pytest.raises(ExtNetworkError):
        await yuzu_client.get_status()


@pytest.mark.asyncio
async def test_yuzu_invalid_json_is_exterror(ext_mock):
    from nonebot_plugin_awmc_helper.core.ext import ExtError
    from nonebot_plugin_awmc_helper.core.ext.yuzu import yuzu_client

    ext_mock.get("https://www.yuzuchan.moe/api/v2/aliases/maimaidx/votes").respond(
        200, content=b"not json"
    )
    with pytest.raises(ExtError, match="无效数据"):
        await yuzu_client.get_status()


@pytest.mark.asyncio
async def test_lxns_token_grant_network_error_is_extnetworkerror(ext_mock):
    from nonebot_plugin_awmc_helper.core.ext import ExtNetworkError, lxns

    ext_mock.post(
        "https://maimai.lxns.net/api/v0/oauth/token"
    ).side_effect = httpx.ConnectError("boom")
    with pytest.raises(ExtNetworkError):
        await lxns.fetch_token("ABCD-EFGH-JKLM")
