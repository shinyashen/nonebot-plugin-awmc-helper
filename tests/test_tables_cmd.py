"""awmc.tables 指令测试：`更新定数表` 刷新必须执行（finish 截断回归）、
牌子牌单校验（不存在的牌名拒答）。"""

import pytest
from nonebug import App


@pytest.mark.asyncio
async def test_plate_invalid_kind_rejected(app: App):
    """真将不存在（素材包无此牌头，原版 Hoshino 亦硬编码拒答「真系没有真将哦」）。

    拒答文案由牌单派生：真代真实牌种只有 极/神/舞舞。
    """
    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import (
        Bot,
        Message,
        MessageSegment,
    )
    from nonebot.adapters.onebot.v11 import (
        Adapter as OnebotV11Adapter,
    )

    from nonebot_plugin_awmc_helper.plugins import tables as plugin

    event = fake_group_message_event_v11(message="真将进度")
    expected = Message(
        [
            MessageSegment.at(12345678),
            MessageSegment.text(" 没有找到「真将」牌子。真代牌为 真极/真神/真舞舞"),
        ]
    )
    async with app.test_matcher(plugin.plate_cmd) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter))
        ctx.receive_event(bot, event)
        # UniSession 依赖注入触发群信息/成员信息拉取
        ctx.should_call_api(
            "get_group_info",
            {"group_id": 87654321},
            result={
                "group_id": 87654321,
                "group_name": "g",
                "member_count": 1,
                "max_member_count": 10,
            },
        )
        ctx.should_call_api(
            "get_group_member_info",
            {"group_id": 87654321, "user_id": 12345678, "no_cache": True},
            result={
                "user_id": 12345678,
                "role": "member",
                "card": "",
                "nickname": "t",
            },
        )
        ctx.should_call_send(event, expected, result=None, bot=bot)
        ctx.should_finished()


@pytest.mark.asyncio
async def test_update_rating_continues_after_progress(app: App, monkeypatch):
    import nonebot
    from fake import fake_private_message_event_v11
    from nonebot.adapters.onebot.v11 import (
        Bot,
        Message,
        MessageSegment,
    )
    from nonebot.adapters.onebot.v11 import (
        Adapter as OnebotV11Adapter,
    )

    from nonebot_plugin_awmc_helper.plugins import tables as plugin
    from nonebot_plugin_awmc_helper.core.render import table_template

    monkeypatch.setattr(nonebot.get_driver().config, "superusers", {"12345678"})

    async def fake_refresh(_service) -> tuple[int, list[str]]:
        return 5, []

    monkeypatch.setattr(table_template, "refresh_all_rating_tables", fake_refresh)

    event = fake_private_message_event_v11(message="更新定数表", user_id=12345678)
    async with app.test_matcher(plugin.update_rating) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter))
        ctx.receive_event(bot, event)
        # 私聊：无 at，发送层去前导空格
        ctx.should_call_send(
            event,
            Message([MessageSegment.text("正在生成定数表底图，请稍候……")]),
            result=None,
            bot=bot,
        )
        ctx.should_call_send(
            event,
            Message([MessageSegment.text("定数表底图生成完成（5 谱面次）。")]),
            result=None,
            bot=bot,
        )
        ctx.should_finished()
