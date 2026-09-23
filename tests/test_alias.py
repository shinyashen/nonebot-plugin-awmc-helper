"""awmc.alias 别名子插件测试（nonebug + respx）。"""

import pytest
from nonebug import App

BASE = "https://www.yuzuchan.moe/api/v2"


@pytest.fixture
async def songs(tmp_path):
    """注入样例曲库 + 独立临时数据库。"""
    from mocks import seed_service

    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.core.songs import song_service

    store.set_db_file(tmp_path / "awmc.db")
    await store.init_db()
    await seed_service(song_service)
    yield
    song_service._ready.clear()
    store.set_db_file(None)


async def _assert_reply(
    app: App,
    matcher,
    text: str,
    reply: str,
    *,
    user_id=12345678,
    role="member",
    with_session=False,
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
            # handler 注入了 uninfo Session（缓存已关），会实时拉取群/成员信息
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
                {"group_id": 87654321, "user_id": user_id, "no_cache": True},
                result={
                    "user_id": user_id,
                    "role": role,
                    "card": "",
                    "nickname": "test",
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
async def test_alias_of_song(app: App, songs):
    from nonebot_plugin_awmc_helper.plugins import alias

    await _assert_reply(
        app,
        alias.alias_song,
        "企鹅舞有什么别名",
        "该曲目有以下别名：\nID：231、10231\n企鹅舞",
        with_session=True,
    )


@pytest.mark.asyncio
async def test_alias_by_id(app: App, songs):
    from nonebot_plugin_awmc_helper.plugins import alias

    await _assert_reply(
        app,
        alias.alias_song,
        "id 500有什么别名",
        "该曲目有以下别名：\nID：500、10500\n普瑞\n普雷呃伦斯",
        with_session=True,
    )


@pytest.mark.asyncio
async def test_alias_dx_only_song_ids(app: App, songs):
    """只有 DX 谱的曲：仅展示 DX id（根 id+10000），不纳入标准位根 id。"""
    from mocks import make_diff, make_song, sample_songs, seed_service
    from maimai_py import SongType

    from nonebot_plugin_awmc_helper.plugins import alias
    from nonebot_plugin_awmc_helper.core.songs import song_service

    dx_only = make_song(
        603, "DXOnlySong", aliases=["敌敌畏"], diffs=[make_diff(type=SongType.DX)]
    )
    await seed_service(song_service, [*sample_songs(), dx_only])
    await _assert_reply(
        app,
        alias.alias_song,
        "敌敌畏有什么别名",
        "该曲目有以下别名：\nID：10603\n敌敌畏",
        with_session=True,
    )


@pytest.mark.asyncio
async def test_alias_utage_only_song_ids(app: App, songs):
    """只有宴谱的曲：仅展示宴谱机台 id（无标准/DX 位）。"""
    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.plugins import alias
    from nonebot_plugin_awmc_helper.core.songs import song_service

    await store.add_local_alias(901, "宴曲", "u")
    await song_service.reload_alias_index()
    await _assert_reply(
        app,
        alias.alias_song,
        "id 901有什么别名",
        "该曲目有以下别名：\nID：100001\n宴曲",
        with_session=True,
    )


@pytest.mark.asyncio
async def test_alias_multi_match_forward(app: App, songs):
    """多曲命中别名：OB11 以合并转发逐曲展示（首条为命中数量）。"""
    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.plugins import alias
    from nonebot_plugin_awmc_helper.core.songs import song_service

    # 本地别名让「企鹅」同时命中 231（柚子别名企鹅舞）与 500（本地+柚子别名）
    await store.add_local_alias(231, "企鹅", "u")
    await store.add_local_alias(500, "企鹅", "u")
    await song_service.reload_alias_index()

    event = fake_group_message_event_v11(message="企鹅有什么别名", user_id=12345678)
    forward = Message(
        [
            MessageSegment.node_custom(
                1234567890, "Bot", Message("找到2个相同别名的曲目：")
            ),
            MessageSegment.node_custom(
                1234567890, "Bot", Message("ID：231、10231\n企鹅舞\n企鹅")
            ),
            MessageSegment.node_custom(
                1234567890, "Bot", Message("ID：500、10500\n普瑞\n普雷呃伦斯\n企鹅")
            ),
        ]
    )
    async with app.test_matcher(alias.alias_song) as ctx:
        bot = ctx.create_bot(
            base=Bot,
            adapter=nonebot.get_adapter(OnebotV11Adapter),
            self_id="1234567890",  # 合并转发节点 user_id 取 self_id，须为数字
        )
        ctx.receive_event(bot, event)
        # handler 注入 uninfo Session，会实时拉取群/成员信息
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
        # message/messages 双参数（LLOneBot 读 message、NapCat 读 messages）
        ctx.should_call_api(
            "send_group_forward_msg",
            {"group_id": 87654321, "message": forward, "messages": forward},
            result=None,
        )
        ctx.should_finished()


@pytest.mark.asyncio
async def test_alias_multi_match_forward_fallback(app: App, songs):
    """合并转发失败（默认 bot 非数字 self_id 无法构造节点）→ 降级普通消息。"""
    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.plugins import alias
    from nonebot_plugin_awmc_helper.core.songs import song_service

    await store.add_local_alias(231, "企鹅", "u")
    await store.add_local_alias(500, "企鹅", "u")
    await song_service.reload_alias_index()

    msg = (
        "找到2个相同别名的曲目：\n"
        "ID：231、10231\n企鹅舞\n企鹅\n======\n"
        "ID：500、10500\n普瑞\n普雷呃伦斯\n企鹅"
    )
    await _assert_reply(app, alias.alias_song, "企鹅有什么别名", msg, with_session=True)


@pytest.mark.asyncio
async def test_alias_not_found(app: App, songs):
    from nonebot_plugin_awmc_helper.plugins import alias

    await _assert_reply(
        app,
        alias.alias_song,
        "不存在的东西有什么别名",
        "未找到此歌曲\n可以使用「添加别名」指令给该乐曲添加别名",
        with_session=True,
    )


@pytest.mark.asyncio
async def test_local_alias_apply(app: App, songs, tmp_path):
    import respx

    from nonebot_plugin_awmc_helper.plugins import alias

    with respx.mock(assert_all_called=False) as m:
        # 柚子无该别名 → 本地添加成功
        m.get(f"{BASE}/aliases/maimaidx/aliases").respond(json={"message": "未找到"})
        await _assert_reply(
            app,
            alias.alias_local_apply,
            "添加本地别名 231 企鹅",
            "已成功为ID「231」添加别名「企鹅」到本地别名库",
            with_session=True,
        )
        from nonebot_plugin_awmc_helper.core.songs import song_service

        aliases = await song_service.aliases_of(231)
        assert aliases is not None
        assert "企鹅" in aliases

        # 重复添加 → 提示已存在（同样必须在 mock 上下文内，避免真实请求柚子）
        await _assert_reply(
            app,
            alias.alias_local_apply,
            "添加本地别名 231 企鹅",
            "本地别名库已存在该别名",
            with_session=True,
        )
    from nonebot_plugin_awmc_helper.core import store as awmc_store

    assert (
        await awmc_store.add_local_alias(500, "企鹅", "u") is True
    )  # 不同曲同别名允许


@pytest.mark.asyncio
async def test_local_alias_dup_on_server(app: App, songs):
    import respx

    from nonebot_plugin_awmc_helper.plugins import alias

    with respx.mock(assert_all_called=False) as m:
        m.get(f"{BASE}/aliases/maimaidx/aliases").respond(
            json={
                "song_id": 231,
                "name": "PENGUIN",
                "is_votable": False,
                "alias": ["企鹅"],
            }
        )
        await _assert_reply(
            app,
            alias.alias_local_apply,
            "添加本地别名 231 企鹅",
            "该曲目的别名「企鹅」已存在别名服务器",
            with_session=True,
        )


@pytest.mark.asyncio
async def test_apply_and_agree(app: App, songs):
    import respx

    from nonebot_plugin_awmc_helper.plugins import alias

    with respx.mock(assert_all_called=False) as m:
        m.get(f"{BASE}/aliases/maimaidx/aliases").respond(json={"message": "未找到"})
        m.post(f"{BASE}/aliases/maimaidx/apply").respond(json={"message": "申请已提交"})
        await _assert_reply(
            app, alias.alias_apply, "添加别名 231 企鹅", "申请已提交", with_session=True
        )

        m.post(f"{BASE}/aliases/maimaidx/votes").respond(json={"message": "投票成功"})
        await _assert_reply(
            app, alias.alias_agree, "同意别名 ABC123", "投票成功", with_session=True
        )


@pytest.mark.asyncio
async def test_alias_status(app: App, songs):
    import base64

    import respx

    from nonebot_plugin_awmc_helper.plugins import alias
    from nonebot_plugin_awmc_helper.core.ext import yuzu as yuzu_ext

    with respx.mock(assert_all_called=False) as m:
        m.get(f"{BASE}/aliases/maimaidx/votes").respond(
            json=[
                {
                    "song_id": 231,
                    "apply_alias": "企鹅",
                    "tag": "ABC123",
                    "name": "PENGUIN",
                    "agree_votes": 3,
                    "votes": 10,
                }
            ]
        )
        import nonebot
        from fake import fake_group_message_event_v11
        from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
        from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

        votes = await yuzu_ext.yuzu_client.get_status()
        lines = [
            f"- {v.tag}：\n- ID：{v.song_id}"
            f"\n- 别名：{v.apply_alias}\n- 票数：{v.agree_votes}/{v.votes}"
            for v in votes
        ]
        lines.append("第「1」页，共「1」页")
        from nonebot_plugin_awmc_helper.core.render.tools import (
            text_to_image,
            image_to_bytes,
        )

        png = image_to_bytes(text_to_image("\n".join(lines)))
        expected = Message(
            [
                MessageSegment.at(12345678),
                MessageSegment.image(f"base64://{base64.b64encode(png).decode()}"),
            ]
        )

        event = fake_group_message_event_v11(message="当前投票")
        async with app.test_matcher(alias.alias_status) as ctx:
            bot = ctx.create_bot(
                base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter)
            )
            ctx.receive_event(bot, event)
            ctx.should_call_send(event, expected, result=None, bot=bot)
            ctx.should_finished()


@pytest.mark.asyncio
async def test_vote_hint_in_music_query(app: App, songs):
    """是什么歌未命中别名但柚子有进行中投票 → 投票提示。"""
    import respx

    from nonebot_plugin_awmc_helper.plugins import music_query

    with respx.mock(assert_all_called=False) as m:
        m.get(f"{BASE}/aliases/maimaidx/songs").respond(
            json={
                "type": "ongoing",
                "data": [
                    {
                        "song_id": 231,
                        "apply_alias": "企鹅",
                        "tag": "T9",
                        "name": "PENGUIN",
                        "agree_votes": 0,
                        "votes": 10,
                    }
                ],
            }
        )
        event_user = 12345678
        import nonebot
        from fake import fake_group_message_event_v11
        from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
        from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

        event = fake_group_message_event_v11(message="企鹅是什么歌", user_id=event_user)
        expected = Message(
            [
                MessageSegment.at(event_user),
                MessageSegment.text(
                    " 未找到别名为「企鹅」的歌曲，但找到与此相同别名的投票："
                    "\n- T9\n    ID 231: 企鹅\n"
                    "※ 可以使用指令「同意别名 XXXXX」进行投票"
                ),
            ]
        )
        async with app.test_matcher(music_query.search_alias_song) as ctx:
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
                {"group_id": 87654321, "user_id": event_user, "no_cache": True},
                result={
                    "user_id": event_user,
                    "role": "member",
                    "card": "",
                    "nickname": "t",
                },
            )
            ctx.should_call_send(event, expected, result=None, bot=bot)
            ctx.should_finished()
