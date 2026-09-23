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
from ...config import NICKNAME
from ...constants import (
    GENRE_TO_ZH,
    VERSION_IMAGE,
    ACHIEVEMENT_LIST,
    JP_VERSION_IMAGE,
    version_zh,
    display_song_id,
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

    ra = [int(ScoreCoefficient(r).ra(level_value)) for r in ACHIEVEMENT_LIST[-6:]]
    ra.append(int(ScoreCoefficient(ACHIEVEMENT_LIST[-1]).ra(level_value)) + 1)
    return sorted(ra, reverse=True)


def new_best_score(
    song_id: int,
    level_index_value: int,
    value: int,
    best_list: list[ScoreExtend],
    song_type: SongType,
) -> int:
    """NB new_best_score：已在 B50 → 打到此 ra 的净提升；未入 B50 → 相对入线线。"""
    lowest = int(best_list[-1].dx_rating or 0) if best_list else 0
    for v in best_list:
        if (
            v.id == song_id
            and v.type == song_type
            and v.level_index.value == level_index_value
        ):
            old = int(v.dx_rating or 0)
            return value - old if value >= old else 0
    return value - lowest


def _major_diffs(song: Song, prefer_type: SongType | None = None) -> list:
    """取主类型 5 档难度，按难度序。

    NB 原版数据模型中双谱歌曲是两个条目（各带类型与谱面表）；maimai_py 模型
    合并为一个 Song 后 ``difficulties.dx`` 对双谱曲恒非空，若不指定偏好则 SD
    谱面不可达。``prefer_type=STANDARD``（前缀「标准/标」搜索）时显示 SD 谱面。
    """
    if prefer_type == SongType.STANDARD and song.difficulties.standard:
        diffs = song.difficulties.standard
    else:
        diffs = song.difficulties.dx or song.difficulties.standard
    return sorted(diffs, key=lambda d: d.level_index.value)


def _is_new(version: int) -> bool:
    """当前版本曲目标「新曲」（NB isnew 语义，以 maimai-py 当前版本为准）。"""
    from maimai_py import current_version

    return version >= current_version.value


def _chart_version(song: Song, prefer_sd: bool) -> int:
    """卡片主类型谱面组的登场版本（组内谱面版本一致，取首谱面，§2.3）。

    SD/DX 同曲不同版本（老曲补 DX，如 835/10835）时显示版本跟随卡片主类型
    而非曲级最小值——对齐 maimaiDX 基准的 per-type 条目语义
    （``song.version_int = base.version``）；无谱面或版本缺失回落曲级 version。
    """
    diffs = song.difficulties.standard if prefer_sd else song.difficulties.dx
    if not diffs:
        diffs = song.difficulties.standard or song.difficulties.dx
    return diffs[0].version if diffs and diffs[0].version else song.version


def _jp_version_logo_name(version: int) -> str | None:
    """日服版本码 → 日服 logo 文件名；追加批次码回落基础码。

    追加批次码 = 基础版本码（500 的倍数）+ 批内序号（如 26513 = CiRCLE PLUS + 13），
    精确匹配落空时按 500 取整回基础码；旧框（<20000）中日 logo 相同，交由通用路径。
    """
    if name := JP_VERSION_IMAGE.get(version):
        return name
    return JP_VERSION_IMAGE.get(version // 500 * 500) if version >= 20000 else None


def _version_image(version: int, jp: bool = False) -> Image.Image | None:
    if jp:
        # 日服视图：DX 世代用日服 logo（pic/jp/，含 MAGiCAL 等超枚举版本）
        jp_name = _jp_version_logo_name(version)
        if jp_name:
            path = assets.static_path() / "mai" / "pic" / "jp" / f"{jp_name}.png"
            if path.exists():
                return Image.open(path).convert("RGBA")
    ver = Version.from_value(version)
    if ver is None:
        return None
    name = VERSION_IMAGE.get(ver) or version_zh(version)
    path = assets.static_path() / "mai" / "pic" / f"{name}.png"
    if path.exists():
        return Image.open(path).convert("RGBA")
    return None


def _fit_version_logo(
    img: Image.Image, box: tuple[int, int] = (182, 90)
) -> Image.Image:
    """版本 logo 等比适配槽位：裁透明留白 → 高≥72 且 宽≥140（取大者）→ 不超槽位。

    各世代素材画布的留白量与宽高比差异很大（国服 AR 1.2~4.9、日服 1.4~2.8），
    直接 resize 到槽位（比例 2.02）会带来 10~140% 的拉伸变形；按内容双下限
    等比缩放才能兼顾不变形与整组 logo 视觉大小一致。
    """
    alpha = img.getchannel("A").point(lambda v: 255 if v > 16 else 0)  # type: ignore[arg-type]
    if bb := alpha.getbbox():
        img = img.crop(bb)
    scale = max(72 / img.height, 140 / img.width)
    scale = min(scale, box[0] / img.width, box[1] / img.height)
    return img.resize(
        (round(img.width * scale), round(img.height * scale)),
        Image.Resampling.LANCZOS,
    )


def song_chart_info(
    song: Song,
    calc: bool,
    is_full: bool,
    best_list: list[ScoreExtend],
    theme: str = "prism_plus",
    prefer_type: SongType | None = None,
    jp: bool = False,
) -> bytes:
    """查歌卡（含可选的用户成绩/加分预测），布局坐标对齐 NB 版。

    ``prefer_type=STANDARD``：双谱歌曲显示 SD 徽章与 SD 难度表（前缀「标准/标」
    搜索）；其余按 NB 默认（有 DX 用 DX）。
    ``jp=True``：日服视图渲染，版本 logo 用日服世代图（pic/jp/）。
    """
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
    prefer_sd = prefer_type == SongType.STANDARD and song.difficulties.standard
    type_abbr = "SD" if prefer_sd else ("DX" if song.difficulties.dx else "SD")
    chart_version = _chart_version(song, prefer_sd)
    # 日服视图与国服新曲标无关：统一不渲染「新曲だよ!」徽章
    if _is_new(chart_version) and not jp:
        im.alpha_composite(
            Image.open(base / "UI_CMN_TabTitle_NewSong.png").resize((249, 120)),
            (842, 100),
        )
    cover = assets.cover(song.id).resize((242, 242))
    im.alpha_composite(cover, (133, 197))
    version_img = _version_image(chart_version, jp)
    if version_img is not None:
        logo = _fit_version_logo(version_img)
        im.alpha_composite(
        logo, (800 + (182 - logo.width) // 2, 370 + (90 - logo.height) // 2)
    )
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
        f"ID {display_song_id(song)}",
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

    diffs = _major_diffs(song, prefer_type)
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
        # TOTAL 列 = 五项 notes 之和（对齐 NB 六列布局）
        notes = (
            diff.tap_num,
            diff.hold_num,
            diff.slide_num,
            diff.touch_num,
            diff.break_num,
        )
        mr.text(
            (480, 590 + spacing),
            str(sum(notes)),
            font=font(25, FONT_RODIN),
            fill=text_color,
            anchor="mm",
        )
        for n, value in enumerate(notes):
            mr.text(
                (602 + 122 * n, 590 + spacing),
                str(value),
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
    # 底部版权行（对齐 NB 原版 maimaiDX 卡面）
    credit = (
        "Designed by Yuri-YuzuChaN & BlueDeer233. "
        f"Generated by {NICKNAME or 'awmc-helper'} BOT"
    )
    mr.text(
        (600, 1220),
        credit,
        font=font(25, FONT_RODIN),
        fill=text_color,
        anchor="mm",
        stroke_width=3,
        stroke_fill=(255, 255, 255, 255),
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
    utage_id = next(
        (getattr(d, "diff_id", None) for d in song.get_difficulties(SongType.UTAGE)),
        None,
    )
    mr.text(
        (405, 435),
        f"ID {utage_id if utage_id is not None else song.id}",
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
    # 底部版权行（对齐 NB 原版宴会场卡）
    credit = (
        "Designed by Yuri-YuzuChaN & BlueDeer233. "
        f"Generated by {NICKNAME or 'awmc-helper'} BOT"
    )
    mr.text(
        (600, 1100),
        credit,
        font=font(25, FONT_RODIN),
        fill=text_color,
        anchor="mm",
        stroke_width=3,
        stroke_fill=(255, 255, 255, 255),
    )
    return image_to_bytes(im)


def is_banquet(song: Song) -> bool:
    """纯宴会谱曲（无 SD/DX 谱面）判定。"""
    return (
        not song.difficulties.standard
        and not song.difficulties.dx
        and bool(song.difficulties.utage)
    )
