"""awmc.alias 别名子插件测试（nonebug + respx）。

锚全部为真实数据（2026-09-29 取材）：样例曲库见 mocks.sample_songs
（199 チルノ / 8 True Love Song / 624 KISS CANDY FLAVOR，别名均为柚子实测，
「糖糖」为 8/624 共持的真实多命中别名）；日服兜底用真实日服限定曲
テリトリーバトル(1396，落雪实测别名 领土战争/小男娘/奈伊)。
"""

import pytest
from nonebug import App

BASE = "https://www.yuzuchan.moe/api/v2"

_SELF_ID = "1234567890"  # 合并转发节点 uin 取 bot self_id，须先配好再断言


def _forward_nodes(texts: list[str]) -> list[dict]:
    """try_send_forward 的节点构造期望（name/uin 键裸 dict，见 core/forward.py）。"""
    from nonebot_plugin_awmc_helper.core.forward import NODE_NICKNAME

    return [
        {
            "type": "node",
            "data": {
                "name": NODE_NICKNAME,
                "uin": _SELF_ID,
                "content": [{"type": "text", "data": {"text": text}}],
            },
        }
        for text in texts
    ]


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

    # 199 チルノ：SD+DX+蛸宴三组 → 三 id 展示；别名 7 条均为柚子实测
    await _assert_reply(
        app,
        alias.alias_song,
        "⑨有什么别名",
        "该曲目有以下别名：\nID：199、10199、100199\n"
        "琪露诺的完美算术教室\n数学课堂\n⑨\n算数教室\n琪露诺\nbaka\n算术教室",
        with_session=True,
    )


@pytest.mark.asyncio
async def test_alias_by_id(app: App, songs):
    from nonebot_plugin_awmc_helper.plugins import alias

    # 8 True Love Song：仅 SD → 单 id；别名 8 条均为柚子实测
    await _assert_reply(
        app,
        alias.alias_song,
        "id 8有什么别名",
        # 「true love song」与曲名归一相同 → 合并视图去重，不展示
        "该曲目有以下别名：\nID：8\n"
        "会员制餐厅\n真的爱情歌\n糖糖\n"
        "小管弦乐\n真爱歌\n真爱\n真情歌",
        with_session=True,
    )


@pytest.mark.asyncio
async def test_alias_dx_only_song_ids(app: App, songs):
    """只有 DX 谱的曲：仅展示 DX id（根 id+10000），不纳入标准位根 id。

    用真实 DX 重制曲 BLACK ROSE（日服 11001，柚子实测别名）注入样例库。
    """
    from mocks import make_diff, make_song, sample_songs, seed_service
    from maimai_py import SongType

    from nonebot_plugin_awmc_helper.plugins import alias
    from nonebot_plugin_awmc_helper.core.songs import song_service

    dx_only = make_song(
        1001,
        "BLACK ROSE",
        aliases=["黑玫瑰", "迪卢克", "黑肉丝", "黑蔷薇"],
        artist="Yunosuke",
        diffs=[make_diff(type=SongType.DX)],
    )
    await seed_service(song_service, [*sample_songs(), dx_only])
    await _assert_reply(
        app,
        alias.alias_song,
        "黑玫瑰有什么别名",
        "该曲目有以下别名：\nID：11001\n黑玫瑰\n迪卢克\n黑肉丝\n黑蔷薇",
        with_session=True,
    )


@pytest.mark.asyncio
async def test_alias_utage_only_song_ids(app: App, songs):
    """只有宴谱的曲：仅展示宴谱机台 id（无标准/DX 位）。

    真实数据中纯宴宿主仅出现在国服视图（基曲未进国服的宴谱），运行时样例以
    构造曲补位（注明）；宴 diff_id 沿用 6 位机台命名。
    """
    from mocks import make_song, make_utage, sample_songs, seed_service

    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.plugins import alias
    from nonebot_plugin_awmc_helper.core.songs import song_service

    utage_only = make_song(
        901,
        "（构造）纯宴样例",
        diffs=[],
        utage=[make_utage(diff_id=100901, kanji="宴")],
    )
    await seed_service(song_service, [*sample_songs(), utage_only])
    await store.add_local_alias(901, "宴曲", "u")
    await song_service.reload_alias_index()
    await _assert_reply(
        app,
        alias.alias_song,
        "id 901有什么别名",
        "该曲目有以下别名：\nID：100901\n宴曲",
        with_session=True,
    )


@pytest.mark.asyncio
async def test_alias_multi_match_forward(app: App, songs):
    """多曲命中别名：OB11 以合并转发逐曲展示（首条为命中数量）。

    「糖糖」为 8/624 在柚子别名库共持的真实别名，无需本地别名即可多命中。
    """
    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    from nonebot_plugin_awmc_helper.plugins import alias

    event = fake_group_message_event_v11(message="糖糖有什么别名", user_id=12345678)
    forward = _forward_nodes(
        [
            "找到2个相同别名的曲目：",
            "ID：8\n会员制餐厅\n真的爱情歌\n糖糖\n小管弦乐\n真爱歌\n真爱\n真情歌",
            "ID：624\n小女孩福瑞\nkcf\n糖糖\n亲甜滴\n糖的味道\n小红帽\n亲糖口味",
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
    """合并转发发送失败（协议端报错）→ 降级普通消息。"""
    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    from nonebot_plugin_awmc_helper.plugins import alias

    event = fake_group_message_event_v11(message="糖糖有什么别名", user_id=12345678)
    block8 = "ID：8\n会员制餐厅\n真的爱情歌\n糖糖\n小管弦乐\n真爱歌\n真爱\n真情歌"
    block624 = "ID：624\n小女孩福瑞\nkcf\n糖糖\n亲甜滴\n糖的味道\n小红帽\n亲糖口味"
    forward = _forward_nodes(["找到2个相同别名的曲目：", block8, block624])
    msg = f"找到2个相同别名的曲目：\n{block8}\n======\n{block624}"
    async with app.test_matcher(alias.alias_song) as ctx:
        bot = ctx.create_bot(
            base=Bot,
            adapter=nonebot.get_adapter(OnebotV11Adapter),
            self_id=_SELF_ID,
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
        # 协议端报错 → try_send_forward 返回 False → 降级普通消息
        ctx.should_call_api(
            "send_group_forward_msg",
            {"group_id": 87654321, "message": forward, "messages": forward},
            result=None,
            exception=RuntimeError("protocol error"),
        )
        ctx.should_call_send(
            event,
            Message(
                [
                    MessageSegment.at(12345678),
                    MessageSegment.text(f" {msg}"),
                ]
            ),
            result=None,
            bot=bot,
        )
        ctx.should_finished()


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


# ---------------------------------------------------------------------------
# 日服兜底（Q32）：仅日服曲目的别名不在国服索引，回退日服视图与合并库
# ---------------------------------------------------------------------------


def _seed_jp_view(monkeypatch, songs_by_id: dict):
    """用假日服视图替身替换 ``_jp_songs_map``（测试环境无规范表数据）。"""
    from nonebot_plugin_awmc_helper.core.songs import song_service

    async def fake_jp_map():
        return songs_by_id

    monkeypatch.setattr(song_service, "_jp_songs_map", fake_jp_map)


# テリトリーバトル(1396) 的落雪实测别名（真实日服限定曲）
_TERRITORY_ALIASES = ["领土战争", "小男娘", "奈伊"]


@pytest.mark.asyncio
async def test_alias_jp_fallback_by_name(app: App, songs, monkeypatch):
    """仅日服曲目：国服别名未命中 → 日服视图兜底，ID 行带日服限定标注。"""
    from mocks import make_diff, make_song
    from maimai_py import SongType

    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.plugins import alias

    territory = make_song(
        1396, "テリトリーバトル", artist="MASAKI", diffs=[make_diff(type=SongType.DX)]
    )
    # 不进国服样例库：1396 仅存在于日服视图（真实可见性）
    await store.save_song_aliases("munet", {1396: _TERRITORY_ALIASES})
    _seed_jp_view(monkeypatch, {1396: territory})
    await _assert_reply(
        app,
        alias.alias_song,
        "领土战争有什么别名",
        "该曲目有以下别名：\nID：11396（日服限定）\n奈伊\n小男娘\n领土战争",
        with_session=True,
    )


@pytest.mark.asyncio
async def test_alias_jp_fallback_by_id(app: App, songs, monkeypatch):
    """仅日服曲目按 id 查别名：国服视图无此曲 → 日服视图兜底。"""
    from mocks import make_diff, make_song
    from maimai_py import SongType

    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.plugins import alias

    territory = make_song(
        1396, "テリトリーバトル", artist="MASAKI", diffs=[make_diff(type=SongType.DX)]
    )
    await store.save_song_aliases("munet", {1396: _TERRITORY_ALIASES})
    _seed_jp_view(monkeypatch, {1396: territory})
    await _assert_reply(
        app,
        alias.alias_song,
        "1396有什么别名",
        "该曲目有以下别名：\nID：11396（日服限定）\n奈伊\n小男娘\n领土战争",
        with_session=True,
    )


@pytest.mark.asyncio
async def test_alias_jp_fallback_multi(app: App, songs, monkeypatch):
    """仅日服曲目多曲命中：合并转发逐曲展示，各 ID 行带日服限定标注。

    真实库中暂无「日限曲间共享别名」样本（落雪全量核查 0 组），第二首以
    构造日限曲补位（注明）。
    """
    import nonebot
    from fake import fake_group_message_event_v11
    from mocks import make_diff, make_song
    from maimai_py import SongType
    from nonebot.adapters.onebot.v11 import Bot
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.plugins import alias

    territory = make_song(
        1396, "テリトリーバトル", artist="MASAKI", diffs=[make_diff(type=SongType.DX)]
    )
    ghost = make_song(
        555,
        "（构造）日服限定样例",
        artist="构造条目",
        diffs=[make_diff(type=SongType.DX)],
    )
    # 两首均不进国服样例库（仅日服可见）
    await store.save_song_aliases(
        "munet", {1396: _TERRITORY_ALIASES, 555: ["领土战争"]}
    )
    _seed_jp_view(monkeypatch, {1396: territory, 555: ghost})

    event = fake_group_message_event_v11(message="领土战争有什么别名", user_id=12345678)
    forward = _forward_nodes(
        [
            "找到2个相同别名的曲目：",
            "ID：10555（日服限定）\n领土战争",
            "ID：11396（日服限定）\n奈伊\n小男娘\n领土战争",
        ]
    )
    async with app.test_matcher(alias.alias_song) as ctx:
        bot = ctx.create_bot(
            base=Bot,
            adapter=nonebot.get_adapter(OnebotV11Adapter),
            self_id=_SELF_ID,
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
        ctx.should_call_api(
            "send_group_forward_msg",
            {"group_id": 87654321, "message": forward, "messages": forward},
            result=None,
        )
        ctx.should_finished()


@pytest.mark.asyncio
async def test_local_alias_apply(app: App, songs, tmp_path):
    import respx

    from nonebot_plugin_awmc_helper.plugins import alias

    with respx.mock(assert_all_called=False) as m:
        # 柚子无该别名（「真正的爱之歌」为落雪实测别名，柚子未收录）→ 本地添加成功
        m.get(f"{BASE}/aliases/maimaidx/aliases").respond(json={"message": "未找到"})
        await _assert_reply(
            app,
            alias.alias_local_apply,
            "添加本地别名 8 真正的爱之歌",
            "已成功为ID「8」添加别名「真正的爱之歌」到本地别名库",
            with_session=True,
        )
        from nonebot_plugin_awmc_helper.core.songs import song_service

        aliases = await song_service.aliases_of(8)
        assert aliases is not None
        assert "真正的爱之歌" in aliases

        # 重复添加 → 提示已存在（同样必须在 mock 上下文内，避免真实请求柚子）
        await _assert_reply(
            app,
            alias.alias_local_apply,
            "添加本地别名 8 真正的爱之歌",
            "本地别名库已存在该别名",
            with_session=True,
        )
    from nonebot_plugin_awmc_helper.core import store as awmc_store

    assert (
        await awmc_store.add_local_alias(624, "真正的爱之歌", "u") is True
    )  # 不同曲同别名允许


@pytest.mark.asyncio
async def test_local_alias_dup_on_server(app: App, songs):
    """柚子已收录（真实别名「糖糖」在 8 名下）→ 判重命中。"""
    import respx

    from nonebot_plugin_awmc_helper.plugins import alias

    with respx.mock(assert_all_called=False) as m:
        m.get(f"{BASE}/aliases/maimaidx/aliases").respond(
            json={
                "song_id": 8,
                "name": "True Love Song",
                "is_votable": False,
                "alias": ["糖糖"],
            }
        )
        await _assert_reply(
            app,
            alias.alias_local_apply,
            "添加本地别名 8 糖糖",
            "该曲目的别名「糖糖」已存在别名服务器",
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
            app, alias.alias_apply, "添加别名 8 糖糖", "申请已提交", with_session=True
        )

        m.post(f"{BASE}/aliases/maimaidx/votes").respond(json={"message": "投票成功"})
        await _assert_reply(
            app, alias.alias_agree, "同意别名 ABC123", "投票成功", with_session=True
        )


@pytest.mark.asyncio
async def test_alias_status(app: App, songs):
    """当前投票列表：用真实进行中投票（Pixel Galaxy/像素银河，2026-09-29 实测）。"""
    import base64

    import respx

    from nonebot_plugin_awmc_helper.plugins import alias
    from nonebot_plugin_awmc_helper.core.ext import yuzu as yuzu_ext

    with respx.mock(assert_all_called=False) as m:
        m.get(f"{BASE}/aliases/maimaidx/votes").respond(
            json=[
                {
                    "song_id": 11878,
                    "apply_alias": "像素银河",
                    "tag": "95V3R",
                    "name": "Pixel Galaxy",
                    "agree_votes": 0,
                    "votes": 5,
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
    """是什么歌未命中别名但柚子有进行中投票 → 投票提示（真实投票条目）。"""
    import respx

    from nonebot_plugin_awmc_helper.plugins import music_query

    with respx.mock(assert_all_called=False) as m:
        m.get(f"{BASE}/aliases/maimaidx/songs").respond(
            json={
                "type": "ongoing",
                "data": [
                    {
                        "song_id": 11878,
                        "apply_alias": "像素银河",
                        "tag": "95V3R",
                        "name": "Pixel Galaxy",
                        "agree_votes": 0,
                        "votes": 5,
                    }
                ],
            }
        )
        event_user = 12345678
        import nonebot
        from fake import fake_group_message_event_v11
        from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
        from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

        event = fake_group_message_event_v11(
            message="像素银河是什么歌", user_id=event_user
        )
        expected = Message(
            [
                MessageSegment.at(event_user),
                MessageSegment.text(
                    " 未找到别名为「像素银河」的歌曲，但找到与此相同别名的投票："
                    "\n- 95V3R\n    ID 11878: 像素银河\n"
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


@pytest.mark.asyncio
async def test_local_alias_jp_only_song(app: App, songs, monkeypatch):
    """日服限定曲可添加本地别名（L-28）：CN 未命中回退 jp_by_id 判存在。"""
    import respx
    from mocks import make_diff, make_song
    from maimai_py import SongType, LevelIndex

    from nonebot_plugin_awmc_helper.plugins import alias

    jp_only = make_song(
        1396,
        "テリトリーバトル",
        diffs=[
            make_diff(
                type=SongType.DX,
                level_index=LevelIndex.MASTER,
                level="13",
                level_value=13.5,
            )
        ],
    )
    _seed_jp_view(monkeypatch, {1396: jp_only})

    with respx.mock(assert_all_called=False) as m:
        m.get(f"{BASE}/aliases/maimaidx/aliases").respond(json={"message": "未找到"})
        await _assert_reply(
            app,
            alias.alias_local_apply,
            "添加本地别名 1396 领土战争",
            "已成功为ID「1396」添加别名「领土战争」到本地别名库",
            with_session=True,
        )
    from nonebot_plugin_awmc_helper.core import store

    # aliases_of 是 CN 视图域（JP-only 曲 None 属既有口径），持久化断言查表
    local = [la for la in await store.get_local_aliases() if la.song_id == 1396]
    assert [la.alias for la in local] == ["领土战争"]
