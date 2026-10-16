"""linfo 定数历史卡（2026-10-16 新增，单难度横向时间轴版式）。

与分数线卡同体系（主题底图 / 白卡投影 / 字体 / 素材全部复用），内容为
「歌曲信息卡 + 定数变迁时间轴」两段。时间轴只画变化点：DX 世代裁剪后的
节点序列（``songdb.dx_history_points``，旧框末值折成 DX 基线首节点），
节点 = 版本 logo + 定数，节点间 Δ 芯片；>5 节点两行排布（顶行取上整），
两行同列距同左缘，跨行 Δ 挂次行行首槽位。

双数据源口径（调用方按绑定服务传入 ``jp``）：

- 国服视图：大数字 = 国服现行定数（≤ 国服当前版本最新值），日服已调至
  更新值时数字行下附红字提示，现行节点紫框「现行」、日服最新节点粉框
  「日服最新」；
- 日服视图：大数字 = 日服现行定数（即最新值），恒只有末节点紫框
  「现行」，不存在「日服最新」概念。

⚠️ 与 score_line 同款坑：ImageDraw 在 RGBA 上直写不做 alpha 合成——
Δ 芯片的半透背景必须画在独立透明层后 ``Image.alpha_composite``（直写
会把 RGB 落成实色、alpha 通道在转 RGB 时丢弃，恒 255 实色）。
"""

import math

from PIL import Image, ImageDraw
from maimai_py import Version, SongType, LevelIndex

from .fonts import FONT_HAN, FONT_NUM, FONT_RODIN, font
from .tools import credit_text, truncate_hoshino
from .assets import assets
from .nb_chart import LOGO_SIZE, major_type_of, version_image, paste_version_logo
from .score_line import _DARK, _GRAY, _card, _theme_bg, _theme_style
from ...constants import GENRE_TO_ZH, DIFF_DISPLAY_NAMES, ds_text, chart_display_id

W = 1200

# 时间轴网格（9 节点上限反推：行首槽 64 + 顶行 5×128 + 4×72 = 992 ≤ 1000）
NODE_W, PILL_H, ARROW_W, SLOT_W = 128, 86, 72, 64
_NAME_SIZE, _VALUE_SIZE = 20, 34

_MASTER = (159, 81, 220, 255)
_UP_TEXT, _UP_BG = (214, 31, 130, 255), (249, 62, 172, 120)
_DOWN_TEXT, _DOWN_BG = (43, 138, 68, 255), (58, 168, 90, 120)
_LINE = (185, 181, 214, 255)

_VER_SHORT: dict[Version, str] = {
    Version.MAIMAI_DX: "DX",
    Version.MAIMAI_DX_PLUS: "DX+",
    Version.MAIMAI_DX_SPLASH: "SPLASH",
    Version.MAIMAI_DX_SPLASH_PLUS: "SPLASH+",
    Version.MAIMAI_DX_UNIVERSE: "UNiVERSE",
    Version.MAIMAI_DX_UNIVERSE_PLUS: "UNiVERSE+",
    Version.MAIMAI_DX_FESTIVAL: "FESTiVAL",
    Version.MAIMAI_DX_FESTIVAL_PLUS: "FESTiVAL+",
    Version.MAIMAI_DX_BUDDIES: "BUDDiES",
    Version.MAIMAI_DX_BUDDIES_PLUS: "BUDDiES+",
    Version.MAIMAI_DX_PRISM: "PRiSM",
    Version.MAIMAI_DX_PRISM_PLUS: "PRiSM+",
    Version.MAIMAI_DX_CIRCLE: "CiRCLE",
    Version.MAIMAI_DX_CIRCLE_PLUS: "CiRCLE+",
    Version.MAIMAI_DX_MAGICAL: "MAGiCAL",
}

_FOOTNOTE = (
    "※ 仅统计 DX 世代定数 · 未调整的版本沿用前值不占节点 · "
    "换行时行首箭头为自上行末节点的变化"
)


def _fit_node_logo(img: Image.Image, box: tuple[int, int] = (112, 56)) -> Image.Image:
    """节点槽位版 ``nb_chart.fit_version_logo``：裁透明留白 → 内容双下限
    （高 46 / 宽 88，按槽位等比缩小自 182×90 槽的 72/140）→ 不超槽位。
    各世代 logo 画布留白差异大，直接 resize 会变形或大小不一。"""
    alpha = img.getchannel("A").point(lambda v: 255 if v > 16 else 0)  # type: ignore[arg-type]
    if bb := alpha.getbbox():
        img = img.crop(bb)
    scale = max(46 / img.height, 88 / img.width)
    scale = min(scale, box[0] / img.width, box[1] / img.height)
    return img.resize(
        (round(img.width * scale), round(img.height * scale)),
        Image.Resampling.LANCZOS,
    )


def linfo_card(
    song,
    diff,
    points: list[tuple[int, float]],
    *,
    cn_current: int,
    jp: bool,
    theme: str,
) -> bytes:
    """定数历史卡。

    ``song``/``diff``：命中曲对象与谱面（非宴，宴谱调用方拦截）；
    ``points``：DX 世代变化点（``songdb.dx_history_points`` 产出，长度 ≥2，
    长度 1 应由调用方走纯文本「定数没变过」分支）；``cn_current``：国服
    当前版本码（日服视图不使用）；``jp``：日服数据源口径。
    """
    assert len(points) >= 2, "单点历史应由调用方走纯文本分支"
    li: LevelIndex = diff.level_index
    style = _theme_style(theme)

    # 行布局：≤5 单行；>5 两行（顶行取上整），两行同列距同左缘
    n = len(points)
    rows = 1 if n <= 5 else 2
    top = n if rows == 1 else math.ceil(n / 2)
    slot = SLOT_W if rows == 2 else 0
    grid_w = slot + top * NODE_W + (top - 1) * ARROW_W
    x0 = 100 + (1000 - grid_w) // 2
    block_h = PILL_H + 10 + 24 + 40
    tl_h = 104 + block_h * rows + (26 if rows == 2 else 0) + 76
    H = 490 + tl_h + 96

    im = _theme_bg(theme, H)
    draw = ImageDraw.Draw(im)

    # ---- 标题行（与分数线卡同位）----
    im.alpha_composite(assets.pic("logo.png", theme).resize(LOGO_SIZE), (40, 24))
    draw.text(
        (600, 84),
        "定数历史",
        font=font(52, FONT_HAN),
        fill=(255, 255, 255, 255),
        anchor="mm",
        stroke_width=4,
        stroke_fill=style["title_stroke"],
    )

    # ---- 歌曲信息卡（复刻分数线歌曲卡版式，难度徽章位让给定数数字）----
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
    # 分类行用中文字体（Torus 无 CJK 字形）；长分类截断避让右侧大数字
    genre = GENRE_TO_ZH.get(song.genre) or song.genre.value or "-"
    draw.text(
        (530, 370),
        truncate_hoshino(genre, 10),
        font=font(21),
        fill=_GRAY,
        anchor="lm",
    )
    draw.text(
        (392, 408),
        f"ID {chart_display_id(song, diff)}",
        font=font(22, FONT_NUM),
        fill=_DARK,
        anchor="lm",
    )
    is_sd = major_type_of(song, diff.type) == SongType.STANDARD
    type_abbr = "SD" if is_sd else "DX"
    if badge := assets.type_badge(type_abbr, (80, 30)):
        im.alpha_composite(badge, (530, 408 - 12))
    draw.text((624, 408), f"{type_abbr} 谱面", font=font(18), fill=_GRAY, anchor="lm")
    # 版本 logo（谱面登场版本，日服口径与历史轴一致）
    paste_version_logo(im, diff.version or song.version, (840, 204, 240, 104), jp=jp)

    # 现行定数（原难度徽章位）：大数字 + 标签行与 ID 行同 y 同字号
    jp_val = points[-1][1]
    if jp:
        cur_val = jp_val
    else:
        eligible = [p for p in points if p[0] <= cn_current]
        cur_val = eligible[-1][1] if eligible else points[0][1]
    draw.text(
        (960, 364),
        ds_text(cur_val),
        font=font(50, FONT_NUM),
        fill=_DARK,
        anchor="mm",
    )
    draw.text(
        (960, 408),
        "日服现行定数" if jp else "国服现行定数",
        font=font(22),
        fill=_DARK,
        anchor="mm",
    )
    if not jp and jp_val != cur_val:
        draw.text(
            (960, 434),
            f"日服已调至 {ds_text(jp_val)}",
            font=font(16),
            fill=_UP_TEXT,
            anchor="mm",
        )

    # ---- 定数变迁卡（小节标题 = 分数线内容卡口径：主题色文字）----
    tl_y = 490
    _card(im, (60, tl_y, 1140, tl_y + tl_h))
    draw.text(
        (100, tl_y + 42),
        f"定数变迁 · {DIFF_DISPLAY_NAMES[li.value]}",
        font=font(26, FONT_HAN),
        fill=style["accent"],
        anchor="lm",
    )

    row_ys = [tl_y + 118]
    if rows == 2:
        row_ys.append(row_ys[0] + block_h + 26)

    # 第一遍：节点；第二遍：连接件（芯片背景走独立透明层，见模块注释）
    connectors: list[tuple[int, int, float]] = []
    idx = 0
    for r, y in enumerate(row_ys):
        count = top if r == 0 else n - top
        for c in range(count):
            ver, val = points[idx]
            nx = x0 + slot + c * (NODE_W + ARROW_W)
            if idx > 0:
                if c == 0 and rows == 2:
                    connectors.append(
                        (x0 + SLOT_W // 2 + 18, y, val - points[idx - 1][1])
                    )
                else:
                    connectors.append((nx - 4, y, val - points[idx - 1][1]))
            _draw_node(
                im,
                draw,
                points,
                ver,
                val,
                nx,
                y,
                idx=idx,
                cn_current=cn_current,
                jp=jp,
            )
            idx += 1

    fch = font(20, FONT_NUM)
    # 芯片背景走独立透明层 alpha_composite（直写不混合的坑见模块注释），
    # 箭头线与数字在合成后的成图上直写（不透明色）
    chip_layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    chip_draw = ImageDraw.Draw(chip_layer)
    chips: list[tuple[tuple[float, float, float, float], int, int, int, float]] = []
    for tip_x, y, d in connectors:
        cy = y + PILL_H // 2
        text = f"{d:+.1f}"
        tw = draw.textlength(text, font=fch)
        ax0, ax1 = tip_x - ARROW_W + 8, tip_x
        cx_mid = (ax0 + ax1) / 2
        box = (cx_mid - (tw + 18) / 2, cy - 38, cx_mid + (tw + 18) / 2, cy - 8)
        chip_draw.rounded_rectangle(box, 8, fill=_UP_BG if d > 0 else _DOWN_BG)
        chips.append((box, ax0, ax1, cy, d))
    im.alpha_composite(chip_layer)
    for box, ax0, ax1, cy, d in chips:
        tcol = _UP_TEXT if d > 0 else _DOWN_TEXT
        draw.line((ax0, cy, ax1, cy), fill=_LINE, width=3)
        draw.polygon(
            [(ax1 - 10, cy - 6), (ax1 + 2, cy), (ax1 - 10, cy + 6)], fill=_LINE
        )
        draw.text(
            ((box[0] + box[2]) / 2, cy - 23),
            f"{d:+.1f}",
            font=fch,
            fill=tcol,
            anchor="mm",
        )

    draw.text(
        (600, tl_y + tl_h - 34), _FOOTNOTE, font=font(17), fill=_GRAY, anchor="mm"
    )
    draw.text(
        (600, H - 56),
        credit_text(),
        font=font(20, FONT_RODIN),
        fill=style["accent"],
        anchor="mm",
        stroke_width=3,
        stroke_fill=(255, 255, 255, 255),
    )
    from .tools import image_to_bytes

    return image_to_bytes(im)


def _draw_node(im, draw, points, ver, val, nx, y, *, idx, cn_current, jp) -> None:
    """时间轴节点：白药丸 + 版本 logo + 版本名 + 定数 + 现行/日服最新标记。"""
    n = len(points)
    is_last = idx == n - 1
    if jp:
        # 日服视图：最新即现行，无「日服最新」概念
        is_cur, is_jp_latest = is_last, False
    else:
        is_cur = ver <= cn_current and (is_last or points[idx + 1][0] > cn_current)
        is_jp_latest = is_last and points[-1][0] > cn_current
    if is_cur:
        border, bw = _MASTER, 3
    elif is_jp_latest:
        border, bw = _UP_TEXT, 3
    else:
        border, bw = (220, 216, 240, 255), 1
    pill = Image.new("RGBA", (NODE_W, PILL_H), (0, 0, 0, 0))
    pd = ImageDraw.Draw(pill)
    pd.rounded_rectangle(
        (bw, bw, NODE_W - bw, PILL_H - bw),
        14,
        fill=(255, 255, 255, 195),
        outline=border,
        width=bw,
    )
    logo = version_image(ver, jp=True)
    if logo is not None:
        logo = _fit_node_logo(logo)
        pill.alpha_composite(
            logo, ((NODE_W - logo.width) // 2, (PILL_H - logo.height) // 2)
        )
    im.alpha_composite(pill, (nx, y))
    draw.text(
        (nx + NODE_W / 2, y + PILL_H + 16),
        _VER_SHORT[Version(ver)],
        font=font(_NAME_SIZE),
        fill=_GRAY,
        anchor="mm",
    )
    draw.text(
        (nx + NODE_W / 2, y + PILL_H + 52),
        ds_text(val),
        font=font(_VALUE_SIZE, FONT_NUM),
        fill=_DARK,
        anchor="mm",
    )
    if is_cur or is_jp_latest:
        tag = "现行" if is_cur else "日服最新"
        tcol = _MASTER if is_cur else _UP_TEXT
        tw = draw.textlength(tag, font=font(17))
        draw.rounded_rectangle(
            (nx + NODE_W - tw - 26, y - 12, nx + NODE_W + 4, y + 12), 12, fill=tcol
        )
        draw.text(
            (nx + NODE_W - tw / 2 - 11, y),
            tag,
            font=font(17),
            fill=(255, 255, 255, 255),
            anchor="mm",
        )
