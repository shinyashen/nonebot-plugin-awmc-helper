"""段位认定卡渲染（普通段位/真段位/随机段位，2026-10-04）。

素材与版式约定见 ``static/mai/pic/jp/dan/README.md``。要点：

- 素材解析 **国服区优先**（``pic/dan/``）→ 日服原版兜底（``pic/jp/dan/``）；
  Box_Recovery / TrackStart 两张后续换国服素材时落盘同名文件即自动切换；
- 底图三变体（烤字标题：段位認定/真段位認定/ランダム段位認定）原样使用，
  不做任何修改；
- 三类数字精灵图均为 4×4 格：0-9→格 0-9、``+``→10、``-``→11、``.``→13
  （方点小数位，裁边贴底）；MLevel 的 ``LV`` 徽标（格 14）与 ``+`` 裁透明边
  后按内容拼接（等级块跨卡统一左对齐，LV 底对齐数字底、+ 顶对齐数字顶，
  裁切含 alpha 阈值 120 去光晕）；
- 血量表盘（Base_Life）与血量数字（LifeNum）**同序号成套**：(0,10]→03、
  (10,100]→02、>100→01，04 暂不用；
- 达成率数字分色：[0,80) Blue / [80,97) Red / [97,101] Gold；% 字形精灵图
  没有，用 Torus 字体取同色系近似绘制，底部与数字实心字形底对齐；
- 等级数字色号 = 难度序 +1（绿黄红紫白），宴谱 10 暂不用；
- 扣血红字 / 回复绿字（FOT-NewRodin 日文字体）；
- 类型徽章（``pic/SD.png``/``pic/DX.png``，static 既有）移入标题蓝胶囊条
  右端（h20，曲名截断避让）；卡框烤入的「でらっくスコア」字样由底部白条
  的白矩形（恰好盖字）清除，白条左绘信息行、槽 pill 上居中绘底分。
"""

from dataclasses import field, dataclass

from PIL import Image, ImageDraw

from .fonts import FONT_HAN, FONT_RODIN, font
from .tools import fit_text, image_to_bytes
from .assets import assets

# ---------------------------------------------------------------- 素材定位


def dan_asset(name: str) -> Image.Image:
    """段位素材读取：国服区 ``pic/dan/`` 优先，日服区 ``pic/jp/dan/`` 兜底。"""
    for sub in ("dan", "jp/dan"):
        path = assets.static_path() / "mai" / "pic" / sub / name
        if path.exists():
            return assets.get(path)
    raise FileNotFoundError(f"段位素材缺失: {name}（pic/dan 与 pic/jp/dan 均无）")


# 段位种名 id → DaniTitle 称号字图序号（无 11=皆伝：现行段位无此段位，冗余不接）
_DANI_TITLE_NO: dict[str, int] = {
    **{f"{i}dan": i for i in range(1, 11)},
    **{f"shin_{i}dan": 11 + i for i in range(1, 11)},
    "shin_kaiden": 22,
    "ura_kaiden": 23,
}
# 段位种名 id → 国服奖励牌序号（pic/UI_DNM_DaniPlate_XX，编号同上；00 初心者不接）
_CN_PLATE_NO: dict[str, int] = _DANI_TITLE_NO
# 随机段位段位牌：难度 + 档序 → 素材尾名
_RANDOM_PLATE: dict[tuple[str, int], str] = {
    (diff, tier): f"UI_DNM_DaniPlate_{diff.upper()}{tier:02d}"
    for diff in ("exp", "mst")
    for tier in range(1, 5)
}

_BASE_BY_KIND = {
    "normal": "UI_DNM_Result_Base_01",
    "shin": "UI_DNM_Result_Base_02",
    "random": "UI_DNM_Result_Base_03",
}

# ---------------------------------------------------------------- 渲染输入


@dataclass
class DanSongCard:
    """段位卡单曲行输入。

    ``song_id`` 供封面取用（削除曲等 None → 默认封面，卡面仅标题有效）；
    ``cover`` 显式给图时优先（如已解析出的规范表封面）。
    """

    title: str
    kind: str  # std / dx（类型徽章与 KopMBase 卡框选择）
    level: str  # 显示等级（"15" / "14+"）
    level_index: int  # 难度序 0..4（basic..remaster）→ 等级数字色号
    ds: str  # 定数展示（"15.0"）
    charter: str
    bpm: str
    base_score: str  # 底分字段展示原文（"0 (+0)"）
    achievement: float  # 达成率 0~101
    song_id: int | None = None
    cover: Image.Image | None = None


@dataclass
class DanCardData:
    """段位卡整体输入（标题用底图烤字原题，不做中文替换）。"""

    dan_id: str  # 段位种名 id（1dan..ura_kaiden / random_expert_* / random_master_*）
    life: int
    damage_great: int
    damage_good: int
    damage_miss: int
    clear_bonus: int
    songs: list[DanSongCard] = field(default_factory=list)
    logo: Image.Image | None = None  # 奖励区顶部 logo；None = 跳过不画


# ---------------------------------------------------------------- 取值规则


def life_variant(life: int) -> int:
    """初始血量 → 表盘/数字序号：(0,10]→3、(10,100]→2、>100→1（04 暂不用）。"""
    if life > 100:
        return 1
    if life > 10:
        return 2
    return 3


def score_variant(achievement: float) -> str:
    """达成率 → 数字精灵图色名：[97,101] Gold / [80,97) Red / [0,80) Blue。"""
    if achievement >= 97:
        return "Gold"
    if achievement >= 80:
        return "Red"
    return "Blue"


def _base_kind(dan_id: str) -> str:
    if dan_id.startswith("random_"):
        return "random"
    if dan_id.startswith("shin") or dan_id == "ura_kaiden":
        return "shin"
    return "normal"


# ---------------------------------------------------------------- 精灵图文字

# 字符 → 4×4 格序（0-9 直接用数值；符号见模块 docstring）
_SPRITE_CHARS = {"+": 10, "-": 11, ",": 12, ".": 13}
LV_CELL = 14  # MLevel 专属「LV」徽标格


def _sprite_line(sheet: Image.Image, cells: list[int], scale: float) -> Image.Image:
    """按 4×4 格序逐格裁切横排拼接。

    常规格步进 = 格宽（含素材自带留白）；`,`/`.`（格 12/13）为小字形：裁掉
    透明边**保比缩放**（禁拉伸）、贴底放置、前后留 6px——对齐 mock 小数位
    紧凑观感。
    """
    cell_w, cell_h = sheet.width // 4, sheet.height // 4
    step = round(cell_w * scale)
    h = round(cell_h * scale)
    placements: list[tuple[Image.Image, int, int]] = []  # (图, x, y)
    x = 0
    for idx in cells:
        cell = sheet.crop(
            (
                (idx % 4) * cell_w,
                (idx // 4) * cell_h,
                (idx % 4 + 1) * cell_w,
                (idx // 4 + 1) * cell_h,
            )
        )
        if idx in (12, 13):
            if bbox := cell.getchannel("A").getbbox():
                cell = cell.crop(bbox)
            cell = cell.resize(
                (max(round(cell.width * scale), 1), max(round(cell.height * scale), 1)),
                Image.LANCZOS,
            )
            placements.append((cell, x, h - cell.height))
            x += cell.width + round(6 * scale)
        else:
            placements.append((cell.resize((step, h), Image.LANCZOS), x, 0))
            x += step
    out = Image.new("RGBA", (x, h), (0, 0, 0, 0))
    for cell, px, py in placements:
        out.alpha_composite(cell, (px, py))
    return out


def _sprite_cells(text: str) -> list[int]:
    """文本 → 格序（LV 徽标不经此函数，由 :func:`_draw_level` 裁格单独处理）。"""
    cells: list[int] = []
    for ch in text:
        if ch.isdigit():
            cells.append(int(ch))
        elif ch in _SPRITE_CHARS:
            cells.append(_SPRITE_CHARS[ch])
        else:
            raise ValueError(f"精灵图不支持的字符: {ch!r}")
    return cells


# % 字形配色（精灵图无 %，Torus 近似绘制；取自各色数字的亮 fill/暗 outline）
_PCT_COLORS = {
    "Blue": ((124, 196, 248), (30, 64, 168)),
    "Red": ((250, 168, 178), (198, 32, 74)),
    "Gold": ((250, 214, 120), (196, 138, 24)),
}
# 扣血/回复文字色（mock 取色）：红=扣血、绿=加血
DAMAGE_RED = (233, 60, 52)
RECOVER_GREEN = (56, 194, 74)
# 信息行深藏青（KopMBase 标题药丸底色同系）
INFO_NAVY = (28, 42, 96)


def _draw_achievement(card: Image.Image, value: float, left: int, bottom: int) -> None:
    """达成率数字（填满 ACHIEVEMENT 标签与白区底部之间，格高 44）＋ 小号 %
    （精灵图无 %，Torus 同色近似）：% 底部与数字字形的实际底部对齐（按内容
    bbox 取，排除精灵格留白与淡影）。"""
    sheet = dan_asset(f"UI_Num_Score_1110000_{score_variant(value)}.png")
    scale = 44 / (sheet.height / 4)
    line = _sprite_line(sheet, _sprite_cells(f"{value:.4f}"), scale)
    top = bottom - line.height
    card.alpha_composite(line, (left, top))
    # 阈值 220：只取实心字形（排除格底淡影），实测可见底 = 行内 y41；
    # % 的 stroke 向外扩 2px，锚点再上移 2px 使视觉底与数字可见底平齐
    alpha = line.getchannel("A").point(lambda v: 255 if v > 220 else 0)
    content_bottom = (alpha.getbbox() or (0, 0, 0, line.height))[3] - 2
    draw = ImageDraw.Draw(card)
    fill, outline = _PCT_COLORS[score_variant(value)]
    draw.text(
        (left + line.width + 5, top + content_bottom),
        "%",
        font=font(22, "Torus SemiBold.otf"),
        fill=(*fill, 255),
        stroke_width=2,
        stroke_fill=(*outline, 255),
        anchor="ls",
    )


def _draw_level(
    card: Image.Image, song: DanSongCard, left_x: int, center_y: int
) -> None:
    """右侧 Lv 块：LV 徽标 + 等级数字 + 上标 +。

    精灵图每格裁下后**再横裁一次左右空白**（保格高，数字基线一致）；LV 裁边
    后底对齐数字底（再上移 4px 光学校正），+ 裁边后顶对齐数字顶；块整体在
    ``left_x`` 处统一左对齐（跨卡 LV 列对齐，右缘随位数浮动）。
    """
    sheet = dan_asset(f"UI_NUM_MLevel_{song.level_index + 1:02d}.png")
    cell_w, cell_h = sheet.width / 4, sheet.height / 4
    scale = 55 / cell_h  # 数字格高 55

    def cell(idx: int) -> Image.Image:
        return sheet.crop(
            (
                int((idx % 4) * cell_w),
                int((idx // 4) * cell_h),
                int((idx % 4 + 1) * cell_w),
                int((idx // 4 + 1) * cell_h),
            )
        )

    def trim_horizontal(img: Image.Image) -> Image.Image:
        """只裁左右空白（保格高，数字间基线/顶底一致）。淡光晕边不算内容：
        alpha 阈值 120 以下视为空白，避免 bbox 被半透明描边撑大。"""
        alpha = img.getchannel("A").point(lambda v: 255 if v > 120 else 0)
        if bbox := alpha.getbbox():
            return img.crop((bbox[0], 0, bbox[2], img.height))
        return img

    def trim_full(img: Image.Image, glyph_h: float) -> Image.Image:
        """全裁透明边（同阈值）后按目标字高保比缩放。"""
        alpha = img.getchannel("A").point(lambda v: 255 if v > 120 else 0)
        if bbox := alpha.getbbox():
            img = img.crop(bbox)
        return img.resize(
            (round(img.width * glyph_h / img.height), round(glyph_h)),
            Image.LANCZOS,
        )

    digits = []
    for ch in song.level.removesuffix("+"):
        d = trim_horizontal(cell(int(ch)))
        digits.append(d.resize((round(d.width * scale), 55), Image.LANCZOS))
    lv = trim_full(cell(LV_CELL), 22)
    plus = trim_full(cell(10), 16) if song.level.endswith("+") else None

    gap = 0
    total_w = lv.width + gap + sum(d.width for d in digits) + gap * len(digits)
    if plus:
        total_w += gap + plus.width
    x = left_x  # 跨卡统一左对齐（右缘随位数浮动）
    digits_top = center_y - 27
    # LV 较数字底再上移 4px（底部对齐偏沉，光学居中）
    card.alpha_composite(lv, (x, digits_top + 55 - lv.height - 4))
    x += lv.width + gap
    for d in digits:
        card.alpha_composite(d, (x, digits_top))
        x += d.width + gap
    if plus:
        card.alpha_composite(plus, (x - gap, digits_top))  # + 顶对齐数字顶


def _draw_strip(card: Image.Image, song: DanSongCard) -> None:
    """底部：色带左段绘信息行（定数/谱师/BPM，超宽降字号仍超才省略）；右段
    白色槽 pill 内的白矩形**刚好盖住**烤入的「でらっくスコア」字样（实测
    bbox ≈ 511-581 × 117-131），底分居中落在槽 pill 上。"""
    draw = ImageDraw.Draw(card)
    draw.rectangle([506, 113, 586, 132], fill=(255, 255, 255, 255))
    # 信息行与上方 ACHIEVEMENT 标签的 A 对齐（实测标签起点 x=146），单空格
    # 分隔更紧凑，超宽按 14→12→10 降字号仍超才省略
    info = f"定数: {song.ds} 谱师: {song.charter} BPM: {song.bpm}"
    for size in (14, 12, 10):
        info_font = font(size, FONT_HAN)
        if draw.textlength(info, font=info_font) <= 495 - 146:
            break
    info = fit_text(info, info_font, 495 - 146)
    draw.text((146, 122), info, font=info_font, fill=(*INFO_NAVY, 255), anchor="lm")
    draw.text(
        (565, 122),
        f"底分 {song.base_score}",
        font=font(15, FONT_HAN),
        fill=(*INFO_NAVY, 255),
        anchor="mm",
    )


def _paste_type_badge(card: Image.Image, kind: str) -> None:
    """类型徽章（スタンダード/でらっくす，static 既有）：移入蓝胶囊条右端，
    h20 使条完整包裹（条 y 10-40，上下各留 5px），返回徽章左缘 x 供曲名避让。"""
    badge = assets.pic("SD.png" if kind == "std" else "DX.png")
    scale = 20 / badge.height
    badge = badge.resize((round(badge.width * scale), 20), Image.LANCZOS)
    left = 623 - badge.width
    card.alpha_composite(badge, (left, 13))
    return left


# ---------------------------------------------------------------- 整卡渲染


def _draw_song_card(im: Image.Image, song: DanSongCard, left: int, top: int) -> None:
    """单曲行：全部在 640×140 原尺寸卡框上作画后整体缩放，保证素材内部比例。

    卡框分区（asset 坐标，实测）：封面槽 (12,12)-(128,128)、标题药丸
    (256,10)-(628,40)（曲名左对齐、日文字体、超宽省略）、ACHIEVEMENT 标签
    下方白区 60-106 为达成率位、右侧色区（白区右缘至卡缘）上等级下类型徽章、
    底部白条 106-130 为信息行＋底分（白矩形盖掉烤入的「でらっくスコア」）。
    """
    frame_name = {0: "BSC", 1: "ADV", 2: "EXP", 3: "MST", 4: "MST_Re"}[song.level_index]
    card = dan_asset(f"UI_CMN_RSL_KopMBase_{frame_name}.png").copy()
    draw = ImageDraw.Draw(card)

    # 封面（削除曲等无封面 → 默认封面 0.png）
    cover = song.cover
    if cover is None:
        cover = assets.cover(song.song_id or 0)
    card.alpha_composite(cover.resize((117, 117), Image.LANCZOS), (12, 12))

    # 类型徽章移入蓝胶囊条右端，曲名截断避让徽章（先贴徽章再写曲名）
    badge_left = _paste_type_badge(card, song.kind)

    # 曲名（蓝条内左对齐，日文字体，超宽省略；字号收在药丸高度内）。
    # 锚点 y=22：字形 bbox 实测中心 161.5，较药丸几何中心 163.7 略高 2px
    # （拉丁大写字形的光学居中位）；25 为几何居中、18 明显偏高，取中
    name_font = font(15, FONT_RODIN)
    name = fit_text(song.title, name_font, badge_left - 270 - 8)
    draw.text((270, 22), name, font=name_font, fill=(255, 255, 255, 255), anchor="lm")

    # 右侧色区：白槽 pill 先处理（信息行/底分），再画放大后的等级块
    _draw_strip(card, song)
    _draw_level(card, song, 525, 72)
    # 达成率从 ACHIEVEMENT 标签（实测 x 146 起）的约 H 处（x≈168）起画
    _draw_achievement(card, song.achievement, 168, 106)

    # 四条歌曲条顶格堆叠（无空隙）
    card = card.resize((581, 127), Image.LANCZOS)
    im.alpha_composite(card, (left, top))


def _draw_dan_title_card(im: Image.Image, dan_id: str) -> None:
    """左下称号卡：底图白卡上叠花丸＋称号字图；随机段位为花丸＋段位牌。"""
    hanamaru = dan_asset("UI_DNM_Icon_Hanamaru.png")
    scale = 204 / hanamaru.width
    hanamaru = hanamaru.resize((204, round(hanamaru.height * scale)), Image.LANCZOS)
    im.alpha_composite(hanamaru, (194 - 102, 786 - hanamaru.height // 2))
    if _base_kind(dan_id) == "random":
        diff, tier = ("exp" if "expert" in dan_id else "mst"), int(dan_id[-1])
        plate = dan_asset(f"{_RANDOM_PLATE[(diff, tier)]}.png")
        scale = min(218 / plate.width, 200 / plate.height)
        plate = plate.resize(
            (round(plate.width * scale), round(plate.height * scale)), Image.LANCZOS
        )
        im.alpha_composite(plate, (194 - plate.width // 2, 786 - plate.height // 2))
        return
    title_img = dan_asset(f"UI_DNM_DaniTitle_{_DANI_TITLE_NO[dan_id]:02d}.png")
    # 称号字图按内容 bbox 裁透明边后适配卡宽
    if bbox := title_img.getchannel("A").getbbox():
        title_img = title_img.crop(bbox)
    scale = min(214 / title_img.width, 150 / title_img.height)
    title_img = title_img.resize(
        (round(title_img.width * scale), round(title_img.height * scale)), Image.LANCZOS
    )
    im.alpha_composite(
        title_img, (194 - title_img.width // 2, 786 - title_img.height // 2)
    )


def _draw_gauge(im: Image.Image, data: DanCardData) -> None:
    """血量表盘 + 同序号数字（居中叠在盘心）。

    数字格高 45（56 的 80%），每格横裁空白（alpha 阈值 120 去光晕）后紧贴拼接。
    """
    variant = life_variant(data.life)
    gauge = dan_asset(f"UI_DNM_Base_Life_{variant:02d}.png").resize(
        (180, 180), Image.LANCZOS
    )
    im.alpha_composite(gauge, (334, 680))
    digits = dan_asset(f"UI_DNM_LifeNum_{variant:02d}.png")
    line = _sprite_line(digits, _sprite_cells(str(data.life)), 45 / (digits.height / 4))
    im.alpha_composite(line, (424 - line.width // 2, 770 - line.height // 2))


def _draw_damage_boxes(im: Image.Image, data: DanCardData) -> None:
    """三条扣血药丸（标签与心已烤）＋ 红色扣血数字；回复条左移至表盘与扣血
    药丸正下方，绿色回复数字落在条内黑药丸的心右侧。

    数字均以药丸黑区（烤心右侧）水平居中，字号收在黑区高度内。
    """
    draw = ImageDraw.Draw(im)
    for i, (box, value) in enumerate(
        (
            ("UI_DNM_Box_Damage_01", data.damage_great),
            ("UI_DNM_Box_Damage_02", data.damage_good),
            ("UI_DNM_Box_Damage_03", data.damage_miss),
        )
    ):
        pill = dan_asset(f"{box}.png").resize((208, 48), Image.LANCZOS)
        y = 684 + i * 60
        im.alpha_composite(pill, (530, y))
        # 黑区 asset x49-98 → abs 628-726，烤心至 ~674，数字区 676-722 居中
        draw.text(
            (699, y + 24),
            f"-{value}",
            font=font(20, FONT_RODIN),
            fill=(*DAMAGE_RED, 255),
            anchor="mm",
        )
    bar = dan_asset("UI_DNM_Box_Recovery_01.png")
    # 绿条中轴线对齐上方元素（表盘 334-514 + 扣血条 530-738）的组中心 x=536
    im.alpha_composite(bar, (368, 868))
    # 黑药丸 asset x146-236 → abs 514-604，烤心至 ~538，数字区居中 571
    draw.text(
        (571, 882),
        f"+{data.clear_bonus}",
        font=font(16, FONT_RODIN),
        fill=(*RECOVER_GREEN, 255),
        anchor="mm",
    )


def _draw_reward(im: Image.Image, dan_id: str, logo: Image.Image | None) -> None:
    """右下奖励列：版本 logo → 通关奖励（CLEAR BONUS，待换国服）→ 奖励牌/奖励票。

    整组向左下偏移（-15, +15），使组的横中轴与左侧元素组的横中轴（y≈786）对齐。
    """
    dx, dy = -15, 15
    center = 866 + dx
    if logo is not None:
        scale = min(166 / logo.width, 96 / logo.height)
        fitted = logo.resize(
            (round(logo.width * scale), round(logo.height * scale)), Image.LANCZOS
        )
        im.alpha_composite(fitted, (center - fitted.width // 2, 668 + dy))
    bonus = dan_asset("UI_DNM_TrackStart_Text_01.png")
    scale = 140 / bonus.width
    bonus = bonus.resize((140, round(bonus.height * scale)), Image.LANCZOS)
    im.alpha_composite(bonus, (center - 70, 770 + dy))
    if _base_kind(dan_id) == "random":
        # 随机段位通关奖励 = 1.5 倍奖励票：按内容 bbox 裁掉画布留白后放大
        ticket = dan_asset("UI_CMN_Tix_Icon_Free.png")
        if bbox := ticket.getchannel("A").getbbox():
            ticket = ticket.crop(bbox)
        scale = 176 / ticket.width
        ticket = ticket.resize(
            (round(ticket.width * scale), round(ticket.height * scale)), Image.LANCZOS
        )
        im.alpha_composite(ticket, (center - ticket.width // 2, 900 - ticket.height))
        return
    plate = assets.pic(f"UI_DNM_DaniPlate_{_CN_PLATE_NO[dan_id]:02d}.png")
    scale = min(166 / plate.width, 74 / plate.height)
    plate = plate.resize(
        (round(plate.width * scale), round(plate.height * scale)), Image.LANCZOS
    )
    im.alpha_composite(plate, (center - plate.width // 2, 800 + dy))


def render_dan_card(data: DanCardData) -> bytes:
    """渲染段位认定卡，返回 PNG 字节（标题用底图烤字原题，背景不做修改）。"""
    im = dan_asset(f"{_BASE_BY_KIND[_base_kind(data.dan_id)]}.png").copy()

    # 四条歌曲条顶格堆叠（无空隙）：127px 高 × 4 = 508，恰好铺满歌曲区
    tops = [141, 268, 395, 522]
    for song, top in zip(data.songs, tops):
        _draw_song_card(im, song, 272, top)

    _draw_dan_title_card(im, data.dan_id)
    _draw_gauge(im, data)
    _draw_damage_boxes(im, data)
    _draw_reward(im, data.dan_id, data.logo)
    return image_to_bytes(im)
