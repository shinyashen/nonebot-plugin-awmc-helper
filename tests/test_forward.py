"""core/forward 合并转发发送兜底：协议端黑洞/报错时必须返回 False 交给调用方降级。"""

import asyncio

import pytest


@pytest.mark.asyncio
async def test_forward_send_timeout_returns_false(monkeypatch):
    """协议端收到动作后不回包（LLBot 实测黑洞）：超时返回 False 而非无限挂起。"""
    import nonebot
    from nonebot.adapters.onebot.v11 import Bot
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    from nonebot_plugin_awmc_helper.core import forward as forward_mod

    adapter = nonebot.get_adapter(OnebotV11Adapter)
    bot = Bot(adapter=adapter, self_id="1234567890")

    started = asyncio.Event()

    async def black_hole(*args, **kwargs):
        started.set()
        await asyncio.sleep(5)

    monkeypatch.setattr(bot, "call_api", black_hole)
    monkeypatch.setattr(forward_mod, "FORWARD_SEND_TIMEOUT", 0.05)
    loop = asyncio.get_running_loop()
    begin = loop.time()
    assert await forward_mod.try_send_forward(bot, ["hi"], group_id=123) is False
    assert started.is_set()
    assert loop.time() - begin < 2


@pytest.mark.asyncio
async def test_forward_send_error_returns_false(monkeypatch):
    """协议端返回错误（ActionFailed）：返回 False 交由调用方降级为普通消息。"""
    import nonebot
    from nonebot.adapters.onebot.v11 import Bot
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter
    from nonebot.adapters.onebot.v11.exception import ActionFailed

    from nonebot_plugin_awmc_helper.core import forward as forward_mod

    adapter = nonebot.get_adapter(OnebotV11Adapter)
    bot = Bot(adapter=adapter, self_id="1234567890")

    async def rejected(*args, **kwargs):
        raise ActionFailed({"status": "failed", "retcode": 1404})

    monkeypatch.setattr(bot, "call_api", rejected)
    assert await forward_mod.try_send_forward(bot, ["hi"], group_id=123) is False
