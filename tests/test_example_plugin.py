"""示例插件 awmc_plugins/nonebot_plugin_awmc_example 集成测试。

示例插件由主插件的 awmc_plugins/ 加载器在会话初始化时自动加载
（conftest 以仓库根为 CWD 调用 load_from_toml）。本文件验证它经
真实加载链路加载后指令可用——core 公开接口一变，此测试即红。
"""

import base64

import pytest
from nonebug import App


@pytest.fixture
async def songs(tmp_path):
    """注入样例曲库 + 独立临时数据库（别名查询走本地库）。"""
    from mocks import sample_songs, seed_service

    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.core.songs import song_service

    store.set_db_file(tmp_path / "awmc.db")
    await store.init_db()
    await seed_service(song_service, sample_songs())
    yield
    song_service._ready.clear()
    store.set_db_file(None)


def _penguin_song():
    from mocks import sample_songs

    return next(s for s in sample_songs() if s.id == 231)


@pytest.mark.asyncio
async def test_ext_song_query_replies_image(app: App, songs):
    """ext点歌 penguin：标题模糊命中 PENGUIN(231)，回复渲染图。"""
    import nonebot
    import nonebot_plugin_awmc_example as example
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    from nonebot_plugin_awmc_helper.core.render.tools import (
        text_to_image,
        image_to_bytes,
    )

    event = fake_group_message_event_v11(message="ext点歌 penguin")
    png = image_to_bytes(
        text_to_image(example.format_result("penguin", [_penguin_song()], 5))
    )
    expected = Message(
        [
            MessageSegment.at(12345678),
            MessageSegment.image(f"base64://{base64.b64encode(png).decode()}"),
        ]
    )
    async with app.test_matcher(example.song_cmd) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter))
        ctx.receive_event(bot, event)
        # UniSession 注入需要 uninfo 拉取群/成员信息
        ctx.should_call_api(
            "get_group_info",
            {"group_id": 87654321},
            result={
                "group_id": 87654321,
                "group_name": "测试群",
                "member_count": 10,
                "max_member_count": 100,
            },
        )
        ctx.should_call_api(
            "get_group_member_info",
            {"group_id": 87654321, "user_id": 12345678, "no_cache": True},
            result={
                "user_id": 12345678,
                "role": "member",
                "card": "",
                "nickname": "test",
            },
        )
        ctx.should_call_send(event, expected, result=None, bot=bot)
        ctx.should_finished()


@pytest.mark.asyncio
async def test_ext_song_not_found(app: App, songs):
    """无命中时回退友好文案。"""
    import nonebot
    import nonebot_plugin_awmc_example as example
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    event = fake_group_message_event_v11(message="ext点歌 存在感为零的曲子")
    expected = Message(
        [
            MessageSegment.at(12345678),
            MessageSegment.text(" 没找到包含「存在感为零的曲子」的曲目"),
        ]
    )
    async with app.test_matcher(example.song_cmd) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter))
        ctx.receive_event(bot, event)
        ctx.should_call_api(
            "get_group_info",
            {"group_id": 87654321},
            result={
                "group_id": 87654321,
                "group_name": "测试群",
                "member_count": 10,
                "max_member_count": 100,
            },
        )
        ctx.should_call_api(
            "get_group_member_info",
            {"group_id": 87654321, "user_id": 12345678, "no_cache": True},
            result={
                "user_id": 12345678,
                "role": "member",
                "card": "",
                "nickname": "test",
            },
        )
        ctx.should_call_send(event, expected, result=None, bot=bot)
        ctx.should_finished()
