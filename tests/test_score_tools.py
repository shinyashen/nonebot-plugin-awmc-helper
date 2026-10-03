"""awmc.score_tools 分数线指令测试（2026-10-10 图片卡版）。

样例取自 mocks 真实快照构造（199 チルノ SD+DX 双谱 + 8 True Love Song
SD-only）；「白雪样例」为颜色字开头别名的构造补位（快照无原型，注明）。
解析语义：难度色与别名可连写；色字归属由命中下标反推（剥色候选命中才算
难度色，整串命中 = 色字属于别名 → 缺失反馈）。
"""

import base64
from pathlib import Path

import pytest
from mocks import requires_assets
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


def _dx_199_master():
    from mocks import sample_songs
    from maimai_py import SongType, LevelIndex

    song = next(s for s in sample_songs() if s.id == 199)
    diff = next(
        d
        for d in song.get_difficulties(SongType.DX)
        if d.level_index == LevelIndex.MASTER
    )
    return song, diff


def _card_of(song, diff, line: float = 100.0):
    from nonebot_plugin_awmc_helper.core.calc import score_line
    from nonebot_plugin_awmc_helper.core.render.score_line import score_line_card

    result = score_line(diff, line)
    assert result is not None
    return score_line_card(song, diff, line, result)


def _purple_199_card():
    """「分数线 紫199 100」期望图：DX 优先（与旧版行为一致）。"""
    song, diff = _dx_199_master()
    return _card_of(song, diff)


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


async def _assert_image_reply(
    app: App, matcher, text: str, expect_png, *, user_id=12345678, suffix: str = ""
):
    """断言回复为渲染图（bytes 与同一渲染函数一致）+ 可选文本后缀。"""
    import inspect

    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    result = expect_png()
    if inspect.isawaitable(result):
        result = await result
    png = result
    segments = [
        MessageSegment.at(user_id),
        MessageSegment.image(f"base64://{base64.b64encode(png).decode()}"),
    ]
    expected = (
        Message([*segments, MessageSegment.text(suffix)])
        if suffix
        else Message(segments)
    )
    event = fake_group_message_event_v11(message=text, user_id=user_id)
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
async def test_score_line_command_draws_card(app: App, songs):
    """「分数线 紫199 100」：图片出分数线计算卡（旧「色+id」连写兼容，DX 优先）。"""
    from nonebot_plugin_awmc_helper.plugins import score_tools

    await _assert_image_reply(
        app, score_tools.score_line_cmd, "分数线 紫199 100", _purple_199_card
    )


@requires_assets
@pytest.mark.asyncio
async def test_score_line_alias_unique_after_color_filter(app: App, songs):
    """颜色+别名连写出卡：8 号 SD-only，红（EXPERT）过滤后唯一 → 出卡。"""
    from mocks import sample_songs
    from maimai_py import LevelIndex

    from nonebot_plugin_awmc_helper.plugins import score_tools

    song = next(s for s in sample_songs() if s.id == 8)
    diff = next(
        d for d in song.difficulties.standard if d.level_index == LevelIndex.EXPERT
    )
    await _assert_image_reply(
        app,
        score_tools.score_line_cmd,
        "分数线 红会员制餐厅 100",
        lambda: _card_of(song, diff),
    )


@pytest.mark.asyncio
async def test_score_line_alias_dual_type_lists_entries(app: App, songs):
    """颜色+双谱曲别名：SD/DX 都有该难度 → 列条目请用户指定 id。"""
    from nonebot_plugin_awmc_helper.plugins import score_tools
    from nonebot_plugin_awmc_helper.core.songs import (
        cn_song_map,
        song_service,
        entries_list_text,
    )

    entries = await song_service.entries_for_name("琪露诺", cn_title=True)
    songs_list = [s for _, s, _ in entries]
    cn_songs = await cn_song_map(songs_list)
    flags = [cn_songs[s.id] is None for s in songs_list]
    expected = entries_list_text(
        entries,
        flags,
        hint="※ 请使用「分数线 <难度色><id> <线>」指定谱面",
    )
    await _assert_reply(
        app,
        score_tools.score_line_cmd,
        "分数线 紫琪露诺 100",
        expected,
    )


@pytest.mark.asyncio
async def test_score_line_digits_fallback_to_full_query(app: App, songs):
    """数字候选未命中不阻塞：剥色数字非 id（如 紫90299）时回「未找到」。"""
    from nonebot_plugin_awmc_helper.plugins import score_tools

    await _assert_reply(
        app,
        score_tools.score_line_cmd,
        "分数线 紫90299 100",
        "未找到「90299」或「紫90299」对应的乐曲，可用「查歌」确认后再试",
    )


def _utage_host(**utage_kw):
    """宴谱宿主：蛸チルノ（快照真实物量），别名「蛸」供宴前缀按名查找。"""
    from mocks import make_song, make_utage

    return make_song(
        199,
        "チルノのパーフェクトさんすう教室",
        aliases=["蛸"],
        utage=[make_utage(**utage_kw)],
    )


def _utage_kw_for_card(**overrides):
    """与 _utage_host 默认一致的宴谱构造参数（期望图与实际发送同源）。"""
    from mocks import make_utage

    return make_utage(**overrides)


def _utage_card(utage_diff, line: float = 100.0, jp: bool = False):
    from nonebot_plugin_awmc_helper.core.calc import score_line
    from nonebot_plugin_awmc_helper.core.render.score_line import score_line_card

    result = score_line(utage_diff, line)
    assert result is not None
    from mocks import make_song

    song = make_song(
        199, "チルノのパーフェクトさんすう教室", aliases=["蛸"], utage=[utage_diff]
    )
    return score_line_card(song, utage_diff, line, result, jp=jp)


@requires_assets
@requires_assets
@pytest.mark.asyncio
async def test_score_line_circle_theme_draws_card(app: App, songs):
    """circle 主题出卡：粉底图 + 粉系表头（用户绑定主题跟随渲染）。"""
    song, diff = _dx_199_master()
    from nonebot_plugin_awmc_helper.core.calc import score_line

    result = score_line(diff, 100)
    assert result is not None
    from nonebot_plugin_awmc_helper.core.render.score_line import score_line_card

    png = score_line_card(song, diff, 100, result, theme="circle")
    # circle 主题：底图为 circle/b50.png 缩放（与 prism 渐变底不同源）
    assert png != score_line_card(song, diff, 100, result, theme="prism_plus")
    assert png.startswith(b"\x89PNG")


@pytest.mark.asyncio
@requires_assets
@pytest.mark.asyncio
async def test_score_line_utage_direct_id(app: App, db):
    """6 位宴谱 diff_id 直查（无色无前缀）：diff_id 即完整规格，直接出卡。"""
    from mocks import seed_service

    from nonebot_plugin_awmc_helper.plugins import score_tools
    from nonebot_plugin_awmc_helper.core.songs import song_service

    await seed_service(song_service, [_utage_host()])
    await _assert_image_reply(
        app,
        score_tools.score_line_cmd,
        "分数线 100199 100",
        lambda: _utage_card(_utage_kw_for_card()),
    )


@requires_assets
@pytest.mark.asyncio
async def test_score_line_utage_prefix_single(app: App, db):
    """宴前缀 + 别名：唯一宴谱直出卡。"""
    from mocks import make_utage, seed_service

    from nonebot_plugin_awmc_helper.plugins import score_tools
    from nonebot_plugin_awmc_helper.core.songs import song_service

    utage = make_utage()
    await seed_service(song_service, [_utage_host()])
    await _assert_image_reply(
        app, score_tools.score_line_cmd, "分数线 宴蛸 100", lambda: _utage_card(utage)
    )


@pytest.mark.asyncio
async def test_score_line_utage_prefix_multi_lists_ids(app: App, db):
    """宴前缀 + 多宴谱（GDP 类）：候选列表只列宴谱 diff_id。"""
    from mocks import make_song, make_utage, seed_service

    from nonebot_plugin_awmc_helper.plugins import score_tools
    from nonebot_plugin_awmc_helper.core.songs import (
        cn_song_map,
        song_service,
        entries_list_text,
    )

    host = make_song(
        199,
        "チルノのパーフェクトさんすう教室",
        aliases=["蛸"],
        utage=[
            make_utage(diff_id=100199, kanji="蛸"),
            make_utage(diff_id=100901, kanji="宴"),
        ],
    )
    await seed_service(song_service, [host])
    chosen = [(100199, host, None), (100901, host, None)]
    cn_songs = await cn_song_map([host])
    jp = cn_songs[host.id] is None
    expected = entries_list_text(
        chosen,
        [jp, jp],
        hint="※ 请使用「分数线 宴<id> <线>」指定谱面",
    )
    await _assert_reply(app, score_tools.score_line_cmd, "分数线 宴蛸 100", expected)


@pytest.mark.asyncio
async def test_score_line_utage_prefix_none_hints(app: App, songs):
    """宴前缀命中无宴谱的曲：给出「没有宴谱」提示。"""
    from nonebot_plugin_awmc_helper.plugins import score_tools

    await _assert_reply(
        app,
        score_tools.score_line_cmd,
        "分数线 宴会员制餐厅 100",
        "该乐曲没有宴谱",
    )


@pytest.mark.asyncio
async def test_score_line_white_missing_hint(app: App, songs):
    """色前缀命中但该难度不存在（8 号无白谱）：给出「没有 Re:Master 谱」提示。"""
    from nonebot_plugin_awmc_helper.plugins import score_tools

    await _assert_reply(
        app,
        score_tools.score_line_cmd,
        "分数线 白会员制餐厅 100",
        "该乐曲没有Re:Master谱",
    )


@requires_assets
@pytest.mark.asyncio
async def test_score_line_utage_buddy_card(app: App, db):
    """buddy 宴谱：202 上限口径出卡（物量字段即左右机台合计，直算）。"""
    from mocks import seed_service

    from nonebot_plugin_awmc_helper.plugins import score_tools
    from nonebot_plugin_awmc_helper.core.songs import song_service

    kw = {
        "diff_id": 100902,
        "kanji": "協",
        "is_buddy": True,
        "tap_num": 649,
        "hold_num": 76,
        "slide_num": 53,
        "touch_num": 164,
        "break_num": 10,
    }
    await seed_service(song_service, [_utage_host(**kw)])
    await _assert_image_reply(
        app,
        score_tools.score_line_cmd,
        "分数线 100902 101.5",
        lambda: _utage_card(_utage_kw_for_card(**kw), 101.5),
    )


@requires_assets
@pytest.mark.asyncio
async def test_score_line_missing_color_feedback(app: App, db):
    """无难度色但有命中：复用「是什么歌」同款回复（谱面卡）+ 缺色提示。

    「白雪」首字符为颜色字（白）但剥色后「雪」不命中、整串命中别名 →
    白属于别名，不算难度色（快照无颜色开头别名原型，构造补位并注明）。"""
    from mocks import make_diff, make_song, seed_service
    from maimai_py import SongType, LevelIndex

    from nonebot_plugin_awmc_helper.plugins import score_tools
    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.chart_card import chart_card_bytes

    song = make_song(
        903,
        "（构造）白雪样例",
        aliases=["白雪"],
        diffs=[
            make_diff(
                type=SongType.STANDARD,
                level_index=LevelIndex.MASTER,
                level="12",
                level_value=12.5,
            )
        ],
    )
    await seed_service(song_service, [song])
    # 与「是什么歌」同一渲染：谱面卡（绑定无凭据 → 不嵌 B50）+ 提示语
    png = await chart_card_bytes(song, None, SongType.STANDARD, False)
    await _assert_image_reply(
        app,
        score_tools.score_line_cmd,
        "分数线 白雪 100",
        lambda: png,
        suffix="您要找的是不是这首？\n"
        "※ 分数线需指定难度色：分数线 <绿/黄/红/紫/白><id/别名> <线>",
    )


@requires_assets
@pytest.mark.asyncio
async def test_score_line_color_alias_conflict_prefers_full(app: App, db):
    """剥色命中异曲的冲突：整串命中优先、色字属于别名（绿9 形态）。

    服务器合并别名库实证 22 例（绿9→剥色「9」命中ケロ⑨destiny）：构造
    曲 904 别名「绿9」+ 曲 905 别名「9」，「分数线 绿9 100」必须回 904 的
    「是什么歌」+缺色提示，而不是 905 的绿谱卡。"""
    from mocks import make_diff, make_song, seed_service
    from maimai_py import SongType, LevelIndex

    from nonebot_plugin_awmc_helper.plugins import score_tools
    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.chart_card import chart_card_bytes

    host = make_song(
        904,
        "（构造）绿9样例",
        aliases=["绿9"],
        diffs=[
            make_diff(
                type=SongType.STANDARD,
                level_index=LevelIndex.MASTER,
                level="12",
                level_value=12.5,
            )
        ],
    )
    decoy = make_song(
        905,
        "（构造）九样例",
        aliases=["9"],
        diffs=[
            make_diff(
                type=SongType.DX,
                level_index=LevelIndex.MASTER,
                level="13",
                level_value=13.0,
            )
        ],
    )
    await seed_service(song_service, [host, decoy])
    png = await chart_card_bytes(host, None, SongType.STANDARD, False)
    await _assert_image_reply(
        app,
        score_tools.score_line_cmd,
        "分数线 绿9 100",
        lambda: png,
        suffix="您要找的是不是这首？\n"
        "※ 分数线需指定难度色：分数线 <绿/黄/红/紫/白><id/别名> <线>",
    )


@pytest.mark.asyncio
async def test_score_line_help(app: App):
    """「分数线 帮助」：渲染注册表里的分数线详情页（文案单源）。"""
    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    from nonebot_plugin_awmc_helper.plugins import score_tools
    from nonebot_plugin_awmc_helper.core.help import (
        CommandPage,
        page_entries,
        help_registry,
    )

    entry = page_entries(help_registry, CommandPage(spec=score_tools._score_line_spec))[
        0
    ]
    assert isinstance(entry, str)  # 指令详情页恒为纯文本节点
    text = entry
    event = fake_group_message_event_v11(message="分数线 帮助")
    expected = Message(
        [
            MessageSegment.at(12345678),
            MessageSegment.text(" " + text),
        ]
    )
    async with app.test_matcher(score_tools.score_line_cmd) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter))
        ctx.receive_event(bot, event)
        # handler 带 SessionBinding 依赖（无色回退复用查歌回复），需 mock 群信息
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
