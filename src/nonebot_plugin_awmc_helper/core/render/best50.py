"""B50 成绩图（NB 版视觉移植：nonebot-plugin-maimaidx core/image/best50.py + base.py）。

- 主题底图 ``{theme}/b50.png``（1400×1600），prism_plus/circle 双主题；
- 头部：logo、牌子（水鱼 plate 字符串 → plate_version 素材；无则默认 550101）、
  DXRating 段位徽章、Drating 数字图、玩家名、B35/B15 统计称号条；
- 成绩行：b35 35 条 + b15 15 条，5 列布局，行卡 ``b50_score_{难度}.png``，
  含曲绘/类型/评级徽章/FC-FS 图标/DX 星/定数→RA。
"""

from PIL import Image, ImageDraw
from maimai_py import FCType, FSType, RateType, LevelIndex, ScoreExtend

from .fonts import FONT_HAN, FONT_NUM, FONT_MONO, font
from .tools import image_to_bytes
from .assets import assets
from ...config import NICKNAME
from ...constants import SYNC_FILE, COMBO_FILE

RA_THRESHOLD = [
    (1000, "01"),
    (2000, "02"),
    (4000, "03"),
    (7000, "04"),
    (10000, "05"),
    (12000, "06"),
    (13000, "07"),
    (14000, "08"),
    (14500, "09"),
    (15000, "10"),
]

DX_STAR_FILE = "UI_GAM_Gauge_DXScoreIcon_0{num}.png"

RATE_FILE = {
    "SSSP": "SSSp",
    "SSS": "SSS",
    "SSP": "SSp",
    "SS": "SS",
    "SP": "Sp",
    "S": "S",
    "AAA": "AAA",
    "AA": "AA",
    "A": "A",
    "BBB": "BBB",
    "BB": "BB",
    "B": "B",
    "C": "C",
    "D": "D",
}

DIFF_BG = {
    LevelIndex.BASIC: "b50_score_basic.png",
    LevelIndex.ADVANCED: "b50_score_advanced.png",
    LevelIndex.EXPERT: "b50_score_expert.png",
    LevelIndex.MASTER: "b50_score_master.png",
    LevelIndex.ReMASTER: "b50_score_remaster.png",
}


def _dx_star(dx_score: int | None, level_dx_score: int) -> int:
    """DX 百分比 → 星数 0-5（maimai-py 同款阈值）。"""
    if not dx_score or level_dx_score <= 0:
        return 0
    pct = dx_score / level_dx_score * 100
    if pct <= 85:
        return 0
    if pct <= 90:
        return 1
    if pct <= 93:
        return 2
    if pct <= 95:
        return 3
    if pct <= 97:
        return 4
    return 5


def _ra_badge(theme: str, rating: int) -> Image.Image:
    """按 Rating 取 DXRating 段位徽章。"""
    num = "11"
    for limit, n in RA_THRESHOLD:
        if rating < limit:
            num = n
            break
    path = assets.static_path() / "mai" / "pic" / theme / f"UI_CMN_DXRating_{num}.png"
    return Image.open(path).convert("RGBA")


def _rate_badge(theme: str, rate: RateType | None) -> Image.Image | None:
    if rate is None:
        return None
    name = RATE_FILE.get(rate.name, rate.name)
    path = assets.static_path() / "mai" / "pic" / theme / f"UI_TTR_Rank_{name}.png"
    if not path.exists():
        return None
    return Image.open(path).convert("RGBA")


def _combo_icon(fc: FCType | None) -> Image.Image | None:
    if fc is None:
        return None
    name = COMBO_FILE.get(fc.name)
    path = assets.static_path() / "mai" / "pic" / f"UI_MSS_MBase_Icon_{name}.png"
    return Image.open(path).convert("RGBA") if path.exists() else None


def _sync_icon(fs: FSType | None) -> Image.Image | None:
    if fs is None:
        return None
    name = SYNC_FILE.get(fs.name)
    path = assets.static_path() / "mai" / "pic" / f"UI_MSS_MBase_Icon_{name}.png"
    return Image.open(path).convert("RGBA") if path.exists() else None


def _dx_star_badge(theme: str, star: int) -> Image.Image | None:
    if star <= 0:
        return None
    path = assets.static_path() / "mai" / "pic" / theme / DX_STAR_FILE.format(num=star)
    if not path.exists():
        path = assets.static_path() / "mai" / "pic" / DX_STAR_FILE.format(num=star)
    if not path.exists():
        return None
    return Image.open(path).convert("RGBA")


def _draw_row(
    im: Image.Image,
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    score: ScoreExtend,
    theme: str,
) -> None:
    im.alpha_composite(
        Image.open(
            assets.static_path() / "mai" / "pic" / DIFF_BG[score.level_index]
        ).convert("RGBA"),
        (x, y),
    )
    cover = assets.cover(score.id % 10000).resize((75, 75))
    im.alpha_composite(cover, (x + 12, y + 12))
    type_abbr = "DX" if score.type.name == "DX" else "SD"
    type_path = assets.static_path() / "mai" / "pic" / f"{type_abbr}.png"
    if type_path.exists():
        im.alpha_composite(
            Image.open(type_path).convert("RGBA").resize((37, 14)), (x + 51, y + 91)
        )
    rate = _rate_badge(theme, score.rate)
    if rate is not None:
        im.alpha_composite(rate.resize((63, 28)), (x + 92, y + 78))
    fc = _combo_icon(score.fc)
    if fc is not None:
        im.alpha_composite(fc.resize((34, 34)), (x + 154, y + 77))
    fs = _sync_icon(score.fs)
    if fs is not None:
        im.alpha_composite(fs.resize((34, 34)), (x + 185, y + 77))
    star = _dx_star_badge(theme, _dx_star(score.dx_score, score.level_dx_score))
    if star is not None:
        im.alpha_composite(star.resize((47, 26)), (x + 217, y + 80))

    draw.text(
        (x + 26, y + 98),
        str(score.id),
        font=font(13, FONT_MONO),
        fill="#3c3c3c",
        anchor="mm",
    )
    draw.text(
        (x + 93, y + 14),
        score.title[:24],
        font=font(14, FONT_HAN),
        fill="#4a4a4a",
        anchor="lm",
    )
    draw.text(
        (x + 93, y + 38),
        f"{score.achievements or 0:.4f}%",
        font=font(30, FONT_NUM),
        fill="#4a4a4a",
        anchor="lm",
    )
    draw.text(
        (x + 219, y + 65),
        f"{score.dx_score or 0}/{score.level_dx_score}",
        font=font(15, FONT_MONO),
        fill="#4a4a4a",
        anchor="mm",
    )
    draw.text(
        (x + 93, y + 65),
        f"{score.level_value:.1f} → {int(score.dx_rating or 0)}",
        font=font(15, FONT_MONO),
        fill="#4a4a4a",
        anchor="lm",
    )


def draw_b50_nb(
    player_name: str,
    rating: int,
    rating_b35: int,
    rating_b15: int,
    scores_b35: list[ScoreExtend],
    scores_b15: list[ScoreExtend],
    theme: str = "prism_plus",
    plate: str | None = None,
) -> Image.Image:
    """NB 版 B50 大图。"""
    pic = assets.static_path() / "mai" / "pic"
    im = Image.open(pic / theme / "b50.png").convert("RGBA")
    draw = ImageDraw.Draw(im)

    im.alpha_composite(
        Image.open(pic / theme / "logo.png").convert("RGBA").resize((249, 120)),
        (14, 60),
    )
    plate_path = None
    if plate:
        candidate = assets.static_path() / "mai" / "plate_version" / f"{plate}.png"
        if candidate.exists():
            plate_path = candidate
    if plate_path is None:
        plate_path = pic / "UI_Plate_550101.png"
    im.alpha_composite(
        Image.open(plate_path).convert("RGBA").resize((800, 130)), (300, 60)
    )
    im.alpha_composite(_ra_badge(theme, rating).resize((186, 35)), (435, 72))
    for n, digit in enumerate(f"{rating:05d}"):
        digit_path = pic / f"UI_NUM_Drating_{digit}.png"
        im.alpha_composite(
            Image.open(digit_path).convert("RGBA").resize((17, 20)),
            (520 + 15 * n, 80),
        )
    name_path = pic / "Name.png"
    if name_path.exists():
        im.alpha_composite(Image.open(name_path).convert("RGBA"), (435, 115))
    draw.text(
        (445, 135),
        player_name[:16],
        font=font(20, FONT_HAN),
        fill="#000000",
        anchor="lm",
    )
    shougou = assets.static_path() / "mai" / "shougou" / "UI_CMN_Shougou_Rainbow.png"
    if shougou.exists():
        im.alpha_composite(
            Image.open(shougou).convert("RGBA").resize((270, 27)), (435, 160)
        )
    draw.text(
        (570, 172),
        f"B35: {rating_b35} + B15: {rating_b15} = {rating}",
        font=font(14, FONT_MONO),
        fill="#000000",
        anchor="mm",
    )

    # 成绩行：b35 从 y=235、b15 从 y=1085，均 5 列、行距 114（NB 版布局）
    for data, initial_y in ((scores_b35, 235), (scores_b15, 1085)):
        for num, score in enumerate(data):
            row, col = divmod(num, 5)
            x = 16 + col * 276
            y = initial_y + row * 114
            _draw_row(im, draw, x, y, score, theme)

    draw.text(
        (700, 1570),
        "Designed by Yuri-YuzuChaN & BlueDeer233. "
        f"Generated by {NICKNAME or 'awmc-helper'} BOT",
        font=font(22, FONT_HAN),
        fill="#888888",
        anchor="mm",
    )
    return im


def best50_bytes(
    player_name: str,
    rating: int,
    rating_b35: int,
    rating_b15: int,
    scores_b35: list[ScoreExtend],
    scores_b15: list[ScoreExtend],
    theme: str = "prism_plus",
    plate: str | None = None,
) -> bytes:
    return image_to_bytes(
        draw_b50_nb(
            player_name,
            rating,
            rating_b35,
            rating_b15,
            scores_b35,
            scores_b15,
            theme,
            plate,
        )
    )


def score_list_bytes(
    title: str, scores: list[ScoreExtend], page: int = 1, per_page: int = 25
) -> bytes:
    """通用成绩列表（ap50 / 分数列表复用）：25 条/页，行卡同 NB 风格。"""
    from ..utils import paginate

    page_data, total = paginate(scores, page, per_page)
    header_h = 64
    row_h = 46
    w = 840
    h = header_h + row_h * max(len(page_data), 1) + 16
    im = Image.new("RGBA", (w, h), "#f5f6f8")
    draw = ImageDraw.Draw(im)
    real = min(max(page, 1), total)
    draw.text(
        (20, 18), f"{title}（第 {real}/{total} 页）", font=font(26), fill="#333333"
    )
    f_small = font(17, FONT_MONO)
    f_title = font(20)
    for i, score in enumerate(page_data):
        y = header_h + i * row_h
        draw.rounded_rectangle((16, y, w - 16, y + row_h - 6), 8, fill="#ffffff")
        cover = assets.cover(score.id % 10000).resize((38, 38))
        im.alpha_composite(cover, (24, y + 3))
        draw.text(
            (72, y + 5),
            score.title[:28],
            font=f_title,
            fill="#333333",
        )
        draw.text(
            (72, y + 26),
            f"{score.level}·{score.level_value:.1f}",
            font=f_small,
            fill="#999999",
        )
        ach = f"{score.achievements or 0:.4f}%"
        draw.text((w - 320, y + 11), ach, font=font(18, FONT_MONO), fill="#555555")
        draw.text(
            (w - 150, y + 12),
            f"RA {int(score.dx_rating or 0)}",
            font=font(18, FONT_NUM),
            fill="#e6761f",
        )
    return image_to_bytes(im)
