"""awmc.random_song 随机选曲指令测试：随个（DX/等级）、随个流行、
mai什么（含加分退化路径与 _pick_rise_song 算法）。"""

import pytest
from mocks import requires_assets
from nonebug import App
from maimai_py import SongType  # maimai_py 不依赖 nonebot 初始化


def _designed_only_songs() -> list:
    """样例曲库的「带谱师谱面」子集（本文件全程使用）。

    真实数据低难度谱面无谱师（源数据 note_designer=None 形态），生产运行时
    由规范表以「-」填充（songdb §5.5 `_diff_from_row`），本文件覆盖的
    谱面卡渲染与随机指令会直接读谱师行，注入保留 None 原始形态会崩溃，
    故只保留带谱师的真实谱面（值为真实快照子集，不引入编造数据）。
    """
    from mocks import sample_songs

    songs = sample_songs()
    for s in songs:
        s.difficulties.standard = [
            d for d in s.difficulties.standard if d.note_designer
        ]
        s.difficulties.dx = [d for d in s.difficulties.dx if d.note_designer]
    return [
        s
        for s in songs
        if s.difficulties.standard or s.difficulties.dx or s.difficulties.utage
    ]


@pytest.fixture
async def songs(tmp_path):
    from mocks import seed_service

    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.core.songs import song_service

    store.set_db_file(tmp_path / "awmc.db")
    await store.init_db()
    await seed_service(song_service, _designed_only_songs())
    yield
    song_service._ready.clear()
    store.set_db_file(None)


async def _assert_reply(
    app: App, matcher, text: str, reply: str, *, user_id=12345678, with_session=False
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
@requires_assets
async def test_random_chart(app: App, songs):
    from nonebot_plugin_awmc_helper.plugins import random_song
    from nonebot_plugin_awmc_helper.core.songs import song_service

    got = await song_service.random(song_type=SongType.DX, level="13")
    assert got is not None
    assert got[0].id == 199

    # matcher：样例中 DX 13 唯一（199），断言回复图片（同源渲染）
    import base64

    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    from nonebot_plugin_awmc_helper.core.binding import binding_service
    from nonebot_plugin_awmc_helper.core.chart_card import chart_card_bytes

    picked = await song_service.random(song_type=SongType.DX, level="13")
    assert picked is not None
    song, _diff = picked
    # 未绑定用户：binding.ensure 装配 QQ 公开凭据后 B50 拉取失败 → 纯谱面卡
    binding = await binding_service.ensure("OneBot V11", "12345678")
    png = await chart_card_bytes(song, binding)
    event = fake_group_message_event_v11(message="随个dx13")
    expected = Message(
        [
            MessageSegment.at(12345678),
            MessageSegment.image(f"base64://{base64.b64encode(png).decode()}"),
        ]
    )
    async with app.test_matcher(random_song.random_chart) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter))
        ctx.receive_event(bot, event)
        # 绑定解析触发群信息 API
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
async def test_random_chart_no_match(app: App, songs):
    from nonebot_plugin_awmc_helper.plugins import random_song

    # UniSession 依赖注入在 handler 入口展开（无论是否命中都会触发群信息 API）
    await _assert_reply(
        app,
        random_song.random_chart,
        "随个白14",
        "没有符合条件的谱面，换一个试试吧",
        with_session=True,
    )


@pytest.mark.asyncio
async def test_genre_random_service(app: App, songs):
    """随个分类服务层：分类过滤 + 宴谱排除语义 + disabled 过滤。"""
    from maimai_py import Genre

    from nonebot_plugin_awmc_helper.core.songs import song_service

    got = await song_service.random(genre=Genre.maimai)
    assert got is not None
    assert got[0].id == 8  # 舞萌分类唯一（902 disabled 不入池）
    got = await song_service.random(genre=Genre.ゲームバラエティ)
    assert got is not None
    assert got[0].id == 624
    # 东方Project：199 的普通谱在池（宴谱排除口径下仍非空）
    got = await song_service.random(genre=Genre.東方Project, exclude_utage=True)
    assert got is not None
    assert got[0].id == 199
    # 宴谱排除语义：等级 12+ 仅 199 的蛸宴命中 → 排除后落空、放行后唯一
    assert await song_service.random(level="12+", exclude_utage=True) is None
    got = await song_service.random(level="12+", exclude_utage=False)
    assert got is not None
    assert got[0].id == 199


@pytest.mark.asyncio
@requires_assets
async def test_genre_random_matcher(app: App, songs):
    """随个流行指令：命中渲染谱面卡（舞萌分类唯一曲保证确定性）、未命中给提示。"""
    import base64

    import nonebot
    from fake import fake_group_message_event_v11
    from maimai_py import Genre
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    from nonebot_plugin_awmc_helper.plugins import random_song
    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.binding import binding_service
    from nonebot_plugin_awmc_helper.core.chart_card import chart_card_bytes

    # 舞萌分类唯一曲 True Love Song（8）→ 随机确定（902 disabled 不入舞萌池）
    binding = await binding_service.ensure("OneBot V11", "12345678")
    picked = await song_service.random(genre=Genre.maimai)
    assert picked is not None
    assert picked[0].id == 8
    png = await chart_card_bytes(picked[0], binding)

    event = fake_group_message_event_v11(message="随个舞萌")
    expected = Message(
        [
            MessageSegment.at(12345678),
            MessageSegment.image(f"base64://{base64.b64encode(png).decode()}"),
        ]
    )
    async with app.test_matcher(random_song.genre_random) as ctx:
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

    # 已知分类无曲给提示（样例库无宴会场曲目）
    await _assert_reply(
        app,
        random_song.genre_random,
        "随个宴会",
        "没有符合条件的谱面，换一个试试吧",
        with_session=True,
    )


@pytest.mark.asyncio
async def test_random_service_and_card(songs, monkeypatch):
    """mai什么底层：随机选曲 + 曲目卡渲染（matcher 仅 3 行薄封装）。"""
    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.render import song as song_render

    got = await song_service.random(exclude_utage=True)
    assert got is not None
    song, _diff = got
    png = song_render.song_card_bytes(song)
    assert png.startswith(b"\x89PNG")

    # 无符合条件时不抛错、返回 None
    assert await song_service.random(song_type=SongType.DX, level="1") is None


@pytest.mark.asyncio
async def test_pick_rise_song(songs, monkeypatch):
    """Q8：mai什么加分算法——定数窗口与 SSS+ 排除语义（NB get_mai_what）。"""
    import dataclasses

    from maimai_py import Score, RateType, LevelIndex, ScoreExtend

    from nonebot_plugin_awmc_helper.core.calc import compute_rating
    from nonebot_plugin_awmc_helper.plugins.random_song import _pick_rise_song

    # 固定选 SD 侧
    monkeypatch.setattr(
        "nonebot_plugin_awmc_helper.plugins.random_song._random.randint", lambda a, b: 0
    )

    def extend(sid, title, level_index, ach, ra):
        base = Score(
            id=sid,
            level="10",
            level_index=level_index,
            achievements=ach,
            fc=None,
            fs=None,
            dx_score=2000,
            dx_rating=ra,
            play_count=None,
            play_time=None,
            rate=RateType.SSS,
            type=SongType.STANDARD,
        )
        return ScoreExtend(
            **dataclasses.asdict(base),
            title=title,
            level_value=10.5,
            level_dx_score=2400,
            dx_star=4,
            version=25000,
        )

    # 末位 ra=234 → ds=10.4 → 窗口 [10.4,11.4] 命中 199 的 SD Expert(10.4)
    ra234 = compute_rating(10.4, 100.5)
    b50 = [extend(8, "True Love Song", LevelIndex.BASIC, 99.0, ra234)]
    got = await _pick_rise_song(b50)
    assert got is not None
    assert got.id == 199

    # SSS+ 排除：199 已 SSS+ → 无候选
    b50.append(
        extend(199, "チルノのパーフェクトさんすう教室", LevelIndex.EXPERT, 100.5, 9999)
    )
    got = await _pick_rise_song(b50)
    assert got is None

    # 窗口无候选：末位 ra → ds=7.3 → 窗口 [7.3,8.3] 样例库为空（SD 定数相邻带 7.2/10.2）
    b50 = [
        extend(8, "True Love Song", LevelIndex.BASIC, 99.0, compute_rating(7.3, 100.5))
    ]
    assert await _pick_rise_song(b50) is None


@pytest.mark.asyncio
@requires_assets
async def test_mai_what_rise_fallback(app: App, songs, monkeypatch):
    """未绑定时 mai什么加分 退化为普通随机（原版行为）。"""
    import base64

    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    from nonebot_plugin_awmc_helper.plugins import random_song
    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.binding import binding_service
    from nonebot_plugin_awmc_helper.core.chart_card import chart_card_bytes

    async def fake_random(*a, **kw):
        all_songs = await song_service.get_all()
        return all_songs[0], all_songs[0].get_difficulties()[0]

    monkeypatch.setattr(song_service, "random", fake_random)
    picked = await song_service.random(exclude_utage=True)
    assert picked is not None
    song, _diff = picked
    # 退化路径同样渲染谱面卡（未绑定 → B50 拉取失败 → 纯谱面卡）
    binding = await binding_service.ensure("OneBot V11", "12345678")
    png = await chart_card_bytes(song, binding)

    event = fake_group_message_event_v11(message="mai什么加分")
    expected = Message(
        [
            MessageSegment.at(12345678),
            MessageSegment.image(f"base64://{base64.b64encode(png).decode()}"),
        ]
    )
    async with app.test_matcher(random_song.mai_what_rise) as ctx:
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
