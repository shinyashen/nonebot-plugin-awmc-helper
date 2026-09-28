"""core/render/score.py DrawScore 行卡渲染冒烟：推分双栏（draw_rise）、
等级进度卡（draw_plan/draw_category）。"""

from mocks import requires_assets


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
            title="チルノのパーフェクトさんすう教室",
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
            title="チルノのパーフェクトさんすう教室",
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
