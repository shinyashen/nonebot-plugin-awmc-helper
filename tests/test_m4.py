"""M4：core/calc 推分与分数线、ext/divingfish、tables/score_tools matchers 测试。"""

from pathlib import Path

import respx
import pytest
from nonebug import App

BASE_DF = "https://www.diving-fish.com/api/maimaidxprober"


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


# ---------------------------------------------------------------------------
# core.calc 单元对拍
# ---------------------------------------------------------------------------


def test_score_line_formula():
    """分数线公式对拍原版：紫799（DX Master）100% 线。"""
    from mocks import make_diff

    from nonebot_plugin_awmc_helper.core.calc import score_line

    diff = make_diff(
        tap_num=700, hold_num=100, slide_num=100, touch_num=100, break_num=20
    )
    result = score_line(diff, 100)
    total = 700 * 500 + 100 * 1000 + 100 * 1500 + 100 * 500 + 20 * 2500
    assert result is not None
    assert result["total"] == total
    assert result["tap_great"] == total * 1 / 10000
    assert result["breaks"] == 20

    assert score_line(diff, 101.5) is None  # 非法线
    assert score_line(diff, -1) is None


def test_compute_rating_consistent_with_library():
    """RA 计算与 maimai-py ScoreCoefficient 一致。"""
    from maimai_py.utils import ScoreCoefficient

    from nonebot_plugin_awmc_helper.core.calc import compute_rating

    assert compute_rating(13.5, 100.5) == ScoreCoefficient(100.5).ra(13.5)
    assert compute_rating(13.5, 99.0) == ScoreCoefficient(99.0).ra(13.5)


def test_rise_recommend_basic():
    """推分推荐：未入线曲目按目标档位给出提升。"""
    import dataclasses

    from mocks import sample_songs
    from maimai_py import Score, RateType, SongType, LevelIndex, ScoreExtend

    from nonebot_plugin_awmc_helper.core.calc import rise_recommend

    def extend(base, **kw):
        return ScoreExtend(**dataclasses.asdict(base), **kw)

    b50 = [
        extend(
            Score(
                id=231,
                level="13",
                level_index=LevelIndex.MASTER,
                achievements=100.0,
                fc=None,
                fs=None,
                dx_score=2000,
                dx_rating=200,
                play_count=None,
                play_time=None,
                rate=RateType.SSS,
                type=SongType.DX,
            ),
            title="PENGUIN",
            level_value=13.2,
            level_dx_score=2100,
            dx_star=4,
            version=25000,
        )
    ]
    candidates = [s for s in sample_songs() if s.id == 500]  # DX Master 13.7
    rec = rise_recommend(b50, candidates, target=10, latest_version_value=20000)
    assert rec
    top = rec[0]
    assert top["song"].id == 500
    assert top["gain"] >= 10


# ---------------------------------------------------------------------------
# ext/divingfish respx
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rating_ranking():
    from nonebot_plugin_awmc_helper.core.ext import divingfish as df_ext

    with respx.mock(assert_all_called=False) as m:
        m.get(f"{BASE_DF}/rating_ranking").respond(
            json=[
                {"username": "b", "ra": 12000},
                {"username": "a", "ra": 15000},
            ]
        )
        users = await df_ext.rating_ranking()
    assert users[0].username == "a"  # 降序
    assert users[0].ra == 15000


# ---------------------------------------------------------------------------
# matchers
# ---------------------------------------------------------------------------


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
            Message([MessageSegment.at(user_id), MessageSegment.text(reply)]),
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


@pytest.mark.asyncio
async def test_ds_table_command(app: App, songs):
    import base64

    from nonebot_plugin_awmc_helper.plugins import tables
    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.render.tools import (
        text_to_image,
        image_to_bytes,
    )

    entries = []
    for song in await song_service.get_all():
        for d in song.get_difficulties():
            if d.type != SongType.UTAGE and d.level == "13+":
                entries.append((d.level_value, song, d))
    entries.sort(key=lambda x: -x[0])
    lines = [f"定数表 13+（共 {len(entries)} 谱面）"]
    for ds, song, d in entries:
        type_abbr = "DX" if d.type == SongType.DX else "SD"
        lines.append(f"{ds:.1f}  {type_abbr} 「{song.id}」{song.title}")
    png = image_to_bytes(text_to_image("\n".join(lines), size=20))

    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    event = fake_group_message_event_v11(message="13+定数表")
    expected = Message(
        [
            MessageSegment.at(12345678),
            MessageSegment.image(f"base64://{base64.b64encode(png).decode()}"),
        ]
    )
    async with app.test_matcher(tables.ds_table_cmd) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter))
        ctx.receive_event(bot, event)
        ctx.should_call_send(event, expected, result=None, bot=bot)
        ctx.should_finished()


@pytest.mark.asyncio
async def test_score_list_unbound(app: App, db, songs):
    from nonebot_plugin_awmc_helper.plugins import tables

    await _assert_reply(
        app,
        tables.score_list_cmd,
        "13.7分数列表",
        "尚未绑定查分器，请先使用「绑定水鱼」或「绑定落雪」进行绑定",
    )


@pytest.mark.asyncio
async def test_plate_help(app: App):
    import base64

    from nonebot_plugin_awmc_helper.plugins import tables
    from nonebot_plugin_awmc_helper.constants import PLATE_KIND_ZH
    from nonebot_plugin_awmc_helper.core.render.tools import (
        text_to_image,
        image_to_bytes,
    )

    lines = ["牌子达成条件说明："]
    lines += [f"{kind}：{desc}" for kind, desc in PLATE_KIND_ZH.items()]
    lines.append("舞/霸：旧作（含 Re:MASTER 单列）全曲谱面")
    png = image_to_bytes(text_to_image("\n".join(lines)))

    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    event = fake_group_message_event_v11(message="牌子条件")
    expected = Message(
        [
            MessageSegment.at(12345678),
            MessageSegment.image(f"base64://{base64.b64encode(png).decode()}"),
        ]
    )
    async with app.test_matcher(tables.plate_help) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter))
        ctx.receive_event(bot, event)
        ctx.should_call_send(event, expected, result=None, bot=bot)
        ctx.should_finished()


@pytest.mark.asyncio
async def test_completion_grid_smoke(songs):
    """完成表网格渲染冒烟。"""
    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.render.table import completion_grid_bytes

    song = await song_service.by_id(231)
    diff = song.get_difficulty(SongType.DX, 3)
    png = completion_grid_bytes("测试完成表 1/1", [(song, diff, "done")])
    assert png.startswith(b"\x89PNG")
    assert len(png) > 1000


from maimai_py import SongType


@pytest.mark.asyncio
async def test_table_template_overlay(songs):
    """Q7 端到端：生成定数表底图 → 叠加印章 → 出非空 PNG；无底图时返回 None。"""
    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.render import table_template

    # 无底图 → 叠加返回 None（先清理可能的残留）
    (table_template.rating_table_dir() / "13+.png").unlink(missing_ok=True)
    assert await table_template.overlay_rating("13+", "Full Combo", [], 1, 80) is None

    total = await table_template.generate_rating_template("13+", song_service)
    assert total > 0
    path = table_template.rating_table_dir() / "13+.png"
    assert path.exists()

    song231 = await song_service.by_id(231)
    diff = song231.get_difficulties()[0]
    state_list = [(song231, diff, True)]
    png = await table_template.overlay_rating(
        "13+", "Full Combo", state_list, 1, per_page=80
    )
    assert png is not None
    assert png.startswith(b"\x89PNG\r\n")
