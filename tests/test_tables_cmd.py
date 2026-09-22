"""awmc.tables `更新定数表` 指令测试：进度提示后刷新逻辑必须执行（finish 截断回归）。"""

import pytest
from nonebug import App


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
