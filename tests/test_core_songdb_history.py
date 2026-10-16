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
