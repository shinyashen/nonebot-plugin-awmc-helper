"""完成表「模板预渲染 + 成绩实时叠加」（NB 方案，Q7）。

模板布局 1:1 移植 NB ``core/image/update_table.py::UpdateTable``：
- 定数表底图：PRiSM 三色渐变 + 毛玻璃卡 + ``.X`` 定数节标签 + 14 列描边封面
  网格（``border_basic..remaster`` 难度框）；lv15 为三列大图（版本横幅 +
  UNKNOWN 占位）；
- 牌子完成表底图：12 列网格（``border_table_base`` id 框），舞/霸按等级 13
  分两页且 Re:MASTER 曲用紫 id（``border_table_remaster``）。

用户查询时打开底图：定数表叠「Level.」大字（``rating_table_level_text``）、
完成表叠评级/FC/Sync 印章与统计（``rating_table`` 模块 / ``plate_table_draw``
模块），底图缺失时在内存中按同布局现算，不再落盘。
"""

import asyncio
from pathlib import Path
from collections.abc import Sequence

from PIL import Image
from nonebot import logger
from maimai_py import Song, SongType, SongDifficulty

from .fonts import FONT_NUM, FONT_RODIN, font
from .tools import (
    TITLE_BLUE,
    credit_text,
    image_to_bytes,
    generate_prism_bg,
    generate_frosted_card,
)
from .assets import assets
from ..plates import in_plate_scope, major_type_of_plate, plate_version_range
from .nb_chart import version_image
from ...constants import chart_display_id
from .table_layout import (
    LV15_COLS,
    PLATE_COLS,
    RATING_COLS,
    LV15_START_X,
    LV15_START_Y,
    LV15_COL_STEP,
    LV15_ROW_STEP,
    PLATE_START_X,
    PLATE_START_Y,
    RATING_START_X,
    RATING_START_Y,
    PLATE_GRID_STEP,
    RATING_GRID_STEP,
    RATING_GROUP_GAP,
    group_by_ds,
    slot_level_of,
)

FONT_BLUE = TITLE_BLUE
_LEVEL_INDEXES = ("basic", "advanced", "expert", "master", "remaster")
_DIFF_TEXT_COLOR = [
    (255, 255, 255, 255),
    (255, 255, 255, 255),
    (255, 255, 255, 255),
    (255, 255, 255, 255),
    (138, 0, 226, 255),
]


def rating_table_dir() -> Path:
    return assets.static_path() / "mai" / "rating_table"


def plate_table_dir() -> Path:
    return assets.static_path() / "mai" / "plate_table"


def _generate_bg(height: int, separator_height: int) -> Image.Image:
    """NB UpdateTable._generate_bg：三色渐变 + 装饰层 + 分割线。"""
    return generate_prism_bg(height, separator_height)


def _credit(im: Image.Image, height: int) -> None:
    from PIL import ImageDraw

    ImageDraw.Draw(im).text(
        (700, height - 75),
        credit_text(),
        font=font(30, FONT_RODIN),
        fill=FONT_BLUE,
        anchor="mm",
    )


def _rating_grid(
    entries: Sequence[tuple[Song, SongDifficulty]],
) -> Image.Image:
    """NB update_rating_table 布局（lv7–14）：毛玻璃卡 + 定数节封面网格。"""
    groups = group_by_ds(entries)

    current_y = RATING_START_Y
    for charts in groups.values():
        rows = (len(charts) - 1) // RATING_COLS + 1
        current_y += rows * RATING_GRID_STEP + RATING_GROUP_GAP
    height = current_y + 230

    im = generate_frosted_card(_generate_bg(height, 360), (50, 404, 1350, current_y))
    from PIL import ImageDraw

    dr = ImageDraw.Draw(im)

    _credit(im, height)

    start_y = RATING_START_Y
    for ds, charts in groups.items():
        # 节标签 = 定数小数部分（如 ".6"）
        dr.text(
            (70, start_y + 35),
            f".{ds.split('.')[-1]}",
            font=font(40, FONT_RODIN),
            fill=FONT_BLUE,
            anchor="lm",
            stroke_width=4,
            stroke_fill=(255, 255, 255, 255),
        )
        max_row = 0
        for num, (song, diff) in enumerate(charts):
            row, col = divmod(num, RATING_COLS)
            max_row = max(max_row, row)
            x = RATING_START_X + col * RATING_GRID_STEP
            y = start_y + row * RATING_GRID_STEP
            li = diff.level_index.value
            im.alpha_composite(assets.cover(song.id).resize((75, 75)), (x, y))
            im.alpha_composite(
                assets.pic(f"border_{_LEVEL_INDEXES[li]}.png"), (x - 5, y - 5)
            )
            dr.text(
                (x + 56, y + 4),
                str(chart_display_id(song, diff)),
                font=font(13, FONT_NUM),
                fill=_DIFF_TEXT_COLOR[li],
                anchor="mm",
            )
        start_y += (max_row + 1) * RATING_GRID_STEP + RATING_GROUP_GAP
    return im


def _rating_grid_15(
    entries: Sequence[tuple[Song, SongDifficulty]],
) -> Image.Image:
    """NB update_level_15_rating_table：lv15 三列大图（含 UNKNOWN 占位）。"""
    count = len(entries)
    lines = count // LV15_COLS + (1 if count % LV15_COLS else 0)
    height = 650 + lines * LV15_ROW_STEP

    im = _generate_bg(height, 360)
    from PIL import ImageDraw

    dr = ImageDraw.Draw(im)
    _credit(im, height)

    unknown = assets.cover(0).convert("RGBA").resize((330, 330))
    for i in range(lines * 3):
        row, col = divmod(i, LV15_COLS)
        x = LV15_START_X + col * LV15_COL_STEP
        y = LV15_START_Y + row * LV15_ROW_STEP
        im.alpha_composite(assets.pic("chart_white.png"), (x, y))
        if i < count:
            song, diff = entries[i]
            im.alpha_composite(
                assets.cover(song.id).resize((330, 330)), (x + 10, y + 10)
            )
            im.alpha_composite(
                assets.pic("DX.png" if diff.type == SongType.DX else "SD.png"),
                (x + 200, y + 345),
            )

            version_img = version_image(diff.version or song.version)
            if version_img is not None:
                banner = version_img.resize((332, 160))
                im.alpha_composite(banner, (x + 9, y - 80))
            dr.text(
                (x + 100, y + 370),
                str(chart_display_id(song, diff)),
                font=font(35, FONT_RODIN),
                fill=FONT_BLUE,
                anchor="mm",
            )
        else:
            im.alpha_composite(unknown, (x + 10, y + 10))
            im.alpha_composite(assets.pic("DX.png"), (x + 200, y + 345))
            dr.text(
                (x + 100, y + 370),
                "????",
                font=font(35, FONT_RODIN),
                fill=FONT_BLUE,
                anchor="mm",
            )
            dr.text(
                (x + 175, y + 280),
                "UNKNOWN",
                font=font(30, FONT_RODIN),
                fill=FONT_BLUE,
                anchor="mm",
                stroke_width=8,
                stroke_fill=(255, 255, 255, 255),
            )
    return im


def _plate_grid(
    entries: Sequence[tuple[Song, SongDifficulty]],
    major_type: SongType,
    *,
    remaster_entries: Sequence[tuple[Song, SongDifficulty]] | None = None,
    pages: int | None = None,
) -> Image.Image:
    """NB _draw_plate 布局：牌子完成表底图（12 列，ReM 曲紫 id）。"""
    # NB 语义：**一格一曲**（四槽完成小标与达成章由叠章层绘制在同一格），
    # 按 Master 槽等级分组（舞/霸的 ReM 曲用 ReM 槽等级），组内定数降序——
    # 分组/排序必须与 plate_table_draw.draw_plate_table 的叠章侧逐条一致
    master_pair: dict[int, tuple[Song, SongDifficulty]] = {}
    remaster_pair: dict[int, tuple[Song, SongDifficulty]] = {}
    for song, diff in entries:
        if diff.level_index.value == 3:
            master_pair.setdefault(song.id, (song, diff))
        elif diff.level_index.value == 4:
            remaster_pair.setdefault(song.id, (song, diff))
    grouped: dict[str, list[tuple[Song, SongDifficulty]]] = {}
    sort_ds: dict[int, float] = {}
    for song_id, pair in master_pair.items():
        re_pair = remaster_pair.get(song_id)
        level = (
            re_pair[1].level
            if re_pair is not None and remaster_entries
            else pair[1].level
        )
        grouped.setdefault(level, []).append(pair)
        # 组内排序键（Hoshino get_ds_sort_key 同款）：ReM 曲用 ReM 定数，
        # 其余用 Master 定数
        sort_ds[song_id] = (
            re_pair[1].level_value
            if re_pair is not None and remaster_entries
            else pair[1].level_value
        )
    order = sorted(
        grouped, key=lambda lv: (float(lv.rstrip("+")), lv.endswith("+")), reverse=True
    )
    groups = {k: grouped[k] for k in order}
    remaster_ids = {s.id for s, _d in (remaster_entries or [])}

    current_y = PLATE_START_Y
    for charts in groups.values():
        rows = (len(charts) - 1) // PLATE_COLS + 1
        current_y += rows * PLATE_GRID_STEP + RATING_GROUP_GAP
    height = current_y + 180

    im = generate_frosted_card(_generate_bg(height, 400), (50, 444, 1350, current_y))
    from PIL import ImageDraw

    dr = ImageDraw.Draw(im)
    if pages is not None:
        dr.text(
            (700, height - 140),
            f"Pages {pages + 1}/2",
            font=font(40, FONT_RODIN),
            fill=FONT_BLUE,
            anchor="mm",
        )
    _credit(im, height)

    start_y = PLATE_START_Y
    for level, charts in groups.items():
        charts.sort(key=lambda pair: sort_ds[pair[0].id], reverse=True)
        dr.text(
            (72, start_y + 40),
            level,
            font=font(40, FONT_RODIN),
            fill=FONT_BLUE,
            anchor="lm",
            stroke_width=4,
            stroke_fill=(255, 255, 255, 255),
        )
        max_row = 0
        for num, (song, diff) in enumerate(charts):
            row, col = divmod(num, PLATE_COLS)
            max_row = max(max_row, row)
            x = PLATE_START_X + col * PLATE_GRID_STEP
            y = start_y + row * PLATE_GRID_STEP
            is_rem = song.id in remaster_ids
            im.alpha_composite(assets.cover(song.id).resize((80, 80)), (x, y))
            im.alpha_composite(
                assets.pic(
                    "border_table_remaster.png" if is_rem else "border_table_base.png"
                ),
                (x - 5, y - 5),
            )
            dr.text(
                (x + 56, y + 4),
                str(chart_display_id(song, diff)),
                font=font(16, FONT_NUM),
                fill=(138, 0, 226, 255) if is_rem else (255, 255, 255, 255),
                anchor="mm",
            )
        start_y += (max_row + 1) * RATING_GRID_STEP + RATING_GROUP_GAP
    return im


# ---------------------------------------------------------------------------
# 预渲染入口（SUPERUSER 指令与国服更新自动触发共用）
# ---------------------------------------------------------------------------


def _filter_level(
    songs: Sequence[Song], level: str
) -> list[tuple[Song, SongDifficulty]]:
    entries = []
    for song in songs:
        for diff in song.get_difficulties():
            if diff.type != SongType.UTAGE and diff.level == level:
                entries.append((song, diff))
    return entries


async def generate_rating_template(level: str, song_service) -> int:
    """NB 布局生成某等级定数表底图；lv15 走三列大图。返回谱面数。"""
    entries = _filter_level(await song_service.get_all(), level)
    if not entries:
        return 0
    img = _rating_grid_15(entries) if level == "15" else _rating_grid(entries)
    out = rating_table_dir() / f"{level}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(img.save, out)
    return len(entries)


async def generate_plate_template(version: str, kind: str, song_service) -> int:
    """NB 布局生成牌子完成表底图；舞/霸生成两页。返回谱面数。"""
    songs = await song_service.get_all()
    major = major_type_of_plate(version)
    rng = plate_version_range(version)
    if rng is None:
        return 0
    lo, hi = rng
    entries = [
        (song, diff)
        for song in songs
        for diff in song.get_difficulties()
        if in_plate_scope(song, diff, lo, hi, major)
    ]
    if not entries:
        return 0
    out_dir = plate_table_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    if version in ("舞", "霸"):
        remaster = [
            (song, diff)
            for song in songs
            for diff in song.get_difficulties()
            if diff.type == major and diff.level_index.value == 4
        ]
        boundary = 13
        by_level = _by_level(entries, remaster)
        page_groups = [
            {k: v for k, v in by_level.items() if _lv_key(k) >= boundary},
            {k: v for k, v in by_level.items() if _lv_key(k) < boundary},
        ]
        total = 0
        for pages, group in enumerate(page_groups):
            if not any(group.values()):
                continue
            flat = [pair for charts in group.values() for pair in charts]
            img = _plate_grid(flat, major, remaster_entries=remaster, pages=pages)
            await asyncio.to_thread(img.save, out_dir / f"{version}-{pages + 1}.png")
            total += sum(len(v) for v in group.values())
        return total
    img = _plate_grid(entries, major)
    await asyncio.to_thread(img.save, out_dir / f"{version}{kind}.png")
    return len(entries)


def _by_level(
    entries: Sequence[tuple[Song, SongDifficulty]],
    remaster: Sequence[tuple[Song, SongDifficulty]],
) -> dict[str, list[tuple[Song, SongDifficulty]]]:
    grouped: dict[str, list[tuple[Song, SongDifficulty]]] = {}
    for song, diff in entries:
        grouped.setdefault(slot_level_of(song, diff, remaster), []).append((song, diff))
    return grouped


def _lv_key(lv: str) -> float:
    return float(lv.rstrip("+")) + (0.3 if lv.endswith("+") else 0.0)


async def refresh_all_rating_tables(song_service) -> tuple[int, list[str]]:
    """全部等级（lv7–15）定数表底图；返回 (谱面次, 失败等级)。失败保留旧底图。"""
    from ...constants import LEVEL_LIST

    async with _template_lock:
        total, failed = 0, []
        for lv in LEVEL_LIST[6:]:
            try:
                total += await generate_rating_template(lv, song_service)
            except Exception:
                logger.exception(f"定数表底图生成失败：{lv}")
                failed.append(lv)
        return total, failed


async def refresh_all_plate_tables(song_service) -> tuple[int, list[str]]:
    """全部版本牌种完成表底图（含舞/霸两页）；返回 (谱面次, 失败项)。"""
    from ...constants import PLATE_CHARS, PLATE_KINDS

    async with _template_lock:
        total, failed = 0, []
        for version in PLATE_CHARS:
            for kind in PLATE_KINDS if version in ("舞", "霸") else ("将",):
                try:
                    total += await generate_plate_template(version, kind, song_service)
                except Exception:
                    logger.exception(f"完成表底图生成失败：{version}{kind}")
                    failed.append(f"{version}{kind}")
        return total, failed


_template_lock = asyncio.Lock()
"""预渲染串行锁：CPU 密集，手动指令与自动触发共用同一把。"""


async def draw_rating_table_with_fallback(
    level: str,
    plan: str,
    scores: list,
    entries: list,
    *,
    theme: str,
    song_service,
) -> bytes | None:
    """定数表渲染；底图缺失时现场生成一次后重试（仍失败返回 None）。"""
    from .rating_table import draw_rating_table

    png = draw_rating_table(level, plan, scores, entries, theme=theme)
    if png is None:
        await generate_rating_template(level, song_service)
        png = draw_rating_table(level, plan, scores, entries, theme=theme)
    return png


async def draw_plate_table_with_fallback(
    version: str,
    kind: str,
    scores: list,
    entries: list,
    *,
    page: int,
    song_service,
) -> bytes | None:
    """牌子完成表渲染；底图缺失时现场生成一次后重试（仍失败返回 None）。"""
    from .plate_table_draw import draw_plate_table

    png = draw_plate_table(version, kind, scores, entries, page=page)
    if png is None:
        await generate_plate_template(version, kind, song_service)
        png = draw_plate_table(version, kind, scores, entries, page=page)
    return png


# ---------------------------------------------------------------------------
# 用户查询渲染
# ---------------------------------------------------------------------------


def rating_table_text_bytes(
    level: str, entries: Sequence[tuple[Song, SongDifficulty]]
) -> bytes:
    """`<等级>定数表`（NB DrawRatingTable(level_text=True) 版式）。

    底图存在时直接叠「Level. {level}」大字；缺失时按 NB 布局现算（不落盘）；
    最终按 NB 同款 0.8 缩放输出。坐标对 NB 1400 宽底图原生适配。
    """
    from PIL import ImageDraw

    path = rating_table_dir() / f"{level}.png"
    if path.exists():
        im = Image.open(path).convert("RGBA")
    else:
        im = _rating_grid_15(entries) if level == "15" else _rating_grid(entries)
    dr = ImageDraw.Draw(im)
    dr.text(
        (495, 220),
        "Level.",
        font=font(70, FONT_RODIN),
        fill=FONT_BLUE,
        anchor="ld",
        stroke_width=8,
        stroke_fill=(255, 255, 255, 255),
    )
    dr.text(
        (750, 220),
        level,
        font=font(100, FONT_RODIN),
        fill=FONT_BLUE,
        anchor="ld",
        stroke_width=8,
        stroke_fill=(255, 255, 255, 255),
    )
    im = im.resize(
        (round(im.size[0] * 0.8), round(im.size[1] * 0.8)), Image.Resampling.LANCZOS
    )
    return image_to_bytes(im)
