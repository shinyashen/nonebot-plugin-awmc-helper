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
            if d.type != SongType.UTAGE and d.level == "13":
                entries.append((song, d))
    assert entries

    total = await table_template.generate_rating_template("13", song_service)
    assert total > 0
    assert (table_template.rating_table_dir() / "13.png").exists()

    # 无成绩 → 空盖章但统计头正常；出非空 PNG
    png = draw_rating_table("13", None, [], entries)
    assert png is not None
    assert png.startswith(b"\x89PNG\r\n")


@requires_assets
@pytest.mark.asyncio
async def test_rating_grid_per_type_id(songs, monkeypatch):
    """定数表/等级完成表底图网格 id 必须为谱面级展示 id（DX=根 id+10000）。

    素材与毛玻璃卡打桩后用 text 间谍捕获绘制串：199 的 DX 谱应画 10199，
    根 id「199」不得出现；lv7-14 网格与 lv15 大图两条路径都查。
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

    song = await song_service.by_id(199)
    assert song is not None
    dx = song.get_difficulty(SongType.DX, LevelIndex.MASTER)
    assert dx is not None
    assert chart_display_id(song, dx) == 10199

    table_template._rating_grid([(song, dx)])
    assert "10199" in drawn
    assert "199" not in drawn

    drawn.clear()
    lv15 = make_diff(
        type=SongType.DX,
        level_index=LevelIndex.MASTER,
        level="15",
        level_value=15.0,
    )
    table_template._rating_grid_15([(song, lv15)])
    assert "10199" in drawn
    assert "199" not in drawn


@pytest.mark.asyncio
async def test_rating_template_draw_offloads_loop(songs, monkeypatch, tmp_path):
    """定数表底图绘制段让出事件循环（L-6）：绘制跑在工作线程，
    绘制进行中事件循环仍可调度其它协程（同步实现会卡死循环调度）。"""
    import time
    import asyncio
    import threading

    from PIL import Image as PILImage

    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.render import table_template

    loop_thread = threading.get_ident()
    draw_threads: list[int] = []
    draw_done = threading.Event()

    def fake_grid(entries):
        draw_threads.append(threading.get_ident())
        time.sleep(0.05)  # 模拟数百张封面加载/缩放的绘制耗时
        draw_done.set()
        return PILImage.new("RGBA", (10, 10))

    monkeypatch.setattr(table_template, "_rating_grid", fake_grid)
    monkeypatch.setattr(
        table_template, "rating_table_dir", lambda: tmp_path / "rating_table"
    )

    ticks_during_draw = 0

    async def ticker():
        nonlocal ticks_during_draw
        while not draw_threads:  # noqa: ASYNC110 — 等绘制开始，让出即测试目的
            await asyncio.sleep(0)
        while not draw_done.is_set():  # 绘制进行中，循环应能继续调度本协程
            ticks_during_draw += 1
            await asyncio.sleep(0)

    await asyncio.gather(
        table_template.generate_rating_template("13", song_service),
        ticker(),
    )

    assert draw_threads, "绘制入口未被调用"
    assert all(t != loop_thread for t in draw_threads), "绘制跑在事件循环线程内"
    assert ticks_during_draw > 0, "绘制期间事件循环未调度其它协程"


@requires_assets
@pytest.mark.asyncio
async def test_level_header_suffix_centered(monkeypatch):
    """表头 QoL（2026-10-01）：表型说明（定数表/完成表）随表头整体水平居中；
    「Level. xx」整段日文字体（RODIN），条件串与后缀统一中文字体（HAN）。
    font() 进程级 lru_cache 同参同对象，按 is 断言字体口径。"""
    from PIL import Image as PILImage
    from PIL import ImageDraw

    from nonebot_plugin_awmc_helper.core.render import table_template
    from nonebot_plugin_awmc_helper.core.render.fonts import (
        FONT_HAN,
        FONT_RODIN,
        font,
    )

    dr = ImageDraw.Draw(PILImage.new("RGBA", (1400, 400)))
    drawn: list[tuple[tuple, str, object]] = []
    orig_text = ImageDraw.ImageDraw.text

    def spy(self, xy, text, *a, **kw):
        drawn.append((xy, str(text), kw.get("font")))
        return orig_text(self, xy, text, *a, **kw)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", spy)

    # 单等级（13定数表）：Level. 前缀 + 等级（RODIN）+ 表型说明（HAN）
    table_template.draw_level_header(dr, "13", 220, suffix="定数表")
    assert [t for _, t, _ in drawn] == ["Level.", "13", " 定数表"]
    assert drawn[0][2] is font(70, FONT_RODIN)
    assert drawn[1][2] is font(100, FONT_RODIN)
    assert drawn[2][2] is font(70, FONT_HAN)
    total = sum(dr.textlength(t, font=f) for _, t, f in drawn)
    assert abs(drawn[0][0][0] - (700 - total / 2)) < 1.0  # 块居中于画布中轴

    # 条件版（东方定数表）：无 Level. 前缀，条件串与后缀全 HAN
    drawn.clear()
    table_template.draw_level_header(dr, "东方", 220, prefix=None, suffix="定数表")
    assert [t for _, t, _ in drawn] == ["东方", " 定数表"]
    assert drawn[0][2] is font(100, FONT_HAN)
    assert drawn[1][2] is font(70, FONT_HAN)
