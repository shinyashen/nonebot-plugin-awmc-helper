"""分数线计算卡（2026-10-10 新版式，草稿定稿移植）。

双主题（prism_plus 渐变底 / circle 粉底图，best50 同体系）+ 三段白色半透
圆角卡：歌曲信息 / 目标线与总预算 / 判定损失表。数值由
:func:`~nonebot_plugin_awmc_helper.core.calc.score_line` 按专栏口径计算
（等效 GREAT TAP；BREAK 七档含额外分通道折算）。

⚠️ PIL 的 ``ImageDraw`` 在 RGBA 图上直写像素不做 alpha 合成——半透明白
行背景必须走 crop → ``Image.alpha_composite`` → paste（直写会把 RGB 落成
纯白、透明度只进 alpha 通道，聊天客户端白底下合成即变纯白）。
"""

from PIL import Image, ImageDraw, ImageFilter
from maimai_py import SongType, LevelIndex, SongDifficulty

from .fonts import FONT_HAN, FONT_NUM, FONT_RODIN, font
from .tools import (
    TEXT_BLUE,
    CIRCLE_PINK,
    ID_TEXT_COLORS,
    credit_text,
    image_to_bytes,
    truncate_hoshino,
    generate_prism_bg,
)
from .assets import assets
from .nb_chart import paste_version_logo
from ...constants import (
    GENRE_TO_ZH,
    DEFAULT_THEME,
    DIFF_DISPLAY_NAMES,
    chart_display_id,
)

# 行卡文字用深灰（半透白卡上对比稳定，双主题共用）
_DARK = (90, 88, 108, 255)
_GRAY = (120, 118, 138, 255)
_WHITE = (255, 255, 255, 255)
_UTAGE_COLOR = (210, 57, 174, 255)
"""宴谱主题色（对齐宴会谱面卡底图的紫描边取色，双主题共用）。"""
_CARD_ALPHA = 195
# 斑马纹（奇偶行透明度区分，用户定稿 80/120）；合成式叠加透出底图淡彩
_ROW_ALPHA_ODD = 80
_ROW_ALPHA_EVEN = 120

W, H = 1200, 1400


def _theme_style(theme: str) -> dict:
    """主题配色单源：正文/强调色对齐 :func:`theme_text_color`，表头、标题
    描边、分隔线随主题取色（prism 蓝紫系 / circle 粉系）。"""
    if theme == "circle":
        return {
            "accent": CIRCLE_PINK,
            "title_stroke": (214, 31, 130, 255),
            "header": CIRCLE_PINK,
            "divider": (246, 214, 226, 255),
        }
    return {
        "accent": TEXT_BLUE,
        "title_stroke": (159, 141, 250, 255),
        "header": (129, 122, 246, 255),
        "divider": (220, 216, 240, 255),
    }


def _theme_bg(theme: str) -> Image.Image:
    """主题底图：prism_plus 程序渐变（generate_prism_bg）；circle 用主题
    b50.png 大底图（1400×1600 RGB）缩放到画布尺寸。"""
    if theme == "circle":
        im = assets.pic("b50.png", theme).convert("RGBA")
        im = im.resize((W, H), Image.Resampling.LANCZOS)
        # 底图自带的游乐园装饰带（下部 ~1129-1400）会干扰页脚文字：
        # 叠一层向下渐隐的粉白，装饰淡出、署名可读
        fade = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        fade_draw = ImageDraw.Draw(fade)
        for y in range(1120, H):
            alpha = min(235, int((y - 1120) * 235 / (H - 1120)))
            fade_draw.line(((0, y), (W, y)), fill=(255, 235, 245, alpha))
        return Image.alpha_composite(im, fade)
    return generate_prism_bg(H, width=W)


def _card(im: Image.Image, box: tuple[int, int, int, int]) -> None:
    """白色半透明圆角卡 + 柔和投影。"""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    shadow = Image.new("RGBA", (w + 40, h + 40), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (20, 20, 20 + w, 20 + h), 30, fill=(80, 60, 120, 70)
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(8))
    im.alpha_composite(shadow, (x0 - 20 + 6, y0 - 20 + 10))
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle(
        (0, 0, w - 1, h - 1), 30, fill=(255, 255, 255, _CARD_ALPHA)
    )
    im.alpha_composite(layer, (x0, y0))


def _row_fill(im: Image.Image, box: tuple[int, int, int, int], alpha: int) -> None:
    """半透明白行背景（合成式，见模块 docstring 的直写坑）。"""
    region = im.crop(box)
    overlay = Image.new("RGBA", region.size, (255, 255, 255, alpha))
    im.paste(Image.alpha_composite(region, overlay), box[:2])


def _table(
    im: Image.Image,
    box: tuple[int, int, int, int],
    head_x: int,
    col_xs: list[int],
    header: list[str],
    rows: list[list[str]],
    row_alphas: list[int],
    row_h: int = 64,
    header_size: int = 24,
    body_size: int = 26,
    header_fill: tuple = (129, 122, 246, 255),
) -> int:
    """主题色头表格：head_x 行头列左对齐、col_xs 数值列居中（均相对 box 左缘）。

    返回结束 y。行高 ``row_h``，逐行底色取 ``row_alphas`` 循环。"""
    x0, y0, w, _ = box
    hh = 56
    head_h = hh + 12
    draw = ImageDraw.Draw(im)
    draw.rounded_rectangle((x0, y0, x0 + w, y0 + head_h), 16, fill=header_fill)
    draw.rectangle((x0, y0 + hh - 14, x0 + w, y0 + head_h), fill=header_fill)
    draw.text(
        (x0 + head_x, y0 + head_h // 2),
        header[0],
        font=font(header_size),
        fill=_WHITE,
        anchor="lm",
    )
    for cx, text in zip(col_xs, header[1:]):
        draw.text(
            (x0 + cx, y0 + head_h // 2),
            text,
            font=font(header_size),
            fill=_WHITE,
            anchor="mm",
        )
    y = y0 + head_h
    for i, row in enumerate(rows):
        _row_fill(im, (x0, y, x0 + w, y + row_h), row_alphas[i % len(row_alphas)])
        draw.text(
            (x0 + head_x, y + row_h // 2),
            row[0],
            font=font(body_size),
            fill=_DARK,
            anchor="lm",
        )
        for cx, text in zip(col_xs, row[1:]):
            draw.text(
                (x0 + cx, y + row_h // 2),
                text,
                font=font(body_size, FONT_NUM),
                fill=_DARK,
                anchor="mm",
            )
        y += row_h
    return y


def score_line_card(
    song,
    diff: SongDifficulty,
    line: float,
    result: dict,
    *,
    theme: str = DEFAULT_THEME,
    jp: bool = False,
) -> bytes:
    """分数线计算卡。

    ``song`` / ``diff``：曲对象与命中谱面；``line``：目标达成率；
    ``result``：:func:`~nonebot_plugin_awmc_helper.core.calc.score_line`
    的返回；``jp=True`` 版本 logo 走日服世代图（与查歌卡同口径）。
    """
    notes = (
        diff.tap_num,
        diff.hold_num,
        diff.slide_num,
        diff.touch_num,
        diff.break_num,
    )
    li = diff.level_index
    style = _theme_style(theme)
    im = _theme_bg(theme)
    draw = ImageDraw.Draw(im)

    # 标题行：logo 左上 + 主题描边大标题（prism 紫描边 / circle 白描边）
    im.alpha_composite(assets.pic("logo.png", theme).resize((249, 120)), (40, 24))
    draw.text(
        (600, 84),
        "分数线计算",
        font=font(52, FONT_HAN),
        fill=_WHITE,
        anchor="mm",
        stroke_width=4,
        stroke_fill=style["title_stroke"],
    )

    # ---- 歌曲信息卡 ------------------------------------------------------
    _card(im, (60, 160, 1140, 470))
    im.alpha_composite(assets.cover(song.id).resize((242, 242)), (100, 194))
    draw.text(
        (390, 214),
        truncate_hoshino(song.title, 15),
        font=font(30, FONT_RODIN),
        fill=_DARK,
        anchor="lm",
    )
    draw.text(
        (392, 264),
        truncate_hoshino(song.artist, 21),
        font=font(21),
        fill=_GRAY,
        anchor="lm",
    )
    draw.text(
        (392, 370),
        f"BPM {song.bpm if song.bpm else '-'}",
        font=font(23, FONT_NUM),
        fill=_DARK,
        anchor="lm",
    )
    # 分类行用中文字体（Torus 无 CJK 字形，东方Project 等会缺字）
    genre = GENRE_TO_ZH.get(song.genre) or song.genre.value or "-"
    draw.text((530, 370), genre, font=font(21), fill=_GRAY, anchor="lm")
    draw.text(
        (392, 408),
        f"ID {chart_display_id(song, diff)}",
        font=font(22, FONT_NUM),
        fill=_DARK,
        anchor="lm",
    )
    is_utage = diff.type == SongType.UTAGE
    if is_utage:
        # 宴谱徽章：药丸形（两头整半圆）白边 + 宴色填充 + 宴汉字（日文字体，
        # 对齐宴会卡的紫描边配色），替代 DX/SD 圆标的位置
        kanji = getattr(diff, "kanji", "") or "宴"
        draw.rounded_rectangle(
            (530, 396, 610, 426),
            radius=15,
            fill=_UTAGE_COLOR,
            outline=_WHITE,
            width=2,
        )
        draw.text(
            (570, 411),
            kanji,
            font=font(20 if len(kanji) == 1 else 15, FONT_RODIN),
            fill=_WHITE,
            anchor="mm",
        )
        draw.text((624, 408), "宴谱面", font=font(18), fill=_GRAY, anchor="lm")
    else:
        type_abbr = "DX" if diff.type == SongType.DX else "SD"
        if badge := assets.type_badge(type_abbr, (80, 30)):
            im.alpha_composite(badge, (530, 408 - 12))
        draw.text(
            (624, 408), f"{type_abbr} 谱面", font=font(18), fill=_GRAY, anchor="lm"
        )

    # 难度徽章（底边 = 封面底缘 436）：宴谱用宴色；Re:Master 浅紫底深紫字
    # （对齐歌曲行卡 b50_score_remaster 配色）；其余难度彩底白字
    if is_utage:
        badge_fill = _UTAGE_COLOR
        badge_text = _WHITE
        # 日文字体无简体「场」，用官方日文写法「宴会場」
        diff_name, lv_text = "宴会場", f"Lv {diff.level}"
    elif li == LevelIndex.ReMASTER:
        badge_fill = (230, 197, 255, 255)
        badge_text = ID_TEXT_COLORS[li.value]
        diff_name, lv_text = DIFF_DISPLAY_NAMES[li.value], f"Lv {diff.level_value:.1f}"
    else:
        badge_fill = ID_TEXT_COLORS[li.value]
        badge_text = _WHITE
        diff_name, lv_text = DIFF_DISPLAY_NAMES[li.value], f"Lv {diff.level_value:.1f}"
    bx0, by0, bx1, by1 = 830, 336, 1090, 436
    draw.rounded_rectangle((bx0, by0, bx1, by1), 22, fill=badge_fill)
    bcx = (bx0 + bx1) // 2
    draw.text(
        (bcx, by0 + 32),
        diff_name,
        font=font(26, FONT_RODIN),
        fill=badge_text,
        anchor="mm",
    )
    draw.text(
        (bcx, by0 + 72),
        lv_text,
        font=font(26, FONT_NUM),
        fill=badge_text,
        anchor="mm",
    )

    # 版本 logo：与难度徽章水平居中对齐（中心 x=960）
    paste_version_logo(im, diff.version or song.version, (840, 204, 240, 104), jp=jp)

    # ---- 目标线卡 --------------------------------------------------------
    _card(im, (60, 490, 1140, 710))
    rank_img = assets.rate_badge_of_achievement(line, theme, size=(204, 96))
    if rank_img is not None:
        im.alpha_composite(rank_img, (110, 586 - 48))
    draw.text((360, 528), "目标达成率", font=font(22), fill=_GRAY, anchor="lm")
    f_big = font(54, FONT_RODIN)
    pct_text = f"{line:.4f} %"
    draw.text((360, 586), pct_text, font=f_big, fill=_DARK, anchor="lm")
    # 分隔线按数字实测宽度定位（与 % 保持间距，不随位数变化贴字）
    line_x = round(360 + draw.textlength(pct_text, font=f_big)) + 34
    draw.line((line_x, 528, line_x, 624), fill=style["divider"], width=3)
    draw.text(
        (line_x + 20, 528),
        "允许损失（等效 GREAT TAP 数）",
        font=font(19),
        fill=_GRAY,
        anchor="lm",
    )
    draw.text(
        (line_x + 20, 582),
        f"约 {result['budget']:.2f} 个",
        font=font(48),
        fill=CIRCLE_PINK if theme == "circle" else (249, 62, 172, 255),
        anchor="lm",
    )
    draw.text(
        (360, 656),
        f"TAP {notes[0]} · HOLD {notes[1]} · SLIDE {notes[2]} · TOUCH {notes[3]}"
        f" · BREAK {notes[4]}　(基础分 {result['total_basic']:,})",
        font=font(20),
        fill=_GRAY,
        anchor="lm",
    )
    if result.get("buddy"):
        # 双人宴谱：上限 202、物量为左右机台合计（用户要求的提醒口径）
        draw.text(
            (360, 684),
            "双人宴谱：达成率上限 202%（200 基础 + 2 额外）；物量为左右机台合计",
            font=font(18),
            fill=_UTAGE_COLOR,
            anchor="lm",
        )

    # ---- 判定损失表卡 ----------------------------------------------------
    _card(im, (60, 730, 1140, 1290))
    draw.text(
        (100, 772),
        "各判定损失的等效 GREAT TAP 数",
        font=font(26, FONT_HAN),
        fill=style["accent"],
        anchor="lm",
    )
    from ..calc import NOTE_JUDGES

    end_a = _table(
        im,
        (100, 806, 1000, 0),
        head_x=40,
        col_xs=[420, 640, 860],
        header=["判定", "GREAT", "GOOD", "MISS"],
        rows=[
            [name, f"{v[0]:.1f}", f"{v[1]:.1f}", f"{v[2]:.1f}"]
            for name, v in NOTE_JUDGES
        ],
        row_alphas=[_ROW_ALPHA_ODD, _ROW_ALPHA_EVEN, _ROW_ALPHA_ODD],
        header_fill=style["header"],
    )
    _table(
        im,
        (100, end_a + 24, 1000, 0),
        head_x=40,
        col_xs=[230, 343, 457, 570, 683, 797, 910],
        header=["BREAK 判定", *[name for name, _ in result["break_rows"]]],
        rows=[["等效数", *[f"{v:.2f}" for _, v in result["break_rows"]]]],
        row_alphas=[_ROW_ALPHA_ODD],
        header_size=22,
        body_size=24,
        header_fill=style["header"],
    )

    # ---- 底部说明 + 署名 -------------------------------------------------
    draw.text(
        (600, 1252),
        # 单行渲染（隐式拼接不含 \n；ruff E501 按物理行不超限）
        "※ 1 等效 GREAT TAP = 1 个 TAP 从 Critical Perfect "
        "掉到 GREAT 的损失 (100 基础分)；BREAK 各档含基础分档差与 CP 额外分折算",
        font=font(17),
        fill=_GRAY,
        anchor="mm",
    )
    draw.text(
        (600, 1340),
        credit_text(),
        font=font(20, FONT_RODIN),
        fill=style["accent"],
        anchor="mm",
        stroke_width=3,
        stroke_fill=_WHITE,
    )
    return image_to_bytes(im)
