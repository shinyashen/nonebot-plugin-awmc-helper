"""完成度表格绘图：完成表/进度的封面网格。"""

from collections.abc import Sequence

from PIL import Image, ImageDraw
from maimai_py import Song

from .fonts import font
from .tools import fit_text, rounded_mask, image_to_bytes
from .assets import assets

CELL = 104
COVER = 84
GAP = 10

# 标记色：已达成 / 已游玩未达成 / 未游玩
COLOR_DONE = "#ffcf3f"
COLOR_PLAYED = "#8a8f99"
COLOR_NEW = "#2e323c"
BG = "#f2f3f5"


def draw_completion_grid(
    title: str,
    items: Sequence[tuple[Song, object, str]],
    per_row: int = 7,
) -> Image.Image:
    """完成表网格。

    ``items`` 为 (song, diff, state) 三元组，state ∈ done/played/new。
    """
    header_h = 64
    rows = -(-len(items) // per_row)
    w = per_row * (CELL + GAP) + GAP + 16
    h = header_h + rows * (CELL + GAP) + 24
    img = Image.new("RGBA", (w, h), BG)
    draw = ImageDraw.Draw(img)
    draw.text((20, 18), title, font=font(26), fill="#333")

    f_small = font(14)
    for i, (song, _diff, state) in enumerate(items):
        r, c = divmod(i, per_row)
        x = GAP + c * (CELL + GAP) + 8
        y = header_h + r * (CELL + GAP)
        color = {"done": COLOR_DONE, "played": COLOR_PLAYED, "new": COLOR_NEW}.get(
            state, COLOR_NEW
        )
        draw.rounded_rectangle(
            (x, y, x + CELL, y + CELL), 10, fill="#ffffff", outline=color, width=3
        )
        cover = assets.cover(song.id).resize((COVER, COVER))
        img.paste(cover, (x + (CELL - COVER) // 2, y + 4), rounded_mask(cover.size, 8))
        lvl = getattr(_diff, "level", "")
        draw.text(
            (x + 6, y + COVER + 6),
            fit_text(f"{song.id} {lvl}", f_small, CELL - 12),
            font=f_small,
            fill="#666",
        )
    return img


def completion_grid_bytes(
    title: str, items: list[tuple[Song, object, str]], per_row: int = 7
) -> bytes:
    return image_to_bytes(draw_completion_grid(title, items, per_row))
