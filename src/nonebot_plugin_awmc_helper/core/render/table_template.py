"""完成表「模板预渲染 + 成绩实时叠加」（NB 方案，Q7）。

- SUPERUSER 指令预渲染**不含用户数据的底图**：
  - 定数表底图：``static/mai/rating_table/{level}.png``
  - 牌子完成表底图：``static/mai/plate_table/{version}{kind}.png``
- 用户查询时打开底图，按成绩在格子角上叠印章（达成/游玩未达成两种），
  并写头部统计；格子布局是确定性函数，叠加时无需存坐标。
"""

import asyncio
from pathlib import Path
from collections.abc import Sequence

from PIL import Image
from nonebot import logger
from maimai_py import Song, SongType, SongDifficulty

from .fonts import FONT_MONO, FONT_RODIN, font
from .tools import fit_text, image_to_bytes
from .assets import assets

CELL = 104
COVER = 84
GAP = 10
PER_ROW = 7

# 印章素材（pic 根目录）
DONE_STAMP = "complete_1.png"
PLAYED_STAMP = "unfinished_1.png"


def rating_table_dir() -> Path:
    return assets.static_path() / "mai" / "rating_table"


def plate_table_dir() -> Path:
    return assets.static_path() / "mai" / "plate_table"


def _grid_layout(index: int, per_row: int = PER_ROW) -> tuple[int, int]:
    r, c = divmod(index, per_row)
    return 10 + c * (CELL + GAP), 70 + r * (CELL + GAP)


def _grid_layout_offset(
    index: int, top_offset: int, per_row: int = PER_ROW
) -> tuple[int, int]:
    r, c = divmod(index, per_row)
    return 10 + c * (CELL + GAP), top_offset + r * (CELL + GAP)


def _grid_size(
    count: int, per_row: int = PER_ROW, top_offset: int = 70
) -> tuple[int, int]:
    rows = -(-count // per_row)
    return (per_row * (CELL + GAP) + GAP + 10, top_offset + rows * (CELL + GAP) + 16)


def _draw_grid_base(
    items: list[tuple[Song, SongDifficulty]], top_offset: int = 70
) -> Image.Image:
    """底图：标题占位 + 每格封面/ID/等级（无任何用户状态）。

    ``top_offset``：格子起始 y（默认 70 紧凑布局；定数表 Level 大字版式
    传 240 预留标题区）。
    """
    from PIL import Image, ImageDraw

    w, h = _grid_size(len(items), top_offset=top_offset)
    img = Image.new("RGBA", (w, h), "#f2f3f5")
    draw = ImageDraw.Draw(img)
    f_small = font(14, FONT_MONO)
    for i, (song, diff) in enumerate(items):
        x, y = _grid_layout_offset(i, top_offset)
        draw.rounded_rectangle(
            (x, y, x + CELL, y + CELL), 10, fill="#ffffff", outline="#2e323c", width=2
        )
        cover = assets.cover(song.id).resize((COVER, COVER))
        img.paste(cover, (x + (CELL - COVER) // 2, y + 4), None)
        draw.text(
            (x + 6, y + COVER + 6),
            fit_text(f"{song.id} {diff.level}", f_small, CELL - 12),
            font=f_small,
            fill="#666666",
        )
    return img


async def generate_rating_template(level: str, song_service) -> int:
    """生成某等级的定数表底图，返回谱面数。"""
    items = []
    for song in await song_service.get_all():
        for diff in song.get_difficulties():
            if diff.type != SongType.UTAGE and diff.level == level:
                items.append((song, diff))
    if not items:
        return 0
    img = _draw_grid_base(items)
    from PIL import ImageDraw

    draw = ImageDraw.Draw(img)
    draw.text((10, 20), f"定数表 {level}", font=font(30), fill="#333333")
    out = rating_table_dir() / f"{level}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(img.save, out)
    return len(items)


async def generate_plate_template(version: str, kind: str, song_service) -> int:
    """生成牌子完成表底图（该版本全部谱面），返回谱面数。"""
    from maimai_py import plate_to_version

    version_char = version
    primary = plate_to_version.get(version_char)
    if version_char in ("舞", "霸"):
        versions = [v for v in plate_to_version.values() if v.value < 20000]
    elif primary is not None:
        versions = [primary]
    else:
        return 0
    if not versions:
        return 0
    lo = min(v.value for v in versions)
    hi = max(v.value for v in versions)
    items = []
    for song in await song_service.get_all():
        for diff in song.get_difficulties():
            if diff.type == SongType.UTAGE or not (lo <= diff.version <= hi):
                continue
            items.append((song, diff))
    if not items:
        return 0
    img = _draw_grid_base(items)
    from PIL import ImageDraw

    draw = ImageDraw.Draw(img)
    draw.text((10, 20), f"{version}{kind} 完成表", font=font(30), fill="#333333")
    out = plate_table_dir() / f"{version}{kind}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(img.save, out)
    return len(items)


def _open_template(path: Path) -> Image.Image:
    return Image.open(path).convert("RGBA")


def _stamp(img: Image.Image, index: int, done: bool) -> None:
    x, y = _grid_layout(index)
    name = DONE_STAMP if done else PLAYED_STAMP
    stamp_path = assets.static_path() / "mai" / "pic" / name
    if not stamp_path.exists():
        return
    stamp = Image.open(stamp_path).convert("RGBA").resize((34, 20))
    img.alpha_composite(stamp, (x + CELL - 38, y + 4))


async def overlay_rating(
    level: str,
    plan_name: str,
    state_list: Sequence[tuple[Song, object, bool]],
    page: int,
    per_page: int,
) -> bytes | None:
    """定数完成表：打开底图按页叠加印章。state_list 为 (song, diff, done)。"""
    path = rating_table_dir() / f"{level}.png"
    if not path.exists():
        return None
    base = _open_template(path)
    from PIL import ImageDraw

    draw = ImageDraw.Draw(base)
    done_count = sum(1 for _, _, d in state_list if d)
    seg = state_list[(page - 1) * per_page : page * per_page]
    for i, (_song, _diff, done) in enumerate(seg):
        _stamp(base, i, done)
    total_pages = -(-len(state_list) // per_page)
    draw.text(
        (10, 52),
        f"{plan_name} {done_count}/{len(state_list)}"
        f"　第 {min(max(page, 1), total_pages)}/{total_pages} 页",
        font=font(18, FONT_MONO),
        fill="#e6761f",
    )
    return image_to_bytes(base)


_FONT_BLUE = (114, 188, 254, 255)


def rating_table_level_text(
    level: str, entries: list[tuple[Song, SongDifficulty]]
) -> bytes:
    """`<等级>定数表`（R8，NB DrawRatingTable(level_text=True) 版式）。

    顶部预留 240px 标题区 + 「Level. {level}」大字 + 0.8 缩放（NB 同款视觉）。
    预渲染底图存在时合成到标题画布复用（我方底图无 NB 的大片顶部留白，
    直接叠加会压住首行格子）；缺失时实时绘制网格。字号按 NB 1400 宽底图
    等比映射到我方 862 宽底图。
    """
    from PIL import Image, ImageDraw

    path = rating_table_dir() / f"{level}.png"
    header = 240
    if path.exists():
        base = _open_template(path)
        im = Image.new("RGBA", (base.size[0], base.size[1] + header), "#f2f3f5")
        im.paste(base, (0, header))
    else:
        im = _draw_grid_base(entries, top_offset=header)
    scale = im.size[0] / 1400  # NB 底图 1400 宽的等比映射
    dr = ImageDraw.Draw(im)
    x = round(495 * scale)
    y = 220
    dr.text(
        (x, y),
        "Level.",
        font=font(round(70 * scale) or 44, FONT_RODIN),
        fill=_FONT_BLUE,
        anchor="ld",
        stroke_width=round(8 * scale) or 5,
        stroke_fill=(255, 255, 255, 255),
    )
    dr.text(
        (x + round(255 * scale) + 10, y),
        level,
        font=font(round(100 * scale) or 62, FONT_RODIN),
        fill=_FONT_BLUE,
        anchor="ld",
        stroke_width=round(8 * scale) or 5,
        stroke_fill=(255, 255, 255, 255),
    )
    im = im.resize(
        (round(im.size[0] * 0.8), round(im.size[1] * 0.8)), Image.Resampling.LANCZOS
    )
    return image_to_bytes(im)


async def overlay_plate(
    version: str,
    kind: str,
    cleared_keys: set[tuple],
    items: list[tuple[Song, SongDifficulty]],
    done_count: int,
) -> bytes | None:
    """牌子完成表：底图叠加达成印章。items 为该牌子全部 (song, diff)。"""
    path = plate_table_dir() / f"{version}{kind}.png"
    if not path.exists():
        return None
    base = _open_template(path)
    for i, (song, diff) in enumerate(items):
        if (song.id, diff.type, diff.level_index.value) in cleared_keys:
            _stamp(base, i, True)
    from PIL import ImageDraw

    draw = ImageDraw.Draw(base)
    draw.text(
        (10, 52),
        f"已达成 {done_count}/{len(items)}",
        font=font(18, FONT_MONO),
        fill="#e6761f",
    )
    return image_to_bytes(base)


def ensure_dirs() -> None:
    if not assets.static_path().exists():
        return
    rating_table_dir().mkdir(parents=True, exist_ok=True)
    plate_table_dir().mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# 全量预渲染（SUPERUSER 指令与国服更新自动触发共用，song-db-design §7.3）
# ---------------------------------------------------------------------------

_template_lock = asyncio.Lock()
"""预渲染串行锁：CPU 密集，手动指令与自动触发共用同一把。"""


async def refresh_all_rating_tables(song_service) -> tuple[int, list[str]]:
    """全部等级（lv7–15）定数表底图；返回 (谱面次, 失败等级)。失败保留旧底图不抛出。"""
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
    """全部版本牌种完成表底图；返回 (谱面次, 失败版本牌种)。

    牌名映射缺失的版本（库滞后于日服新版本，§7.4）在 generate 内自然返回 0 跳过。
    """
    from ...constants import PLATE_CHARS, PLATE_KINDS

    async with _template_lock:
        total, failed = 0, []
        for version in PLATE_CHARS:
            if version in ("舞", "霸"):
                continue
            for kind in PLATE_KINDS:
                try:
                    total += await generate_plate_template(version, kind, song_service)
                except Exception:
                    logger.exception(f"完成表底图生成失败：{version}{kind}")
                    failed.append(f"{version}{kind}")
        return total, failed
