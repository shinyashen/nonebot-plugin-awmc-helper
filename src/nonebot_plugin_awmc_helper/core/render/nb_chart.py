"""NB 版查歌卡移植（nonebot-plugin-maimaidx core/image/chart.py 的 1:1 移植）。

使用素材包主题底图 ``{theme}/chart_info.png``，叠加 logo/曲绘/版本图/类型图标，
绘制标题/曲师/BPM/ID/分类与各难度谱面数据；Expert/Master/Re:MASTER 行绘制
7 档达成率的 RA 及 B50 加分预测（``ra(↑gain)``，语义对齐 NB ``new_best_score``）。

布局坐标与 NB 版完全一致，底图 1200×1300（宴会场 1200×1200）。
"""

from pathlib import Path

from PIL import Image, ImageDraw
from maimai_py import Song, Version, SongType, ScoreExtend

from .fonts import FONT_HAN, FONT_RODIN, font
from .tools import (
    TEXT_BLUE,
    CIRCLE_PINK,
    credit_text,
    image_to_bytes,
    truncate_hoshino,
)
from .assets import assets
from ...constants import (
    GENRE_TO_ZH,
    DEFAULT_THEME,
    VERSION_IMAGE,
    ACHIEVEMENT_LIST,
    JP_VERSION_IMAGE,
    version_zh,
    display_song_id,
    chart_display_id,
)


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
    """NB new_best_score：已在 B50 → 打到此 ra 的净提升；未入 B50 → 相对入线线。

    入线线取 best_list 的**最低** RA（NB 传入的 b50 列表按 ra 降序、[-1] 即
    末位；我方调用方不保证有序，故显式取 min），调用方须保证结果 > 0 才显示
    「↑N」（负提升不应展示）。
    """
    lowest = min((int(v.dx_rating or 0) for v in best_list), default=0)
    for v in best_list:
        if (
            v.id == song_id
            and v.type == song_type
            and v.level_index.value == level_index_value
        ):
            old = int(v.dx_rating or 0)
            return value - old if value >= old else 0
    return value - lowest


def notes_cell_text(value: int, col: int, *, is_dx: bool, row_has_notes: bool) -> str:
    """物量单元格文本（「0 即 -」约定 + touch 例外，2026-09-25 用户口径）。

    物量列序为 (tap, hold, slide, touch, break)，touch 即 col 3：

    - 有值 → 数字；
    - 0 且为 **DX 谱 touch 列**且行内其他物量非全 0 → ``"0"``——DX 谱确有
      15 张无 touch 谱面（ローリンガール exp 等，实测 otoge 全量），真 0
      如实显示；SD 谱 touch 恒 0（2336/2336，touch 为 DX 谱面机制）→ ``-``；
    - 其余 0 → ``-``（含整行全 0 = 物量未收录，两种谱型均画 -）。
    """
    if value:
        return str(value)
    if col == 3 and is_dx and row_has_notes:
        return "0"
    return "-"


def major_diffs(song: Song, prefer_type: SongType | None = None) -> list:
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


def is_new_chart(version: int) -> bool:
    """当前版本曲目标「新曲」（NB isnew 语义，以 maimai-py 当前版本为准）。"""
    from maimai_py import current_version

    return version >= current_version.value


def chart_version_of(song: Song, prefer_sd: bool) -> int:
    """卡片主类型谱面组的登场版本（组内谱面版本一致，取首谱面，§2.3）。

    SD/DX 同曲不同版本（老曲补 DX，如 835/10835）时显示版本跟随卡片主类型
    而非曲级最小值——对齐 maimaiDX 基准的 per-type 条目语义
    （``song.version_int = base.version``）；无谱面或版本缺失回落曲级 version。
    """
    diffs = song.difficulties.standard if prefer_sd else song.difficulties.dx
    if not diffs:
        diffs = song.difficulties.standard or song.difficulties.dx
    return diffs[0].version if diffs and diffs[0].version else song.version


def _display_card_id(song: Song, prefer_sd: bool) -> int:
    """卡片展示 id 跟随主类型谱面组（与 :func:`chart_version_of` 同口径）。

    老曲补 DX（如 835/10835）：DX 卡显示查分器 id = 根 id + 10000，SD 卡显示
    根 id；无谱面回落曲级 :func:`display_song_id`。
    """
    diffs = song.difficulties.standard if prefer_sd else song.difficulties.dx
    if not diffs:
        diffs = song.difficulties.standard or song.difficulties.dx
    return chart_display_id(song, diffs[0]) if diffs else display_song_id(song)


def _jp_version_logo_name(version: int) -> str | None:
    """日服版本码 → 日服 logo 文件名；追加批次码回落基础码。

    追加批次码 = 基础版本码（500 的倍数）+ 批内序号（如 26513 = CiRCLE PLUS + 13），
    ``Version.from_value``「≤ 取最近」语义天然完成回落；旧框版本不在
    JP_VERSION_IMAGE，落空走通用路径。
    """
    if not version:
        return None
    ver = Version.from_value(version)
    if ver is None:
        return None
    return JP_VERSION_IMAGE.get(ver)


def version_image(version: int, jp: bool = False) -> Image.Image | None:
    if jp:
        # 日服视图：DX 世代用日服 logo（pic/jp/，含 MAGiCAL）
        jp_name = _jp_version_logo_name(version)
        if jp_name:
            path = assets.static_path() / "mai" / "pic" / "jp" / f"{jp_name}.png"
            if path.exists():
                return assets.get(path)
    ver = Version.from_value(version)
    if ver is None:
        return None
    name = VERSION_IMAGE.get(ver) or version_zh(version)
    path = assets.static_path() / "mai" / "pic" / f"{name}.png"
    if path.exists():
        return assets.get(path)
    return None


def fit_version_logo(img: Image.Image, box: tuple[int, int] = (182, 90)) -> Image.Image:
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
    theme: str = DEFAULT_THEME,
    prefer_type: SongType | None = None,
    jp: bool = False,
    *,
    id_text: str | None = None,
    genre_text: str | None = None,
    cover_path: Path | None = None,
) -> bytes:
    """查歌卡（含可选的用户成绩/加分预测），布局坐标对齐 NB 版。

    ``prefer_type=STANDARD``：双谱歌曲显示 SD 徽章与 SD 难度表（前缀「标准/标」
    搜索）；其余按 NB 默认（有 DX 用 DX）。
    ``jp=True``：日服视图渲染，版本 logo 用日服世代图（pic/jp/）。
    ``id_text``/``genre_text``/``cover_path``：覆写 ID 行/分类行/封面——pending
    临时卡（id 未收录新曲）传 ``"ID —"``、分类名或 ``"-"``、payload 封面落盘
    路径；缺省按 song 自身绘制（封面按曲 id 候选链取）。
    BPM 与物量遵循「0 即 -」约定（0 只代表未收录）；touch 列例外——SD 谱
    touch 恒 0（机制上无 touch）显示 ``-``，DX 谱 touch=0 为真实数据（确有
    无 touch 的 DX 谱面）显示 ``0``，详见 :func:`notes_cell_text`。
    """
    im = assets.canvas("chart_info.png", theme)
    mr = ImageDraw.Draw(im)
    f_han = font(24, FONT_HAN)
    f_rodin = font(28, FONT_RODIN)
    text_color = CIRCLE_PINK if theme == "circle" else TEXT_BLUE

    im.alpha_composite(assets.pic("logo.png", theme).resize((249, 120)), (65, 25))
    prefer_sd = prefer_type == SongType.STANDARD and bool(song.difficulties.standard)
    type_abbr = "SD" if prefer_sd else ("DX" if song.difficulties.dx else "SD")
    chart_version = chart_version_of(song, prefer_sd)
    # 日服视图与国服新曲标无关：统一不渲染「新曲だよ!」徽章
    if is_new_chart(chart_version) and not jp:
        im.alpha_composite(
            assets.pic("UI_CMN_TabTitle_NewSong.png").resize((249, 120)),
            (842, 100),
        )
    if cover_path is not None and cover_path.exists():
        cover = Image.open(cover_path).convert("RGBA")
    else:
        cover = assets.cover(song.id)
    im.alpha_composite(cover.resize((242, 242)), (133, 197))
    version_img = version_image(chart_version, jp)
    if version_img is not None:
        logo = fit_version_logo(version_img)
        im.alpha_composite(
            logo, (800 + (182 - logo.width) // 2, 370 + (90 - logo.height) // 2)
        )
    if badge := assets.type_badge(type_abbr, (80, 30)):
        im.alpha_composite(badge, (295, 410))

    title = truncate_hoshino(song.title, 40)
    mr.text(
        (405, 220),
        title,
        font=f_rodin,
        fill=text_color,
        anchor="lm",
    )
    artist = truncate_hoshino(song.artist, 50)
    mr.text(
        (407, 265),
        artist,
        font=font(20, FONT_RODIN),
        fill=text_color,
        anchor="lm",
    )
    mr.text(
        (460, 345),
        str(song.bpm) if song.bpm else "-",
        font=font(24, FONT_RODIN),
        fill=text_color,
        anchor="lm",
    )
    mr.text(
        (405, 435),
        id_text or f"ID {_display_card_id(song, prefer_sd)}",
        font=font(22, FONT_RODIN),
        fill=text_color,
        anchor="lm",
    )
    if genre_text is None:
        genre_text = GENRE_TO_ZH.get(song.genre, song.genre.value)
    mr.text((665, 435), genre_text, font=f_han, fill=text_color, anchor="mm")

    diffs = major_diffs(song, prefer_type)
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
        designer = truncate_hoshino(diff.note_designer, 19)
        mr.text(
            (310, 590 + spacing),
            designer,
            font=font(20, FONT_HAN),
            fill=text_color,
            anchor="mm",
        )
        # TOTAL 列 = 五项 notes 之和（对齐 NB 六列布局）；0 即 -（约定俗成）
        notes = (
            diff.tap_num,
            diff.hold_num,
            diff.slide_num,
            diff.touch_num,
            diff.break_num,
        )
        total = sum(notes)
        mr.text(
            (480, 590 + spacing),
            str(total) if total else "-",
            font=font(25, FONT_RODIN),
            fill=text_color,
            anchor="mm",
        )
        row_has_notes = any(notes)
        for n, value in enumerate(notes):
            mr.text(
                (602 + 122 * n, 590 + spacing),
                notes_cell_text(
                    value,
                    n,
                    is_dx=diff.type == SongType.DX,
                    row_has_notes=row_has_notes,
                ),
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
                    # ≤0 的加分为无意义信息，直接显示 RA 本身（用户口径）
                    if new <= 0:
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
    mr.text(
        (600, 1220),
        credit_text(),
        font=font(25, FONT_RODIN),
        fill=text_color,
        anchor="mm",
        stroke_width=3,
        stroke_fill=(255, 255, 255, 255),
    )
    return image_to_bytes(im)


def song_chart_banquet_info(song: Song, utage_diffs=None, jp: bool = False) -> bytes:
    """宴会场谱面卡（Hoshino/NB chart.py::song_chart_banquet_info 1:1 移植）。

    底图 ``chart_info_enkaijou.png``（1200×1200）：左侧曲绘与 utg 玩家牌、
    右侧标题/曲师/BPM/ID/分类（白字描边），下方 kanji 牌 + 等级 + 六列
    notes（total/tap/hold/slide/touch/brak）。

    ``utage_diffs``：要画的宴谱。一个宴谱 id（diff_id）只对应一张谱面——
    卡片恒只画其中一张：id/条目召唤传命中的那张；缺省取宿主曲第一张
    （kanji/等级/描述同源，不会出现牌信息与物量行不同谱）。
    ``jp=True``：宿主曲为日服限定（JP 视图对象）——「新曲だよ!」徽章是
    国服当前版本口径，日服曲不渲染（同 :func:`song_chart_info` 的 jp 口径）。
    """
    im = assets.canvas("chart_info_enkaijou.png")
    mr = ImageDraw.Draw(im)
    stroke = (210, 57, 174, 255)
    white = (255, 255, 255, 255)

    utage_diffs = list(
        utage_diffs
        if utage_diffs is not None
        else song.get_difficulties(SongType.UTAGE)
    )
    # 一个宴谱 id 只对应一张谱面：卡片恒取一张（缺省=宿主曲第一张）
    utage_diffs = utage_diffs[:1]
    first = next(iter(utage_diffs), None)
    is_buddy = bool(getattr(first, "is_buddy", False))

    # kanji 牌底、双人宴标记与玩家牌
    im.alpha_composite(
        assets.pic("utg_kanji.png"),
        (140, 660 if is_buddy else 730),
    )
    if is_buddy:
        # 底图 utg_2p 自带 TOTAL..BREAK 表头与 1P/2P 两条数据行，行位 820/920
        p_y, base_y, step_y = 715, 820, 100
        im.alpha_composite(assets.pic("utg_buddy.png"), (255, 660))
        player_file = "utg_2p.png"
    else:
        # 底图 utg_1p 只有一条 1P 数据行，行位 890
        p_y, base_y, step_y = 785, 890, 0
        player_file = "utg_1p.png"
    im.alpha_composite(assets.pic(player_file), (98, p_y))

    # logo / 新曲标
    im.alpha_composite(
        assets.pic("logo.png", "prism_plus").resize((249, 120)), (10, 35)
    )
    # 版本/新曲标口径 = 宴谱组自己的登场版本：宿主曲整曲最小版本常由普通谱
    # 决定（悪戯センセーション DX 21000 / 宴[奏] 26509），按整曲取会画错世代；
    # 宴谱组无版本时回落整曲版本。日服限定卡（jp）用日服世代图（pic/jp/）
    chart_version = getattr(first, "version", 0) or song.version
    # 「新曲」标是国服当前版本口径：日服限定曲不渲染（对齐 song_chart_info）
    if is_new_chart(chart_version) and not jp:
        im.alpha_composite(
            assets.pic("UI_CMN_TabTitle_NewSong.png").resize((249, 120)),
            (950, 165),
        )

    # 曲绘 / 版本
    im.alpha_composite(assets.cover(song.id).resize((242, 242)), (133, 246))
    version_img = version_image(chart_version, jp=jp)
    if version_img is not None:
        logo = fit_version_logo(version_img)
        im.alpha_composite(
            logo, (800 + (182 - logo.width) // 2, 415 + (90 - logo.height) // 2)
        )

    def t(pos, text, size, *, anchor="mm", sw=0, fill=white, han=False):
        # 描边色对齐 Hoshino/NB 现行源码（紫 210,57,174,255）：曾按 NB 旧版
        # (0,0,0,0) 透明字面拍板黑描边，二库现行均为紫描边，按权威改回；
        # 简体字样（分类中文名等）走中文字体——FOT-NewRodin 为日文字体，
        # 缺部分简体字形（与 song_chart_info 的分类行同口径）
        mr.text(
            pos,
            text,
            font=font(size, FONT_HAN if han else FONT_RODIN),
            fill=fill,
            anchor=anchor,
            stroke_width=sw,
            stroke_fill=(210, 57, 174, 255) if sw else None,
        )

    # 标题 / 曲师 / BPM / ID / 分类（白字紫描边；截断规则同 song_chart_info）
    kanji = getattr(first, "kanji", "") if first else ""
    t((216, p_y - 28), kanji, 18)
    ban_title = truncate_hoshino(song.title, 36)
    t((405, 265), ban_title, 28, anchor="lm", sw=3)
    ban_artist = truncate_hoshino(song.artist, 50)
    t((407, 320), ban_artist, 20, anchor="lm", sw=3)
    t((460, 393), str(song.bpm), 24, anchor="lm", sw=3)
    utage_id = next((getattr(d, "diff_id", None) for d in utage_diffs), None)
    card_id = utage_id if utage_id is not None else song.id
    t((405, 475), f"ID {card_id}", 22, anchor="lm", sw=3)
    t((680, 475), GENRE_TO_ZH.get(song.genre, song.genre.value), 22, sw=3, han=True)
    # 描述（Hoshino 原版不截断，直接绘制）
    t((595, 595), getattr(first, "description", ""), 25)
    # 等级（玩家牌内）
    t((180, p_y + 28), f"Lv. {first.level if first else '?'}", 24, sw=3)
    # 六列 notes（total/tap/hold/slide/touch/brak）：卡片只画一张宴谱；
    # buddy 谱物量在 buddy_notes 左右手两组（主物量列入库即存左右合计），
    # 按底图 1P/2P 行各占一行（NB 原版同款展开）；buddy_notes 缺失（快照降级
    # 回填为 None）时回退顶层行（此时为合计值）
    if first is not None:
        buddy = getattr(first, "buddy_notes", None)
        if getattr(first, "is_buddy", False) and buddy is not None:
            rows = [
                (
                    buddy.left_tap_num,
                    buddy.left_hold_num,
                    buddy.left_slide_num,
                    buddy.left_touch_num,
                    buddy.left_break_num,
                ),
                (
                    buddy.right_tap_num,
                    buddy.right_hold_num,
                    buddy.right_slide_num,
                    buddy.right_touch_num,
                    buddy.right_break_num,
                ),
            ]
        else:
            rows = [
                (
                    first.tap_num,
                    first.hold_num,
                    first.slide_num,
                    first.touch_num,
                    first.break_num,
                )
            ]
        for row, notes in enumerate(rows):
            values = (sum(notes), *notes)
            for n, value in enumerate(values):
                t((330 + 140 * n, base_y + step_y * row), str(value), 25, sw=3)

    mr.text(
        (600, 1100),
        credit_text(),
        font=font(25, FONT_RODIN),
        fill=stroke,
        anchor="mm",
        stroke_width=3,
        stroke_fill=white,
    )
    return image_to_bytes(im)


def is_banquet(song: Song) -> bool:
    """纯宴会谱曲（无 SD/DX 谱面）判定。"""
    return (
        not song.difficulties.standard
        and not song.difficulties.dx
        and bool(song.difficulties.utage)
    )
