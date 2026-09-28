"""core/render 牌子渲染：plate_progress 进度总览卡（R7）与 plate_table_draw
各牌种达标判定边界。"""

import pytest
from mocks import requires_assets


@pytest.fixture
async def songs(tmp_path):
    from mocks import seed_service

    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.core.songs import song_service

    store.set_db_file(tmp_path / "awmc.db")
    await store.init_db()
    await seed_service(song_service)
    yield
    song_service._ready.clear()
    store.set_db_file(None)


@requires_assets
def test_plate_progress_card_smoke():
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


@pytest.mark.asyncio
async def test_plate_qualified_kinds(songs):
    """各牌种达标判定边界：舞舞须 FSD/FSDp（曾把方向写反漏 FSDp、误纳 Sync/FS/FSP）。"""
    from maimai_py import FCType, FSType, RateType, SongType, LevelIndex, ScoreExtend

    from nonebot_plugin_awmc_helper.core.render.plate_table_draw import _qualified

    def score(fs=None, fc=None, ach=100.0):
        return ScoreExtend(
            id=1,
            level="13+",
            level_index=LevelIndex.MASTER,
            achievements=ach,
            fc=fc,
            fs=fs,
            dx_score=None,
            dx_rating=250.0,
            play_count=1,
            play_time=None,
            rate=RateType.SSSP,
            type=SongType.STANDARD,
            title="t",
            level_value=13.0,
            level_dx_score=0,
            dx_star=None,
            version=15000,
        )

    # 舞舞：仅 FSD/FSDp 达标
    assert _qualified("舞舞", score(fs=FSType.FSD))
    assert _qualified("舞舞", score(fs=FSType.FSDP))
    for fs in (FSType.SYNC, FSType.FS, FSType.FSP):
        assert not _qualified("舞舞", score(fs=fs))
    assert not _qualified("舞舞", score(fs=None))
    # 极/神：枚举值越小越强
    assert _qualified("极", score(fc=FCType.FC))
    assert _qualified("极", score(fc=FCType.APP))
    assert not _qualified("极", score())
    assert _qualified("神", score(fc=FCType.AP))
    assert _qualified("神", score(fc=FCType.APP))
    assert not _qualified("神", score(fc=FCType.FC))
    # 者/将：达成率阈值
    assert _qualified("者", score(ach=80.0))
    assert not _qualified("者", score(ach=79.9))
    assert _qualified("将", score(ach=100.0))
    assert not _qualified("将", score(ach=99.9))
    assert not _qualified("将", None)
