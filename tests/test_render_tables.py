"""core/render 定数表底图管线：table_template 底图生成、rating_table 盖章、
网格必须画谱面级展示 id（DX=根 id+10000）。"""

from pathlib import Path

import pytest
from mocks import requires_assets

from maimai_py import SongType, LevelIndex


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


@pytest.mark.asyncio
@requires_assets
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


@requires_assets
@pytest.mark.asyncio
async def test_rating_grid_per_type_id(songs, monkeypatch):
    """定数表/等级完成表底图网格 id 必须为谱面级展示 id（DX=根 id+10000）。

    素材与毛玻璃卡打桩后用 text 间谍捕获绘制串：DX 曲 500 应画 10500，
    根 id「500」不得出现；lv7-14 网格与 lv15 大图两条路径都查。
    """
    from PIL import Image as PILImage
    from PIL import ImageDraw
    from mocks import make_diff

    from nonebot_plugin_awmc_helper.constants import chart_display_id
    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.render import table_template

    class _StubAssets:
        @staticmethod
        def pic(name, theme="prism_plus"):
            return PILImage.new("RGBA", (200, 200))

        @staticmethod
        def cover(song_id):
            return PILImage.new("RGBA", (400, 400))

    monkeypatch.setattr(table_template, "assets", _StubAssets())
    monkeypatch.setattr(table_template, "generate_frosted_card", lambda bg, box: bg)

    drawn: list[str] = []
    orig_text = ImageDraw.ImageDraw.text

    def spy(self, xy, text, *a, **kw):
        drawn.append(str(text))
        return orig_text(self, xy, text, *a, **kw)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", spy)

    song = await song_service.by_id(500)
    assert song is not None
    dx = song.get_difficulty(SongType.DX, LevelIndex.MASTER)
    assert dx is not None
    assert chart_display_id(song, dx) == 10500

    table_template._rating_grid([(song, dx)])
    assert "10500" in drawn
    assert "500" not in drawn

    drawn.clear()
    lv15 = make_diff(
        type=SongType.DX,
        level_index=LevelIndex.MASTER,
        level="15",
        level_value=15.0,
    )
    table_template._rating_grid_15([(song, lv15)])
    assert "10500" in drawn
    assert "500" not in drawn
