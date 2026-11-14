"""core/maxbest 理论最大 best50/40 测试：满分行构造、视图路由与拆分。

曲库按用例需要现造（mocks.make_song/make_diff，版本码覆盖 35/15 与 25/15
两侧），RA 断言与 ScoreCoefficient 现算值对照并抽查手算精确值。导入放函数
内：顶层 import 会在 nonebug 初始化前触发插件包加载（见 conftest）。
"""

import pytest


@pytest.fixture
async def db(tmp_path):
    from nonebot_plugin_awmc_helper.core import store

    store.set_db_file(tmp_path / "awmc.db")
    await store.init_db()
    yield
    store.set_db_file(None)


@pytest.fixture
async def lib(db):
    """自定义曲库装载器：``await lib(songs)`` 注入 song_service（teardown 清态）。"""
    from nonebot_plugin_awmc_helper.core.songs import song_service

    async def _load(songs):
        from mocks import seed_service

        return await seed_service(song_service, songs)

    yield _load
    song_service._ready.clear()


def _song(song_id: int, ds: float, *, version: int = 21000, **diff_kwargs):
    """单谱面曲（max50 用例最小造数）：一张谱面一首歌。"""
    from mocks import make_diff, make_song
    from maimai_py import SongType, LevelIndex

    return make_song(
        song_id,
        f"曲{song_id}",
        diffs=[
            make_diff(
                type=SongType.DX,
                level_index=LevelIndex.MASTER,
                level="13",
                level_value=ds,
                version=version,
                **diff_kwargs,
            )
        ],
    )


@pytest.mark.asyncio
async def test_max_scores_full_badges():
    """满分行构造：SSS+ 101.0000% / AP+ / 舞舞 / DX 分满 → 星满 / RA 理论最大。"""
    from maimai_py import FCType, FSType, RateType, SongType, LevelIndex
    from maimai_py.utils import ScoreCoefficient

    from nonebot_plugin_awmc_helper.core.calc import max_scores

    rows = max_scores([_song(1, 13.6, version=21000)])
    assert len(rows) == 1
    row = rows[0]
    assert row.achievements == 101.0
    assert row.rate is RateType.SSSP
    assert row.fc is FCType.APP
    assert row.fs is FSType.FSDP
    # DX 分满 = 物量合计 × 3（maimai_py level_dx_score 单一事实源）
    assert row.dx_score == row.level_dx_score == (457 + 43 + 107 + 49 + 37) * 3
    assert row.dx_star == 5
    # 理论 RA：达成率封顶 100.5（公式内部），ds 13.6 → int(22.4×13.6×1.005)=306
    assert row.dx_rating == ScoreCoefficient(101.0).ra(13.6) == 306
    assert row.type is SongType.DX
    assert row.level_index is LevelIndex.MASTER
    assert row.version == 21000
    assert row.level_value == 13.6
    assert row.title == "曲1"


@pytest.mark.asyncio
async def test_max_scores_excludes_utage():
    """宴谱不参与 rating：宿主曲普通谱入行、宴谱跳过，纯宴曲整曲无行。"""
    from mocks import make_diff, make_song, make_utage
    from maimai_py import SongType

    from nonebot_plugin_awmc_helper.core.calc import max_scores

    songs = [
        _song(1, 13.6),
        # 宿主曲带宴谱：普通谱入行，宴谱不入
        make_song(2, "曲2", diffs=[make_diff()], utage=[make_utage()]),
        # 纯宴曲：整曲无行
        make_song(3, "曲3", diffs=[], utage=[make_utage(diff_id=100003)]),
    ]
    rows = max_scores(songs)
    assert [r.id for r in rows] == [1, 2]
    assert all(r.type is not SongType.UTAGE for r in rows)


@pytest.mark.asyncio
async def test_max_scores_zero_notes_star_none():
    """物量缺失（dx_score=0）的谱面：DX 星回退 None（除零守卫，net_score 同款）。"""
    from nonebot_plugin_awmc_helper.core.calc import max_scores

    rows = max_scores(
        [_song(1, 13.6, tap_num=0, hold_num=0, slide_num=0, touch_num=0, break_num=0)]
    )
    assert rows[0].dx_score == 0
    assert rows[0].dx_star is None


@pytest.mark.asyncio
async def test_max_bests_split_and_caps(lib):
    """35/15 切分与封顶：旧侧取满 35、新侧（version ≥ 现行版本码）取满 15，
    RA 降序、合计 = 两侧之和；两次执行结果一致（稳定排序保证确定性）。"""
    from maimai_py import current_version

    from nonebot_plugin_awmc_helper.core.maxbest import max_bests

    cur = current_version.value
    songs = [_song(i, round(10.0 + i * 0.1, 1), version=21000) for i in range(40)]
    songs += [_song(1000 + i, round(10.0 + i * 0.1, 1), version=cur) for i in range(20)]
    await lib(songs)

    bests = await max_bests(None)
    assert len(bests.scores_b35) == 35
    assert len(bests.scores_b15) == 15
    assert all(s.version < cur for s in bests.scores_b35)
    assert all(s.version >= cur for s in bests.scores_b15)
    # RA 降序：旧侧首行 = 定数最高（13.9），新侧末行 = 入线定数（10.5）
    assert bests.scores_b35[0].level_value == 13.9
    assert bests.scores_b15[-1].level_value == 10.5
    assert bests.rating_b35 == sum(s.dx_rating for s in bests.scores_b35)
    assert bests.rating_b15 == sum(s.dx_rating for s in bests.scores_b15)
    assert bests.rating == bests.rating_b35 + bests.rating_b15

    again = await max_bests(None)
    assert [s.id for s in again.scores_b35 + again.scores_b15] == [
        s.id for s in bests.scores_b35 + bests.scores_b15
    ]


@pytest.mark.asyncio
async def test_max_bests_legacy_old_coef_25_15(lib):
    """legacy（b40max）：RA 改 FiNALE 旧系数满档（SSS+ 14.0）+ 恒拆 25/15。"""
    from nonebot_plugin_awmc_helper.core.calc import _old_ra, max_sort_key
    from nonebot_plugin_awmc_helper.core.maxbest import max_bests

    songs = [_song(i, 13.0, version=21000) for i in range(30)]
    songs += [_song(1000 + i, 13.0, version=25500) for i in range(5)]
    await lib(songs)

    bests = await max_bests(None, legacy=True)
    assert len(bests.scores_b35) == 25  # 旧侧封顶 25（b50max 为 35）
    assert len(bests.scores_b15) == 5
    # 旧系数精确值：13×14×1.005 = 182.91 → 182（test_old_ra_math 同款）
    assert all(s.dx_rating == _old_ra(13.0, 101.0) == 182 for s in bests.scores_b35)
    # 排序键四元组：RA / DX 分（满值 2079）/ 达成率 / 定数
    assert max_sort_key(bests.scores_b35[0]) == (182, 2079, 101.0, 13.0)


@pytest.mark.asyncio
async def test_max_bests_jp_view(lib, monkeypatch):
    """NET 绑定 → 日服视图：池取 jp_all、分界 current_version_jp（27000）。"""
    from maimai_py import current_version_jp

    from nonebot_plugin_awmc_helper.core.score import score_service
    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.store import UserBinding
    from nonebot_plugin_awmc_helper.core.maxbest import max_bests

    cur_jp = current_version_jp.value
    songs = [_song(i, 13.0, version=cur_jp) for i in range(20)]
    songs += [_song(100 + i, 14.0, version=25000) for i in range(40)]
    await lib(songs)

    monkeypatch.setattr(score_service, "view_of", lambda service: "jp")

    async def fake_jp_all():
        return songs

    monkeypatch.setattr(song_service, "jp_all", fake_jp_all)
    binding = UserBinding(platform="qq", user_id="10001", service="net")
    bests = await max_bests(binding)
    assert len(bests.scores_b35) == 35
    assert len(bests.scores_b15) == 15
    assert all(s.version >= cur_jp for s in bests.scores_b15)
    assert all(s.version < cur_jp for s in bests.scores_b35)
