"""曲目相关绘图：谱面信息卡、搜索结果列表、随机谱面结果。"""

from PIL import Image, ImageDraw
from maimai_py import Song, SongType, LevelIndex, SongDifficulty

from .fonts import FONT_MONO, font
from .tools import fit_text, text_size, rounded_mask, image_to_bytes
from ..utils import paginate
from .assets import assets
from ...constants import GENRE_TO_ZH, version_zh

LEVEL_COLORS = {
    LevelIndex.BASIC: "#22bb5b",
    LevelIndex.ADVANCED: "#fb9a0f",
    LevelIndex.EXPERT: "#f64861",
    LevelIndex.MASTER: "#a45eeb",
    LevelIndex.ReMASTER: "#ba5cf5",
}

CARD_BG = "#f2f3f5"
CARD_W = 720


def draw_song_card(song: Song) -> Image:
    """绘制谱面信息卡：曲绘 + 基本信息 + 各难度谱面数据。"""
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
    draw.text((x, y), f"{song.id}", font=font(26, FONT_MONO), fill="#8a8f99")
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
            f"谱师 {fit_text(diff.note_designer, f_small, 150)}"
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


def draw_song_list(songs: list[Song], page: int = 1, per_page: int = 25) -> Image:
    """搜索结果列表图（默认 25 条/页）。"""
    page_data, total = paginate(songs, page, per_page)

    row_h = 44
    header_h = 64
    w = 860
    h = header_h + row_h * max(len(page_data), 1) + 48
    img = Image.new("RGBA", (w, h), CARD_BG)
    draw = ImageDraw.Draw(img)
    draw.text(
        (20, 18),
        f"共 {len(songs)} 个结果，第 {page}/{total} 页",
        font=font(26),
        fill="#333",
    )

    f_id = font(24, FONT_MONO)
    f_title = font(26)
    f_artist = font(20)
    y = header_h
    for song in page_data:
        draw.rounded_rectangle((20, y, w - 20, y + row_h - 8), 8, fill="#ffffff")
        draw.text((36, y + 7), str(song.id), font=f_id, fill="#e2641f")
        draw.text(
            (110, y + 5),
            fit_text(song.title, f_title, w - 480),
            font=f_title,
            fill="#333",
        )
        draw.text(
            (w - 350, y + 10),
            fit_text(song.artist, f_artist, 320),
            font=f_artist,
            fill="#888",
        )
        y += row_h
    return img


def song_card_bytes(song: Song) -> bytes:
    return image_to_bytes(draw_song_card(song))


def song_list_bytes(songs: list[Song], page: int = 1, per_page: int = 25) -> bytes:
    return image_to_bytes(draw_song_list(songs, page, per_page))


def random_song_bytes(song: Song, diff: SongDifficulty) -> bytes:
    """随机谱面结果图：难度徽章 + 谱面信息卡。"""
    from PIL import ImageDraw

    badge_h = 74
    card = draw_song_card(song)
    img = Image.new("RGBA", (CARD_W, badge_h + card.size[1]), CARD_BG)
    draw = ImageDraw.Draw(img)
    color = LEVEL_COLORS.get(diff.level_index, "#c79b5f")
    draw.rounded_rectangle((20, 10, 170, 64), 10, fill=color)
    f = font(28, FONT_MONO)
    label = (
        f"宴 {diff.level}"
        if diff.type == SongType.UTAGE
        else f"{'DX' if diff.type == SongType.DX else 'SD'} {diff.level}"
    )
    tw = text_size(label, f)[0]
    draw.text((20 + (150 - tw) // 2, 19), label, font=f, fill="#fff")
    card_partial = card.crop((0, 0, CARD_W, card.size[1]))
    img.paste(card_partial, (0, badge_h))
    return image_to_bytes(img)
