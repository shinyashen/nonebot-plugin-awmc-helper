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

版式坐标均为像素实测值（素材/画布坐标系见各常量注释），调整前先跑
``local/scratch/render_dan_preview.py`` 出三样式样张比对。
"""

from dataclasses import field, dataclass

from PIL import Image, ImageDraw

from .fonts import FONT_HAN, FONT_NUM, FONT_RODIN, font
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
# 段位种名 id → 国服奖励牌序号（pic/UI_DNM_DaniPlate_XX，编号与称号同构；00 初心者不接）
_CN_PLATE_NO: dict[str, int] = _DANI_TITLE_NO
# 随机段位段位牌：（难度, 档序）→ 素材尾名（素材名前缀即 EXP/MST）
_RANDOM_PLATE: dict[tuple[str, int], str] = {
    (diff, tier): f"UI_DNM_DaniPlate_{prefix}{tier:02d}"
    for diff, prefix in (("expert", "EXP"), ("master", "MST"))
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
    base_score: str
    # 底分字段展示原文（如 "570 (+128)"）。语义（2026-10-04 用户定案）：
    # 底分 = 玩家该谱当前单曲 RA；(+N) = 打到 100.5000% 时按
    # nb_chart.new_best_score 计的 B50 净提升——best_list 须传完整 B50
    # （b35+b15，段位课题曲常为新曲）；负提升显示 +0。成绩数据源不可用时
    # 降级：达成率全 0.0000%、底分照口径算（无成绩即 0）且**不显示括号**，
    # 与歌曲卡无数据时一致。本字段只收展示串，数值由查询层计算后传入。
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


# ---------------------------------------------------------------- 版式常量
# 全部为定稿实测值；调整任何一项前先跑 local/scratch/render_dan_preview.py
# 出三样式样张比对，并同步 static/mai/pic/jp/dan/README.md。

# 画布（底图原生尺寸）
CANVAS_SIZE = (980, 928)

# 歌曲条：640×140 卡框等比缩放后顶格堆叠（无空隙），左缘对齐蝴蝶结留位
CARD_X, CARD_W, CARD_H = 272, 581, 127
CARD_TOPS = (141, 268, 395, 522)
# 卡框 asset 坐标（640×140 实测）：封面槽、标题药丸、达成率、等级、类型徽章
COVER_POS, COVER_SIZE = (12, 12), 117
TITLE_TEXT_X, TITLE_TEXT_Y = 270, 22  # y=22 为字形光学居中（25 是几何居中）
BADGE_HEIGHT, BADGE_RIGHT, BADGE_TOP = 20, 623, 13
ACHV_LEFT, ACHV_BOTTOM, ACHV_DIGIT_H = 168, 106, 44  # 从 ACHIEVEMENT 的约 H 处起画
ACHV_PCT_FONT, ACHV_PCT_GAP = 22, 5
LEVEL_LEFT, LEVEL_CENTER_Y, LEVEL_DIGIT_H = 525, 72, 55
LEVEL_LV_H, LEVEL_PLUS_H, LEVEL_LV_RAISE = 22, 16, 4  # LV 底对齐后整体再上移 4

# 底部信息行（卡框 asset 坐标）：白矩形恰好盖住烤入的「でらっくスコア」
STRIP_COVER_RECT = (506, 113, 586, 132)
STRIP_INFO_X, STRIP_INFO_RIGHT, STRIP_INFO_Y = 146, 495, 122  # 与标签 A 对齐
STRIP_INFO_SIZES = (14, 12, 10)  # 超宽逐级降字号，仍超才省略
BASE_SCORE_RIGHT, BASE_SCORE_Y, BASE_SCORE_FONT = 565, 122, 15

# 左下称号卡：白卡中心（底图坐标）
TITLE_CARD_CENTER = (194, 786)
HANAMARU_SIZE, TITLE_FIT, RANDOM_PLATE_FIT = 204, (214, 150), (218, 200)

# 血量表盘 + 同序号数字（组中心与盘心重合）
GAUGE_POS, GAUGE_SIZE = (334, 680), 180
GAUGE_NUM_CENTER, LIFE_DIGIT_H = (424, 770), 45

# 扣血药丸（组）+ 回复条：绿条中轴线对齐表盘+药丸组的中心 x=536
DAMAGE_PILL_POS, DAMAGE_PILL_SIZE, DAMAGE_PILL_PITCH = (530, 684), (208, 48), 60
DAMAGE_NUM_X = 699  # 药丸黑区（烤心右侧）水平居中位
RECOVERY_BAR_POS, RECOVERY_NUM_POS = (368, 868), (571, 882)

# 右下奖励列：组中心 x、各元素纵坐标（整组已按用户定稿左移/下移）
REWARD_CENTER = 851
LOGO_BOX, LOGO_TOP = (166, 96), 683
BONUS_WIDTH, BONUS_TOP = 140, 785
TICKET_WIDTH, TICKET_BOTTOM = 176, 900  # 底部与绿横幅底部（y=900）平齐
PLATE_BOX, PLATE_TOP = (166, 74), 815

# ---------------------------------------------------------------- 精灵图文字

# 字符 → 4×4 格序（0-9 直接用数值；符号见模块 docstring）
_SPRITE_CHARS = {"+": 10, "-": 11, ",": 12, ".": 13}
LV_CELL = 14  # MLevel 专属「LV」徽标格

# 光晕裁切阈值：alpha 以下视为空白（精灵图半透明描边不参与内容 bbox）
_HALO_THRESHOLD = 120


def _cell(sheet: Image.Image, idx: int) -> Image.Image:
    """4×4 精灵图取第 idx 格（原图坐标，不缩放）。"""
    cw, ch = sheet.width // 4, sheet.height // 4
    return sheet.crop(
        (
            (idx % 4) * cw,
            (idx // 4) * ch,
            (idx % 4 + 1) * cw,
            (idx // 4 + 1) * ch,
        )
    )


def _solid_bbox(img: Image.Image, threshold: int = _HALO_THRESHOLD):
    """实心内容 bbox（alpha ≥ threshold；淡光晕边不算），无内容返回 None。"""
    alpha = img.getchannel("A").point(lambda v: 255 if v > threshold else 0)
    return alpha.getbbox()


def _trim_horizontal(img: Image.Image) -> Image.Image:
    """只裁左右空白（保格高，数字间基线/顶底一致）。"""
    if bbox := _solid_bbox(img):
        return img.crop((bbox[0], 0, bbox[2], img.height))
    return img


def _trim_fit(img: Image.Image, height: int) -> Image.Image:
    """全裁透明边后按目标字高保比缩放。"""
    if bbox := _solid_bbox(img):
        img = img.crop(bbox)
    return img.resize((round(img.width * height / img.height), height), Image.LANCZOS)


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
        if idx in (12, 13):
            cell = _cell(sheet, idx)
            if bbox := cell.getchannel("A").getbbox():
                cell = cell.crop(bbox)
            cell = cell.resize(
                (max(round(cell.width * scale), 1), max(round(cell.height * scale), 1)),
                Image.LANCZOS,
            )
            placements.append((cell, x, h - cell.height))
            x += cell.width + round(6 * scale)
        else:
            normal = _cell(sheet, idx).resize((step, h), Image.LANCZOS)
            placements.append((normal, x, 0))
            x += step
    out = Image.new("RGBA", (x, h), (0, 0, 0, 0))
    for cell, px, py in placements:
        out.alpha_composite(cell, (px, py))
    return out


# % 字形配色（精灵图无 %，Torus 近似绘制；取自各色数字的亮 fill/暗 outline）
_PCT_COLORS = {
    "Blue": ((124, 196, 248), (30, 64, 168)),
    "Red": ((250, 168, 178), (198, 32, 74)),
    "Gold": ((250, 214, 120), (196, 138, 24)),
}
# % 实心底测量阈值与描边外扩补偿：数字可见底按实心 bbox（220）取，而 %
# stroke 会向外扩 2px，锚点同步上移使视觉底平齐
_PCT_SOLID_THRESHOLD = 220
_PCT_STROKE_COMPENSATE = 2

# 扣血/回复文字色（mock 取色）：红=扣血、绿=加血
DAMAGE_RED = (233, 60, 52)
RECOVER_GREEN = (56, 194, 74)
# 信息行深藏青（KopMBase 标题药丸底色同系）
INFO_NAVY = (28, 42, 96)

# KopMBase 难度序 → 卡框尾名
_FRAME_BY_LEVEL_INDEX = {0: "BSC", 1: "ADV", 2: "EXP", 3: "MST", 4: "MST_Re"}


# ---------------------------------------------------------------- 分区绘制


def _draw_achievement(card: Image.Image, value: float, left: int, bottom: int) -> None:
    """达成率数字（填满 ACHIEVEMENT 标签与白区底部之间）＋ 小号 %（精灵图
    无 %，Torus 同色近似）：% 底部与数字实心字形底对齐（按 bbox 实测并补偿
    描边外扩）。"""
    sheet = dan_asset(f"UI_Num_Score_1110000_{score_variant(value)}.png")
    line = _sprite_line(
        sheet, _sprite_cells(f"{value:.4f}"), ACHV_DIGIT_H / (sheet.height / 4)
    )
    top = bottom - line.height
    card.alpha_composite(line, (left, top))
    # 实心底（阈值 220 排除格底淡影），减去 stroke 外扩后作 % 锚点
    alpha = line.getchannel("A").point(lambda v: 255 if v > _PCT_SOLID_THRESHOLD else 0)
    content_bottom = (alpha.getbbox() or (0, 0, 0, line.height))[3]
    content_bottom -= _PCT_STROKE_COMPENSATE
    draw = ImageDraw.Draw(card)
    fill, outline = _PCT_COLORS[score_variant(value)]
    draw.text(
        (left + line.width + ACHV_PCT_GAP, top + content_bottom),
        "%",
        font=font(ACHV_PCT_FONT, FONT_NUM),
        fill=(*fill, 255),
        stroke_width=2,
        stroke_fill=(*outline, 255),
        anchor="ls",
    )


def _draw_level(
    card: Image.Image, song: DanSongCard, left_x: int, center_y: int
) -> None:
    """右侧 Lv 块：LV 徽标 + 等级数字 + 上标 +。

    精灵图每格横裁左右空白（保格高，数字基线一致）；LV 裁边后底对齐数字底
    （再上移 LEVEL_LV_RAISE 光学校正），+ 裁边后顶对齐数字顶；块整体在
    ``left_x`` 处统一左对齐（跨卡 LV 列对齐，右缘随位数浮动）。

    等级串非「数字[+]」形（削除曲 fallback "-" 等）整块跳过不画。
    """
    digits_text = song.level.removesuffix("+")
    if not digits_text.isdigit():
        return
    sheet = dan_asset(f"UI_NUM_MLevel_{song.level_index + 1:02d}.png")
    scale = LEVEL_DIGIT_H / (sheet.height / 4)
    digits = []
    for ch in digits_text:
        d = _trim_horizontal(_cell(sheet, int(ch)))
        digits.append(d.resize((round(d.width * scale), LEVEL_DIGIT_H), Image.LANCZOS))
    lv = _trim_fit(_cell(sheet, LV_CELL), LEVEL_LV_H)
    plus = (
        _trim_fit(_cell(sheet, _SPRITE_CHARS["+"]), LEVEL_PLUS_H)
        if song.level.endswith("+")
        else None
    )

    x = left_x
    digits_top = center_y - LEVEL_DIGIT_H // 2
    lv_top = digits_top + LEVEL_DIGIT_H - lv.height - LEVEL_LV_RAISE
    card.alpha_composite(lv, (x, lv_top))
    x += lv.width
    for d in digits:
        card.alpha_composite(d, (x, digits_top))
        x += d.width
    if plus:
        card.alpha_composite(plus, (x, digits_top))  # + 顶对齐数字顶


def _draw_strip(card: Image.Image, song: DanSongCard) -> None:
    """底部：色带左段绘信息行（定数/谱师/BPM，超宽降字号仍超才省略）；右段
    白色槽 pill 内的白矩形恰好盖住烤入的「でらっくスコア」字样，底分居中
    落在槽 pill 上。"""
    draw = ImageDraw.Draw(card)
    draw.rectangle(STRIP_COVER_RECT, fill=(255, 255, 255, 255))
    info = f"定数: {song.ds} 谱师: {song.charter} BPM: {song.bpm}"
    for size in STRIP_INFO_SIZES:
        info_font = font(size, FONT_HAN)
        if draw.textlength(info, font=info_font) <= STRIP_INFO_RIGHT - STRIP_INFO_X:
            break
    info = fit_text(info, info_font, STRIP_INFO_RIGHT - STRIP_INFO_X)
    draw.text(
        (STRIP_INFO_X, STRIP_INFO_Y),
        info,
        font=info_font,
        fill=(*INFO_NAVY, 255),
        anchor="lm",
    )
    draw.text(
        (BASE_SCORE_RIGHT, BASE_SCORE_Y),
        f"底分 {song.base_score}",
        font=font(BASE_SCORE_FONT, FONT_HAN),
        fill=(*INFO_NAVY, 255),
        anchor="mm",
    )


def _paste_type_badge(card: Image.Image, kind: str) -> int:
    """类型徽章（スタンダード/でらっくす，static 既有）：移入蓝胶囊条右端，
    h20 使条完整包裹（条 y 10-40，上下各留 5px），返回徽章左缘 x 供曲名避让。"""
    badge = assets.pic("SD.png" if kind == "std" else "DX.png")
    badge = _fit_height(badge, BADGE_HEIGHT)
    left = BADGE_RIGHT - badge.width
    card.alpha_composite(badge, (left, BADGE_TOP))
    return left


def _draw_song_card(im: Image.Image, song: DanSongCard, left: int, top: int) -> None:
    """单曲行：全部在 640×140 原尺寸卡框上作画后整体缩放，保证素材内部比例。

    卡框分区（asset 坐标，实测）：封面槽 (12,12)-(129,129)、标题药丸
    (256,10)-(628,40)、ACHIEVEMENT 标签（x146 起）下方白区至 y106 为达成率位、
    右侧色区为等级位、底部色带/白条为信息行＋底分。
    """
    frame = _FRAME_BY_LEVEL_INDEX[song.level_index]
    card = dan_asset(f"UI_CMN_RSL_KopMBase_{frame}.png").copy()
    draw = ImageDraw.Draw(card)

    # 封面（削除曲等无封面 → 默认封面 0.png）
    cover = song.cover or assets.cover(song.song_id or 0)
    card.alpha_composite(cover.resize((COVER_SIZE,) * 2, Image.LANCZOS), COVER_POS)

    # 类型徽章先贴，曲名左对齐并截断避让（y=22 光学居中，见 TITLE_TEXT_Y）
    badge_left = _paste_type_badge(card, song.kind)
    name_font = font(15, FONT_RODIN)
    name = fit_text(song.title, name_font, badge_left - TITLE_TEXT_X - 8)
    draw.text(
        (TITLE_TEXT_X, TITLE_TEXT_Y),
        name,
        font=name_font,
        fill=(255, 255, 255, 255),
        anchor="lm",
    )

    _draw_strip(card, song)
    _draw_level(card, song, LEVEL_LEFT, LEVEL_CENTER_Y)
    _draw_achievement(card, song.achievement, ACHV_LEFT, ACHV_BOTTOM)

    card = card.resize((CARD_W, CARD_H), Image.LANCZOS)
    im.alpha_composite(card, (left, top))


def _draw_dan_title_card(im: Image.Image, dan_id: str) -> None:
    """左下称号卡：白卡上花丸衬底，叠称号字图；随机段位叠段位牌。"""
    cx, cy = TITLE_CARD_CENTER
    hanamaru = _fit_height(dan_asset("UI_DNM_Icon_Hanamaru.png"), HANAMARU_SIZE)
    im.alpha_composite(hanamaru, (cx - hanamaru.width // 2, cy - hanamaru.height // 2))
    if _base_kind(dan_id) == "random":
        diff = "expert" if "_expert" in dan_id else "master"
        tier = int(dan_id.rsplit("_", 1)[1])
        plate = dan_asset(f"{_RANDOM_PLATE[(diff, tier)]}.png")
        plate = _fit_box(plate, *RANDOM_PLATE_FIT)
        im.alpha_composite(plate, (cx - plate.width // 2, cy - plate.height // 2))
        return
    title_img = dan_asset(f"UI_DNM_DaniTitle_{_DANI_TITLE_NO[dan_id]:02d}.png")
    if bbox := title_img.getchannel("A").getbbox():
        title_img = title_img.crop(bbox)
    title_img = _fit_box(title_img, *TITLE_FIT)
    im.alpha_composite(
        title_img, (cx - title_img.width // 2, cy - title_img.height // 2)
    )


def _draw_gauge(im: Image.Image, data: DanCardData) -> None:
    """血量表盘 + 同序号数字（整格步进拼接，居中叠在盘心）。"""
    variant = life_variant(data.life)
    gauge = dan_asset(f"UI_DNM_Base_Life_{variant:02d}.png").resize(
        (GAUGE_SIZE,) * 2, Image.LANCZOS
    )
    im.alpha_composite(gauge, GAUGE_POS)
    digits = dan_asset(f"UI_DNM_LifeNum_{variant:02d}.png")
    line = _sprite_line(
        digits, _sprite_cells(str(data.life)), LIFE_DIGIT_H / (digits.height / 4)
    )
    im.alpha_composite(
        line,
        (GAUGE_NUM_CENTER[0] - line.width // 2, GAUGE_NUM_CENTER[1] - line.height // 2),
    )


def _draw_damage_boxes(im: Image.Image, data: DanCardData) -> None:
    """三条扣血药丸（标签与心已烤）＋ 红色扣血数字；回复条中轴线对齐上方
    元素组中心，绿色回复数字落在条内黑药丸的心右侧（均水平居中）。"""
    draw = ImageDraw.Draw(im)
    for i, (box, value) in enumerate(
        (
            ("UI_DNM_Box_Damage_01", data.damage_great),
            ("UI_DNM_Box_Damage_02", data.damage_good),
            ("UI_DNM_Box_Damage_03", data.damage_miss),
        )
    ):
        pill = dan_asset(f"{box}.png").resize(DAMAGE_PILL_SIZE, Image.LANCZOS)
        y = DAMAGE_PILL_POS[1] + i * DAMAGE_PILL_PITCH
        im.alpha_composite(pill, (DAMAGE_PILL_POS[0], y))
        draw.text(
            (DAMAGE_NUM_X, y + DAMAGE_PILL_SIZE[1] // 2),
            f"-{value}",
            font=font(20, FONT_RODIN),
            fill=(*DAMAGE_RED, 255),
            anchor="mm",
        )
    bar = dan_asset("UI_DNM_Box_Recovery_01.png")
    im.alpha_composite(bar, RECOVERY_BAR_POS)
    draw.text(
        RECOVERY_NUM_POS,
        f"+{data.clear_bonus}",
        font=font(16, FONT_RODIN),
        fill=(*RECOVER_GREEN, 255),
        anchor="mm",
    )


def _fit_box(img: Image.Image, width: int, height: int) -> Image.Image:
    """等比缩放至完全落入 (width, height) 框（取 min 比例）。"""
    scale = min(width / img.width, height / img.height)
    return img.resize(
        (round(img.width * scale), round(img.height * scale)), Image.LANCZOS
    )


def _fit_height(img: Image.Image, height: int) -> Image.Image:
    """等比缩放至目标高。"""
    return img.resize((round(img.width * height / img.height), height), Image.LANCZOS)


def _fit_width(img: Image.Image, width: int) -> Image.Image:
    """等比缩放至目标宽。"""
    return img.resize((width, round(img.height * width / img.width)), Image.LANCZOS)


def _draw_reward(im: Image.Image, dan_id: str, logo: Image.Image | None) -> None:
    """右下奖励列：版本 logo → 通关奖励（CLEAR BONUS，待换国服）→ 奖励牌
    （随机段位为 1.5 倍奖励票，底对齐绿横幅）。"""
    if logo is not None:
        logo = _fit_box(logo, *LOGO_BOX)
        im.alpha_composite(logo, (REWARD_CENTER - logo.width // 2, LOGO_TOP))
    bonus = _fit_width(dan_asset("UI_DNM_TrackStart_Text_01.png"), BONUS_WIDTH)
    im.alpha_composite(bonus, (REWARD_CENTER - bonus.width // 2, BONUS_TOP))
    if _base_kind(dan_id) == "random":
        # 随机段位通关奖励 = 1.5 倍奖励票：裁画布留白后放大，底对齐绿横幅
        ticket = dan_asset("UI_CMN_Tix_Icon_Free.png")
        if bbox := ticket.getchannel("A").getbbox():
            ticket = ticket.crop(bbox)
        ticket = _fit_width(ticket, TICKET_WIDTH)
        im.alpha_composite(
            ticket, (REWARD_CENTER - ticket.width // 2, TICKET_BOTTOM - ticket.height)
        )
        return
    plate = assets.pic(f"UI_DNM_DaniPlate_{_CN_PLATE_NO[dan_id]:02d}.png")
    plate = _fit_box(plate, *PLATE_BOX)
    im.alpha_composite(plate, (REWARD_CENTER - plate.width // 2, PLATE_TOP))


# ---------------------------------------------------------------- 整卡渲染


def render_dan_card(data: DanCardData) -> bytes:
    """渲染段位认定卡，返回 PNG 字节（标题用底图烤字原题，背景不做修改）。"""
    im = dan_asset(f"{_BASE_BY_KIND[_base_kind(data.dan_id)]}.png").copy()

    for song, top in zip(data.songs, CARD_TOPS):
        _draw_song_card(im, song, CARD_X, top)

    _draw_dan_title_card(im, data.dan_id)
    _draw_gauge(im, data)
    _draw_damage_boxes(im, data)
    _draw_reward(im, data.dan_id, data.logo)
    return image_to_bytes(im)
