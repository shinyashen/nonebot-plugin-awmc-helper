"""awmc.songdb `重载补充数据`：进度提示后工作逻辑必须执行（finish 截断回归）。"""

import pytest
from nonebug import App


@pytest.mark.asyncio
async def test_reload_extra_continues_after_progress(app: App, monkeypatch):
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

    from nonebot_plugin_awmc_helper.core import songs as songs_mod
    from nonebot_plugin_awmc_helper.core import songdb
    from nonebot_plugin_awmc_helper.plugins import songdb as plugin

    monkeypatch.setattr(nonebot.get_driver().config, "superusers", {"12345678"})

    async def fake_apply() -> dict:
        return {"sources": 1, "applied": 123, "changed": True}

    async def fake_prerender() -> str:
        return "定数表 1 谱面次（失败 0）"

    monkeypatch.setattr(songdb, "apply_external_sources", fake_apply)
    monkeypatch.setattr(songs_mod, "_prerender_templates", fake_prerender)

    event = fake_private_message_event_v11(message="重载补充数据", user_id=12345678)
    async with app.test_matcher(plugin.reload_extra) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter))
        ctx.receive_event(bot, event)
        # 私聊：无 at，发送层去前导空格
        ctx.should_call_send(
            event,
            Message([MessageSegment.text("正在重载外部补充数据……")]),
            result=None,
            bot=bot,
        )
        ctx.should_call_send(
            event,
            Message([MessageSegment.text("补充数据已重载并重建底图（源 1 个）。")]),
            result=None,
            bot=bot,
        )
        ctx.should_finished()
