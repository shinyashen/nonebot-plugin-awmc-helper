"""awmc.base 帮助系统入口与项目地址指令测试（M10）。"""

import base64

import pytest
from nonebug import App

SELF_ID = "1234567890"


def _forward_nodes(entries) -> list[dict]:
    """try_send_forward 预期发出的节点裸 dict（与 core.forward 构造同源）。"""
    from nonebot_plugin_awmc_helper.core.forward import NODE_NICKNAME

    return [
        {
            "type": "node",
            "data": {
                "name": NODE_NICKNAME,
                "uin": SELF_ID,
                "content": [{"type": "text", "data": {"text": e}}],
            },
        }
        for e in entries
    ]


def _text_reply(user_id: int, text: str):
    from nonebot.adapters.onebot.v11 import Message, MessageSegment

    return Message([MessageSegment.at(user_id), MessageSegment.text(" " + text)])


def _entries_of(page):
    from nonebot_plugin_awmc_helper.core.help import page_entries, help_registry

    return page_entries(help_registry, page)


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
async def test_help_overview_forward(app: App):
    """总览：合并转发，节点与 page_entries 同源。"""
    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    from nonebot_plugin_awmc_helper.plugins import base
    from nonebot_plugin_awmc_helper.core.help import OverviewPage

    entries = _entries_of(OverviewPage(include_hidden=False))
    nodes = _forward_nodes(entries)
    event = fake_group_message_event_v11(message="舞萌帮助")
    async with app.test_matcher(base.help_cmd) as ctx:
        bot = ctx.create_bot(
            base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter), self_id=SELF_ID
        )
        ctx.receive_event(bot, event)
        ctx.should_call_api(
            "send_group_forward_msg",
            {"group_id": 87654321, "message": nodes, "messages": nodes},
            result=None,
        )
        ctx.should_finished()


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["wmhelp", "MAI帮助", "帮助maimaidx"])
async def test_root_aliases(app: App, text: str):
    """根词别名（含大小写）命中同一帮助 matcher。"""
    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    from nonebot_plugin_awmc_helper.plugins import base
    from nonebot_plugin_awmc_helper.core.help import OverviewPage

    nodes = _forward_nodes(_entries_of(OverviewPage(include_hidden=False)))
    event = fake_group_message_event_v11(message=text)
    async with app.test_matcher(base.help_cmd) as ctx:
        bot = ctx.create_bot(
            base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter), self_id=SELF_ID
        )
        ctx.receive_event(bot, event)
        ctx.should_call_api(
            "send_group_forward_msg",
            {"group_id": 87654321, "message": nodes, "messages": nodes},
            result=None,
        )
        ctx.should_finished()


@pytest.mark.asyncio
async def test_command_detail_plain_message(app: App):
    """单节点指令详情页：普通消息而非转发卡。"""
    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    from nonebot_plugin_awmc_helper.plugins import base
    from nonebot_plugin_awmc_helper.core.help import CommandPage, help_registry

    spec = help_registry.lookup_command("b50")
    assert spec is not None
    text = _entries_of(CommandPage(spec=spec))[0]
    event = fake_group_message_event_v11(message="舞萌帮助 b50")
    async with app.test_matcher(base.help_cmd) as ctx:
        bot = ctx.create_bot(
            base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter), self_id=SELF_ID
        )
        ctx.receive_event(bot, event)
        ctx.should_call_send(event, _text_reply(12345678, text), result=None, bot=bot)
        ctx.should_finished()


@pytest.mark.asyncio
async def test_category_page_forward(app: App):
    """类别页（含旧触发词 帮助maimaiDX排卡 → 排卡类别）走转发。"""
    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    from nonebot_plugin_awmc_helper.plugins import base
    from nonebot_plugin_awmc_helper.core.help import CategoryPage, help_registry

    nodes = _forward_nodes(
        _entries_of(CategoryPage(help_registry.categories["arcade"]))
    )
    for text in ("舞萌帮助 排卡", "帮助maimaiDX排卡"):
        event = fake_group_message_event_v11(message=text)
        async with app.test_matcher(base.help_cmd) as ctx:
            bot = ctx.create_bot(
                base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter), self_id=SELF_ID
            )
            ctx.receive_event(bot, event)
            ctx.should_call_api(
                "send_group_forward_msg",
                {"group_id": 87654321, "message": nodes, "messages": nodes},
                result=None,
            )
            ctx.should_finished()


@pytest.mark.asyncio
async def test_guide_page_forward(app: App):
    """指南页：头节点含标题与前置条件，一步一节点。"""
    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    from nonebot_plugin_awmc_helper.plugins import base
    from nonebot_plugin_awmc_helper.core.help import GuidePage, help_registry

    page = GuidePage(guide=help_registry.guides["查分上手"])
    entries = _entries_of(page)
    assert len(entries) == 1 + len(page.guide.steps)
    nodes = _forward_nodes(entries)
    event = fake_group_message_event_v11(message="舞萌帮助 查分上手")
    async with app.test_matcher(base.help_cmd) as ctx:
        bot = ctx.create_bot(
            base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter), self_id=SELF_ID
        )
        ctx.receive_event(bot, event)
        ctx.should_call_api(
            "send_group_forward_msg",
            {"group_id": 87654321, "message": nodes, "messages": nodes},
            result=None,
        )
        ctx.should_finished()


@pytest.mark.asyncio
async def test_forward_fallback_to_image(app: App):
    """转发失败降级为整图（page_text 同源渲染）。"""
    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    from nonebot_plugin_awmc_helper.plugins import base
    from nonebot_plugin_awmc_helper.core.help import (
        CategoryPage,
        page_text,
        help_registry,
    )
    from nonebot_plugin_awmc_helper.core.render.tools import text_image_bytes

    page = CategoryPage(help_registry.categories["arcade"])
    entries = _entries_of(page)
    png = text_image_bytes(page_text(help_registry, page), size=22)
    nodes = _forward_nodes(entries)
    event = fake_group_message_event_v11(message="舞萌帮助 排卡")
    async with app.test_matcher(base.help_cmd) as ctx:
        bot = ctx.create_bot(
            base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter), self_id=SELF_ID
        )
        ctx.receive_event(bot, event)
        ctx.should_call_api(
            "send_group_forward_msg",
            {"group_id": 87654321, "message": nodes, "messages": nodes},
            result=None,
            exception=RuntimeError("protocol error"),
        )
        ctx.should_call_send(
            event,
            Message(
                [
                    MessageSegment.at(12345678),
                    MessageSegment.image(f"base64://{base64.b64encode(png).decode()}"),
                ]
            ),
            result=None,
            bot=bot,
        )
        ctx.should_finished()


@pytest.mark.asyncio
async def test_not_found_hint(app: App):
    """未命中条目：提示可用入口而非静默。"""
    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    from nonebot_plugin_awmc_helper.plugins import base

    hint = (
        "没有找到「麻瓜条目」。\n"
        "发送「舞萌帮助」查看总览；详情支持 类别/指令/指南 三类条目。"
    )
    event = fake_group_message_event_v11(message="舞萌帮助 麻瓜条目")
    async with app.test_matcher(base.help_cmd) as ctx:
        bot = ctx.create_bot(
            base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter), self_id=SELF_ID
        )
        ctx.receive_event(bot, event)
        ctx.should_call_send(event, _text_reply(12345678, hint), result=None, bot=bot)
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
