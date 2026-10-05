"""awmc.tables 指令测试：`更新定数表` 刷新必须执行（finish 截断回归）、
牌子牌单校验（不存在的牌名拒答）、13定数表、牌子条件帮助。"""

from typing import ClassVar
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
    # P2-b 收编：进度尾缀统一走条件化进度 matcher 的牌子身份回认
    async with app.test_matcher(plugin.progress_cmd) as ctx:
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
    async with app.test_matcher(tables.progress_cmd) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter))
        ctx.receive_event(bot, event)
        # 统一 handler 带 SessionQueryBinding（定数表 @ 代查）：会话注入触
        # 发群信息/成员信息拉取（定数表无凭据语义，绑定解析成功即放行）
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
            result={"user_id": 12345678, "role": "member", "card": "", "nickname": "t"},
        )
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
    monkeypatch.setattr(plugin, "plate_progress_overview", fake_overview)

    event = fake_group_message_event_v11(message="暁将进度")
    expected = Message([MessageSegment.at(12345678), MessageSegment.text(" 进度 OK")])
    # P2-b 收编：进度尾缀统一走条件化进度 matcher 的牌子身份回认
    async with app.test_matcher(plugin.progress_cmd) as ctx:
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
    from nonebot_plugin_awmc_helper.core import combo

    assert "spp" not in combo._PLAN_WORD


@pytest.mark.asyncio
async def test_score_table_at_target(app: App, db, songs, monkeypatch):
    """完成表 @代查（2026-09-30 扩展）：成绩按 at 目标绑定行拉取
    （resolve_query_binding 单源，目标只读不建行）。"""
    from types import SimpleNamespace

    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    from nonebot_plugin_awmc_helper.core.render import table_template
    from nonebot_plugin_awmc_helper.core.binding import binding_service
    from nonebot_plugin_awmc_helper.plugins.tables import matchers as plugin

    target = await binding_service.ensure("OneBot V11", "99999999")
    await binding_service.bind_divingfish_username(target, "fishuser")

    captured = {}

    async def fake_scores_all(binding, notify_slow=None):
        captured["user_id"] = binding.user_id
        return SimpleNamespace(scores=[])

    async def fake_base_image(entries, level=None):
        captured["level"] = level
        return "im-sentinel"

    def fake_cond(
        im,
        plan,
        scores,
        entries,
        *,
        header_text,
        header_prefix=None,
        theme=None,
        checker=None,
        by_level=None,
    ):
        captured["header"] = header_text
        captured["header_prefix"] = header_prefix
        captured["plan"] = plan
        captured["by_level"] = by_level
        return b"png"

    monkeypatch.setattr(plugin.score_service, "get_scores_all", fake_scores_all)
    monkeypatch.setattr(table_template, "rating_table_base_image", fake_base_image)
    monkeypatch.setattr(plugin, "draw_rating_table_cond", fake_cond)

    event = fake_group_message_event_v11(
        message=Message(
            [MessageSegment.text("13fc完成表"), MessageSegment.at(99999999)]
        ),
        user_id=12345678,
    )
    async with app.test_matcher(plugin.progress_cmd) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter))
        ctx.receive_event(bot, event)
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
            result={"user_id": 12345678, "role": "member", "card": "", "nickname": "t"},
        )
        ctx.should_call_send(
            event,
            Message(
                [MessageSegment.at(12345678), MessageSegment.image("base64://cG5n")]
            ),
            result=None,
            bot=bot,
        )
        ctx.should_finished()
    assert captured["user_id"] == "99999999"
    assert captured["level"] == "13"  # 单等级条件 → 文件底图优先
    # 表头拆段：单等级传等级串 + Level. 前缀（「Level. xx」整段日文字体口径，
    # 2026-10-01 QoL），表型说明由渲染层补
    assert captured["header"] == "13"
    assert captured["header_prefix"] == "Level."
    assert captured["by_level"] is False  # 单等级+判型混合 → 定数节盖章（防错位）


@pytest.mark.asyncio
async def test_plate_at_net_target_routes(app: App, db, songs, monkeypatch):
    """牌子 @日服 NET 目标（2026-10-04 起 NET 开放牌子）：随目标绑定路由，
    版本/牌种归一后进入完成表编排（口径转日服由 sheet 侧消费）。"""
    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter
    from nonebot_plugin_alconna.uniseg import UniMessage

    from nonebot_plugin_awmc_helper.core.binding import binding_service
    from nonebot_plugin_awmc_helper.plugins.tables import matchers as plugin

    target = await binding_service.ensure("OneBot V11", "99999999")
    await binding_service.bind_net(target, sega_id="sid", password="pw")

    captured = {}

    async def fake_sheet(binding, version, kind, page, *, preview=False):
        captured["user_id"] = binding.user_id
        captured["vk"] = (version, kind, page)
        await UniMessage.text("完成表 OK").finish(at_sender=True)

    monkeypatch.setattr(plugin, "plate_completion_sheet", fake_sheet)

    event = fake_group_message_event_v11(
        message=Message(
            [MessageSegment.text("晓将完成表"), MessageSegment.at(99999999)]
        ),
        user_id=12345678,
    )
    async with app.test_matcher(plugin.progress_cmd) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter))
        ctx.receive_event(bot, event)
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
            result={"user_id": 12345678, "role": "member", "card": "", "nickname": "t"},
        )
        ctx.should_call_send(
            event,
            Message([MessageSegment.at(12345678), MessageSegment.text(" 完成表 OK")]),
            result=None,
            bot=bot,
        )
        ctx.should_finished()
    assert captured["user_id"] == "99999999"
    assert captured["vk"] == ("晓", "将", 1)


@pytest.mark.asyncio
async def test_plate_cn_roster_rejected(app: App, db):
    """国服数据源查 丸将（CiRCLE 为日服版本）：真实存在的牌子也要落在
    绑定源的可查牌单内，拒绝并给口径上限提示（2026-10-04 定案）。"""
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
    from nonebot_plugin_awmc_helper.core.binding import binding_service

    # 显式钉水鱼绑定：口径限制按绑定数据源收口（默认残留行不可依赖）
    binding = await binding_service.ensure("OneBot V11", "12345678")
    await binding_service.bind_divingfish_username(binding, "tester")

    event = fake_group_message_event_v11(message="丸将进度", user_id=12345678)
    expected = Message(
        [
            MessageSegment.at(12345678),
            MessageSegment.text(" 没有找到「丸将」牌子。国服数据源可查至「彩」代牌子"),
        ]
    )
    async with app.test_matcher(plugin.progress_cmd) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter))
        ctx.receive_event(bot, event)
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
async def test_plate_jp_roster_progress(app: App, db, monkeypatch):
    """日服数据源查 丸将进度（JP 牌单）：查库路由到门面 get_plates（NET
    侧本地判牌由 test_core_sources 覆盖）。"""
    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter
    from nonebot_plugin_alconna.uniseg import UniMessage

    from nonebot_plugin_awmc_helper.core.binding import binding_service
    from nonebot_plugin_awmc_helper.plugins.tables import matchers as plugin

    binding = await binding_service.ensure("OneBot V11", "12345678")
    await binding_service.bind_net(binding, sega_id="sid", password="pw")

    async def fake_get_plates(b, plate, notify_slow=None):
        assert plate == "丸将"
        return ("sentinel",)

    async def fake_overview(binding, plates, version, kind, page):
        assert plates == ("sentinel",)
        assert (version, kind) == ("丸", "将")
        await UniMessage.text("进度 OK").finish(at_sender=True)

    monkeypatch.setattr(plugin.score_service, "get_plates", fake_get_plates)
    monkeypatch.setattr(plugin, "plate_progress_overview", fake_overview)

    event = fake_group_message_event_v11(message="丸将进度", user_id=12345678)
    expected = Message([MessageSegment.at(12345678), MessageSegment.text(" 进度 OK")])
    async with app.test_matcher(plugin.progress_cmd) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter))
        ctx.receive_event(bot, event)
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
            result={"user_id": 12345678, "role": "member", "card": "", "nickname": "t"},
        )
        ctx.should_call_send(event, expected, result=None, bot=bot)
        ctx.should_finished()


@pytest.mark.asyncio
async def test_plate_completion_sheet_scope_split(app: App, db, songs, monkeypatch):
    """完成表口径分流（2026-10-04）：日服绑定 → JP 视图曲集 + JP 区间
    （丸 将命中样例库 CiRCLE 段谱面）；国服绑定维持 CN 视图与 CN 区间。"""
    from types import SimpleNamespace

    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.render import table_template
    from nonebot_plugin_awmc_helper.core.binding import binding_service
    from nonebot_plugin_awmc_helper.plugins.tables import sheet

    class _Sentinel(Exception):
        pass

    calls = {}
    real_get_all = song_service.get_all

    async def fake_jp_all():
        calls["view"] = "jp"
        return await real_get_all()

    async def fake_cn_all():
        calls["view"] = "cn"
        return await real_get_all()

    async def fake_draw(
        version, kind, scores, entries, *, page, song_service, jp=False
    ):
        calls["draw"] = (version, kind, len(entries), jp)
        raise _Sentinel

    async def fake_scores(b, notify_slow=None):
        return SimpleNamespace(scores=[])

    monkeypatch.setattr(song_service, "jp_all", fake_jp_all)
    monkeypatch.setattr(song_service, "get_all", fake_cn_all)
    monkeypatch.setattr(table_template, "draw_plate_table_with_fallback", fake_draw)
    monkeypatch.setattr(sheet.score_service, "get_scores_all", fake_scores)

    jp_binding = await binding_service.ensure("OneBot V11", "12345678")
    await binding_service.bind_net(jp_binding, sega_id="sid", password="pw")
    with pytest.raises(_Sentinel):
        await sheet.plate_completion_sheet(jp_binding, "丸", "将", 1)
    assert calls["view"] == "jp"
    version, kind, n_entries, jp = calls["draw"]
    assert (version, kind, jp) == ("丸", "将", True)
    assert n_entries > 0  # 样例库 CiRCLE 段（version=26000）谱面进入丸范围

    calls.clear()
    cn_binding = await binding_service.ensure("OneBot V11", "12345679")
    await binding_service.bind_divingfish_username(cn_binding, "tester")
    with pytest.raises(_Sentinel):
        await sheet.plate_completion_sheet(cn_binding, "超", "将", 1)
    assert calls["view"] == "cn"
    assert calls["draw"] == ("超", "将", calls["draw"][2], False)
    assert calls["draw"][2] > 0  # 样例库 Splash 段（version=12000）谱面进入超范围


@pytest.mark.asyncio
async def test_plate_cn_preview_routing(app: App, db, monkeypatch):
    """国服查日服领先牌路由（2026-10-05）：完成表带 preview=True 进编排，
    进度总览维持牌单拒绝（test_plate_cn_roster_rejected）。"""
    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter
    from nonebot_plugin_alconna.uniseg import UniMessage

    from nonebot_plugin_awmc_helper.core.binding import binding_service
    from nonebot_plugin_awmc_helper.plugins.tables import matchers as plugin

    binding = await binding_service.ensure("OneBot V11", "12345678")
    await binding_service.bind_divingfish_username(binding, "tester")

    captured = {}

    async def fake_sheet(binding, version, kind, page, *, preview=False):
        captured["args"] = (version, kind, preview)
        await UniMessage.text("完成表 OK").finish(at_sender=True)

    monkeypatch.setattr(plugin, "plate_completion_sheet", fake_sheet)

    async def _run(message: str, version: str, kind: str, preview: bool):
        captured.clear()
        event = fake_group_message_event_v11(message=message, user_id=12345678)
        expected = Message(
            [MessageSegment.at(12345678), MessageSegment.text(" 完成表 OK")]
        )
        async with app.test_matcher(plugin.progress_cmd) as ctx:
            bot = ctx.create_bot(
                base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter)
            )
            ctx.receive_event(bot, event)
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
        assert captured["args"] == (version, kind, preview)

    # 丸（CiRCLE，日服领先）与 未（占位字）完成表均降级为预览放行
    await _run("丸将完成表", "丸", "将", True)
    await _run("未将完成表", "未", "将", True)


@pytest.mark.asyncio
async def test_plate_completion_cn_preview_sheet(app: App, db, songs, monkeypatch):
    """完成表预览语义（2026-10-05）：强制 JP 视图、不取成绩不盖标志，
    发图前提示待实装；「未」提示须带具体版本名（非官方牌名）。"""
    from types import SimpleNamespace

    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.render import table_template
    from nonebot_plugin_awmc_helper.core.binding import binding_service
    from nonebot_plugin_awmc_helper.plugins.tables import sheet

    calls = {}
    real_get_all = song_service.get_all

    async def fake_jp_all():
        calls["view"] = "jp"
        return await real_get_all()

    async def fake_draw(
        version, kind, scores, entries, *, page, song_service, jp=False
    ):
        calls["draw"] = (version, kind, len(scores), jp)
        return b"png"

    async def fake_scores(b, notify_slow=None):
        calls["scores"] = True
        return SimpleNamespace(scores=[])

    class FakeUniMessage:
        sent: ClassVar[list] = []

        def __init__(self, *parts):
            self.parts = parts

        @classmethod
        def text(cls, s):
            return cls(s)

        @classmethod
        def image(cls, **kw):
            return cls(kw)

        async def send(self, **kw):
            FakeUniMessage.sent.append(("send", self.parts))

        async def finish(self, **kw):
            FakeUniMessage.sent.append(("finish", self.parts))

    monkeypatch.setattr(song_service, "jp_all", fake_jp_all)
    monkeypatch.setattr(table_template, "draw_plate_table_with_fallback", fake_draw)
    monkeypatch.setattr(sheet.score_service, "get_scores_all", fake_scores)
    monkeypatch.setattr(sheet, "UniMessage", FakeUniMessage)

    # 样例库补一首 MAGiCAL 段曲（未 = 现行代占位，27000）：缺曲则空范围提前收尾
    from mocks import make_diff, make_song, sample_songs, seed_service

    magical = make_song(8010, "（构造）MAGiCAL 曲", diffs=[make_diff(version=27000)])
    await seed_service(song_service, [*sample_songs(), magical])

    cn = await binding_service.ensure("OneBot V11", "12345678")
    await binding_service.bind_divingfish_username(cn, "tester")

    # 丸将预览：JP 视图、成绩零拉取（无章可盖）、提示待实装
    FakeUniMessage.sent.clear()
    await sheet.plate_completion_sheet(cn, "丸", "将", 1, preview=True)
    assert calls["view"] == "jp"
    assert calls["draw"] == ("丸", "将", 0, True)
    assert "scores" not in calls
    (mode, parts), (finish_mode, _img) = FakeUniMessage.sent
    assert mode == "send"
    assert "国服待实装" in parts[0]
    assert finish_mode == "finish"

    # 未将预览：提示须带具体版本名（未 非官方名）与待实装
    FakeUniMessage.sent.clear()
    calls.pop("view", None)
    await sheet.plate_completion_sheet(cn, "未", "将", 1, preview=True)
    assert calls["view"] == "jp"
    _mode, parts = FakeUniMessage.sent[0]
    assert "MAGiCAL" in parts[0]
    assert "待实装" in parts[0]

    # 日服源查 未将（非预览）：成绩照取，提示占位字与版本名
    jp = await binding_service.ensure("OneBot V11", "12345679")
    await binding_service.bind_net(jp, sega_id="sid", password="pw")
    FakeUniMessage.sent.clear()
    await sheet.plate_completion_sheet(jp, "未", "将", 1)
    assert calls["scores"] is True
    assert calls["draw"] == ("未", "将", 0, True)
    _mode, parts = FakeUniMessage.sent[0]
    assert "MAGiCAL" in parts[0]
    assert "待实装" not in parts[0]


@pytest.mark.asyncio
async def test_score_list_at_trailing_space(app: App, db, songs, monkeypatch):
    """@代查回归（2026-09-30 线上实测）：QQ 客户端在 at 段后自动留空格，
    消息串以尾随空格结束——锚定正则必须容忍，否则指令完全不触发
    （消息形状原样取自服务器日志）。

    P2-b 收编后「13+」经解析器成为等级条件（键集过滤，样例库无 13+ 谱面），
    空态文案为谱面集口径「没有符合条件的谱面」。
    """
    from types import SimpleNamespace

    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    from nonebot_plugin_awmc_helper.core.binding import binding_service
    from nonebot_plugin_awmc_helper.plugins.tables import matchers as plugin

    target = await binding_service.ensure("OneBot V11", "99999999")
    await binding_service.bind_divingfish_username(target, "fishuser")

    captured = {}

    async def fake_scores_all(binding, notify_slow=None):
        captured["user_id"] = binding.user_id
        return SimpleNamespace(scores=[])

    monkeypatch.setattr(plugin.score_service, "get_scores_all", fake_scores_all)

    event = fake_group_message_event_v11(
        message=Message(
            [
                MessageSegment.text("13+分数列表"),
                MessageSegment("at", {"qq": "99999999", "name": "你的避税有点多了"}),
                MessageSegment.text(" "),
            ]
        ),
        user_id=12345678,
    )
    async with app.test_matcher(plugin.score_list_cmd) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter))
        ctx.receive_event(bot, event)
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
            result={"user_id": 12345678, "role": "member", "card": "", "nickname": "t"},
        )
        ctx.should_call_send(
            event,
            Message(
                [
                    MessageSegment.at(12345678),
                    MessageSegment.text(" 没有符合条件的谱面"),
                ]
            ),
            result=None,
            bot=bot,
        )
        ctx.should_finished()
    # 收编后「13+」在样例库无谱面，空键集于 get_scores_all 之前短路（captured
    # 不再有值）；at 代查目标可达性由 test_score_table_at_target 继续覆盖
    assert captured == {}


# ------------------------------------------------- P2-b 条件化进度/分数列表


def _fake_scores():
    """样例成绩（真实曲目 + 合理值）：199 SD 紫（东方曲、13 级锚）。"""
    from maimai_py import FCType, RateType, SongType, LevelIndex, ScoreExtend
    from maimai_py.utils import ScoreCoefficient

    def score(song_id, type_, li, ach, level_value, *, fc=None):
        return ScoreExtend(
            id=song_id,
            level="13",
            level_index=li,
            achievements=ach,
            fc=fc,
            fs=None,
            dx_score=2000,
            dx_rating=int(ScoreCoefficient(ach).ra(level_value)),
            play_count=None,
            play_time=None,
            rate=RateType._from_achievement(ach),
            type=type_,
            title="t",
            level_value=level_value,
            level_dx_score=3000,
            dx_star=None,
            version=26000,
        )

    return [
        score(199, SongType.STANDARD, LevelIndex.MASTER, 100.5, 13.3, fc=FCType.AP),
        score(199, SongType.DX, LevelIndex.MASTER, 97.0, 13.0),
    ]


def _combo_progress_expected(cond_text: str, completed: list) -> "tuple[int, int, int]":
    """进度总览几何（handler 同 NB 公式），供预期图构造。"""

    def played_rows(count: int) -> int:
        return max(4, -(-count // 5))

    comp_limit = 60 if True else 30
    c_y = played_rows(len(completed[:comp_limit])) * 109 + 140
    u_y = played_rows(0) * 109 + 140
    n_y = max(4, -(-0 // 20)) * 65 + 140
    return c_y, u_y, n_y


@pytest.mark.asyncio
@requires_assets
async def test_combo_progress_renders(app: App, db, songs, monkeypatch):
    """东方进度：条件化三段总览（评级章默认判型，199 SD 紫 100.5% 已完成）。"""
    import base64 as _b64
    from types import SimpleNamespace

    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    from nonebot_plugin_awmc_helper.core.binding import binding_service
    from nonebot_plugin_awmc_helper.plugins.tables import matchers as plugin
    from nonebot_plugin_awmc_helper.core.render.score import DrawScore

    binding = await binding_service.ensure("OneBot V11", "12345678")
    await binding_service.bind_divingfish_username(binding, "tester")

    scores = _fake_scores()
    completed = [scores[0]]  # 199 SD 紫（东方曲、≥80%）

    async def fake_scores_all(b, notify_slow=None):
        return SimpleNamespace(scores=scores)

    monkeypatch.setattr(plugin.score_service, "get_scores_all", fake_scores_all)

    c_y, u_y, n_y = _combo_progress_expected("东方", completed)
    card = DrawScore(150 + c_y + u_y + n_y, service="Diving-Fish")
    expected_png = card.draw_plan("东方", completed, c_y, [], u_y, [], "", 60, goal="A")

    event = fake_group_message_event_v11(message="东方进度", user_id=12345678)
    expected = Message(
        [
            MessageSegment.at(12345678),
            MessageSegment.image(f"base64://{_b64.b64encode(expected_png).decode()}"),
        ]
    )
    async with app.test_matcher(plugin.progress_cmd) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter))
        ctx.receive_event(bot, event)
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
            result={"user_id": 12345678, "role": "member", "card": "", "nickname": "t"},
        )
        ctx.should_call_send(event, expected, result=None, bot=bot)
        ctx.should_finished()


@pytest.mark.asyncio
async def test_combo_progress_inapplicable_rejected(app: App, db, songs, monkeypatch):
    """理想进度：修改类条件不适用于进度（§9.7 C 类）→ 拒绝并提示。"""
    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    from nonebot_plugin_awmc_helper.plugins import tables as plugin

    event = fake_group_message_event_v11(message="理想进度", user_id=12345678)
    expected = Message(
        [
            MessageSegment.at(12345678),
            MessageSegment.text(" 理想 不适用于进度"),
        ]
    )
    async with app.test_matcher(plugin.progress_cmd) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter))
        ctx.receive_event(bot, event)
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
            result={"user_id": 12345678, "role": "member", "card": "", "nickname": "t"},
        )
        ctx.should_call_send(event, expected, result=None, bot=bot)
        ctx.should_finished()


@pytest.mark.asyncio
async def test_combo_score_table_cond(app: App, db, songs, monkeypatch):
    """东方fc完成表：条件底图现算链路（draw_rating_table_cond 消费）。"""
    from types import SimpleNamespace

    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    from nonebot_plugin_awmc_helper.core.render import table_template
    from nonebot_plugin_awmc_helper.core.binding import binding_service
    from nonebot_plugin_awmc_helper.plugins.tables import matchers as plugin

    binding = await binding_service.ensure("OneBot V11", "12345678")
    await binding_service.bind_divingfish_username(binding, "tester")

    async def fake_scores_all(b, notify_slow=None):
        return SimpleNamespace(scores=[])

    captured = {}

    async def fake_base_image(entries, level=None):
        captured["level"] = level
        return "im-sentinel"

    def fake_cond(
        im,
        plan,
        scores,
        entries,
        *,
        header_text,
        header_prefix=None,
        theme=None,
        checker=None,
        by_level=None,
    ):
        captured["header"] = header_text
        captured["header_prefix"] = header_prefix
        captured["plan"] = plan
        captured["by_level"] = by_level
        return b"png"

    monkeypatch.setattr(plugin.score_service, "get_scores_all", fake_scores_all)
    monkeypatch.setattr(table_template, "rating_table_base_image", fake_base_image)
    monkeypatch.setattr(plugin, "draw_rating_table_cond", fake_cond)

    event = fake_group_message_event_v11(message="东方fc完成表", user_id=12345678)
    expected = Message(
        [MessageSegment.at(12345678), MessageSegment.image("base64://cG5n")]
    )
    async with app.test_matcher(plugin.progress_cmd) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter))
        ctx.receive_event(bot, event)
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
            result={"user_id": 12345678, "role": "member", "card": "", "nickname": "t"},
        )
        ctx.should_call_send(event, expected, result=None, bot=bot)
        ctx.should_finished()
    # 条件版：无等级条件 → 底图现算（level=None）；表头=规范化 label 串
    # （评级档大写，QoL3），无 Level. 前缀；plan=fc 判型；跨等级 → 标级大类盖章
    assert captured == {
        "level": None,
        "header": "东方·FC",
        "header_prefix": None,
        "plan": "fc",
        "by_level": True,
    }


@pytest.mark.asyncio
async def test_combo_ds_table_inapplicable(app: App, db, songs):
    """fc定数表：达标型成绩条件不适用于定数表（§9.7 B1）→ 拒绝提示。"""
    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    from nonebot_plugin_awmc_helper.plugins import tables as plugin

    event = fake_group_message_event_v11(message="fc定数表", user_id=12345678)
    expected = Message(
        [MessageSegment.at(12345678), MessageSegment.text(" FC 不适用于定数表")]
    )
    async with app.test_matcher(plugin.progress_cmd) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter))
        ctx.receive_event(bot, event)
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
            result={"user_id": 12345678, "role": "member", "card": "", "nickname": "t"},
        )
        ctx.should_call_send(event, expected, result=None, bot=bot)
        ctx.should_finished()
