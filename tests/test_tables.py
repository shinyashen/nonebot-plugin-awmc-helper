"""awmc.tables 指令测试：`更新定数表` 刷新必须执行（finish 截断回归）、
牌子牌单校验（不存在的牌名拒答）、13定数表、牌子条件帮助。"""

from pathlib import Path

import pytest
from mocks import requires_assets
from nonebug import App
from maimai_py import SongType


@pytest.fixture
async def db(tmp_path: Path):
    from nonebot_plugin_awmc_helper.core import store

    store.set_db_file(tmp_path / "awmc.db")
    await store.init_db()
    yield
    store.set_db_file(None)


@pytest.fixture
async def songs(db):
    from mocks import seed_service

    from nonebot_plugin_awmc_helper.core.songs import song_service

    await seed_service(song_service)
    yield
    song_service._ready.clear()


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


@pytest.mark.asyncio
@requires_assets
async def test_ds_table_command(app: App, songs):
    import base64

    from nonebot_plugin_awmc_helper.plugins import tables
    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.render import table_template

    # 与 handler 相同的调用路径 → 相同数据 → 相同渲染（NB 版式网格）；
    # 先清掉真实目录可能残留的预渲染底图，固定走实时生成分支（xdist 下
    # 期望图与 handler 渲染必须基于同一磁盘状态）
    (table_template.rating_table_dir() / "13.png").unlink(missing_ok=True)
    entries = []
    for song in await song_service.get_all():
        for d in song.get_difficulties():
            if d.type != SongType.UTAGE and d.level == "13":
                entries.append((song, d))
    png = await table_template.rating_table_text_bytes("13", entries)

    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    event = fake_group_message_event_v11(message="13定数表")
    expected = Message(
        [
            MessageSegment.at(12345678),
            MessageSegment.image(f"base64://{base64.b64encode(png).decode()}"),
        ]
    )
    async with app.test_matcher(tables.ds_table_cmd) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter))
        ctx.receive_event(bot, event)
        ctx.should_call_send(event, expected, result=None, bot=bot)
        ctx.should_finished()


@pytest.mark.asyncio
async def test_plate_help(app: App):
    import base64

    from nonebot_plugin_awmc_helper.plugins import tables
    from nonebot_plugin_awmc_helper.constants import PLATE_KIND_ZH
    from nonebot_plugin_awmc_helper.core.render.tools import (
        text_to_image,
        image_to_bytes,
    )

    lines = ["牌子达成条件说明："]
    lines += [f"{kind}：{desc}" for kind, desc in PLATE_KIND_ZH.items()]
    lines.append("舞/霸：旧作（含 Re:MASTER 单列）全曲谱面")
    png = image_to_bytes(text_to_image("\n".join(lines)))

    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    event = fake_group_message_event_v11(message="牌子条件")
    expected = Message(
        [
            MessageSegment.at(12345678),
            MessageSegment.image(f"base64://{base64.b64encode(png).decode()}"),
        ]
    )
    async with app.test_matcher(tables.plate_help) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter))
        ctx.receive_event(bot, event)
        ctx.should_call_send(event, expected, result=None, bot=bot)
        ctx.should_finished()


@pytest.mark.asyncio
async def test_plate_traditional_alias_reachable(app: App, monkeypatch):
    """繁体/和制牌写法可达且入口归一（L-3）：暁将 → 晓将 正常走查库渲染。

    正则层扩 core.plates 别名字符识别，handler 归一后校验/查库/渲染全按
    简体口径（PLATE_CHARS 预渲染迭代不受扩容影响）。
    """
    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import (
        Adapter as OnebotV11Adapter,
    )
    from nonebot_plugin_alconna.uniseg import UniMessage

    from nonebot_plugin_awmc_helper.plugins.tables import matchers as plugin

    async def fake_get_plates(binding, plate, notify_slow=None):
        assert plate == "晓将"
        return ("sentinel",)

    async def fake_overview(binding, plates, version, kind, page):
        assert plates == ("sentinel",)
        assert (version, kind) == ("晓", "将")
        await UniMessage.text("进度 OK").finish(at_sender=True)

    monkeypatch.setattr(plugin.score_service, "get_plates", fake_get_plates)
    monkeypatch.setattr(plugin, "_plate_progress_overview", fake_overview)

    event = fake_group_message_event_v11(message="暁将进度")
    expected = Message([MessageSegment.at(12345678), MessageSegment.text(" 进度 OK")])
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


def test_plan_spp_removed():
    """「spp」计划档删除（L-32）：游戏无 S++ 档，原实现与 s 同阈值静默等同。"""
    from nonebot_plugin_awmc_helper.plugins.tables import matchers as tm
    from nonebot_plugin_awmc_helper.plugins.tables.sheet import PLANS

    assert "spp" not in tm.PLAN_RE
    assert "spp" not in PLANS
