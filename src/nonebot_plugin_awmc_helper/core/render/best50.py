"""B50 成绩图（信息布局与原版同源：b35/b15 双列 + rating 头部）。"""

from PIL import Image, ImageDraw
from maimai_py import SongType, LevelIndex, ScoreExtend

from .fonts import FONT_NUM, FONT_MONO, font
from .tools import fit_text, text_size, rounded_mask, image_to_bytes
from ..utils import paginate
from .assets import assets

LEVEL_COLORS = {
    LevelIndex.BASIC: "#22bb5b",
    LevelIndex.ADVANCED: "#fb9a0f",
    LevelIndex.EXPERT: "#f64861",
    LevelIndex.MASTER: "#a45eeb",
    LevelIndex.ReMASTER: "#ba5cf5",
}

BG = "#f5f6f8"
CARD_W = 840
CELL_W = 400
CELL_H = 46
COVER = 38


def _draw_cell(
    img: Image.Image,
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    rank: int,
    score: ScoreExtend,
):
    draw.rounded_rectangle((x, y, x + CELL_W, y + CELL_H - 6), 8, fill="#ffffff")
    # 封面
    cover = assets.cover(score.id % 10000).resize((COVER, COVER))
    mask = rounded_mask(cover.size, 6)
    img.paste(cover, (x + 6, y + 4), mask)
    # 标题 + 曲师（隐藏）
    f_title = font(19)
    title = fit_text(score.title, f_title, CELL_W - COVER - 120)
    draw.text((x + COVER + 14, y + 3), title, font=f_title, fill="#333")
    # 底部行：等级徽章 + 定数 + 达成率 + RA
    color = LEVEL_COLORS.get(score.level_index, "#999")
    f_small = font(16, FONT_MONO)
    badge = f"{'DX' if score.type == SongType.DX else 'SD'}{score.level}"
    bw = text_size(badge, f_small)[0]
    draw.rounded_rectangle(
        (x + COVER + 14, y + 24, x + COVER + 20 + bw, y + 40), 5, fill=color
    )
    draw.text((x + COVER + 17, y + 26), badge, font=f_small, fill="#fff")
    draw.text(
        (x + COVER + 28 + bw, y + 26),
        f"{score.level_value:.1f}",
        font=f_small,
        fill="#999",
    )
    f_num = font(17, FONT_MONO)
    ach = f"{score.achievements or 0:.4f}%"
    aw = text_size(ach, f_num)[0]
    draw.text((x + CELL_W - 74 - aw, y + 26), ach, font=f_num, fill="#555")
    ra = str(int(score.dx_rating or 0))
    draw.text((x + CELL_W - 66, y + 25), ra, font=font(18, FONT_NUM), fill="#e6761f")
    # 排名
    rank_text = f"{rank:02d}"
    draw.text(
        (x + CELL_W - 34, y + 2), rank_text, font=font(14, FONT_MONO), fill="#c0c4cc"
    )


def draw_b50(
    player_name: str,
    rating: int,
    rating_b35: int,
    rating_b15: int,
    scores_b35: list[ScoreExtend],
    scores_b15: list[ScoreExtend],
) -> Image.Image:
    """B50 大图：头部（玩家/rating/分侧统计）+ 双列 35/15 成绩。"""
    rows = max(len(scores_b35), len(scores_b15))
    header_h = 108
    body_h = rows * CELL_H + 20
    img = Image.new("RGBA", (CARD_W, header_h + body_h + 16), BG)
    draw = ImageDraw.Draw(img)

    # —— 头部 ——
    draw.rounded_rectangle((16, 14, CARD_W - 16, header_h - 4), 12, fill="#2e323c")
    draw.text(
        (36, 30), fit_text(player_name, font(30), 420), font=font(30), fill="#fff"
    )
    draw.text(
        (36, 70),
        f"B35 {rating_b35}　B15 {rating_b15}",
        font=font(20, FONT_MONO),
        fill="#aab0bd",
    )
    f_rating = font(52, FONT_NUM)
    rating_text = str(rating)
    rw = text_size(rating_text, f_rating)[0]
    draw.text((CARD_W - 60 - rw, 34), rating_text, font=f_rating, fill="#ffcc5c")
    draw.text(
        (CARD_W - 60 - rw, 92 - 26), "RATING", font=font(14, FONT_NUM), fill="#aab0bd"
    )

    # —— 双列成绩 ——
    for col, (scores, start_rank) in enumerate(((scores_b35, 1), (scores_b15, 36))):
        x = 16 + col * (CELL_W + 8)
        y0 = header_h + 4
        for i, score in enumerate(scores):
            _draw_cell(img, draw, x, y0 + i * CELL_H, start_rank + i, score)
    return img


def best50_bytes(
    player_name: str,
    rating: int,
    rating_b35: int,
    rating_b15: int,
    scores_b35: list[ScoreExtend],
    scores_b15: list[ScoreExtend],
) -> bytes:
    return image_to_bytes(
        draw_b50(player_name, rating, rating_b35, rating_b15, scores_b35, scores_b15)
    )


def draw_score_list(
    title: str, scores: list[ScoreExtend], page: int = 1, per_page: int = 25
) -> Image.Image:
    """通用成绩列表（ap50 / 分数列表复用）：25 条/页。"""
    page_data, total = paginate(scores, page, per_page)
    header_h = 64
    row_h = 46
    w = CARD_W
    h = header_h + row_h * max(len(page_data), 1) + 16
    img = Image.new("RGBA", (w, h), BG)
    draw = ImageDraw.Draw(img)
    draw.text(
        (20, 18),
        f"{title}（第 {min(max(page, 1), total)}/{total} 页）",
        font=font(26),
        fill="#333",
    )
    f_small = font(17, FONT_MONO)
    f_title = font(20)
    for i, score in enumerate(page_data):
        y = header_h + i * row_h
        draw.rounded_rectangle((16, y, w - 16, y + row_h - 6), 8, fill="#ffffff")
        color = LEVEL_COLORS.get(score.level_index, "#999")
        draw.rounded_rectangle((28, y + 8, 34, y + row_h - 14), 3, fill=color)
        cover = assets.cover(score.id % 10000).resize((COVER, COVER))
        img.paste(cover, (44, y + 4), rounded_mask(cover.size, 6))
        draw.text(
            (94, y + 5),
            fit_text(score.title, f_title, w - 470),
            font=f_title,
            fill="#333",
        )
        draw.text(
            (94, y + 27),
            f"{score.level}·{score.level_value:.1f}",
            font=f_small,
            fill="#999",
        )
        ach = f"{score.achievements or 0:.4f}%"
        aw = text_size(ach, f_num := font(18, FONT_MONO))[0]
        draw.text((w - 150 - aw, y + 11), ach, font=f_num, fill="#555")
        draw.text(
            (w - 120, y + 12),
            f"RA {int(score.dx_rating or 0)}",
            font=font(18, FONT_NUM),
            fill="#e6761f",
        )
    return img


def score_list_bytes(
    title: str, scores: list[ScoreExtend], page: int = 1, per_page: int = 25
) -> bytes:
    return image_to_bytes(draw_score_list(title, scores, page, per_page))
