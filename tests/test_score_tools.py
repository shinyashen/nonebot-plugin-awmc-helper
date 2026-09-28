"""awmc.score_tools 分数线指令测试。"""

from pathlib import Path

import pytest
from nonebug import App


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


async def _assert_reply(
    app: App, matcher, text: str, reply: str, *, user_id=12345678, with_session=True
):
    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    event = fake_group_message_event_v11(message=text, user_id=user_id)
    async with app.test_matcher(matcher) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter))
        ctx.receive_event(bot, event)
        if with_session:
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
                {"group_id": 87654321, "user_id": user_id, "no_cache": True},
                result={
                    "user_id": user_id,
                    "role": "member",
                    "card": "",
                    "nickname": "t",
                },
            )
        ctx.should_call_send(
            event,
            Message([MessageSegment.at(user_id), MessageSegment.text(f" {reply}")]),
            result=None,
            bot=bot,
        )
        ctx.should_finished()


@pytest.mark.asyncio
async def test_score_line_command(app: App, songs):
    from nonebot_plugin_awmc_helper.plugins import score_tools

    # 样例 231 的 DX Master：tap500 hold50 slide50 touch50 brk10
    total = 500 * 500 + 50 * 1000 + 50 * 1500 + 50 * 500 + 10 * 2500
    tap_great = total * 1 / 10000
    per_tap = 10000 / total
    b50_tap = total * (0.01 / 10) / 4 / 100
    b50_pct = total * (0.01 / 10) / 4 / total * 100
    expected = (
        "PENGUIN「大师」\n"
        "分数线「100.0%」\n允许的最多「TAP」「GREAT」数量为\n"
        f"「{tap_great:.2f}」(每个-{per_tap:.4f}%),\n"
        "「BREAK」50落(一共「10」个)\n"
        f"等价于「{b50_tap:.3f}」个「TAP」"
        f"「GREAT」(-{b50_pct:.4f}%)"
    )
    await _assert_reply(
        app,
        score_tools.score_line_cmd,
        "分数线 紫231 100",
        expected,
        with_session=False,
    )


@pytest.mark.asyncio
async def test_score_line_help(app: App):
    import base64

    from nonebot_plugin_awmc_helper.plugins import score_tools
    from nonebot_plugin_awmc_helper.core.render.tools import (
        text_to_image,
        image_to_bytes,
    )

    png = image_to_bytes(text_to_image(score_tools.SCORE_LINE_HELP))
    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    event = fake_group_message_event_v11(message="分数线 帮助")
    expected = Message(
        [
            MessageSegment.at(12345678),
            MessageSegment.image(f"base64://{base64.b64encode(png).decode()}"),
        ]
    )
    async with app.test_matcher(score_tools.score_line_cmd) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter))
        ctx.receive_event(bot, event)
        ctx.should_call_send(event, expected, result=None, bot=bot)
        ctx.should_finished()
