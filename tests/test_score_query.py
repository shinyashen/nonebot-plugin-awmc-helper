"""awmc.score_query 查分子插件测试。"""

import asyncio

import pytest
from mocks import requires_assets
from nonebug import App

BASE_DF = "https://www.diving-fish.com/api/maimaidxprober"


@pytest.fixture
async def db(tmp_path):
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
async def test_b50_unbound_hint(db):
    """OneBot v11 运行时（platform 兜底 "unknown"）未绑定用户不再提示：
    user_id 可作 QQ 号装配凭据，b50 走水鱼 QQ 公开查询——
    「尚未绑定」提示仅非 QQ 平台可达，语义覆盖见 test_core_binding.py。"""
    from nonebot_plugin_awmc_helper.core.binding import binding_service

    binding = await binding_service.ensure("OneBot V11", "12345678")
    assert binding_service.identifier_or_none(binding) is not None


def _curve_song():
    """带拟合统计的样例曲（ginfo 用，MASTER DX 谱挂 CurveObject）。"""
    from mocks import make_diff, make_song
    from maimai_py import FCType, RateType, SongType, LevelIndex
    from maimai_py.models import CurveObject

    curve = CurveObject(
        sample_size=12345,
        fit_level_value=13.8,
        avg_achievements=98.76,
        stdev_achievements=2.34,
        avg_dx_score=2555.0,
        rate_sample_size={RateType.SSS: 100, RateType.SSP: 50},
        fc_sample_size={FCType.FC: 80, FCType.AP: 20},
    )
    return make_song(
        199,
        "チルノのパーフェクトさんすう教室",
        aliases=["琪露诺"],
        diffs=[
            make_diff(
                type=SongType.STANDARD,
                level_index=LevelIndex.EXPERT,
                level="10",
                level_value=10.4,
            ),
            make_diff(
                type=SongType.DX,
                level_index=LevelIndex.MASTER,
                level="13",
                level_value=13.2,
                curve=curve,
            ),
        ],
    )


async def _send_image_reply(
    app, matcher, text: str, expected, user_id=12345678, *, with_session=True
):
    """带图/文本回复的通用断言。with_session=False 用于无 UniSession 依赖的
    matcher（如 ginfo）：发送前不会触发群信息 API 调用。"""
    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot
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
        ctx.should_call_send(event, expected, result=None, bot=bot)
        ctx.should_finished()


@requires_assets
@pytest.mark.asyncio
async def test_ginfo_rich_chart_card(app: App, db, songs):
    """ginfo 富谱面卡 + 双环统计卡（R2/R9）：紫231 → DX 主类型卡 + 统计卡。"""
    import base64

    from mocks import seed_service
    from maimai_py import SongType, LevelIndex
    from nonebot.adapters.onebot.v11 import Message, MessageSegment

    from nonebot_plugin_awmc_helper.plugins import score_query
    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.render import stats as stats_render
    from nonebot_plugin_awmc_helper.core.render import nb_chart
    from nonebot_plugin_awmc_helper.plugins.score_query.render import _ginfo_image

    seeded = await seed_service(song_service, [_curve_song()])
    song = seeded[0]
    diff = song.get_difficulty(SongType.DX, LevelIndex.MASTER)
    assert diff is not None
    assert diff.curve is not None

    # 与 handler 相同的调用路径 → 相同数据 → 相同渲染（R9 统计卡）
    card = nb_chart.song_chart_info(song, False, False, [], "prism_plus", None)
    expected_png = _ginfo_image(card, stats_render.song_global_data(song, diff))
    expected = Message(
        [
            MessageSegment.at(12345678),
            MessageSegment.image(f"base64://{base64.b64encode(expected_png).decode()}"),
        ]
    )
    await _send_image_reply(
        app, score_query.ginfo, "ginfo 199", expected, with_session=False
    )


@pytest.mark.asyncio
async def test_ginfo_sd_chart_without_curve(app: App, db, songs):
    """ginfo 指定 SD 色谱（红231，无曲线数据）→ 提示暂无游玩统计。"""
    from mocks import seed_service
    from nonebot.adapters.onebot.v11 import Message, MessageSegment

    from nonebot_plugin_awmc_helper.plugins import score_query
    from nonebot_plugin_awmc_helper.core.songs import song_service

    await seed_service(song_service, [_curve_song()])
    expected = Message(
        [
            MessageSegment.at(12345678),
            MessageSegment.text(" 该谱面暂无游玩统计（新谱样本不足或曲线数据未加载）"),
        ]
    )
    await _send_image_reply(
        app, score_query.ginfo, "ginfo 红199", expected, with_session=False
    )


@requires_assets
@pytest.mark.asyncio
async def test_b50_username_lookup(app: App, db, songs):
    """b50 <水鱼用户名> 公开代查：respx mock 水鱼接口，期望图与真实渲染同源。"""
    import base64

    import respx
    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    from nonebot_plugin_awmc_helper.plugins import score_query
    from nonebot_plugin_awmc_helper.core.score import score_service
    from nonebot_plugin_awmc_helper.core.utils import player_display_name
    from nonebot_plugin_awmc_helper.core.render.best50 import best50_bytes

    payload = {
        "username": "someone",
        "rating": 350,
        "nickname": "昵称酱",  # 卡片显示昵称而非账号用户名（原版 df_to_player 同款）
        "plate": "彩将",
        "additional_rating": 1,
        "charts": {
            "sd": [],
            "dx": [
                {
                    "song_id": 10199,  # 199 的 DX 谱
                    "level": "13",
                    "level_index": 3,
                    "achievements": 100.5,
                    "fc": "ap",
                    "fs": "fsd",
                    "dxScore": 2000,
                    "rate": "sssp",
                    "ra": 350,
                }
            ],
        },
    }
    with respx.mock(assert_all_called=False) as m:
        m.post(f"{BASE_DF}/query/player").respond(json=payload)
        # 与 handler 相同的调用路径 → 相同数据 → 相同渲染
        player, bests = await score_service.get_b50_by_username("someone")
        expected_png = await best50_bytes(
            player_display_name(player),
            bests.rating,
            bests.rating_b35,
            bests.rating_b15,
            bests.scores_b35,
            bests.scores_b15,
            player=player,
            service="divingfish",
        )

        event = fake_group_message_event_v11(message="b50 someone")
        expected = Message(
            [
                MessageSegment.at(12345678),
                MessageSegment.image(
                    f"base64://{base64.b64encode(expected_png).decode()}"
                ),
            ]
        )
        async with app.test_matcher(score_query.b50) as ctx:
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


def _score_extend(
    song_id: int,
    type_,
    level_index,
    *,
    level: str = "13",
    level_value: float = 13.2,
    achievements: float = 100.5,
    fc=None,
    fs=None,
    dx_score: int = 2900,
    dx_rating: float = 350,
    rate="sssp",
):
    """构造 ScoreExtend（minfo 成绩卡/推分卡用）。"""
    import dataclasses

    from maimai_py import Score, FCType, FSType, RateType, ScoreExtend

    base = Score(
        id=song_id,
        level=level,
        level_index=level_index,
        achievements=achievements,
        fc=FCType(fc) if isinstance(fc, int) else fc,
        fs=FSType(fs) if isinstance(fs, int) else fs,
        dx_score=dx_score,
        dx_rating=dx_rating,
        play_count=None,
        play_time=None,
        rate=RateType[rate.upper()] if isinstance(rate, str) else rate,
        type=type_,
    )
    return ScoreExtend(
        **dataclasses.asdict(base),
        title="チルノのパーフェクトさんすう教室",
        level_value=level_value,
        level_dx_score=3000,
        dx_star=None,
        version=25000,
    )


@requires_assets
def test_minfo_card_layout(db):
    """minfo 成绩卡（R1）：NB play_info 版式 1200×900，含已玩/未玩灰行。"""
    import io

    from PIL import Image
    from maimai_py import FCType, FSType, SongType, LevelIndex

    from nonebot_plugin_awmc_helper.core.render import info as info_render

    song = _curve_song()
    scores = [
        _score_extend(
            231,
            SongType.DX,
            LevelIndex.MASTER,
            fc=FCType.AP,
            fs=FSType.FSD,
            dx_score=2900,
            dx_rating=350,
        ),
        _score_extend(
            231,
            SongType.DX,
            LevelIndex.BASIC,
            level="6",
            level_value=6.0,
            achievements=97.12,
            dx_score=900,
            dx_rating=120,
            rate="sss",
        ),
    ]
    png = info_render.song_play_data(song, scores, service="divingfish")
    im = Image.open(io.BytesIO(png))
    assert im.size == (1200, 900)


@requires_assets
def test_minfo_card_unplayed_all_slots(db):
    """未绑定（scores 空）时只展示谱面信息：全部槽灰行 + 4 槽曲「没有该难度」。"""
    import io

    from PIL import Image
    from mocks import make_diff, make_song
    from maimai_py import SongType, LevelIndex

    from nonebot_plugin_awmc_helper.core.render import info as info_render

    song = make_song(
        700,
        "OLD SONG",
        diffs=[
            make_diff(
                type=SongType.STANDARD,
                level_index=li,
                level="9",
                level_value=9.0,
            )
            for li in (
                LevelIndex.BASIC,
                LevelIndex.ADVANCED,
                LevelIndex.EXPERT,
                LevelIndex.MASTER,
            )
        ],
    )
    png = info_render.song_play_data(song, [], prefer_type=SongType.STANDARD)
    assert Image.open(io.BytesIO(png)).size == (1200, 900)


@requires_assets
def test_minfo_card_renders_given_prefer_type(db):
    """成绩卡只按传入的偏好渲染（渲染层不猜类型）：偏好切换即换卡片主类型。"""
    from maimai_py import SongType, LevelIndex

    from nonebot_plugin_awmc_helper.core.render import info as info_render

    song = _curve_song()  # SD EXPERT + DX MASTER
    sd_score = _score_extend(199, SongType.STANDARD, LevelIndex.EXPERT)
    # 主类型由指令侧定好后传入（数字 id 形状 / 条目类型），未指定偏好时 DX 优先
    assert info_render.song_play_data(song, [sd_score]) == info_render.song_play_data(
        song, [sd_score], prefer_type=SongType.DX
    )
    assert info_render.song_play_data(
        song, [sd_score], prefer_type=SongType.STANDARD
    ) != info_render.song_play_data(song, [sd_score])


@pytest.mark.asyncio
async def test_get_minfo_unplayed_maps_to_none(songs, monkeypatch):
    """core 契约：已绑定且无成绩 → None（未游玩）；未绑定 → 空成绩 PlayerSong。"""
    from maimai_py import PlayerSong

    from nonebot_plugin_awmc_helper.core import client as client_mod
    from nonebot_plugin_awmc_helper.core.score import score_service
    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.binding import binding_service

    async def fake_minfo(song, identifier, provider=None, **kwargs):
        return PlayerSong(song, [])

    monkeypatch.setattr(client_mod.client, "minfo", fake_minfo)
    song = await song_service.by_id(199)
    assert song is not None

    binding = await binding_service.ensure("OneBot V11", "12345678")
    assert binding_service.identifier_or_none(binding) is not None
    assert await score_service.get_minfo(song, binding) is None
    # 未绑定：纯谱面视图（PlayerSong 原样返回，scores 为空）
    unbound = await score_service.get_minfo(song, None)
    assert unbound is not None
    assert unbound.scores == []


@pytest.mark.asyncio
async def test_get_minfo_type_scoped_unplayed(songs, monkeypatch):
    """core 契约：指定谱面类型时该类型无成绩即未游玩（另一类型有分不算）。

    双谱曲「标准谱有分、DX 谱没分」查 DX → None（提示未游玩），而不是把这曲的
    标准谱成绩当成 DX 成绩、画出全「未游玩」的空卡。
    """
    from maimai_py import SongType, LevelIndex, PlayerSong

    from nonebot_plugin_awmc_helper.core import client as client_mod
    from nonebot_plugin_awmc_helper.core.score import score_service
    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.binding import binding_service

    sd_scores = [_score_extend(199, SongType.STANDARD, LevelIndex.EXPERT)]

    async def fake_minfo(song, identifier, provider=None, **kwargs):
        return PlayerSong(song, sd_scores)

    monkeypatch.setattr(client_mod.client, "minfo", fake_minfo)
    song = await song_service.by_id(199)
    assert song is not None
    binding = await binding_service.ensure("OneBot V11", "12345678")

    assert await score_service.get_minfo(song, binding, SongType.DX) is None
    sd_hit = await score_service.get_minfo(song, binding, SongType.STANDARD)
    assert sd_hit is not None
    assert sd_hit.scores == sd_scores
    # 不指定类型（名称命中多条目）时任一类型有成绩即算玩过
    any_hit = await score_service.get_minfo(song, binding)
    assert any_hit is not None


@pytest.mark.asyncio
async def test_minfo_unplayed_hint(app: App, db, songs, monkeypatch):
    """已绑定但无成绩 → 不渲染成绩卡，提示未游玩（对齐 Hoshino 原版）。"""
    from nonebot_plugin_awmc_helper.plugins import score_query
    from nonebot_plugin_awmc_helper.core.score import score_service

    async def fake_minfo(song, binding, song_type=None, notify_slow=None):
        return None

    monkeypatch.setattr(score_service, "get_minfo", fake_minfo)
    await _assert_reply(app, score_query.minfo, "minfo 199", "尚未游玩过该曲目")


@requires_assets
@pytest.mark.asyncio
async def test_minfo_alias_lists_entry_ids(app: App, db, songs, monkeypatch):
    """别名查双谱曲（玩过）→ 不猜卡片主类型，列出 SD/DX 条目 id 交用户指定。

    「相信彩虹」场景（标准谱有分）：与「是什么歌」同格式同语义（对齐 Hoshino
    基准 info 的多 id 分支）。
    """
    from maimai_py import SongType, LevelIndex, PlayerSong

    from nonebot_plugin_awmc_helper.plugins import score_query
    from nonebot_plugin_awmc_helper.core.score import score_service

    sd_scores = [_score_extend(199, SongType.STANDARD, LevelIndex.EXPERT)]

    async def fake_minfo(song_key, binding_key, song_type=None, notify_slow=None):
        return PlayerSong(song_key, sd_scores)

    monkeypatch.setattr(score_service, "get_minfo", fake_minfo)
    await _assert_reply(
        app,
        score_query.minfo,
        "minfo 琪露诺",
        "找到3个谱面："
        "\n199：チルノのパーフェクトさんすう教室"
        "\n10199：チルノのパーフェクトさんすう教室"
        "\n100199：チルノのパーフェクトさんすう教室"
        "\n※ 请使用「ginfo <ID>」查询指定谱面",
    )


@requires_assets
@pytest.mark.asyncio
async def test_minfo_unplayed_song_hints_not_id_list(app: App, db, songs, monkeypatch):
    """别名查双谱曲但**整曲未游玩** → 文本提示，而非 id 列表或全灰卡。"""
    from nonebot_plugin_awmc_helper.plugins import score_query
    from nonebot_plugin_awmc_helper.core.score import score_service

    async def fake_minfo(song_key, binding_key, song_type=None, notify_slow=None):
        return None

    monkeypatch.setattr(score_service, "get_minfo", fake_minfo)
    await _assert_reply(app, score_query.minfo, "minfo 琪露诺", "尚未游玩过该曲目")


@requires_assets
@pytest.mark.asyncio
async def test_minfo_unplayed_chart_type_hints(app: App, db, songs, monkeypatch):
    """查**没打过的谱面类型**（dx 前缀 / DX id）→ 文本提示，不画全「未游玩」空卡。

    双谱曲标准谱有分而 DX 谱没分时，按类型收窄的「未游玩」判定在 core
    （``get_minfo`` 的 ``song_type``）；此处同时断言 handler 传对了类型。
    """
    from maimai_py import SongType

    from nonebot_plugin_awmc_helper.plugins import score_query
    from nonebot_plugin_awmc_helper.core.score import score_service

    seen: list = []

    async def fake_minfo(song_key, binding_key, song_type=None, notify_slow=None):
        seen.append(song_type)
        return None  # 该类型无成绩

    monkeypatch.setattr(score_service, "get_minfo", fake_minfo)
    await _assert_reply(app, score_query.minfo, "minfo dx琪露诺", "尚未游玩过该曲目")
    await _assert_reply(app, score_query.minfo, "minfo 10199", "尚未游玩过该曲目")
    # 单条目/数字 id 均按主类型收窄；双条目（名称无前缀）不指定类型
    assert seen == [SongType.DX, SongType.DX]


@requires_assets
@pytest.mark.asyncio
async def test_minfo_entry_prefix_pins_card_type(app: App, db, songs, monkeypatch):
    """minfo 带谱面前缀（dx/标）时条目收敛到该类型 → 直接出该类型成绩卡。"""
    import base64

    from maimai_py import SongType, LevelIndex, PlayerSong
    from nonebot.adapters.onebot.v11 import Message, MessageSegment

    from nonebot_plugin_awmc_helper.plugins import score_query
    from nonebot_plugin_awmc_helper.constants import DEFAULT_THEME
    from nonebot_plugin_awmc_helper.core.score import score_service
    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.render import info as info_render
    from nonebot_plugin_awmc_helper.core.binding import binding_service

    binding = await binding_service.ensure("OneBot V11", "12345678")
    song = await song_service.by_id(199)
    assert song is not None
    dx_scores = [_score_extend(199, SongType.DX, LevelIndex.MASTER)]

    async def fake_minfo(song_key, binding_key, song_type=None, notify_slow=None):
        assert song_type == SongType.DX  # dx 前缀须把类型收敛到 DX
        return PlayerSong(song, dx_scores)

    monkeypatch.setattr(score_service, "get_minfo", fake_minfo)
    expected_png = info_render.song_play_data(
        song,
        dx_scores,
        service=binding.service,
        theme=binding.theme or DEFAULT_THEME,
        prefer_type=SongType.DX,
    )
    expected = Message(
        [
            MessageSegment.at(12345678),
            MessageSegment.image(f"base64://{base64.b64encode(expected_png).decode()}"),
        ]
    )
    await _send_image_reply(app, score_query.minfo, "minfo dx琪露诺", expected)


@requires_assets
@pytest.mark.parametrize(
    ("key", "type_name", "level_index"),
    [("199", "STANDARD", "EXPERT"), ("10199", "DX", "MASTER")],
)
@pytest.mark.asyncio
async def test_minfo_digit_id_pins_card_type(
    app: App, db, songs, monkeypatch, key, type_name, level_index
):
    """数字 id 按其形状定卡片主类型：根 id → SD 卡，+10000 → DX 卡。"""
    import base64

    from maimai_py import SongType, LevelIndex, PlayerSong
    from nonebot.adapters.onebot.v11 import Message, MessageSegment

    from nonebot_plugin_awmc_helper.plugins import score_query
    from nonebot_plugin_awmc_helper.constants import DEFAULT_THEME
    from nonebot_plugin_awmc_helper.core.score import score_service
    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.render import info as info_render
    from nonebot_plugin_awmc_helper.core.binding import binding_service

    binding = await binding_service.ensure("OneBot V11", "12345678")
    song = await song_service.by_id(199)
    assert song is not None
    expected_type = SongType[type_name]
    scores = [_score_extend(199, expected_type, LevelIndex[level_index])]

    async def fake_minfo(song_key, binding_key, queried_type=None, notify_slow=None):
        assert queried_type is expected_type  # id 形状推断的类型须原样传入 core
        return PlayerSong(song, scores)

    monkeypatch.setattr(score_service, "get_minfo", fake_minfo)
    expected_png = info_render.song_play_data(
        song,
        scores,
        service=binding.service,
        theme=binding.theme or DEFAULT_THEME,
        prefer_type=expected_type,
    )
    expected = Message(
        [
            MessageSegment.at(12345678),
            MessageSegment.image(f"base64://{base64.b64encode(expected_png).decode()}"),
        ]
    )
    await _send_image_reply(app, score_query.minfo, f"minfo {key}", expected)


_B50_PAYLOAD = {
    "username": "someone",
    "rating": 350,
    "nickname": "昵称酱",
    "plate": "彩将",
    "additional_rating": 1,
    "charts": {
        "sd": [],
        "dx": [
            {
                "song_id": 10199,
                "level": "13",
                "level_index": 3,
                "achievements": 100.5,
                "fc": "ap",
                "fs": "fsd",
                "dxScore": 2000,
                "rate": "sssp",
                "ra": 350,
            }
        ],
    },
}


@requires_assets
@pytest.mark.asyncio
async def test_b50_at_unbound_target_qq_fallback(app: App, db, songs):
    """b50 @未绑定目标（OneBot）：at 段不得进用户名参数（CQ 码污染回归）；
    QQ 平台回退「水鱼按目标 QQ 公开查询」（Hoshino 同款），且不给目标落库建行。"""
    import json
    import base64

    import respx
    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.plugins import score_query
    from nonebot_plugin_awmc_helper.core.score import score_service
    from nonebot_plugin_awmc_helper.core.utils import player_display_name
    from nonebot_plugin_awmc_helper.core.render.best50 import best50_bytes

    with respx.mock(assert_all_called=False) as m:
        route = m.post(f"{BASE_DF}/query/player").respond(json=_B50_PAYLOAD)

        # 与 handler 相同调用路径（临时绑定 → QQ 公开查询）构造期望图
        from nonebot_plugin_awmc_helper.core.store import UserBinding

        transient = UserBinding(
            platform="OneBot V11", user_id="99999999", service="divingfish"
        )
        player, bests = await asyncio.gather(
            score_service.get_player(transient), score_service.get_b50(transient)
        )
        expected_png = await best50_bytes(
            player_display_name(player),
            bests.rating,
            bests.rating_b35,
            bests.rating_b15,
            bests.scores_b35,
            bests.scores_b15,
            player=player,
            qqid=99999999,
            service="divingfish",
        )
        # 公开查询键 = 目标 QQ（而非 at 段的 CQ 码字符串）
        for call in route.calls:
            body = json.loads(call.request.content)
            assert body.get("qq") == "99999999"

        event = fake_group_message_event_v11(
            message=Message([MessageSegment.text("b50 "), MessageSegment.at(99999999)]),
            user_id=12345678,
        )
        expected = Message(
            [
                MessageSegment.at(12345678),
                MessageSegment.image(
                    f"base64://{base64.b64encode(expected_png).decode()}"
                ),
            ]
        )
        async with app.test_matcher(score_query.b50) as ctx:
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
    # 代查不给目标落库建行（resolve_query 只读）
    assert await store.get_binding("OneBot V11", "99999999") is None


@requires_assets
@pytest.mark.asyncio
async def test_b50_at_bound_username_target(app: App, db, songs):
    """b50 @已绑用户名目标：公开键 = 目标绑定行的水鱼用户名（优先于 QQ）。"""
    import json
    import base64

    import respx
    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    from nonebot_plugin_awmc_helper.plugins import score_query
    from nonebot_plugin_awmc_helper.core.score import score_service
    from nonebot_plugin_awmc_helper.core.utils import player_display_name
    from nonebot_plugin_awmc_helper.core.binding import binding_service
    from nonebot_plugin_awmc_helper.core.render.best50 import best50_bytes

    binding = await binding_service.ensure("OneBot V11", "99999999")
    await binding_service.bind_divingfish_username(binding, "fishuser")

    with respx.mock(assert_all_called=False) as m:
        route = m.post(f"{BASE_DF}/query/player").respond(json=_B50_PAYLOAD)
        player, bests = await asyncio.gather(
            score_service.get_player(binding), score_service.get_b50(binding)
        )
        expected_png = await best50_bytes(
            player_display_name(player),
            bests.rating,
            bests.rating_b35,
            bests.rating_b15,
            bests.scores_b35,
            bests.scores_b15,
            player=player,
            qqid=99999999,
            service="divingfish",
            theme=binding.theme or "prism_plus",
        )
        for call in route.calls:
            body = json.loads(call.request.content)
            assert body.get("username") == "fishuser"
            assert "qq" not in body  # 用户名优先，不带聊天 QQ

        event = fake_group_message_event_v11(
            message=Message([MessageSegment.text("b50 "), MessageSegment.at(99999999)]),
            user_id=12345678,
        )
        expected = Message(
            [
                MessageSegment.at(12345678),
                MessageSegment.image(
                    f"base64://{base64.b64encode(expected_png).decode()}"
                ),
            ]
        )
        async with app.test_matcher(score_query.b50) as ctx:
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


@pytest.mark.asyncio
async def test_minfo_at_target_uses_target_binding(app: App, db, songs, monkeypatch):
    """minfo 199 @某人：曲目键不混入 at 段（CQ 码污染回归），成绩按目标绑定查询。"""
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Message, MessageSegment

    from nonebot_plugin_awmc_helper.plugins import score_query
    from nonebot_plugin_awmc_helper.core.score import score_service

    captured: dict = {}

    async def fake_minfo(song, binding, song_type=None, notify_slow=None):
        captured["platform"] = binding.platform
        captured["user_id"] = binding.user_id
        return None  # 未游玩 → 提示文案

    monkeypatch.setattr(score_service, "get_minfo", fake_minfo)

    event = fake_group_message_event_v11(
        message=Message(
            [MessageSegment.text("minfo 199 "), MessageSegment.at(99999999)]
        ),
        user_id=12345678,
    )
    import nonebot
    from nonebot.adapters.onebot.v11 import Bot
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    async with app.test_matcher(score_query.minfo) as ctx:
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
        ctx.should_call_send(
            event,
            Message(
                [
                    MessageSegment.at(12345678),
                    MessageSegment.text(" 尚未游玩过该曲目"),
                ]
            ),
            result=None,
            bot=bot,
        )
        ctx.should_finished()
    assert captured["platform"] == "OneBot V11"
    assert captured["user_id"] == "99999999"  # 代查目标而非发送者


# ---------------------------------------------------------------------------
# ginfo 数字入口（L-7）：6 位宴谱 diff_id 不再渲染宿主曲统计卡；
# 数字入口接 core 解析获得日服兜底
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ginfo_utage_id_no_stats(app: App, db):
    """ginfo 6 位宴谱 diff_id → 「宴谱没有游玩统计」而非宿主曲统计卡。

    修复前 by_id 对 6 位 id 取模命中同号普通曲，渲染出与提示语矛盾的
    宿主曲统计卡（L-7）。
    """
    from mocks import make_song, make_utage, seed_service

    from nonebot_plugin_awmc_helper.plugins import score_query
    from nonebot_plugin_awmc_helper.core.songs import song_service

    host = make_song(199, "チルノのパーフェクトさんすう教室", utage=[make_utage()])
    await seed_service(song_service, [host])
    await _assert_reply(
        app, score_query.ginfo, "ginfo 100199", "宴谱没有游玩统计", with_session=False
    )


@requires_assets
@pytest.mark.asyncio
async def test_ginfo_jp_only_numeric_fallback(app: App, db, songs, monkeypatch):
    """ginfo 数字入口接 core 解析：JP-only 曲不再「未找到」，出日服曲统计卡。

    ``songs`` 夹具必带：resolve_raw_chart 走 by_id 先查 CN 视图，
    未种子化会卡死在 ensure_loaded 的 _ready Event 上。
    """
    import base64

    from mocks import make_diff, make_song
    from maimai_py import FCType, RateType, SongType, LevelIndex
    from maimai_py.models import CurveObject
    from nonebot.adapters.onebot.v11 import Message, MessageSegment

    from nonebot_plugin_awmc_helper.plugins import score_query
    from nonebot_plugin_awmc_helper.constants import DEFAULT_THEME
    from nonebot_plugin_awmc_helper.core.render import stats as stats_render
    from nonebot_plugin_awmc_helper.core.render import nb_chart
    from nonebot_plugin_awmc_helper.plugins.score_query.render import _ginfo_image

    curve = CurveObject(
        sample_size=12345,
        fit_level_value=13.8,
        avg_achievements=98.76,
        stdev_achievements=2.34,
        avg_dx_score=2555.0,
        rate_sample_size={RateType.SSS: 100, RateType.SSP: 50},
        fc_sample_size={FCType.FC: 80, FCType.AP: 20},
    )
    jp_song = make_song(
        1634,
        "[協]青春コンプレックス",
        version=24000,
        diffs=[
            make_diff(
                type=SongType.DX,
                level_index=LevelIndex.MASTER,
                level="13",
                level_value=13.2,
                curve=curve,
            )
        ],
    )

    async def fake_jp_map():
        return {1634: jp_song}

    from nonebot_plugin_awmc_helper.core.songs import song_service

    monkeypatch.setattr(song_service, "_jp_songs_map", fake_jp_map)

    diff = jp_song.get_difficulty(SongType.DX, LevelIndex.MASTER)
    assert diff is not None
    assert diff.curve is not None
    card = nb_chart.song_chart_info(jp_song, False, False, [], DEFAULT_THEME, None)
    expected_png = _ginfo_image(card, stats_render.song_global_data(jp_song, diff))
    expected = Message(
        [
            MessageSegment.at(12345678),
            MessageSegment.image(f"base64://{base64.b64encode(expected_png).decode()}"),
        ]
    )
    await _send_image_reply(
        app, score_query.ginfo, "ginfo 1634", expected, with_session=False
    )
