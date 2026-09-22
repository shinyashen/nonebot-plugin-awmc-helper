"""awmc.music_query 查歌子插件测试。"""

import base64

import pytest
from mocks import requires_assets
from nonebug import App


@pytest.fixture
async def songs(tmp_path):
    """注入样例曲库 + 独立临时数据库。"""
    from mocks import make_song, sample_songs, seed_service

    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.core.songs import song_service

    store.set_db_file(tmp_path / "awmc.db")
    await store.init_db()
    extra = [
        make_song(700, "PENGUIN RUSH"),
        make_song(701, "PENGUIN LAND"),
        make_song(702, "PENGUIN PARTY"),
        make_song(703, "PENGUIN STAR"),
    ]
    await seed_service(song_service, sample_songs() + extra)
    yield
    song_service._ready.clear()
    store.set_db_file(None)


async def _assert_reply(
    app: App, matcher_name: str, text: str, reply: str, *, user_id=12345678
):
    """构造群消息并断言文本回复（handler 注入 uninfo Session，需 mock 其信息拉取）。"""
    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    from nonebot_plugin_awmc_helper.plugins import music_query

    matcher = getattr(music_query, matcher_name)
    event = fake_group_message_event_v11(message=text)
    async with app.test_matcher(matcher) as ctx:
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
            {"group_id": 87654321, "user_id": user_id, "no_cache": True},
            result={"user_id": user_id, "role": "member", "card": "", "nickname": "t"},
        )
        expected = Message([MessageSegment.at(user_id), MessageSegment.text(reply)])
        ctx.should_call_send(event, expected, result=None, bot=bot)
        ctx.should_finished()


async def _assert_image_reply(
    app: App,
    matcher_name: str,
    text: str,
    expect_png,
    suffix: str = "",
    *,
    user_id=12345678,
):
    """断言回复为渲染图（bytes 与同一渲染函数一致）+ 可选文本后缀。

    `expect_png` 是无参函数，返回渲染字节（保证期望值与实际发送同源）。
    """
    import inspect

    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    from nonebot_plugin_awmc_helper.plugins import music_query

    matcher = getattr(music_query, matcher_name)
    result = expect_png()
    if inspect.isawaitable(result):
        result = await result
    png = result
    segments = [
        MessageSegment.at(user_id),
        MessageSegment.image(f"base64://{base64.b64encode(png).decode()}"),
    ]
    expected = (
        Message(segments)
        if not suffix
        else Message([*segments, MessageSegment.text(suffix)])
    )

    event = fake_group_message_event_v11(message=text)
    async with app.test_matcher(matcher) as ctx:
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
            {"group_id": 87654321, "user_id": user_id, "no_cache": True},
            result={"user_id": user_id, "role": "member", "card": "", "nickname": "t"},
        )
        ctx.should_call_send(event, expected, result=None, bot=bot)
        ctx.should_finished()


@requires_assets
@pytest.mark.asyncio
async def test_search_single_draws_card(app: App, songs):
    from nonebot_plugin_awmc_helper.plugins import music_query
    from nonebot_plugin_awmc_helper.core.songs import song_service

    song = await song_service.by_id(500)
    await _assert_image_reply(
        app, "search", "查歌 Preferences", lambda: music_query._chart_card(song, None)
    )


@pytest.mark.asyncio
async def test_search_multi_text(app: App, songs):
    await _assert_reply(
        app,
        "search",
        "查歌 PENGUIN",
        "「231」 PENGUIN\n「700」 PENGUIN RUSH\n「701」 PENGUIN LAND"
        "\n「702」 PENGUIN PARTY\n「703」 PENGUIN STAR",
    )


@pytest.mark.asyncio
async def test_search_not_found(app: App, songs):
    await _assert_reply(
        app,
        "search",
        "查歌 不存在的曲子",
        "没有找到这样的乐曲。\n※ 如果是别名请使用「XXX是什么歌」指令进行查询哦。",
    )


@pytest.mark.asyncio
async def test_search_list_image(app: App, songs):
    """5 条以上走列表图：PENGUIN 族 3 首 + Preferences + 宴会曲相关……用「n」命中。"""
    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.render import song as song_render

    matched = await song_service.by_title_fuzzy("p")
    assert len(matched) >= 5
    page = 1
    suffix = f"\n第 {page} 页，共 {len(matched)} 首，可用「查歌 <标题> {page + 1}」翻页"
    await _assert_image_reply(
        app,
        "search",
        f"查歌 p {page}",
        lambda: song_render.song_list_bytes(matched, page),
        suffix=suffix,
    )


@pytest.mark.asyncio
async def test_alias_search_multi(app: App, songs):
    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.core.songs import song_service

    await store.add_local_alias(231, "共同别名", "u1")
    await store.add_local_alias(500, "共同别名", "u1")
    await song_service.reload_alias_index()
    await _assert_reply(
        app,
        "search_alias_song",
        "共同别名是什么歌",
        "找到4个谱面："
        "\n231：PENGUIN"
        "\n10231：PENGUIN"
        "\n500：Preferences"
        "\n10500：Preferences"
        "\n※ 请使用「id xxxxx」查询指定谱面",
    )


@requires_assets
@pytest.mark.asyncio
async def test_alias_search_single(app: App, songs):
    from maimai_py import SongType

    from nonebot_plugin_awmc_helper.plugins import music_query
    from nonebot_plugin_awmc_helper.core.songs import song_service

    song = await song_service.by_id(500)
    # 双谱歌曲无前缀搜索 → 列出谱面类型条目供选择（不设偏好）
    await _assert_reply(
        app,
        "search_alias_song",
        "普瑞是什么歌",
        "找到2个谱面："
        "\n500：Preferences"
        "\n10500：Preferences"
        "\n※ 请使用「id xxxxx」查询指定谱面",
    )
    # 带 dx 前缀 → 直接定位 DX 条目出卡
    await _assert_image_reply(
        app,
        "search_alias_song",
        "dx普瑞是什么歌",
        lambda: music_query._chart_card(song, None, SongType.DX),
        suffix="\n您要找的是不是这首？",
    )
    # 带「标」前缀 → SD 条目出卡
    await _assert_image_reply(
        app,
        "search_alias_song",
        "标普瑞是什么歌",
        lambda: music_query._chart_card(song, None, SongType.STANDARD),
        suffix="\n您要找的是不是这首？",
    )


@pytest.mark.asyncio
async def test_query_chart_not_found(app: App, songs):
    await _assert_reply(app, "query_chart", "id 99999", "未找到ID为「99999」的乐曲")


@requires_assets
@pytest.mark.asyncio
async def test_query_chart_card(app: App, songs):
    from maimai_py import SongType

    from nonebot_plugin_awmc_helper.plugins import music_query
    from nonebot_plugin_awmc_helper.core.songs import song_service

    song = await song_service.by_id(231)
    # SD 形状 id → 卡片显示标准谱（NB 双条目语义：id 即条目类型）
    await _assert_image_reply(
        app,
        "query_chart",
        "id 231",
        lambda: music_query._chart_card(song, None, SongType.STANDARD),
    )


@pytest.mark.asyncio
async def test_render_smoke(songs):
    """绘图冒烟：卡片与列表图渲染出非空 PNG。"""
    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.render.song import (
        song_card_bytes,
        song_list_bytes,
    )

    song231 = await song_service.by_id(231)
    assert song231 is not None
    card = song_card_bytes(song231)
    assert card.startswith(b"\x89PNG")
    assert len(card) > 1000
    listing = song_list_bytes(await song_service.get_all(), 1)
    assert listing.startswith(b"\x89PNG")
    assert len(listing) > 1000
