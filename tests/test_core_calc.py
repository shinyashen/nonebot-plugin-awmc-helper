"""core/calc 纯函数单测：B50 拆分（build_bests）、分数线（score_line）、
RA（compute_rating）与推分推荐（rise_recommend）。

导入放函数内：顶层 import 会在 nonebug 初始化前触发插件包加载（见 conftest）；
maimai_py 本体是纯库，可顶层导入。
"""

import dataclasses

import pytest
from maimai_py import Score, RateType, SongType, LevelIndex, ScoreExtend


def _score(
    song_id: int,
    dx_rating: int = 100,
    version: int | None = 22000,
    play_count: int | None = None,
) -> ScoreExtend:
    base = Score(
        id=song_id,
        level="13",
        level_index=LevelIndex.MASTER,
        achievements=100.0,
        fc=None,
        fs=None,
        dx_score=2000,
        dx_rating=dx_rating,
        play_count=play_count,
        play_time=None,
        rate=RateType.SSS,
        type=SongType.DX,
    )
    return ScoreExtend(
        **dataclasses.asdict(base),
        title=f"s{song_id}",
        level_value=13.0,
        level_dx_score=3000,
        dx_star=None,
        version=version,  # type: ignore[arg-type]
    )


def test_split_by_version_and_sums():
    """旧版本入 b35 侧、新版本（≥ 下界）入 b15 侧，rating = 所列成绩 RA 之和。"""
    from maimai_py import current_version

    from nonebot_plugin_awmc_helper.core.calc import build_bests

    current = current_version.value
    bests = build_bests(
        [
            _score(1, dx_rating=200, version=current - 1),
            _score(2, dx_rating=100, version=current),
            _score(3, dx_rating=50, version=current + 1),
        ],
        key=lambda s: s.dx_rating or 0,
    )
    assert [s.id for s in bests.scores_b35] == [1]
    assert [s.id for s in bests.scores_b15] == [2, 3]
    assert bests.rating_b35 == 200
    assert bests.rating_b15 == 150
    assert bests.rating == 350


def test_custom_key_orders_each_side():
    """pc50 用法：按 play_count 降序（RA 只作平手次序），拆分不变。"""
    from maimai_py import current_version

    from nonebot_plugin_awmc_helper.core.calc import build_bests

    current = current_version.value
    bests = build_bests(
        [
            _score(1, dx_rating=500, version=current - 1, play_count=9),
            _score(2, dx_rating=400, version=current - 2, play_count=7),
            _score(3, dx_rating=300, version=current, play_count=8),
            _score(4, dx_rating=350, version=current, play_count=8),
        ],
        key=lambda s: (s.play_count or 0, s.dx_rating or 0),
    )
    assert [s.id for s in bests.scores_b35] == [1, 2]
    assert [s.id for s in bests.scores_b15] == [4, 3]  # 平手 8 次，RA 高者在前


def test_cap_35_and_15():
    """两侧各自截满：旧 35 / 新 15，超出不挤占另一侧。"""
    from maimai_py import current_version

    from nonebot_plugin_awmc_helper.core.calc import build_bests

    current = current_version.value
    scores = [_score(i, dx_rating=i, version=current - 1) for i in range(40)]
    scores += [_score(1000 + i, dx_rating=i, version=current) for i in range(20)]
    bests = build_bests(scores, key=lambda s: s.dx_rating or 0)
    assert len(bests.scores_b35) == 35
    assert len(bests.scores_b15) == 15
    # 各侧取排序键最高的前 N 条
    assert bests.scores_b35[0].id == 39
    assert bests.scores_b15[0].id == 1019


def test_latest_version_override_boundary():
    """版本码恰为下界算「新版本侧」（≥ 语义）；日服传 current_version_jp 生效。"""
    from nonebot_plugin_awmc_helper.core.calc import build_bests

    bests = build_bests(
        [_score(1, version=23000), _score(2, version=22999)],
        key=lambda s: s.dx_rating or 0,
        latest_version_value=23000,
    )
    assert [s.id for s in bests.scores_b35] == [2]
    assert [s.id for s in bests.scores_b15] == [1]


def test_none_version_counts_as_old():
    """version 缺失按最旧处理（落 b35 侧），不抛异常。"""
    from nonebot_plugin_awmc_helper.core.calc import build_bests

    bests = build_bests(
        [_score(1, version=None), _score(2, version=22000)],
        key=lambda s: s.dx_rating or 0,
        latest_version_value=23000,
    )
    assert [s.id for s in bests.scores_b35] == [1, 2]
    assert bests.scores_b15 == []


def test_bests_empty_input():
    """空输入得空 PlayerBests（rating 全 0），渲染层可安全消费。"""
    from nonebot_plugin_awmc_helper.core.calc import build_bests

    bests = build_bests([], key=lambda s: s.dx_rating or 0)
    assert bests.rating == 0
    assert bests.scores_b35 == []
    assert bests.scores_b15 == []


# ---------------------------------------------------------------------------
# score_line / compute_rating / rise_recommend
# ---------------------------------------------------------------------------


def test_score_line_formula():
    """分数线公式（2026-10-02 专栏口径重写）：预算与原版 tap_great 同值。

    样例（构造物量）：total=475000、bonus_total=2000；每 1 额外分折算
    per_bonus = 475000/(2000×10000) = 0.002375 个等效 GREAT TAP。
    七档期望值 = (2500-基础分)/100 + (100-额外分)×per_bonus：
    50落 0.585625 / 100落 1.17125 / G-1 5.855 / G-2 10.855 / G-3 13.355 /
    GOOD 15.8325 / MISS 24.75。
    """
    from mocks import make_diff

    from nonebot_plugin_awmc_helper.core.calc import score_line

    diff = make_diff(
        tap_num=700, hold_num=100, slide_num=100, touch_num=100, break_num=20
    )
    result = score_line(diff, 100)
    total = 700 * 500 + 100 * 1000 + 100 * 1500 + 100 * 500 + 20 * 2500
    assert result is not None
    assert result["total_basic"] == total
    assert result["total_bonus"] == 20 * 100
    assert result["budget"] == total * 1 / 10000  # 与原版 tap_great 同值
    assert result["breaks"] == 20
    per_bonus = total / (2000 * 10000)
    rows = dict(result["break_rows"])
    assert rows["50落"] == (2500 - 2500) / 100 + (100 - 75) * per_bonus
    assert rows["100落"] == (2500 - 2500) / 100 + (100 - 50) * per_bonus
    assert rows["G-1"] == (2500 - 2000) / 100 + (100 - 40) * per_bonus
    assert rows["G-2"] == (2500 - 1500) / 100 + (100 - 40) * per_bonus
    assert rows["G-3"] == (2500 - 1250) / 100 + (100 - 40) * per_bonus
    assert rows["GOOD"] == (2500 - 1000) / 100 + (100 - 30) * per_bonus
    assert rows["MISS"] == (2500 - 0) / 100 + (100 - 0) * per_bonus
    # 单调递增（50落 < 100落 < G-1 < G-2 < G-3 < GOOD < MISS）
    values = [v for _, v in result["break_rows"]]
    assert values == sorted(values)
    # 可加性：等效数折算的达成率损失 = 基础/额外两通道直算值（专栏口径）
    g3 = rows["G-3"]
    direct = 1250 * 100 / total + 60 * 1 / 2000
    assert g3 * 10000 / total == pytest.approx(direct)
    # 每等效 GREAT TAP 的达成率损失（分数线卡标签口径，2026-10-05）
    assert result["per_great"] == pytest.approx(10000 / total)

    assert score_line(diff, 101.5) is None  # 非法线
    assert score_line(diff, -1) is None


def test_chart_loss_facts():
    """谱面容错事实（寸理论值段/锁血判定共用，万分位）：每 GREAT TAP 与
    每 100落 的达成率损失；与 score_line 的 100落 等效数自洽。"""
    from mocks import make_diff

    from nonebot_plugin_awmc_helper.core.calc import score_line, chart_loss_facts

    diff = make_diff(
        tap_num=700, hold_num=100, slide_num=100, touch_num=100, break_num=20
    )
    great, drop100 = chart_loss_facts(diff)
    total = 700 * 500 + 100 * 1000 + 100 * 1500 + 100 * 500 + 20 * 2500
    assert great == pytest.approx(1e8 / total)
    assert drop100 == pytest.approx(5000 / 20)
    rows = dict(score_line(diff, 100)["break_rows"])
    assert rows["100落"] * great == pytest.approx(drop100)


def test_score_line_utage_buddy_cap():
    """buddy 宴谱上限 202（200 基础 + 2 额外）：线可到 202，预算按 202-线。"""
    from mocks import make_utage

    from nonebot_plugin_awmc_helper.core.calc import score_line, achievement_cap

    utage = make_utage(is_buddy=True)
    assert achievement_cap(utage) == 202
    total = 58 * 500 + 217 * 1000 + 27 * 1500 + 0 * 500 + 7 * 2500
    result = score_line(utage, 101.5)  # 普通谱非法线，buddy 合法
    assert result is not None
    assert result["budget"] == (202 - 101.5) * total / 10000
    assert result["cap"] == 202
    assert result["buddy"] is True
    assert score_line(utage, 202) is None  # 满线无容错
    assert score_line(utage, 202.5) is None  # 超上限
    # 非 buddy 宴谱口径同普通谱（上限 101）
    solo = make_utage(is_buddy=False)
    assert achievement_cap(solo) == 101
    assert score_line(solo, 101.5) is None


def test_rate_type_of_utage_buddy_double_threshold():
    """buddy 宴谱评级阈值 ×2（SSS+=201/SSS=200）：按线一半查普通档位。"""
    from mocks import make_utage
    from maimai_py import RateType

    from nonebot_plugin_awmc_helper.core.calc import rate_type_of

    buddy = make_utage(is_buddy=True)
    solo = make_utage(is_buddy=False)
    assert rate_type_of(buddy, 201) is RateType.SSSP
    assert rate_type_of(buddy, 200) is RateType.SSS
    assert rate_type_of(buddy, 199) is RateType.SSP
    assert rate_type_of(buddy, 100.5) is not RateType.SSSP  # 普通口径会误判
    assert rate_type_of(solo, 100.5) is RateType.SSSP  # 非 buddy 照常


def test_compute_rating_consistent_with_library():
    """RA 计算与 maimai-py ScoreCoefficient 一致。"""
    from maimai_py.utils import ScoreCoefficient

    from nonebot_plugin_awmc_helper.core.calc import compute_rating

    assert compute_rating(13.5, 100.5) == ScoreCoefficient(100.5).ra(13.5)
    assert compute_rating(13.5, 99.0) == ScoreCoefficient(99.0).ra(13.5)


def test_level_value_match_boundaries():
    """定数等值匹配（十分位 round 口径，S-9）：边界与浮点表示误差行为锁定。

    - 13.5 精确相等命中；
    - 13.55 → round(135.5…) = 136 ≠ 135，不与 13.5 混；
    - 13.49 → round(134.9) = 135，十分位口径下与 13.5 同值命中；
    - 14.5（二进制可精确表示）与其浮点近邻 14.499999999999998 一律命中
      ——round 抹平表示误差，这正是 S-9 防「14.5定数」漏配的动机。
    """
    from nonebot_plugin_awmc_helper.core.calc import level_value_match

    assert level_value_match(13.5, 13.5)
    assert not level_value_match(13.55, 13.5)  # 136 vs 135
    assert level_value_match(13.49, 13.5)  # 135 vs 135（十分位舍入同值）
    assert level_value_match(14.5, 14.5)
    assert level_value_match(14.5, 14.5 - 1e-15)  # 浮点尾差不翻车
    assert not level_value_match(13.4, 13.5)
    assert not level_value_match(14.0, 14.5)


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
                id=199,
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
                type=SongType.STANDARD,
            ),
            title="チルノのパーフェクトさんすう教室",
            level_value=13.3,
            level_dx_score=2100,
            dx_star=4,
            version=12000,  # 199 SD Master 真实版本（旧版本侧）
        )
    ]
    candidates = [s for s in sample_songs() if s.id == 624]  # SD Master 13.4 未入线
    rec = rise_recommend(b50, candidates, target=10, latest_version_value=25500)
    assert rec
    top = rec[0]
    assert top["song"].id == 624
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
                id=199,
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
                type=SongType.STANDARD,
            ),
            title="チルノのパーフェクトさんすう教室",
            level_value=13.3,
            level_dx_score=2100,
            dx_star=4,
            version=12000,  # 199 SD Master 真实版本（旧版本侧）
        )
    ]
    candidates = [s for s in sample_songs() if s.id in (199, 624)]
    # level="13" 收敛谱面：199 只剩 SD/DX Master、624 只剩 SD Master，
    # 且 199 的 DX Master 属新版本侧（无新版本 B50 基准）不会进推荐
    rec = rise_recommend(
        b50, candidates, level="13", target=10, latest_version_value=25500
    )
    by_chart = {(r["song"].id, r["diff"].type): r for r in rec}
    # 624 未游玩：旧成绩 0（NB RiseResult 默认值语义）
    assert by_chart[(624, SongType.STANDARD)]["old_achievements"] == 0.0
    assert by_chart[(624, SongType.STANDARD)]["old_ra"] == 0
    # 199 已游玩未入线：旧成绩取 B50 成绩
    assert by_chart[(199, SongType.STANDARD)]["old_achievements"] == 100.0
    assert by_chart[(199, SongType.STANDARD)]["old_ra"] == 200


def test_rise_recommend_version_filter():
    """推分双栏按版本划分（用户口径，NB 旧/新版本谱面推荐）：旧版本 =
    当前版本以前全部谱面（b35 侧）、新版本 = 当前版本谱面（b15 侧），
    SD/DX 均可入任一栏，side 由谱面版本决定而非类型。

    版本组合（24000/25000）为断言需要而构造，真实样例曲库无此组合，
    故曲目/成绩均用（构造）占位命名。
    """
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
            title="（构造）oldSD",
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
            title="（构造）oldDX",
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
            title="（构造）newDX",
            level_value=13.0,
            level_dx_score=2100,
            dx_star=4,
            version=25000,
        ),
    ]

    def cand(song_id, type_, version):
        return make_song(
            song_id,
            f"（构造）s{song_id}",  # 占位曲：版本组合为断言构造
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

    rec = rise_recommend(b50, candidates, target=1, latest_version_value=25000)
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


def test_rise_candidates_and_sssp_ignore():
    """推分候选集构建（P-2）：ds 区间/标级/类型过滤与 SSS+ 排除单一来源。"""
    from mocks import make_diff, make_song

    from nonebot_plugin_awmc_helper.core.calc import (
        rise_candidates,
        sssp_ignore_ids,
    )

    scores = [
        dataclasses.replace(_score(199), achievements=100.5),
        dataclasses.replace(_score(21), achievements=99.0),
    ]
    assert sssp_ignore_ids(scores) == {199}

    songs = [
        make_song(
            199,
            "A",
            diffs=[
                make_diff(type=SongType.STANDARD, level="13+", level_value=13.7),
                make_diff(type=SongType.DX, level="13", level_value=13.0),
            ],
        ),
        make_song(
            21,
            "B",
            diffs=[make_diff(type=SongType.DX, level="14", level_value=14.0)],
        ),
        make_song(
            835,
            "C",
            diffs=[make_diff(type=SongType.STANDARD, level="13", level_value=13.2)],
        ),
    ]

    # ds 区间 + 类型：随机推分口径（199 已 SSS+ 排除）
    got = rise_candidates(
        songs, ds_range=(13.0, 14.0), song_type=SongType.DX, scores=scores
    )
    assert [s.id for s in got] == [21]
    # 仅 ds 区间：推分推荐 ds 模式（无类型过滤、无排除；21 的 DX 14.0 也在
    # 闭区间 [13, 14] 内）
    got = rise_candidates(songs, ds_range=(13.0, 14.0))
    assert [s.id for s in got] == [199, 21, 835]
    # 标级模式：推分推荐 level 模式
    got = rise_candidates(songs, level="13")
    assert [s.id for s in got] == [199, 835]
