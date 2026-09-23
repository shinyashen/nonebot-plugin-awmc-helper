"""M4：core/calc 推分与分数线、ext/divingfish、tables/score_tools matchers 测试。"""

from pathlib import Path

import respx
import pytest
from mocks import requires_assets
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


def test_rise_recommend_old_fields():
    """推分输出携带旧成绩（推分行卡显示用）：未游玩 0 / 已游玩取 B50 成绩。"""
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
    candidates = [s for s in sample_songs() if s.id in (231, 500)]
    rec = rise_recommend(b50, candidates, target=10, latest_version_value=20000)
    by_chart = {(r["song"].id, r["diff"].type): r for r in rec}
    # 500 未游玩：旧成绩 0（NB RiseResult 默认值语义）
    assert by_chart[(500, SongType.DX)]["old_achievements"] == 0.0
    assert by_chart[(500, SongType.DX)]["old_ra"] == 0
    # 231 已游玩未入线：旧成绩取 B50 成绩
    assert by_chart[(231, SongType.DX)]["old_achievements"] == 100.0
    assert by_chart[(231, SongType.DX)]["old_ra"] == 200


@requires_assets
def test_draw_rise_card_smoke():
    """推分推荐行卡（R3）：NB draw_rise 版式，裁剪后 1000×960。"""
    import io
    import dataclasses

    from PIL import Image
    from mocks import sample_songs
    from maimai_py import Score, RateType, SongType, LevelIndex, ScoreExtend

    from nonebot_plugin_awmc_helper.core.calc import rise_recommend
    from nonebot_plugin_awmc_helper.core.render.score import DrawScore

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
    rec = rise_recommend(b50, sample_songs(), target=10, latest_version_value=20000)
    sd = [r for r in rec if r["diff"].type != SongType.DX][:5]
    dx = [r for r in rec if r["diff"].type == SongType.DX][:5]
    png = DrawScore(960, service="DivingFish").draw_rise(sd, dx, 960)
    im = Image.open(io.BytesIO(png))
    assert im.size == (1000, 960)


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
            Message([MessageSegment.at(user_id), MessageSegment.text(f" {reply}")]),
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
    from nonebot_plugin_awmc_helper.core.render import table_template

    # 与 handler 相同的调用路径 → 相同数据 → 相同渲染（NB 版式网格）；
    # 先清掉真实目录可能残留的预渲染底图，固定走实时生成分支（xdist 下
    # 期望图与 handler 渲染必须基于同一磁盘状态）
    (table_template.rating_table_dir() / "13+.png").unlink(missing_ok=True)
    entries = []
    for song in await song_service.get_all():
        for d in song.get_difficulties():
            if d.type != SongType.UTAGE and d.level == "13+":
                entries.append((song, d))
    png = table_template.rating_table_text_bytes("13+", entries)

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
    assert song is not None
    diff = song.get_difficulty(SongType.DX, LevelIndex.MASTER)
    assert diff is not None
    png = completion_grid_bytes("测试完成表 1/1", [(song, diff, "done")])
    assert png.startswith(b"\x89PNG")
    assert len(png) > 1000


from maimai_py import SongType, LevelIndex


@pytest.mark.asyncio
async def test_table_template_overlay(songs, tmp_path, monkeypatch):
    """NB 体系端到端：生成定数表底图 → DrawRatingTable 盖章 → 出非空 PNG。

    底图目录隔离到 tmp：xdist 并行下真实目录的底图写入会与
    test_ds_table_command 的期望渲染竞争（同图期望依赖稳定的磁盘状态）。
    """
    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.render import table_template
    from nonebot_plugin_awmc_helper.core.render.rating_table import draw_rating_table

    monkeypatch.setattr(
        table_template, "rating_table_dir", lambda: tmp_path / "rating_table"
    )
    entries = []
    for song in await song_service.get_all():
        for d in song.get_difficulties():
            if d.type != SongType.UTAGE and d.level == "13+":
                entries.append((song, d))
    assert entries

    total = await table_template.generate_rating_template("13+", song_service)
    assert total > 0
    assert (table_template.rating_table_dir() / "13+.png").exists()

    # 无成绩 → 空盖章但统计头正常；出非空 PNG
    png = draw_rating_table("13+", None, [], entries)
    assert png is not None
    assert png.startswith(b"\x89PNG\r\n")


def _progress_scores():
    """构造进度行卡成绩（13 级 DX 谱：已完成 2 / 未完成 1）。"""
    import dataclasses

    from maimai_py import Score, FCType, RateType, SongType, LevelIndex, ScoreExtend

    def extend(base, **kw):
        return ScoreExtend(**dataclasses.asdict(base), **kw)

    return [
        extend(
            Score(
                id=231,
                level="13",
                level_index=LevelIndex.MASTER,
                achievements=100.5,
                fc=FCType.AP,
                fs=None,
                dx_score=2900,
                dx_rating=350,
                play_count=None,
                play_time=None,
                rate=RateType.SSSP,
                type=SongType.DX,
            ),
            title="PENGUIN",
            level_value=13.2,
            level_dx_score=3000,
            dx_star=5,
            version=25000,
        ),
        extend(
            Score(
                id=500,
                level="13",
                level_index=LevelIndex.MASTER,
                achievements=99.12,
                fc=None,
                fs=None,
                dx_score=2500,
                dx_rating=300,
                play_count=None,
                play_time=None,
                rate=RateType.SSS,
                type=SongType.DX,
            ),
            title="Preferences",
            level_value=13.7,
            level_dx_score=3000,
            dx_star=4,
            version=25000,
        ),
        extend(
            Score(
                id=545,
                level="13",
                level_index=LevelIndex.MASTER,
                achievements=95.43,
                fc=None,
                fs=None,
                dx_score=1200,
                dx_rating=250,
                play_count=None,
                play_time=None,
                rate=RateType.S,
                type=SongType.DX,
            ),
            title="DdxPDX",
            level_value=13.5,
            level_dx_score=3000,
            dx_star=None,
            version=25000,
        ),
    ]


@requires_assets
def test_draw_plan_and_category_smoke():
    """等级进度卡（R4）：三段总览与分类页渲染尺寸符合预期。"""
    import io
    import dataclasses

    from PIL import Image
    from maimai_py import (
        ScoreExtend,
    )

    from nonebot_plugin_awmc_helper.core.render.score import DrawScore

    def extend(base, **kw):
        return ScoreExtend(**dataclasses.asdict(base), **kw)

    scores = _progress_scores()
    notplayed = [(700, 3, 13.4), (701, 3, 13.1)]

    # 三段总览（NB 高度公式）
    c_y = max(4, -(-len(scores[:30]) // 5)) * 109 + 140
    u_y = max(4, -(-0 // 5)) * 109 + 140
    n_y = max(4, -(-len(notplayed[:100]) // 20)) * 65 + 140
    card = DrawScore(150 + c_y + u_y + n_y, service="DivingFish")
    png = card.draw_plan("13", scores, c_y, [], u_y, notplayed, "fc", 30)
    assert Image.open(io.BytesIO(png)).size == (1400, 150 + c_y + u_y + n_y)

    # 分类页
    height = 240 + max(4, -(-len(scores) // 5)) * 109 + 120
    card = DrawScore(height)
    png = card.draw_category("completed", scores, 1, 1)
    assert Image.open(io.BytesIO(png)).size == (1400, height)

    # 未游玩网格
    nh = max(240 + max(4, -(-len(notplayed) // 20)) * 65 + 120, 600)
    card = DrawScore(nh)
    png = card.draw_category("notplayed", notplayed)
    assert Image.open(io.BytesIO(png)).size == (1400, nh)


@requires_assets
def test_plate_progress_card_smoke():
    """牌子进度总览（R7）：倒序槽节 + 进度条 + 封面网格，牌名繁体回退命中素材。"""
    import io
    from pathlib import Path

    from PIL import Image

    from nonebot_plugin_awmc_helper.core.render.plate_progress import (
        plate_progress_bytes,
    )

    def slot(li, cleared, total, n_items):
        items = [(231 + (i * 37) % 700, li, 13.0 - i * 0.1) for i in range(n_items)]
        return {"level_index": li, "cleared": cleared, "total": total, "items": items}

    slots = [slot(li, 40 - li * 5, 52 - li * 4, 3 + li * 5) for li in (3, 2, 1, 0)]
    png = plate_progress_bytes(
        "樱",
        "极",
        service="DivingFish",
        slots=slots,
        total_count=52,
        completed_count=28,
    )
    im = Image.open(io.BytesIO(png))
    assert im.size[0] == 1400
    assert im.size[1] > 900

    # 牌名繁体映射：樱极 → 櫻極.png 存在于素材包
    assert Path("static/mai/plate_version/櫻極.png").exists()


def test_rise_recommend_version_filter():
    """推分双栏按版本划分（用户口径，NB 旧/新版本谱面推荐）：旧版本 =
    当前版本以前全部谱面（b35 侧）、新版本 = 当前版本谱面（b15 侧），
    SD/DX 均可入任一栏，side 由谱面版本决定而非类型。"""
    import dataclasses

    from mocks import make_diff, make_song
    from maimai_py import Score, RateType, SongType, LevelIndex, ScoreExtend

    from nonebot_plugin_awmc_helper.core.calc import rise_recommend

    def extend(base, **kw):
        return ScoreExtend(**dataclasses.asdict(base), **kw)

    b50 = [
        extend(
            Score(
                id=900,
                level="13",
                level_index=LevelIndex.MASTER,
                achievements=99.0,
                fc=None,
                fs=None,
                dx_score=2000,
                dx_rating=200,
                play_count=None,
                play_time=None,
                rate=RateType.SSS,
                type=SongType.STANDARD,
            ),
            title="oldSD",
            level_value=13.0,
            level_dx_score=2100,
            dx_star=4,
            version=24000,
        ),
        extend(
            Score(
                id=910,
                level="13",
                level_index=LevelIndex.MASTER,
                achievements=99.0,
                fc=None,
                fs=None,
                dx_score=2000,
                dx_rating=210,
                play_count=None,
                play_time=None,
                rate=RateType.SSS,
                type=SongType.DX,
            ),
            title="oldDX",
            level_value=13.0,
            level_dx_score=2100,
            dx_star=4,
            version=24000,
        ),
        extend(
            Score(
                id=911,
                level="13",
                level_index=LevelIndex.MASTER,
                achievements=99.0,
                fc=None,
                fs=None,
                dx_score=2000,
                dx_rating=220,
                play_count=None,
                play_time=None,
                rate=RateType.SSS,
                type=SongType.DX,
            ),
            title="newDX",
            level_value=13.0,
            level_dx_score=2100,
            dx_star=4,
            version=25000,
        ),
    ]

    def cand(song_id, type_, version):
        return make_song(
            song_id,
            f"s{song_id}",
            diffs=[
                make_diff(
                    type=type_,
                    level_index=LevelIndex.MASTER,
                    level="13",
                    level_value=13.0,
                    version=version,
                )
            ],
        )

    candidates = [
        cand(921, SongType.DX, 25000),  # 当前版本 DX → 保留
        cand(920, SongType.DX, 24000),  # 旧版本 DX → 排除（新版本栏只推当前版本）
        cand(922, SongType.STANDARD, 25000),  # 当前版本 SD → 排除（旧版本栏语义）
        cand(923, SongType.STANDARD, 24000),  # 旧版本 SD → 保留
    ]
    def by_id(song_id, rec):
        return next(r for r in rec if r["song"].id == song_id)

    rec = rise_recommend(
        b50, candidates, target=1, latest_version_value=25000
    )
    # b50 两侧均有成绩（24000 旧版本侧 / 25000 新版本侧）→ 四首候选全部保留，
    # 归栏只看谱面版本
    got = {(r["song"].id, r["side"]) for r in rec}
    assert got == {
        (921, "new"),
        (920, "old"),
        (922, "new"),
        (923, "old"),
    }
    # 入线基准方向：每栏取该侧**最低** RA（升序首位，NB play_result[-1] 语义）
    from nonebot_plugin_awmc_helper.core.calc import compute_rating

    expected_new_gain = compute_rating(13.0, 99.0) - 220  # 新版本侧最低 RA=911 的 220
    assert by_id(921, rec)["gain"] == expected_new_gain
    expected_old_gain = compute_rating(13.0, 99.0) - 200  # 旧版本侧最低 RA=900 的 200
    assert by_id(923, rec)["gain"] == expected_old_gain


def test_rise_recommend_default_latest_follows_library():
    """latest_version_value 缺省跟随 maimai_py current_version（硬编码会过期）。"""
    import inspect

    from maimai_py import current_version

    from nonebot_plugin_awmc_helper.core.calc import rise_recommend

    sig = inspect.signature(rise_recommend)
    assert sig.parameters["latest_version_value"].default is None
    assert current_version.value > 25000  # CiRCLE 时代：默认值不能停在 PRiSM
