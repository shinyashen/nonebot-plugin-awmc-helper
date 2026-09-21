"""NB 版查歌卡移植（nonebot-plugin-maimaidx core/image/chart.py 的 1:1 移植）。

使用素材包主题底图 ``{theme}/chart_info.png``，叠加 logo/曲绘/版本图/类型图标，
绘制标题/曲师/BPM/ID/分类与各难度谱面数据；Expert/Master/Re:MASTER 行绘制
7 档达成率的 RA 及 B50 加分预测（``ra(↑gain)``，语义对齐 NB ``new_best_score``）。

布局坐标与 NB 版完全一致，底图 1200×1300（宴会场 1200×1200）。
"""

from PIL import Image
from maimai_py import Song, Version, SongType, ScoreExtend

from .fonts import FONT_HAN, FONT_RODIN, font
from .tools import image_to_bytes
from .assets import assets
from ...constants import (
    GENRE_TO_ZH,
    VERSION_IMAGE,
    ACHIEVEMENT_LIST,
    version_zh,
)

# NB base.py 的东亚字宽判定（截断用）
_CHAR_WIDTHS = [
    (126, 1),
    (159, 0),
    (687, 1),
    (710, 0),
    (711, 1),
    (727, 0),
    (733, 1),
    (879, 0),
    (1154, 1),
    (1161, 0),
    (4347, 1),
    (4447, 2),
    (7467, 1),
    (7521, 0),
    (8369, 1),
    (8426, 0),
    (9000, 1),
    (9002, 2),
    (11021, 1),
    (12350, 2),
    (12351, 1),
    (12438, 2),
    (12442, 0),
    (19893, 2),
    (19967, 1),
    (55203, 2),
    (63743, 1),
    (64106, 2),
    (65039, 1),
    (65059, 0),
    (65131, 2),
    (65279, 1),
    (65376, 2),
    (65500, 1),
    (65510, 2),
    (120831, 1),
    (262141, 2),
    (1114109, 1),
]


def column_width(text: str) -> int:
    res = 0
    for ch in text:
        o = ord(ch)
        if o in (0xE, 0xF):
            continue
        for num, wid in _CHAR_WIDTHS:
            if o <= num:
                res += wid
                break
        else:
            res += 1
    return res


def truncate_by_width(text: str, limit: int) -> str:
    if column_width(text) <= limit:
        return text
    res = 0
    out = []
    for ch in text:
        o = ord(ch)
        w = 1
        for num, wid in _CHAR_WIDTHS:
            if o <= num:
                w = wid
                break
        if res + w <= limit:
            out.append(ch)
            res += w
        else:
            break
    return "".join(out) + "..."


def get_best_rating(level_value: float) -> list[int]:
    """高达成率段的 7 档 RA（NB get_best_rating：最后 6 档 + SSS+ 再 +1，降序）。"""
    from maimai_py.utils import ScoreCoefficient

    ra = [ScoreCoefficient(r).ra(level_value) for r in ACHIEVEMENT_LIST[-6:]]
    ra.append(ScoreCoefficient(ACHIEVEMENT_LIST[-1]).ra(level_value) + 1)
    return sorted(ra, reverse=True)


def new_best_score(
    song_id: int,
    level_index_value: int,
    value: int,
    best_list: list[ScoreExtend],
    song_type: SongType,
) -> int:
    """NB new_best_score：已在 B50 → 打到此 ra 的净提升；未入 B50 → 相对入线线。"""
    lowest = best_list[-1].dx_rating or 0 if best_list else 0
    for v in best_list:
        if (
            v.id == song_id
            and v.type == song_type
            and v.level_index.value == level_index_value
        ):
            old = v.dx_rating or 0
            return value - old if value >= old else 0
    return value - lowest


def _major_diffs(song: Song) -> list:
    """NB 语义：取主类型 5 档难度（有 DX 用 DX，否则 SD），按难度序。"""
    diffs = song.difficulties.dx or song.difficulties.standard
    return sorted(diffs, key=lambda d: d.level_index.value)


def _is_new(song: Song) -> bool:
    """当前版本曲目标「新曲」（NB isnew 语义，以 maimai-py 当前版本为准）。"""
    from maimai_py import current_version

    return song.version >= current_version.value


def _version_image(song: Song) -> Image.Image | None:
    ver = Version.from_value(song.version)
    name = VERSION_IMAGE.get(ver) or version_zh(song.version)
    path = assets.static_path() / "mai" / "pic" / f"{name}.png"
    if path.exists():
        return Image.open(path).convert("RGBA")
    return None


def song_chart_info(
    song: Song,
    calc: bool,
    is_full: bool,
    best_list: list[ScoreExtend],
    theme: str = "prism_plus",
) -> bytes:
    """查歌卡（含可选的用户成绩/加分预测），布局坐标对齐 NB 版。"""
    from PIL import ImageDraw

    im = Image.open(
        assets.static_path() / "mai" / "pic" / theme / "chart_info.png"
    ).convert("RGBA")
    mr = ImageDraw.Draw(im)
    f_han = font(24, FONT_HAN)
    f_rodin = font(28, FONT_RODIN)
    text_color = (249, 62, 172, 255) if theme == "circle" else (124, 129, 255, 255)

    base = assets.static_path() / "mai" / "pic"
    im.alpha_composite(
        Image.open(base / theme / "logo.png").resize((249, 120)), (65, 25)
    )
    if _is_new(song):
        im.alpha_composite(
            Image.open(base / "UI_CMN_TabTitle_NewSong.png").resize((249, 120)),
            (842, 100),
        )
    cover = assets.cover(song.id).resize((242, 242))
    im.alpha_composite(cover, (133, 197))
    version_img = _version_image(song)
    if version_img is not None:
        im.alpha_composite(version_img.resize((182, 90)), (800, 370))
    type_abbr = "DX" if song.difficulties.dx else "SD"
    type_path = base / f"{type_abbr}.png"
    if type_path.exists():
        im.alpha_composite(Image.open(type_path).resize((80, 30)), (295, 410))

    mr.text(
        (405, 220),
        truncate_by_width(song.title, 40),
        font=f_rodin,
        fill=text_color,
        anchor="lm",
    )
    mr.text(
        (407, 265),
        truncate_by_width(song.artist, 50),
        font=font(20, FONT_RODIN),
        fill=text_color,
        anchor="lm",
    )
    mr.text(
        (460, 345),
        str(song.bpm),
        font=font(24, FONT_RODIN),
        fill=text_color,
        anchor="lm",
    )
    mr.text(
        (405, 435),
        f"ID {song.id}",
        font=font(22, FONT_RODIN),
        fill=text_color,
        anchor="lm",
    )
    mr.text(
        (665, 435),
        GENRE_TO_ZH.get(song.genre, song.genre.value),
        font=f_han,
        fill=text_color,
        anchor="mm",
    )

    diffs = _major_diffs(song)
    for index, diff in enumerate(diffs):
        color = (255, 255, 255, 255)
        spacing = 70 * index
        mr.text(
            (120, 590 + spacing),
            f"{diff.level}({diff.level_value:.1f})",
            font=font(22, FONT_RODIN),
            fill=color,
            anchor="mm",
        )
        fitting = (
            f"擬 - {diff.curve.fit_level_value:.2f}" if diff.curve is not None else "-"
        )
        mr.text(
            (120, 613 + spacing),
            fitting,
            font=font(15, FONT_RODIN),
            fill=color,
            anchor="mm",
        )
        mr.text(
            (310, 590 + spacing),
            truncate_by_width(diff.note_designer, 19),
            font=f_han,
            fill=text_color,
            anchor="mm",
        )
        for n, field in enumerate(
            ("tap_num", "hold_num", "slide_num", "touch_num", "break_num")
        ):
            mr.text(
                (480 + 122 * n, 590 + spacing),
                str(getattr(diff, field)),
                font=font(25, FONT_RODIN),
                fill=text_color,
                anchor="mm",
            )

        if index > 1:
            ra_list = get_best_rating(diff.level_value)
            for n, value in enumerate(ra_list):
                size = 22
                if not calc:
                    rating = str(value)
                elif not is_full:
                    size = 17
                    rating = f"{value}(↑{value})"
                else:
                    new = new_best_score(
                        song.id, diff.level_index.value, value, best_list, diff.type
                    )
                    if new == 0:
                        rating = str(value)
                    else:
                        size = 17
                        rating = f"{value}(↑{new})"
                mr.text(
                    (295 + 125 * n, 1017 + 46 * (index - 2)),
                    rating,
                    font=font(size, FONT_RODIN),
                    fill=text_color,
                    anchor="mm",
                )
    mr.text(
        (295, 985), "*未实装", font=font(12, FONT_HAN), fill=text_color, anchor="mm"
    )
    return image_to_bytes(im)


def song_chart_banquet_info(song: Song) -> bytes:
    """宴会场谱面卡（底图 chart_info_enkaijou.png）。"""
    from PIL import ImageDraw

    base = assets.static_path() / "mai" / "pic"
    im = Image.open(base / "chart_info_enkaijou.png").convert("RGBA")
    mr = ImageDraw.Draw(im)
    f_han = font(24, FONT_HAN)
    f_rodin = font(28, FONT_RODIN)
    text_color = (124, 129, 255, 255)

    cover = assets.cover(song.id).resize((242, 242))
    im.alpha_composite(cover, (133, 197))
    mr.text(
        (405, 220),
        truncate_by_width(song.title, 40),
        font=f_rodin,
        fill=text_color,
        anchor="lm",
    )
    mr.text(
        (407, 265),
        truncate_by_width(song.artist, 50),
        font=font(20, FONT_RODIN),
        fill=text_color,
        anchor="lm",
    )
    mr.text(
        (460, 345),
        str(song.bpm),
        font=font(24, FONT_RODIN),
        fill=text_color,
        anchor="lm",
    )
    mr.text(
        (405, 435),
        f"ID {song.id}",
        font=font(22, FONT_RODIN),
        fill=text_color,
        anchor="lm",
    )

    y = 560
    for diff in song.get_difficulties(SongType.UTAGE):
        kanji = getattr(diff, "kanji", "")
        desc = truncate_by_width(getattr(diff, "description", ""), 46)
        mr.text(
            (120, y),
            f"{diff.level}({diff.level_value:.1f})",
            font=font(22, FONT_RODIN),
            fill=text_color,
            anchor="mm",
        )
        mr.text((340, y), f"[{kanji}] {desc}", font=f_han, fill=text_color, anchor="mm")
        y += 70
    return image_to_bytes(im)


def is_banquet(song: Song) -> bool:
    """纯宴会谱曲（无 SD/DX 谱面）判定。"""
    return (
        not song.difficulties.standard
        and not song.difficulties.dx
        and bool(song.difficulties.utage)
    )
