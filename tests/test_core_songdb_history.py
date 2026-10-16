"""core/songdb 定数历史辅助：运行时读取（chart_history）与 DX 裁剪
（dx_history_points）——linfo 定数历史指令的数据出口（2026-10-16 新增）。
"""

from pathlib import Path

import pytest


@pytest.fixture
async def tmp_db(tmp_path: Path):
    from nonebot_plugin_awmc_helper.core import store

    store.set_db_file(tmp_path / "awmc.db")
    await store.init_db()
    yield store
    store.set_db_file(None)


def test_dx_history_points_old_frame_baseline():
    """旧框末值折 DX 基线首节点；DX 世代变化点原样保留、版本升序。"""
    from nonebot_plugin_awmc_helper.core.songdb import dx_history_points

    history = [
        (10000, 10.0),
        (12000, 11.2),
        (11000, 10.8),  # 乱序入参应被容忍（按版本取末值）
        (20500, 12.2),
        (25000, 12.6),
    ]
    # 旧框（<20000）末值 = 11.2（GreeN 时代调整后未再变）
    assert dx_history_points(history) == [
        (20000, 11.2),
        (20500, 12.2),
        (25000, 12.6),
    ]


def test_dx_history_points_dx_native_and_flat():
    """DX 世代登场曲无旧框点：首点即登场值；单点 = DX 世代未调整。"""
    from nonebot_plugin_awmc_helper.core.songdb import dx_history_points

    assert dx_history_points([(24500, 13.0)]) == [(24500, 13.0)]
    # 空历史（无 levels 行）原样返回空表
    assert dx_history_points([]) == []


@pytest.mark.asyncio
async def test_chart_history_reads_store(tmp_db):
    """chart_history：按版本升序返回该谱面全部变化点；空谱面返回空表。"""
    from nonebot_plugin_awmc_helper.core.songdb import chart_history

    async with tmp_db.session() as db:
        for ver, val in [(20000, 11.7), (20500, 12.2), (24500, 13.5)]:
            db.add(
                tmp_db.SongChartLevel(
                    song_id=199, kind="dx", level_id=3, version=ver, level_value=val
                )
            )
        await db.commit()

    assert await chart_history(199, "dx", 3) == [
        (20000, 11.7),
        (20500, 12.2),
        (24500, 13.5),
    ]
    assert await chart_history(199, "dx", 4) == []
    # 宴谱不建 levels 行：恒空表
    assert await chart_history(100199, "utage", 0) == []


# ---------------- 国服先行基线校准（with_cn_baseline / group_cn_version）


def test_with_cn_baseline_cn_first_shifts():
    """国服先行（如零号车辆）：首节点提前到国服上线版本，值取 §5.3
    「区间无值取首值」fallback（= 日服轴首值）。cn_debut 用国服自有码
    25007（PRiSM 代国服本地更新后缀）验证轴对齐。"""
    from nonebot_plugin_awmc_helper.core.songdb import with_cn_baseline

    points = [(26000, 13.6), (27000, 13.9)]
    assert with_cn_baseline(points, 25007) == [(25000, 13.6), (27000, 13.9)]
    assert with_cn_baseline(points, 25000) == [(25000, 13.6), (27000, 13.9)]


def test_with_cn_baseline_aligns_cn_suffix_codes():
    """国服后缀码统一对齐到枚举轴（22007→UNiVERSE 22000 / 18508→MURASAKi+
    18500）；对齐后晚于日服首点的国服版本不触发前移。"""
    from nonebot_plugin_awmc_helper.core.songdb import with_cn_baseline

    points = [(21000, 11.0), (22500, 12.0)]
    assert with_cn_baseline(points, 22007) is points
    old_frame = [(20000, 11.0), (20500, 12.0)]
    assert with_cn_baseline(old_frame, 18508) is old_frame


def test_with_cn_baseline_jp_first_untouched():
    """日服先行曲（cn_debut 晚于首点）：日服轴历史不动。"""
    from nonebot_plugin_awmc_helper.core.songdb import with_cn_baseline

    points = [(21000, 11.0), (22500, 12.0)]
    assert with_cn_baseline(points, 23000) is points
    assert with_cn_baseline(points, 21000) is points  # 同版本亦不动


def test_with_cn_baseline_clamps_to_dx_begin():
    """旧框上线的曲：cn_debut 钳到 DX 起点，基线恒为 dx_begin 不前移。"""
    from nonebot_plugin_awmc_helper.core.songdb import (
        with_cn_baseline,
        dx_history_points,
    )

    points = dx_history_points([(15000, 11.0), (20500, 12.0)])
    assert points == [(20000, 11.0), (20500, 12.0)]
    assert with_cn_baseline(points, 15000) is points
    assert with_cn_baseline(points, None) is points


@pytest.mark.asyncio
async def test_group_cn_version_reads_store(tmp_db):
    """group_cn_version：读组级 version_cn；NULL/无组返回 None。"""
    from nonebot_plugin_awmc_helper.core.songdb import group_cn_version

    async with tmp_db.session() as db:
        db.add(
            tmp_db.SongSheetGroup(
                song_id=11946, kind="dx", version=26000, version_cn=25000
            )
        )
        db.add(tmp_db.SongSheetGroup(song_id=11946, kind="sd", version=26000))
        await db.commit()

    assert await group_cn_version(11946, "dx") == 25000
    assert await group_cn_version(11946, "sd") is None  # version_cn NULL
    assert await group_cn_version(999999, "dx") is None  # 无组
