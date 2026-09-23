"""M5：random_song / fortune / guess 测试。"""

import pytest
from nonebug import App
from maimai_py import SongType  # maimai_py 不依赖 nonebot 初始化


@pytest.fixture
async def songs(tmp_path):
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
async def test_random_chart(app: App, songs):
    from nonebot_plugin_awmc_helper.plugins import random_song
    from nonebot_plugin_awmc_helper.core.songs import song_service

    got = await song_service.random(song_type=SongType.DX, level="13+")
    assert got is not None
    assert got[0].id == 500

    # matcher：样例中 DX 13+ 唯一（500），断言回复图片（同源渲染）
    import base64

    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    from nonebot_plugin_awmc_helper.core.binding import binding_service
    from nonebot_plugin_awmc_helper.core.chart_card import chart_card_bytes

    picked = await song_service.random(song_type=SongType.DX, level="13+")
    assert picked is not None
    song, _diff = picked
    # 未绑定用户：binding.ensure 装配 QQ 公开凭据后 B50 拉取失败 → 纯谱面卡
    binding = await binding_service.ensure("OneBot V11", "12345678")
    png = await chart_card_bytes(song, binding)
    event = fake_group_message_event_v11(message="随个dx13+")
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
        app, random_song.random_chart, "随个白14", "没有符合条件的谱面，换一个试试吧",
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
async def test_fortune(app: App, songs):
    """今日mai：同日确定性输出（文本 + 推荐曲卡）。"""
    import random as _random_mod

    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    from nonebot_plugin_awmc_helper.plugins import fortune
    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.render import song as song_render
    from nonebot_plugin_awmc_helper.plugins.fortune import FORTUNE, qqhash

    seed = int("12345678")
    fh = qqhash(seed)
    daily = _random_mod.Random(fh)
    all_songs = await song_service.get_all()
    song = daily.choice(all_songs)
    ds = "/".join(f"{d.level_value:.1f}" for d in song.get_difficulties())

    rp = fh % 100
    h = fh
    lines = [f" 今日人品值：{rp}"]
    for i in range(11):
        wm = h & 3
        h >>= 2
        if wm == 3:
            lines.append(f"宜 {FORTUNE[i]}")
        elif wm == 0:
            lines.append(f"忌 {FORTUNE[i]}")
    lines.append("打机时不要大力拍打或滑动哦")
    lines.append(f"今日推荐歌曲：ID.{song.id} - {song.title}（定数 {ds}）")
    text = "\n".join(lines)

    import nonebot as _nb

    event = fake_group_message_event_v11(message="今日mai")
    expected = Message(
        [
            MessageSegment.at(12345678),
            MessageSegment.text(text),
            MessageSegment.image(
                f"base64://{_b64(song_render.song_card_bytes(song)).decode()}"
            ),
        ]
    )
    async with app.test_matcher(fortune.today_fortune) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=_nb.get_adapter(OnebotV11Adapter))
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
        _ = _nb


def _b64(data: bytes) -> bytes:
    import base64

    return base64.b64encode(data)


@pytest.mark.asyncio
async def test_fortune_hash_stable():
    """同日同 QQ 哈希稳定（确定性）。"""
    from nonebot_plugin_awmc_helper.plugins.fortune import qqhash

    assert qqhash(123456) == qqhash(123456)
    assert qqhash(1) != qqhash(2)


@pytest.mark.asyncio
async def test_guess_crop_smoke(songs):
    """FFT 裁剪冒烟：输入输出尺寸正确。"""
    from PIL import Image

    from nonebot_plugin_awmc_helper.core.render.cover import (
        frequency_weights,
        crop_cover_randomly,
    )

    img = Image.new("RGBA", (400, 400), "#336699")

    w = frequency_weights(img)
    assert w.shape == (400, 400)
    cropped = crop_cover_randomly(img)
    assert cropped.size[0] < 400
    assert cropped.size[1] < 400


@pytest.mark.asyncio
async def test_guess_manager(songs, monkeypatch):
    """猜歌对局管理：开局→提示序列→答对揭晓→清理；答案判定不区分大小写。"""
    from nonebot_plugin_awmc_helper.plugins import guess as guess_plugin
    from nonebot_plugin_awmc_helper.core.songs import song_service

    monkeypatch.setattr(guess_plugin.plugin_config, "awmc_guess_interval", 60)
    monkeypatch.setattr(guess_plugin.plugin_config, "awmc_guess_duration", 60)

    song = await guess_plugin._pick_song()
    assert song is not None
    game = guess_plugin.GuessGame(
        song, pic_mode=False, group_id="g1", bot=None, event=None
    )
    guess_plugin._games["g1"] = game
    assert guess_plugin._game_of("g1") is game
    assert len(game.hints) == 6
    # 6 条提示后进入曲绘阶段
    for i in range(6):
        hint = game.next_hint()
        assert hint is not None
        assert hint.startswith(f"提示{i + 1}")
    assert game.next_hint() is None

    assert not game.match("完全无关的回答")
    assert game.match(song.title.upper())  # 大小写不敏感
    assert game.match(str(song.id))

    # 热门题库：无曲线数据时退化为全曲库
    all_songs = await song_service.get_all()
    assert song in all_songs


@pytest.mark.asyncio
async def test_guess_answer_flow(app: App, songs, monkeypatch):
    """on_message 答案拦截：命中后揭晓并清理对局。"""
    from nonebot_plugin_uninfo import User, Scene, Session, SceneType

    from nonebot_plugin_awmc_helper.plugins import guess as guess_plugin

    monkeypatch.setattr(guess_plugin.plugin_config, "awmc_guess_duration", 60)

    revealed: list[str] = []

    async def fake_reveal(game, prefix):
        revealed.append(prefix)
        guess_plugin._games.pop(game.group_id, None)

    monkeypatch.setattr(guess_plugin, "_reveal", fake_reveal)

    song = await guess_plugin._pick_song()
    assert song is not None
    game = guess_plugin.GuessGame(
        song, pic_mode=True, group_id="g2", bot=None, event=None
    )
    guess_plugin._games["g2"] = game

    session = Session(
        self_id="test",
        adapter="OneBot V11",
        scope="qq_client",
        scene=Scene(id="g2", type=SceneType.GROUP),
        user=User(id="10086"),
        member=None,
        operator=None,
        platform="unknown",
    )
    # 错误答案不触发
    assert not await guess_plugin._handle_answer(session, "乱答的")
    assert guess_plugin._game_of("g2") is game
    # 正确答案揭晓
    assert await guess_plugin._handle_answer(session, song.title)
    assert revealed
    assert guess_plugin._game_of("g2") is None


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

    def extend(sid, level_index, ach, ra):
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
            title=f"T{sid}",
            level_value=10.5,
            level_dx_score=2400,
            dx_star=4,
            version=25000,
        )

    # 末位 ra=236 → ds=10.5 → 窗口 [10.5,11.5] 命中 231 的 SD Expert(10.5)
    ra236 = compute_rating(10.5, 100.5)
    b50 = [extend(500, LevelIndex.BASIC, 99.0, ra236)]
    got = await _pick_rise_song(b50)
    assert got is not None
    assert got.id == 231

    # SSS+ 排除：231 已 SSS+ → 无候选
    b50.append(extend(231, LevelIndex.EXPERT, 100.5, 9999))
    got = await _pick_rise_song(b50)
    assert got is None

    # 窗口无候选：末位 ra 抬高 → ds=11.9 → 窗口 [11.9,12.9] 样例库为空
    b50 = [extend(500, LevelIndex.BASIC, 99.0, compute_rating(11.9, 99.0))]
    assert await _pick_rise_song(b50) is None


@pytest.mark.asyncio
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
