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

import json
import time
import asyncio
from typing import Any
from pathlib import Path
from collections.abc import Callable, Sequence

from PIL import Image, ImageDraw
from nonebot import logger
from maimai_py import Song, SongType, SongDifficulty

from .fonts import FONT_HAN, FONT_RODIN, font
from .tools import (
    WHITE,
    TITLE_BLUE,
    CELL_COVER_75,
    CELL_COVER_80,
    DIFF_TEXT_COLORS,
    credit_text,
    scale_output,
    image_to_bytes,
    draw_cover_cell,
    generate_prism_bg,
    generate_frosted_card,
)
from .assets import assets
from ..plates import in_plate_scope, major_type_of_plate, plate_version_range
from .nb_chart import version_image
from ...constants import LEVEL_INDEX_EN, chart_display_id
from .table_layout import (
    LV15_COLS,
    PLATE_COLS,
    LV15_START_X,
    LV15_START_Y,
    LV15_COL_STEP,
    LV15_ROW_STEP,
    PLATE_START_X,
    PLATE_START_Y,
    PLATE_COL_STEP,
    PLATE_ROW_STEP,
    RATING_START_Y,
    PLATE_GROUP_GAP,
    RATING_GRID_STEP,
    RATING_GROUP_GAP,
    slot_rep,
    iter_grid,
    grid_height,
    group_by_ds,
    grid_geometry,
    slot_level_of,
    group_by_level,
    level_page_key,
)

FONT_BLUE = TITLE_BLUE


def rating_table_dir() -> Path:
    return assets.static_path() / "mai" / "rating_table"


def rating_table_file(level: str) -> Path:
    """定数表底图文件路径单源（预渲染写出与查询读取共用，防手拼漂移）。"""
    return rating_table_dir() / f"{level}.png"


def plate_table_dir() -> Path:
    return assets.static_path() / "mai" / "plate_table"


def _credit(im: Image.Image, height: int) -> None:
    ImageDraw.Draw(im).text(
        (700, height - 75),
        credit_text(),
        font=font(30, FONT_RODIN),
        fill=FONT_BLUE,
        anchor="mm",
    )


def _rating_grid(
    entries: Sequence[tuple[Song, SongDifficulty]], *, by_level: bool = False
) -> Image.Image:
    """NB update_rating_table 布局（lv7–14）：毛玻璃卡 + 节封面网格。

    分组按 ``by_level`` 分流（与盖章侧同函数同序契约）：
    - ``False``（默认，单等级条件/预渲染底图）：``group_by_ds`` 定数节，节
      标签 = 完整定数（"13.9"~"13.0"，与旧版式节序一致）；
    - ``True``（跨等级条件版）：``group_by_level`` 标级大类（"14+"/"14" 各
      一组合并、组内不细分定数小数节，2026-10-01 拍板）。
    """
    groups = group_by_level(entries) if by_level else group_by_ds(entries)
    # by_level（标级大类）：等级串标签（"14+"）较宽——列起点右移对齐牌子
    # 完成表（x=180）并每行少渲染一个（14→13 列）（2026-10-01 用户实测遮挡）
    cols, start_x = grid_geometry(by_level)

    # 画布高度预算：各组网格占高求和（算式单源 grid_height）
    current_y = RATING_START_Y + sum(
        grid_height(
            len(charts),
            cols=cols,
            row_step=RATING_GRID_STEP,
            group_gap=RATING_GROUP_GAP,
        )
        for charts in groups.values()
    )
    height = current_y + 230

    im = generate_frosted_card(
        generate_prism_bg(height, 360), (50, 404, 1350, current_y)
    )
    dr = ImageDraw.Draw(im)

    _credit(im, height)

    start_y = RATING_START_Y
    for label, charts in groups.items():
        # 节标签：定数节 = 小数节（".9"~".0" 旧版式——单等级表头已带等级
        # 语境）；标级大类 = 等级串（"14+"）
        dr.text(
            (70, start_y + 35),
            label if by_level else f".{label.split('.')[-1]}",
            font=font(40, FONT_RODIN),
            fill=FONT_BLUE,
            anchor="lm",
            stroke_width=4,
            stroke_fill=WHITE,
        )
        for x, y, (song, diff), _num in iter_grid(
            charts,
            cols=cols,
            start_x=start_x,
            start_y=start_y,
            row_step=RATING_GRID_STEP,
        ):
            li = diff.level_index.value
            draw_cover_cell(
                im,
                dr,
                x,
                y,
                song_id=song.id,
                id_text=str(chart_display_id(song, diff)),
                cover_size=CELL_COVER_75,
                border=assets.pic(f"border_{LEVEL_INDEX_EN[li]}.png"),
                font_size=13,
                fill=DIFF_TEXT_COLORS[li],
            )
        start_y += grid_height(
            len(charts),
            cols=cols,
            row_step=RATING_GRID_STEP,
            group_gap=RATING_GROUP_GAP,
        )
    return im


def _rating_grid_15(
    entries: Sequence[tuple[Song, SongDifficulty]],
) -> Image.Image:
    """NB update_level_15_rating_table：lv15 三列大图（含 UNKNOWN 占位）。

    底图摆放与 rating_table 盖章侧**必须同序**（见下方排序契约）；lv7-14
    两侧同走 table_layout.group_by_ds，唯 lv15 需各自显式排序。
    """
    # 同序契约：两侧同一「定数降序」排序（sorted 稳定，等值保序）。当前游戏内
    # Level.15 谱面定数全部恰为 15.0（无 15+ 记法，出厂顺序即 id 序），此排序
    # 今日为无操作；两侧统一是防未来出现分档定数时底图封面与章错位。
    entries = sorted(entries, key=lambda pair: pair[1].level_value, reverse=True)
    count = len(entries)
    lines = count // LV15_COLS + (1 if count % LV15_COLS else 0)
    height = 650 + lines * LV15_ROW_STEP

    im = generate_prism_bg(height, 360)
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
                stroke_fill=WHITE,
            )
    return im


def _plate_grid(
    entries: Sequence[tuple[Song, SongDifficulty]],
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
        rep = slot_rep(
            pair[1],
            re_pair[1] if re_pair else None,
            use_remaster=bool(remaster_entries),
        )
        grouped.setdefault(rep.level, []).append(pair)
        # 组内排序键随代表谱面定数（ReM 曲用 ReM 定数，其余用 Master 定数）
        sort_ds[song_id] = rep.level_value
    order = sorted(grouped, key=level_page_key, reverse=True)
    groups = {k: grouped[k] for k in order}
    remaster_ids = {s.id for s, _d in (remaster_entries or [])}

    # 画布高度预算：各组网格占高求和（算式单源 grid_height）
    current_y = PLATE_START_Y + sum(
        grid_height(
            len(charts),
            cols=PLATE_COLS,
            row_step=PLATE_ROW_STEP,
            group_gap=PLATE_GROUP_GAP,
        )
        for charts in groups.values()
    )
    height = current_y + 180

    im = generate_frosted_card(
        generate_prism_bg(height, 400), (50, 444, 1350, current_y)
    )
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
            stroke_fill=WHITE,
        )
        for x, y, (song, diff), _num in iter_grid(
            charts,
            cols=PLATE_COLS,
            start_x=PLATE_START_X,
            start_y=start_y,
            row_step=PLATE_ROW_STEP,
            col_step=PLATE_COL_STEP,
        ):
            is_rem = song.id in remaster_ids
            draw_cover_cell(
                im,
                dr,
                x,
                y,
                song_id=song.id,
                id_text=str(chart_display_id(song, diff)),
                cover_size=CELL_COVER_80,
                border=assets.pic(
                    "border_table_remaster.png" if is_rem else "border_table_base.png"
                ),
                font_size=16,
                fill=DIFF_TEXT_COLORS[4] if is_rem else DIFF_TEXT_COLORS[0],
            )
        start_y += grid_height(
            len(charts),
            cols=PLATE_COLS,
            row_step=PLATE_ROW_STEP,
            group_gap=PLATE_GROUP_GAP,
        )
    return im


# ---------------------------------------------------------------------------
# 预渲染入口（SUPERUSER 指令与国服更新自动触发共用）
# ---------------------------------------------------------------------------


def filter_level(
    songs: Sequence[Song], level: str
) -> list[tuple[Song, SongDifficulty]]:
    """全库指定标级的谱面条目（排除宴谱；底图生成与插件层查询共用）。"""
    entries = []
    for song in songs:
        for diff in song.get_difficulties():
            if diff.type != SongType.UTAGE and diff.level == level:
                entries.append((song, diff))
    return entries


async def generate_rating_template(level: str, song_service) -> int:
    """NB 布局生成某等级定数表底图；lv15 走三列大图。返回谱面数。"""
    entries = filter_level(await song_service.get_all(), level)
    if not entries:
        return 0
    out = rating_table_file(level)
    out.parent.mkdir(parents=True, exist_ok=True)
    # 绘制段 CPU 密集（数百张封面加载/缩放），整体让出事件循环（L-6）；
    # 闭包只引用本协程局部变量，无跨线程共享可变状态
    img = await asyncio.to_thread(
        lambda: _rating_grid_15(entries) if level == "15" else _rating_grid(entries)
    )
    await asyncio.to_thread(img.save, out)
    return len(entries)


_FULL_SET_PLATE_VERSIONS = ("舞", "霸")
"""旧作全集双页版本：完成表输出文件名不含牌种（各牌种共用同一对底图）。"""

_COVER_RETRY_INTERVAL = 3600
"""sidecar 缺口重试间隔（秒）：查询触发、限频一小时，官方站抖动自愈。"""


def plate_template_name(version: str, kind: str, page: int, *, jp: bool = False) -> str:
    """完成表底图文件名（去扩展名）单源：预渲染存图、查询读图、sidecar
    留档共用，防三处手拼漂移。CN 裸名，JP 带 ``-jp`` 后缀（分口径）。"""
    name = (
        f"{version}-{page}"
        if version in _FULL_SET_PLATE_VERSIONS
        else f"{version}{kind}"
    )
    return f"{name}-jp" if jp else name


def plate_template_file(
    version: str, kind: str, page: int, *, jp: bool = False
) -> Path:
    """完成表底图 PNG 路径（:func:`plate_template_name` 加扩展名）。"""
    return plate_table_dir() / f"{plate_template_name(version, kind, page, jp=jp)}.png"


def plate_sidecar_file(version: str, kind: str, page: int, *, jp: bool = False) -> Path:
    """完成表底图的封面缺失留档（JSON sidecar，与底图同名换扩展名）。"""
    return plate_table_dir() / f"{plate_template_name(version, kind, page, jp=jp)}.json"


async def _save_plate_sidecar(
    version: str, kind: str, page: int, *, jp: bool, missing: list[int]
) -> None:
    """封面缺失留档：missing 非空写 ``{缺失集, 时间戳}``，空则删旧档。

    查询侧据此愈合（:func:`_plate_sidecar_heal`）——生成时下载失败的曲
    （官方站抖动）不烙死占位图，封面补齐后自动重渲；快照未提供文件名的
    曲不在 missing（无法在线补，收敛归曲库刷新的指纹失效管）。
    """
    sidecar = plate_sidecar_file(version, kind, page, jp=jp)
    if not missing:
        sidecar.unlink(missing_ok=True)
        return
    payload = json.dumps(
        {"missing": sorted(set(missing)), "ts": time.time()}, ensure_ascii=False
    )
    await asyncio.to_thread(sidecar.write_text, payload)


async def _plate_sidecar_heal(
    version: str, kind: str, page: int, *, jp: bool, song_service
) -> bool:
    """底图存在但生成时有封面缺口：检查缺口状态，需重渲返回 True。

    - 缺口中有曲封面已本地可得（他路补齐，如卡片查询）→ 重渲；
    - 距上次尝试超过 :data:`_COVER_RETRY_INTERVAL` → 经 ``ensure_many``
      重试一轮（探针熔断使官方站不可达时秒级返回），有新落盘 → 重渲，
      无进展仅推进时间戳（避免每查必试）。
    """
    from . import jp_cover

    sidecar = plate_sidecar_file(version, kind, page, jp=jp)
    if not sidecar.exists():
        return False
    try:
        data = json.loads(sidecar.read_text())
        missing = [int(x) for x in data.get("missing") or []]
    except Exception:
        logger.exception(f"封面缺失留档损坏，忽略：{sidecar.name}")
        return False
    if not missing:
        await asyncio.to_thread(sidecar.unlink, True)
        return False
    if await asyncio.to_thread(jp_cover.any_local, missing):
        return True
    if time.time() - float(data.get("ts") or 0) < _COVER_RETRY_INTERVAL:
        return False
    still = await jp_cover.ensure_many(missing)
    if len(still) < len(missing):
        return True
    await _save_plate_sidecar(version, kind, page, jp=jp, missing=still)
    return False


async def generate_plate_template(
    version: str, kind: str, song_service, *, jp: bool = False
) -> int:
    """NB 布局生成牌子完成表底图；舞/霸生成两页。返回谱面数。

    ``jp=True`` 用日服视图曲集与日服版本区间（丸/回 等日服牌；仅查询兜底
    现场生成，预渲染枚举 ``refresh_all_plate_tables`` 仍只做国服牌单）。
    底图名带 ``-jp`` 后缀与 CN 分图：共用牌字（华/爽 等）两口径网格不同，
    同名会让日服查询读到 CN 预渲染网格（2026-10-05 修）。
    """
    songs = await (song_service.jp_all() if jp else song_service.get_all())
    major = major_type_of_plate(version)
    rng = plate_version_range(version, jp=jp)
    if rng is None:
        return 0
    lo, hi = rng
    # 单趟双收：entries（牌区间内全部谱面）与 remaster（舞/霸的 ReM 槽），
    # 免对全库 get_difficulties 展开两遍
    want_remaster = version in _FULL_SET_PLATE_VERSIONS
    entries: list[tuple[Song, SongDifficulty]] = []
    remaster: list[tuple[Song, SongDifficulty]] = []
    for song in songs:
        for diff in song.get_difficulties():
            if in_plate_scope(song, diff, lo, hi, major):
                entries.append((song, diff))
            if want_remaster and diff.type == major and diff.level_index.value == 4:
                remaster.append((song, diff))
    if not entries:
        return 0
    # 底图内嵌曲绘批量补齐（2026-10-05 服务器实测补缺）：卡片渲染的逐卡
    # ensure 惯例在底图路径缺失，JP 曲缺缓存全落 0.png；CN 曲静态命中零开销。
    # 仍缺失的（官方站抖动）留档 sidecar，查询侧择机重试重渲
    from . import jp_cover

    still_missing = await jp_cover.ensure_many(song.id for song, _ in entries)
    if still_missing:
        logger.warning(
            f"完成表底图 {version}{kind}（{'jp' if jp else 'cn'}）生成时 "
            f"{len(still_missing)} 曲封面拉取失败，已留档待重试：{still_missing}"
        )
    out_dir = plate_table_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    if version in _FULL_SET_PLATE_VERSIONS:
        boundary = 13
        by_level = _by_level(entries, remaster)
        page_groups = [
            {k: v for k, v in by_level.items() if level_page_key(k) >= boundary},
            {k: v for k, v in by_level.items() if level_page_key(k) < boundary},
        ]
        total = 0
        for pages, group in enumerate(page_groups):
            if not any(group.values()):
                continue
            flat = [pair for charts in group.values() for pair in charts]
            # 绘制段让出事件循环（L-6）：lambda 在本次迭代内即被 await，
            # 捕获的 flat/pages 不会跨迭代失效
            img = await asyncio.to_thread(
                lambda flat=flat, pages=pages: _plate_grid(
                    flat, remaster_entries=remaster, pages=pages
                )
            )
            await asyncio.to_thread(
                img.save,
                out_dir / f"{plate_template_name(version, kind, pages + 1, jp=jp)}.png",
            )
            await _save_plate_sidecar(
                version, kind, pages + 1, jp=jp, missing=still_missing
            )
            total += sum(len(v) for v in group.values())
        return total
    img = await asyncio.to_thread(_plate_grid, entries)
    await asyncio.to_thread(
        img.save, out_dir / f"{plate_template_name(version, kind, 1, jp=jp)}.png"
    )
    await _save_plate_sidecar(version, kind, 1, jp=jp, missing=still_missing)
    return len(entries)


def _by_level(
    entries: Sequence[tuple[Song, SongDifficulty]],
    remaster: Sequence[tuple[Song, SongDifficulty]],
) -> dict[str, list[tuple[Song, SongDifficulty]]]:
    grouped: dict[str, list[tuple[Song, SongDifficulty]]] = {}
    for song, diff in entries:
        grouped.setdefault(slot_level_of(song, diff, remaster), []).append((song, diff))
    return grouped


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
    """全部版本牌种完成表底图（舞代四牌、霸仅者，均旧作全集双页）。

    返回 (谱面次, 失败项)。
    """
    from ..plates import plate_kinds_of
    from ...constants import PLATE_CHARS

    async with _template_lock:
        total, failed = 0, []
        for version in PLATE_CHARS:
            # 舞/霸完成表按 {版本}-{页}.png 输出且 _plate_grid 与牌种无关：
            # 多牌种只渲染一次，避免同一对 PNG 全量重算 N 遍
            kinds = (
                plate_kinds_of(version)[:1]
                if version in _FULL_SET_PLATE_VERSIONS
                else plate_kinds_of(version)
            )
            for kind in kinds:
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
    plan: "str | None",
    scores: list,
    entries: list,
    *,
    theme: str,
    song_service,
    checker: "Callable[[Any, Any, Any], bool] | None" = None,
    use_file: bool = True,
) -> bytes | None:
    """定数表渲染；底图缺失时现场生成一次后重试（仍失败返回 None）。

    ``use_file=False``（日服视图）：预渲染文件是国服全量网格，与日服曲集
    不一致 → 按 entries 现算底图（不落盘，条件版现算同口径），不再走
    「缺失即补写国服文件」的回退。
    """
    from .rating_table import draw_rating_table

    if not use_file:
        im = await asyncio.to_thread(
            lambda: _rating_grid_15(entries) if level == "15" else _rating_grid(entries)
        )
        return draw_rating_table(
            level, plan, scores, entries, theme=theme, checker=checker, im=im
        )
    png = draw_rating_table(level, plan, scores, entries, theme=theme, checker=checker)
    if png is None:
        await generate_rating_template(level, song_service)
        png = draw_rating_table(
            level, plan, scores, entries, theme=theme, checker=checker
        )
    return png


async def draw_plate_table_with_fallback(
    version: str,
    kind: str,
    scores: list,
    entries: list,
    *,
    page: int,
    song_service,
    jp: bool = False,
) -> bytes | None:
    """牌子完成表渲染；底图缺失时现场生成一次后重试（仍失败返回 None）。

    底图在但 sidecar 有封面缺口（生成时官方站抖动）→ :func:`_plate_sidecar_heal`
    择机重试，封面补齐即重渲（否则占位图烙死到下次指纹变化）。
    """
    from .plate_table_draw import draw_plate_table

    png = draw_plate_table(version, kind, scores, entries, page=page, jp=jp)
    if png is not None and await _plate_sidecar_heal(
        version, kind, page, jp=jp, song_service=song_service
    ):
        png = None  # 缺口已愈：走下方重渲分支
    if png is None:
        await generate_plate_template(version, kind, song_service, jp=jp)
        png = draw_plate_table(version, kind, scores, entries, page=page, jp=jp)
    return png


# ---------------------------------------------------------------------------
# 用户查询渲染
# ---------------------------------------------------------------------------


def draw_level_header(
    dr: ImageDraw.ImageDraw,
    level: str,
    y: int,
    *,
    prefix: "str | None" = "Level.",
    suffix: str = "",
) -> None:
    """完成表/定数表大字表头（rating_table/table_template 共用）。

    两版式 y 有意错位（普通分支统计头不同），由调用方传入。
    ``prefix=None`` 为条件化表头（P2-c）：无 Level. 前缀、直接画条件串，
    超 4 显示字降字号防溢出。``suffix``：表型说明（「完成表」/「定数表」，
    2026-10-01 QoL——标题位原本只承载 Level./条件串，表型无处说明）。

    字体口径（2026-10-01 用户拍板）：「Level. xx」整段日文字体（RODIN，
    数字原为 RODIN，P2-c 曾误改 HAN）；条件串与表型说明统一中文字体
    （HAN——Rodin 无简中字形）。整块标题按实测宽度水平居中于画布中轴
    700（原左起 495 固定位，长条件串头重脚轻）。
    """
    parts: "list[tuple[str, Any]]" = []
    if prefix:
        parts.append((prefix, font(70, FONT_RODIN)))
        parts.append((level, font(100 if _width(level) <= 4 else 60, FONT_RODIN)))
    else:
        parts.append((level, font(100 if _width(level) <= 4 else 60, FONT_HAN)))
    if suffix:
        parts.append((f" {suffix}", font(70, FONT_HAN)))
    total = sum(dr.textlength(text, font=fnt) for text, fnt in parts)
    x = 700 - total / 2
    for text, fnt in parts:
        dr.text(
            (x, y),
            text,
            font=fnt,
            fill=FONT_BLUE,
            anchor="ld",
            stroke_width=8,
            stroke_fill=WHITE,
        )
        x += dr.textlength(text, font=fnt)


def _width(text: str) -> int:
    """显示宽度粗算（全角 2 / 半角 1；标题字号选择用）。

    ⚠️ 与 :func:`tools.column_width` 是**两套独立宽度口径**（第六轮审查定案：
    不合并）——本函数按码点粗分（>0x2E7F 记全角 2），column_width 走 NB
    _CHAR_WIDTHS 逐字宽表；换实现会改变「Level.」大字的 100pt/60pt 字号
    切换点，属视觉变更。粗算口径仅此处表头字号选择用，截断类一律走
    column_width 族。
    """
    return sum(2 if ord(ch) > 0x2E7F else 1 for ch in text)


async def rating_table_base_image(
    entries: Sequence[tuple[Song, SongDifficulty]],
    level: "str | None" = None,
    *,
    use_file: bool = True,
) -> Image.Image:
    """完成表/定数表底图统一入口：level 文件底图优先（既有预渲染，收编后
    单等级条件保持同速同像素），缺失或无条件 level 时按 entries 现算。

    ``use_file=False``（日服视图）：预渲染文件是国服全量网格，与日服曲集
    不一致，按 entries 现算且不落盘（条件版现算同口径，2026-09-30 拍板）。

    条件版现算不落盘、不做进程缓存（2026-09-30 拍板：条件组合长尾命中率
    低，现算 1-3s 可接受）。CPU 密集，整体在工作线程执行（L-6）。
    """
    if level:
        if use_file:
            path = rating_table_file(level)
            if path.exists():
                return Image.open(path).convert("RGBA")
        if level == "15":
            # lv15 三列大图版式（含 UNKNOWN 槽）仅文件缺失现算分支保持
            return await asyncio.to_thread(lambda: _rating_grid_15(entries))
        return await asyncio.to_thread(lambda: _rating_grid(entries))
    # 跨等级条件版：标级大类分组（等级各一组合并、组内不细分定数小数节）
    return await asyncio.to_thread(lambda: _rating_grid(entries, by_level=True))


async def _rating_table_text_bytes(
    entries: Sequence[tuple[Song, SongDifficulty]],
    header: str,
    level: "str | None" = None,
    use_file: bool = True,
) -> bytes:
    """定数表文字版渲染孪生体合并实现（第六轮审查）。

    ``level`` 非 None → 底图走该等级文件底图（缺失现算）且表头带
    「Level.」前缀；None → 条件版（底图按 entries 现算、条件串直接作表头）。
    ``use_file=False``（日服视图）按 entries 现算（口径同
    :func:`rating_table_base_image`）。
    现算分支 CPU 密集，整体在工作线程执行（L-6），故本函数为协程。
    """
    im = await rating_table_base_image(entries, level, use_file=use_file)
    dr = ImageDraw.Draw(im)
    draw_level_header(
        dr, header, 220, prefix="Level." if level else None, suffix="定数表"
    )
    return image_to_bytes(scale_output(im))


async def rating_table_text_bytes(
    level: str,
    entries: Sequence[tuple[Song, SongDifficulty]],
    *,
    use_file: bool = True,
) -> bytes:
    """`<等级>定数表`（NB DrawRatingTable(level_text=True) 版式）。

    底图存在时直接叠「Level. {level}」大字；缺失时按 NB 布局现算（不落盘）；
    最终按 NB 同款 0.8 缩放输出。坐标对 NB 1400 宽底图原生适配。

    签名保持 ``(level, entries)``——plugins/tables 直调（对外名兼容），
    实现统一走 :func:`_rating_table_text_bytes`。
    """
    return await _rating_table_text_bytes(
        entries, level, level=level, use_file=use_file
    )


async def rating_table_cond_text_bytes(
    entries: Sequence[tuple[Song, SongDifficulty]], header_text: str
) -> bytes:
    """条件化定数表（P2-c）：底图按条件谱面集现算（不落盘）、条件串表头。

    签名保持 ``(entries, header_text)``——plugins/tables 直调（对外名兼容），
    实现统一走 :func:`_rating_table_text_bytes`。
    """
    return await _rating_table_text_bytes(entries, header_text)
