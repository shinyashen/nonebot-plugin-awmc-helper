"""awmc.base 帮助与项目地址指令测试。"""

import pytest
from nonebug import App


@pytest.mark.asyncio
async def test_repo_address(app: App):
    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    from nonebot_plugin_awmc_helper.plugins import base

    event = fake_group_message_event_v11(message="项目地址maimaiDX")
    async with app.test_matcher(base.repo_cmd) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter))
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            Message(
                [
                    MessageSegment.at(12345678),
                    MessageSegment.text(" " + base.REPO_URL + "\n求 star，求宣传~"),
                ]
            ),
            result=None,
            bot=bot,
        )
        ctx.should_finished()


@pytest.mark.asyncio
async def test_help_image(app: App):
    """帮助指令回复总览图（与同一渲染函数同源断言）。"""
    import base64

    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    from nonebot_plugin_awmc_helper.plugins import base
    from nonebot_plugin_awmc_helper.core.render.tools import (
        text_to_image,
        image_to_bytes,
    )

    png = image_to_bytes(text_to_image(base.HELP_TEXT, size=22))
    event = fake_group_message_event_v11(message="帮助maimaiDX")
    expected = Message(
        [
            MessageSegment.at(12345678),
            MessageSegment.image(f"base64://{base64.b64encode(png).decode()}"),
        ]
    )
    async with app.test_matcher(base.help_cmd) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter))
        ctx.receive_event(bot, event)
        ctx.should_call_send(event, expected, result=None, bot=bot)
        ctx.should_finished()


def test_player_display_name_prefers_nickname():
    """玩家显示名助手：nickname 优先、无则回退 name（score-updater 接线共用）。"""
    from nonebot_plugin_awmc_helper.core.utils import player_display_name

    class _P:
        name = "账号名"
        nickname = "昵称"

    class _LxnsP:
        name = "只有name"

    assert player_display_name(_P()) == "昵称"
    assert player_display_name(_LxnsP()) == "只有name"

    class _EmptyNick:
        name = "真名"
        nickname = ""

    assert player_display_name(_EmptyNick()) == "真名"  # 空昵称同样回退
