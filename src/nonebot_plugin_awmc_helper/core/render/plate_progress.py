"""牌子进度总览（NB core/image/plate_table.py::DrawPlateProgress 的移植）。

三色渐变底 + 毛玻璃卡：顶部牌子表头与总进度条，下方按难度倒序逐节展示
各难度「n/N + 百分比 + 未达成谱面封面网格（13 列，超过 51 个折叠显示）」。

牌子的达成判定与范围由 maimai_py ``MaimaiPlates`` 完成，本模块只做渲染；
数据由调用方组装为倒序的槽节列表（Re:MASTER → Basic）。
"""

from PIL import Image, ImageDraw

from .fonts import FONT_NUM, FONT_RODIN, font
from .tools import (
    TEXT_BLUE,
    TITLE_BLUE,
    credit_text,
    image_to_bytes,
    generate_prism_bg,
    generate_frosted_card,
)
from .assets import assets

# 难度英文名（对齐 NB DIFFS，BASIC..ReMASTER）
_DIFF_NAMES = ["Basic", "Advanced", "Expert", "Master", "Re:Master"]
_START_X, _START_Y, _GAP = 84, 455, 96


def _progress_bar(im: Image.Image, y: int, progress: float) -> None:
    """993×92 进度条（progress_big 裁剪）。"""
    if progress <= 0:
        return
    big = assets.pic("progress_big.png")
    im.alpha_composite(big.crop((0, 0, int(993 * progress), 92)), (204, y))


def progress_header(
    im: Image.Image, draw: ImageDraw.ImageDraw, completed: int, total: int
) -> None:
    """进度头部：进度条 + n/N + 百分比（进度总览与牌子完成表同源同坐标）。"""
    progress = completed / total if total else 0
    _progress_bar(im, 219, progress)
    text = "COMPLETED!!!" if completed == total else f"{completed}/{total}"
    draw.text(
        (700, 240),
        text,
        font=font(30, FONT_RODIN),
        fill=TEXT_BLUE,
        anchor="mm",
        stroke_width=3,
        stroke_fill=(255, 255, 255, 255),
    )
    draw.text(
        (1190, 240),
        f"{round(progress * 100, 2)}%",
        font=font(30, FONT_RODIN),
        fill=TEXT_BLUE,
        anchor="rm",
        stroke_width=3,
        stroke_fill=(255, 255, 255, 255),
    )


def plate_progress_bytes(
    version: str,
    kind: str,
    *,
    service: str | None = None,
    slots: list[dict],
    total_count: int,
    completed_count: int,
) -> bytes:
    """绘制牌子进度总览。

    ``slots``：倒序槽节（Re:MASTER → Basic），每节含 ``level_index``、
    ``cleared``、``total`` 与未达成 ``items``（``(song_id, level_index, 定数)``）。
    """
    # 高度预算（NB _get_display_row_count：每节最多 4 行）
    current_y = 395
    for slot in slots:
        count = len(slot["items"])
        rows = 1 if count <= 0 else min((count - 1) // 13 + 1, 4)
        current_y += rows * _GAP + 100
    height = current_y + 180

    im = generate_prism_bg(height, 305)
    im = generate_frosted_card(im, (50, 349, 1350, current_y))

    im.alpha_composite(assets.pic("plate_progress_2.png"), (175, 20))
    plate_bg = assets.plate_version(version, kind)
    if plate_bg is not None:
        im.alpha_composite(plate_bg.resize((1000, 161)), (200, 35))

    dr = ImageDraw.Draw(im)

    # 总进度（progress = 完成曲数 / 牌子曲数）
    progress_header(im, dr, completed_count, total_count)

    start_y = _START_Y
    for slot in slots:
        li = slot["level_index"]
        items = slot["items"]
        cleared, total = slot["cleared"], slot["total"]
        im.alpha_composite(assets.pic("progress_bg.png"), (198, start_y - 85))
        group_progress = cleared / total if total else 0
        _progress_bar(im, start_y - 79, group_progress)
        g_text = "COMPLETED!!!" if cleared == total else f"{cleared}/{total}"
        color = TEXT_BLUE
        dr.text(
            (220, start_y - 57),
            _DIFF_NAMES[li],
            font=font(34, FONT_RODIN),
            fill=color,
            anchor="lm",
            stroke_width=4,
            stroke_fill=(255, 255, 255, 255),
        )
        dr.text(
            (700, start_y - 57),
            g_text,
            font=font(36, FONT_RODIN),
            fill=color,
            anchor="mm",
            stroke_width=4,
            stroke_fill=(255, 255, 255, 255),
        )
        dr.text(
            (1190, start_y - 57),
            f"{round(group_progress * 100, 2)}%",
            font=font(20, FONT_RODIN),
            fill=color,
            anchor="rm",
            stroke_width=2,
            stroke_fill=(255, 255, 255, 255),
        )

        # 未达成封面网格（13 列；超过 51 个折叠显示剩余数）
        max_row = 0
        id_bg = assets.pic("border_table_base.png")
        for num, (song_id, li_v, _lv) in enumerate(items):
            row, col = divmod(num, 13)
            max_row = max(max_row, row)
            x = _START_X + col * _GAP
            y = start_y + row * _GAP
            if num >= 51 and len(items[num:]) != 1:
                dr.multiline_text(
                    (x, y + 35),
                    f"余「{len(items[num:])}」\n个未完成",
                    font=font(20, FONT_RODIN),
                    fill=TEXT_BLUE,
                    anchor="lm",
                )
                break
            im.alpha_composite(assets.cover(song_id).resize((80, 80)), (x, y))
            im.alpha_composite(id_bg, (x - 5, y - 5))
            dr.text(
                (x + 56, y + 4),
                str(song_id),
                font=font(16, FONT_NUM),
                fill=(255, 255, 255, 255),
                anchor="mm",
            )
        start_y += (max_row + 1) * _GAP + 100

    dr.text(
        (700, height - 75),
        credit_text(service),
        font=font(22, FONT_RODIN),
        fill=TITLE_BLUE,
        anchor="mm",
    )
    return image_to_bytes(im)
