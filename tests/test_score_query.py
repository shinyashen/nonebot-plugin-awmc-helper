"""awmc.score_query 查分子插件测试。"""

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
    「尚未绑定」提示仅非 QQ 平台可达，语义覆盖见 test_binding.py。"""
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
        231,
        "PENGUIN",
        aliases=["企鹅舞"],
        diffs=[
            make_diff(
                type=SongType.STANDARD,
                level_index=LevelIndex.EXPERT,
                level="10",
                level_value=10.5,
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
        app, score_query.ginfo, "ginfo 231", expected, with_session=False
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
            MessageSegment.text(
                " 暂无该谱面的游玩统计（需部署配置水鱼开发者 Token 以启用曲线数据）"
            ),
        ]
    )
    await _send_image_reply(
        app, score_query.ginfo, "ginfo 红231", expected, with_session=False
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
    from nonebot_plugin_awmc_helper.core.render.best50 import best50_bytes
    from nonebot_plugin_awmc_helper.plugins.score_query.matchers import _display_name

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
                    "song_id": 10231,  # 231 的 DX 谱
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
            _display_name(player),
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
        title="PENGUIN",
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


@pytest.mark.asyncio
async def test_b50_rise_tips(db, songs):
    """「可进 B50」提示（增强保留）：仅对未入 B50 且 RA 高于入线的成绩提示。"""
    import respx
    from maimai_py import SongType, LevelIndex

    from nonebot_plugin_awmc_helper.core.binding import binding_service
    from nonebot_plugin_awmc_helper.plugins.score_query.render import _b50_rise_tips

    binding = await binding_service.ensure("OneBot V11", "12345678")
    # B50：231 的 SD EXPERT 槽（ra 312 入线，须命中曲库存在的谱面）；
    # 候选：DX MASTER 350（应提示）/ DX BASIC 120（低于入线不提示）
    payload = {
        "username": "someone",
        "rating": 312,
        "charts": {
            "sd": [
                {
                    "song_id": 231,
                    "level": "10",
                    "level_index": 2,
                    "achievements": 100.0,
                    "fc": None,
                    "fs": None,
                    "dxScore": 600,
                    "rate": "sss",
                    "ra": 312,
                }
            ],
            "dx": [],
        },
    }
    with respx.mock(assert_all_called=False) as m:
        m.post(f"{BASE_DF}/query/player").respond(json=payload)
        tips = await _b50_rise_tips(
            [
                _score_extend(231, SongType.DX, LevelIndex.MASTER, dx_rating=350),
                _score_extend(
                    231,
                    SongType.DX,
                    LevelIndex.BASIC,
                    level="6",
                    level_value=6.0,
                    dx_rating=120,
                    rate="sss",
                ),
            ],
            binding,
        )
    assert len(tips) == 1
    assert "MASTER" in tips[0]
    assert "+38" in tips[0]
