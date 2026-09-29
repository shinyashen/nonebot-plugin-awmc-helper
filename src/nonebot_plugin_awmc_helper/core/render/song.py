"""曲目相关绘图：谱面信息卡、搜索结果列表、随机谱面结果。

搜索列表图为 Hoshino core/image/song.py::song_list 版式移植：PRiSM 装饰底 +
毛玻璃卡 + 两列曲卡网格（``song_card.png`` 底、80×80 曲绘、版本 logo、
SD/DX 徽章、难度条定数、BPM/分类行）。每页条数沿用本项目 25/页口径
（Hoshino PAGE_SIZE=14 属其翻页 UX，非坐标），网格几何随条数自适应。
"""

from PIL import Image, ImageDraw
from maimai_py import Song, SongType, LevelIndex

from .fonts import FONT_HAN, FONT_MONO, FONT_RODIN, font
from .tools import (
    TEXT_BLUE,
    DIFF_TEXT_COLORS,
    fit_text,
    text_size,
    credit_text,
    rounded_mask,
    image_to_bytes,
    truncate_hoshino,
    generate_frosted_card,
    tricolor_gradient_prism_plus,
)
from ..utils import paginate
from .assets import assets
from .nb_chart import paste_version_logo
from ...constants import GENRE_TO_ZH, version_zh, display_song_id

LEVEL_COLORS = {
    LevelIndex.BASIC: "#22bb5b",
    LevelIndex.ADVANCED: "#fb9a0f",
    LevelIndex.EXPERT: "#f64861",
    LevelIndex.MASTER: "#a45eeb",
    LevelIndex.ReMASTER: "#ba5cf5",
}

CARD_BG = "#f2f3f5"
CARD_W = 720


def draw_song_card(song: Song, id_override: int | None = None) -> Image.Image:
    """绘制谱面信息卡：曲绘 + 基本信息 + 各难度谱面数据。

    ``id_override``：谱面级展示 id（查分器 id 形状）。曲级卡不传，显示根 id；
    代表具体谱面的卡（随机谱面等）应传 :func:`chart_display_id` 的结果。
    """
    diffs = [
        d for d in song.get_difficulties() if d.type in (SongType.STANDARD, SongType.DX)
    ]
    diffs.sort(key=lambda d: (d.type != SongType.STANDARD, d.level_index.value))
    utage = song.get_difficulties(SongType.UTAGE)

    cover_size = 200
    row_h = 42
    header_h = cover_size + 40
    body_h = row_h * (len(diffs) + len(utage)) + 24 + (30 if utage else 0)
    card_h = header_h + body_h + 16

    img = Image.new("RGBA", (CARD_W, card_h), CARD_BG)
    draw = ImageDraw.Draw(img)

    # —— 头部：曲绘 + 标题信息 ——
    cover = assets.cover(song.id).resize((cover_size, cover_size))
    cover = Image.composite(
        cover, Image.new("RGBA", cover.size, CARD_BG), rounded_mask(cover.size, 12)
    )
    img.paste(cover, (20, 20), cover)

    f_title = font(30)
    f_text = font(24)
    f_small = font(20)
    x = cover_size + 40
    y = 26
    draw.text(
        (x, y),
        f"{song.id if id_override is None else id_override}",
        font=font(26, FONT_MONO),
        fill="#8a8f99",
    )
    y += 40
    draw.text(
        (x, y),
        fit_text(song.title, f_title, CARD_W - x - 24),
        font=f_title,
        fill="#333",
    )
    y += 44
    draw.text(
        (x, y),
        fit_text(f"曲师：{song.artist}", f_text, CARD_W - x - 24),
        font=f_text,
        fill="#666",
    )
    y += 36
    genre = GENRE_TO_ZH.get(song.genre, song.genre.value)
    bpm_text = f"分类：{genre}　BPM：{song.bpm}　版本：{version_zh(song.version)}"
    draw.text(
        (x, y), fit_text(bpm_text, f_small, CARD_W - x - 24), font=f_small, fill="#666"
    )

    # —— 难度行 ——
    f_lvl = font(24, FONT_MONO)
    y = header_h
    for diff in diffs:
        color = LEVEL_COLORS.get(diff.level_index, "#999")
        draw.rounded_rectangle((20, y, 132, y + row_h - 8), 8, fill=color)
        type_abbr = "DX" if diff.type == SongType.DX else "SD"
        lvl_name = f"{type_abbr} {diff.level}"
        lvl_w = text_size(lvl_name, f_lvl)[0]
        draw.text((20 + (112 - lvl_w) // 2, y + 5), lvl_name, font=f_lvl, fill="#fff")
        info = (
            f"{diff.level_value:.1f}　"
            f"TAP {diff.tap_num}　HOLD {diff.hold_num}　"
            f"SLIDE {diff.slide_num}　TOUCH {diff.touch_num}　BRK {diff.break_num}　"
            # 谱师缺省（真实数据 SD BASIC/ADVANCED 常无谱师）按「0 即 -」约定画 -
            f"谱师 {fit_text(diff.note_designer or '-', f_small, 150)}"
        )
        draw.text((152, y + 4), info, font=f_small, fill="#555")
        y += row_h
    for diff in utage:
        draw.rounded_rectangle((20, y, 132, y + row_h - 8), 8, fill="#c79b5f")
        lvl_name = f"宴 {diff.level}"
        lvl_w = text_size(lvl_name, f_lvl)[0]
        draw.text((20 + (112 - lvl_w) // 2, y + 5), lvl_name, font=f_lvl, fill="#fff")
        kanji = getattr(diff, "kanji", "")
        desc = fit_text(getattr(diff, "description", ""), f_small, 300)
        draw.text((152, y + 4), f"[{kanji}] {desc}", font=f_small, fill="#555")
        y += row_h

    return img


def draw_song_list(songs: list[Song], page: int = 1, per_page: int = 25) -> Image.Image:
    """搜索结果列表图（Hoshino song_list 版式，默认 25 条/页）。"""
    page_data, total = paginate(songs, page, per_page)
    if not page_data:  # 页码越界回落末页（Hoshino clamp 同语义）
        page = total
        page_data = songs[(page - 1) * per_page : page * per_page]

    lines = -(-len(page_data) // 2)  # 两列行数（Hoshino sum(divmod(n,2)) 同值）
    height = 200 + lines * 145 + 200

    im = tricolor_gradient_prism_plus(1000, height)
    im.alpha_composite(assets.pic("aurora.png").resize((1000, 174)))
    im.alpha_composite(assets.pic("bg_shines.png").resize((1000, 442)))
    pattern = assets.pic("pattern.png").resize((1000, 256))
    for h in range(height // 256 + 1):
        im.alpha_composite(pattern, (0, (256 + 6) * h))
    im.alpha_composite(
        assets.pic("rainbow.png").resize((550, 288)), (225, height - 435)
    )
    im.alpha_composite(
        assets.pic("rainbow_bottom.png").resize((786, 164)), (107, height - 260)
    )

    im = generate_frosted_card(im, (50, 150, 950, 150 + lines * 145 + 100), alpha=0.2)
    im.alpha_composite(
        assets.pic("chara_left.png", "prism_plus").resize((156, 187)), (800, 0)
    )
    im.alpha_composite(assets.pic("moon.png").resize((120, 120)), (60, 20))
    if (logo := assets.pic_optional("maimai でらっくす PRiSM PLUS.png")) is not None:
        im.alpha_composite(logo.resize((210, 101)), (15, 20))
    draw = ImageDraw.Draw(im)

    x_gap, y_gap, start_x, start_y = 450, 145, 70, 200
    for num, song in enumerate(page_data):
        row, col = divmod(num, 2)
        x = start_x + col * x_gap
        y = start_y + row * y_gap

        im.alpha_composite(assets.pic("song_card.png"), (x, y))
        im.alpha_composite(assets.cover(song.id).resize((80, 80)), (x + 10, y + 10))
        paste_version_logo(im, song.version, (x + 315, y - 30, 104, 50))
        utage = song.get_difficulties(SongType.UTAGE)
        is_utage = bool(utage) and not (
            song.difficulties.standard or song.difficulties.dx
        )
        if not is_utage:
            type_abbr = "DX" if song.difficulties.dx else "SD"
            if badge := assets.type_badge(type_abbr, (40, 15)):
                im.alpha_composite(badge, (x + 50, y + 75))
        im.alpha_composite(
            assets.pic("sl_diff_utg.png" if is_utage else "sl_diff.png"),
            (x + 100, y + 95),
        )
        draw.text(
            (x + 50, y + 105),
            str(display_song_id(song)),
            font=font(15, FONT_RODIN),
            fill=TEXT_BLUE,
            anchor="mm",
        )
        title = truncate_hoshino(song.title, 20)
        draw.text(
            (x + 100, y + 25),
            title,
            font=font(20, FONT_RODIN),
            fill=TEXT_BLUE,
            anchor="lm",
        )
        artist = truncate_hoshino(song.artist, 26)
        draw.text(
            (x + 100, y + 50),
            artist,
            font=font(12, FONT_RODIN),
            fill=TEXT_BLUE,
            anchor="lm",
        )
        draw.text(
            (x + 100, y + 80),
            f"BPM: {song.bpm}",
            font=font(15, FONT_RODIN),
            fill=TEXT_BLUE,
            anchor="lm",
        )
        draw.text(
            (x + 230, y + 80),
            GENRE_TO_ZH.get(song.genre, song.genre.value),
            font=font(12, FONT_HAN),
            fill=TEXT_BLUE,
            anchor="lm",
        )
        if is_utage:
            draw.text(
                (x + 125, y + 105),
                f"{utage[0].level_value}",
                font=font(15, FONT_RODIN),
                fill=(255, 255, 255, 255),
                anchor="mm",
            )
        else:
            major = SongType.DX if song.difficulties.dx else SongType.STANDARD
            for diff in song.get_difficulties():
                if diff.type != major or diff.type == SongType.UTAGE:
                    continue
                # 难度配色单源 tools.DIFF_TEXT_COLORS（0-3 白、ReM 紫）
                color = DIFF_TEXT_COLORS[diff.level_index.value]
                draw.text(
                    (x + 125 + 50 * diff.level_index.value, y + 105),
                    f"{diff.level_value}",
                    font=font(15, FONT_RODIN),
                    fill=color,
                    anchor="mm",
                )

    draw.text(
        (500, 70),
        "曲目列表",
        font=font(55, FONT_RODIN),
        fill=TEXT_BLUE,
        anchor="mm",
        stroke_width=3,
        stroke_fill=(255, 255, 255, 255),
    )
    draw.text(
        (500, height - 100),
        f"Page {page}/{total}",
        font=font(35, FONT_RODIN),
        fill=TEXT_BLUE,
        anchor="mm",
        stroke_width=3,
        stroke_fill=(255, 255, 255, 255),
    )
    draw.text(
        (500, height - 30),
        credit_text(),
        font=font(18, FONT_RODIN),
        fill=TEXT_BLUE,
        anchor="mm",
        stroke_width=3,
        stroke_fill=(255, 255, 255, 255),
    )
    return im


def song_card_bytes(song: Song) -> bytes:
    return image_to_bytes(draw_song_card(song))


def song_list_bytes(songs: list[Song], page: int = 1, per_page: int = 25) -> bytes:
    return image_to_bytes(draw_song_list(songs, page, per_page))
