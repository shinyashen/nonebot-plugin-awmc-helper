"""core/calc.build_bests 纯函数单测：版本拆分、自定义排序键、截断与 RA 求和。

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
