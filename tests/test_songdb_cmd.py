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

    async def fake_apply(*, force: bool = False) -> dict:
        assert force  # 显式重载必须强制重应用（重建覆写后哈希门控会漏）
        return {"sources": 1, "applied": 123, "changed": True}

    async def fake_prerender() -> str:
        return "定数表 1 谱面次（失败 0）"

    monkeypatch.setattr(songdb, "apply_external_sources", fake_apply)

    async def fake_gamerch_fill() -> tuple[int, bool]:
        return 0, False

    from nonebot_plugin_awmc_helper.core.ext import gamerch

    monkeypatch.setattr(gamerch, "apply_fill", fake_gamerch_fill)
    monkeypatch.setattr(songs_mod, "prerender_templates", fake_prerender)

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
            Message(
                [
                    MessageSegment.text(
                        "补充数据已重载并重建底图（源 1 个）"
                        "；MuNET 别名走查未启用（awmc_munet_alias_days=0）。"
                    )
                ]
            ),
            result=None,
            bot=bot,
        )
        ctx.should_finished()


@pytest.mark.asyncio
async def test_refresh_songs_success(app: App, monkeypatch):
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
    from nonebot_plugin_awmc_helper.plugins import songdb as plugin

    monkeypatch.setattr(nonebot.get_driver().config, "superusers", {"12345678"})

    async def fake_full_refresh() -> dict:
        return {
            "songs": 1699,
            "groups": 1650,
            "charts": 5000,
            "level_points": 9000,
            "removed": 0,
            "warnings": ["otoge-db 未收录 3 首", "另一条"],
            "cn_current_version": 260901,
            "extra": {"changed": False},
        }

    monkeypatch.setattr(songs_mod, "full_refresh", fake_full_refresh)

    event = fake_private_message_event_v11(message="刷新歌曲库", user_id=12345678)
    async with app.test_matcher(plugin.refresh_songs) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter))
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            Message([MessageSegment.text("正在刷新歌曲库（四源重建，预计几分钟）……")]),
            result=None,
            bot=bot,
        )
        ctx.should_call_send(
            event,
            Message(
                [
                    MessageSegment.text(
                        "歌曲库刷新完成：1699 曲 / 1650 谱面组 / 5000 谱面，"
                        "删除 0 曲，国服当前版本 260901（告警 2 条，详见日志）"
                    )
                ]
            ),
            result=None,
            bot=bot,
        )
        ctx.should_finished()


@pytest.mark.asyncio
async def test_refresh_songs_failure(app: App, monkeypatch):
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
    from nonebot_plugin_awmc_helper.plugins import songdb as plugin

    monkeypatch.setattr(nonebot.get_driver().config, "superusers", {"12345678"})

    async def fake_full_refresh() -> dict:
        return {}

    monkeypatch.setattr(songs_mod, "full_refresh", fake_full_refresh)

    event = fake_private_message_event_v11(message="刷新歌曲库", user_id=12345678)
    async with app.test_matcher(plugin.refresh_songs) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter))
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            Message([MessageSegment.text("正在刷新歌曲库（四源重建，预计几分钟）……")]),
            result=None,
            bot=bot,
        )
        ctx.should_call_send(
            event,
            Message(
                [
                    MessageSegment.text(
                        "刷新失败（详见服务端日志），曲库运行时未受影响。"
                    )
                ]
            ),
            result=None,
            bot=bot,
        )
        ctx.should_finished()


@pytest.mark.asyncio
async def test_reload_starts_munet_walk_when_enabled(app: App, monkeypatch):
    import asyncio

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

    from nonebot_plugin_awmc_helper.core import songdb
    from nonebot_plugin_awmc_helper.config import plugin_config
    from nonebot_plugin_awmc_helper.plugins import songdb as plugin
    from nonebot_plugin_awmc_helper.core.ext import munet, gamerch

    monkeypatch.setattr(nonebot.get_driver().config, "superusers", {"12345678"})
    monkeypatch.setattr(plugin_config, "awmc_munet_alias_days", 7)

    async def fake_apply(*, force: bool = False) -> dict:
        return {"sources": 1, "applied": 0, "changed": False}

    async def fake_gamerch_fill() -> tuple[int, bool]:
        return 0, False

    calls: list[dict] = []

    async def fake_walk(**kwargs):
        calls.append(kwargs)
        return {"status": "done", "aliases": 10}

    monkeypatch.setattr(songdb, "apply_external_sources", fake_apply)
    monkeypatch.setattr(gamerch, "apply_fill", fake_gamerch_fill)
    monkeypatch.setattr(munet, "refresh_aliases_full", fake_walk)

    event = fake_private_message_event_v11(message="重载补充数据", user_id=12345678)
    async with app.test_matcher(plugin.reload_extra) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter))
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            Message([MessageSegment.text("正在重载外部补充数据……")]),
            result=None,
            bot=bot,
        )
        ctx.should_call_send(
            event,
            Message(
                [
                    MessageSegment.text(
                        "补充数据无变化（源 1 个，本次应用 0 处，wiki 无缺口）"
                        "；MuNET 别名走查已后台启动（约 30 分钟，结果见日志）。"
                    )
                ]
            ),
            result=None,
            bot=bot,
        )
        ctx.should_finished()
    # 等后台走查任务执行完毕并校验 force 透传
    while plugin._munet_bg_tasks:
        await asyncio.gather(*list(plugin._munet_bg_tasks))
    assert calls
    assert calls[0]["force"] is True
