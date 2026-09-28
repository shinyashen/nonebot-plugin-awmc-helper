"""core/calc 纯函数单测：B50 拆分（build_bests）、分数线（score_line）、
RA（compute_rating）与推分推荐（rise_recommend）。

导入放函数内：顶层 import 会在 nonebug 初始化前触发插件包加载（见 conftest）；
maimai_py 本体是纯库，可顶层导入。
"""

import dataclasses

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
