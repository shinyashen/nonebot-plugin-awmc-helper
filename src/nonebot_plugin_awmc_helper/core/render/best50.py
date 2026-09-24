"""B50 成绩图（视觉对齐 Hoshino 版 maimaiDX：core/image/best50.py + base.py）。

- 主题底图 ``{theme}/b50.png``（1400×1600），prism_plus/circle 双主题；
- 头部落雪名片：logo、名牌（落雪 name_plate → 在线 UI_Plate_XXXXXX；
  水鱼 plate 字符串 → plate_version；缺省 UI_Plate_550101）、头像
  （落雪 icon → 在线 UI_Icon_XXXXXX → QQ 头像 → 缺省 UI_Icon_509506）、
  DXRating 段位徽章（circle 高 rating 用带星版式）、Drating 数字图、
  段位认定牌（course_rank）与 でらっクラス徽章（class_rank）、称号条
  （无称号回退 B35/B15 统计条）、署名行（含数据来源）；
- 成绩行：b35 35 条 + b15 15 条，5 列布局，行卡 ``b50_score_{难度}.png``；
  标题/达成率/RA/DX 分按难度配色（白，Re:Master 紫），曲目 ID 按难度配色，
  数字一律 Torus（等宽字体过宽会与右侧 DX 分重叠）；曲目 ID 展示游戏内
  全 ID（落雪成绩缺 10000 位，水鱼已是全 id），标题超宽省略号截断。
"""

import asyncio
from io import BytesIO
from bisect import bisect_right
from pathlib import Path

import httpx
from PIL import Image, ImageDraw
from nonebot import logger
from maimai_py import (
    FCType,
    FSType,
    Player,
    RateType,
    SongType,
    LevelIndex,
    ScoreExtend,
)

from ..http import build_smart_transport
from .fonts import FONT_HAN, FONT_NUM, font
from .tools import (
    TEXT_BLUE,
    ID_TEXT_COLORS,
    DIFF_TEXT_COLORS,
    char_width,
    credit_text,
    column_width,
    image_to_bytes,
)
from .assets import assets, online_item_cache_dir
from ...config import plugin_config
from ...constants import RATE_FILE, SYNC_FILE, COMBO_FILE, SERVICE_DISPLAY

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


DIFF_BG = {
    LevelIndex.BASIC: "b50_score_basic.png",
    LevelIndex.ADVANCED: "b50_score_advanced.png",
    LevelIndex.EXPERT: "b50_score_expert.png",
    LevelIndex.MASTER: "b50_score_master.png",
    LevelIndex.ReMASTER: "b50_score_remaster.png",
}

# circle 主题 DXRating ≥14000 的星级（Hoshino _ra_pic_star 同款阈值）
RA_STAR_THRESHOLDS = [
    14000,
    14250,
    14500,
    14750,
    15000,
    15250,
    15500,
    15750,
    16000,
    16250,
    16500,
    16750,
]
RA_STAR_NUMS = [1, 2, 1, 2, 1, 2, 3, 4, 1, 2, 3, 4]

DX_STAR_FILE = "UI_GAM_Gauge_DXScoreIcon_0{num}.png"

FOOTER_COLORS = {
    "prism_plus": TEXT_BLUE,
    "circle": (249, 62, 172, 255),
}

_ITEM_HOST = "https://www.yuzuchan.moe/assets/maimaidx"
"""收藏品（牌子/头像）在线素材站，与 Hoshino 版同源。"""

_inflight: dict[Path, asyncio.Task] = {}
"""在线素材下载去重表（同文件并发只发一次请求）。"""


def game_song_id(score: ScoreExtend) -> int:
    """游戏内曲目 ID：落雪成绩的 DX 谱 id 缺 10000 位（水鱼已是全 id），展示时补全。"""
    if score.type == SongType.DX and score.id < 10000:
        return score.id + 10000
    return score.id


def truncate_title(s: str, limit: int = 18, keep: int = 17) -> str:
    """标题超宽截断：按显示宽度保留前 keep 列再加省略号（Hoshino 同款规则）。"""
    if column_width(s) <= limit:
        return s
    res, out = 0, []
    for ch in s:
        res += char_width(ord(ch))
        if res <= keep:
            out.append(ch)
    return "".join(out) + "..."


def dani_plate_num(course_rank: int) -> str:
    """段位认定牌文件序号：>10 段文件号跳一位（Hoshino 同款）。"""
    return f"{course_rank if course_rank <= 10 else course_rank + 1:02d}"


def ra_badge_num(rating: int, theme: str = "prism_plus") -> str:
    """DXRating 段位牌号：≥15000 归 11 号；circle 主题 16000–16999 用 12 号。"""
    for limit, n in RA_THRESHOLD:
        if rating < limit:
            return n
    if theme == "circle":
        if rating < 16000:
            return "11"
        if rating < 17000:
            return "12"
    return "11"


def ra_star_num(rating: int) -> str:
    idx = bisect_right(RA_STAR_THRESHOLDS, rating) - 1
    return f"0{RA_STAR_NUMS[idx]}"


async def _download_item(url: str, path: Path) -> bool:
    transport = build_smart_transport()
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10, read=30, write=10, pool=10),
            follow_redirects=True,
            transport=transport,
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            content = resp.content

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)

        await asyncio.to_thread(_write)
        return True
    except Exception as e:
        logger.warning(f"b50：在线素材拉取失败 {url}（{e}）")
        return False


async def fetch_item_image(kind: str, item_id: int) -> Image.Image | None:
    """收藏品原图（kind: plate/icon）：本地缓存 → yuzuchan 在线（可关）。

    失败返回 None，由调用方走回退链；下载按目标路径去重。
    """
    path = online_item_cache_dir(kind) / f"UI_{kind.capitalize()}_{item_id:06d}.png"
    if path.exists():
        return Image.open(path).convert("RGBA")
    if not plugin_config.awmc_assets_online:
        return None
    task = _inflight.get(path)
    if task is None:
        url = f"{_ITEM_HOST}/{kind}/{path.name}"
        task = asyncio.create_task(_download_item(url, path))
        _inflight[path] = task
    try:
        ok = await task
    finally:
        # 失败任务也要出表：滞留会让该素材进程内永不重试且 dict 无界增长
        if _inflight.get(path) is task:
            _inflight.pop(path, None)
    if ok:
        return Image.open(path).convert("RGBA")
    return None


async def _qq_avatar(qqid: int) -> Image.Image | None:
    """QQ 头像（无落雪头像时的回退）。"""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://q1.qlogo.cn/g",
                params={"b": "qq", "nk": str(qqid), "s": 100},
            )
            resp.raise_for_status()
            return Image.open(BytesIO(resp.content)).convert("RGBA")
    except Exception as e:
        logger.warning(f"b50：QQ 头像获取失败（{e}）")
        return None


def _dx_star_badge(theme: str, star: int) -> Image.Image | None:
    if star <= 0:
        return None
    pic = assets.static_path() / "mai" / "pic"
    path = pic / theme / DX_STAR_FILE.format(num=star)
    if not path.exists():
        path = pic / DX_STAR_FILE.format(num=star)
    if not path.exists():
        return None
    return Image.open(path).convert("RGBA")


def _rate_badge(theme: str, rate: RateType) -> Image.Image | None:
    name = RATE_FILE.get(rate.name, rate.name)
    return assets.pic_optional(f"UI_TTR_Rank_{name}.png", theme)


def _combo_icon(fc: FCType | None) -> Image.Image | None:
    if fc is None:
        return None
    name = COMBO_FILE.get(fc.name.lower())
    return assets.pic_optional(f"UI_MSS_MBase_Icon_{name}.png")


def _sync_icon(fs: FSType | None) -> Image.Image | None:
    if fs is None:
        return None
    name = SYNC_FILE.get(fs.name.lower())
    return assets.pic_optional(f"UI_MSS_MBase_Icon_{name}.png")


async def _draw_header(
    im: Image.Image,
    draw: ImageDraw.ImageDraw,
    *,
    player_name: str,
    player: Player | None,
    qqid: int | None,
    rating: int,
    rating_b35: int,
    rating_b15: int,
    theme: str,
) -> None:
    """头部落雪名片，元素与层级顺序照搬 Hoshino PlayerBest50.draw。"""
    pic = assets.static_path() / "mai" / "pic"
    im.alpha_composite(
        Image.open(pic / theme / "logo.png").convert("RGBA").resize((249, 120)),
        (14, 60),
    )

    # 名牌：水鱼版本牌字符串 → plate_version；落雪收藏牌 → 在线素材；缺省 550101
    plate_item = getattr(player, "name_plate", None)
    plate_img: Image.Image | None = None
    if isinstance(plate_item, str):
        candidate = assets.static_path() / "mai" / "plate_version" / f"{plate_item}.png"
        if candidate.exists():
            plate_img = Image.open(candidate).convert("RGBA")
    elif plate_item is not None:
        plate_img = await fetch_item_image("plate", plate_item.id)
    if plate_img is None:
        plate_img = Image.open(pic / "UI_Plate_550101.png").convert("RGBA")
    im.alpha_composite(plate_img.resize((800, 130)), (300, 60))

    # 头像：落雪 icon → QQ 头像 → 缺省 509506
    icon_img = None
    icon_item = getattr(player, "icon", None)
    if icon_item is not None:
        icon_img = await fetch_item_image("icon", icon_item.id)
    if icon_img is None and qqid is not None:
        icon_img = await _qq_avatar(qqid)
    if icon_img is None:
        icon_img = Image.open(pic / "UI_Icon_509506.png").convert("RGBA")
    im.alpha_composite(icon_img.resize((120, 120)), (305, 65))

    # DXRating 段位徽章 + rating 数字（circle 高 rating 用带星版式）
    badge_size, star_img = (186, 35), None
    num_x, num_y, num_gap, num_size = 520, 80, 15, (17, 20)
    if theme == "circle" and rating >= 14000:
        badge_size = (170, 35)
        star_img = (
            Image.open(pic / theme / f"UI_CMN_DXRating_Star_{ra_star_num(rating)}.png")
            .convert("RGBA")
            .resize((21, 35))
        )
        num_x, num_y, num_gap, num_size = 515, 82, 13, (14, 17)
    im.alpha_composite(
        Image.open(pic / theme / f"UI_CMN_DXRating_{ra_badge_num(rating, theme)}.png")
        .convert("RGBA")
        .resize(badge_size),
        (435, 72),
    )
    if star_img is not None:
        im.alpha_composite(star_img, (590, 72))
    for n, digit in enumerate(f"{rating:05d}"):
        im.alpha_composite(
            Image.open(pic / f"UI_NUM_Drating_{digit}.png")
            .convert("RGBA")
            .resize(num_size),
            (num_x + num_gap * n, num_y),
        )

    im.alpha_composite(Image.open(pic / "Name.png").convert("RGBA"), (435, 115))

    # 段位认定牌（水鱼无该字段时回退 additional_rating，再回退 0）
    course_rank = getattr(player, "course_rank", None)
    if course_rank is None:
        course_rank = getattr(player, "additional_rating", 0) or 0
    im.alpha_composite(
        Image.open(pic / f"UI_DNM_DaniPlate_{dani_plate_num(course_rank)}.png")
        .convert("RGBA")
        .resize((80, 32)),
        (625, 120),
    )
    class_rank = getattr(player, "class_rank", 0) or 0
    im.alpha_composite(
        Image.open(pic / f"UI_FBR_Class_{class_rank:02d}.png")
        .convert("RGBA")
        .resize((90, 54)),
        (620, 60),
    )

    # 称号条：有称号用对应色底 + 称号名；无则彩虹底 + B35/B15 统计
    trophy = getattr(player, "trophy", None)
    shougou_dir = assets.static_path() / "mai" / "shougou"
    if trophy is not None:
        color = trophy.color if trophy.color else "Normal"
        if not (shougou_dir / f"UI_CMN_Shougou_{color}.png").exists():
            color = "Normal"
        shougou = Image.open(shougou_dir / f"UI_CMN_Shougou_{color}.png").resize(
            (270, 27)
        )
        trophy_text, trophy_font = trophy.name, font(14, FONT_HAN)
    else:
        shougou = Image.open(shougou_dir / "UI_CMN_Shougou_Rainbow.png").resize(
            (270, 27)
        )
        trophy_text = f"B35: {rating_b35} + B15: {rating_b15} = {rating}"
        trophy_font = font(14, FONT_NUM)
    im.alpha_composite(shougou, (435, 160))
    draw.text((570, 172), trophy_text, font=trophy_font, fill="#000000", anchor="mm")

    draw.text(
        (445, 135),
        player_name,
        font=font(20, FONT_HAN),
        fill="#000000",
        anchor="lm",
    )


def draw_score_row(
    im: Image.Image,
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    score: ScoreExtend,
    theme: str,
) -> None:
    """单张 B50 风格成绩行卡（b50_score_* 底图，B50 大图与等级完成表共用）。"""
    diff = score.level_index.value  # LevelIndex.value 恰为 DIFF_*_COLORS 下标 0-4
    pic = assets.static_path() / "mai" / "pic"
    im.alpha_composite(
        Image.open(pic / DIFF_BG[score.level_index]).convert("RGBA"),
        (x, y),
    )
    cover = assets.cover(score.id % 10000).resize((75, 75))
    im.alpha_composite(cover, (x + 12, y + 12))
    type_abbr = "DX" if score.type.name == "DX" else "SD"
    type_path = pic / f"{type_abbr}.png"
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
    star = _dx_star_badge(theme, score.dx_star or 0)
    if star is not None:
        im.alpha_composite(star.resize((47, 26)), (x + 217, y + 80))

    draw.text(
        (x + 26, y + 98),
        str(game_song_id(score)),
        font=font(13, FONT_NUM),
        fill=ID_TEXT_COLORS[diff],
        anchor="mm",
    )
    draw.text(
        (x + 93, y + 14),
        truncate_title(score.title),
        font=font(14, FONT_HAN),
        fill=DIFF_TEXT_COLORS[diff],
        anchor="lm",
    )
    draw.text(
        (x + 93, y + 38),
        f"{score.achievements or 0:.4f}%",
        font=font(30, FONT_NUM),
        fill=DIFF_TEXT_COLORS[diff],
        anchor="lm",
    )
    draw.text(
        (x + 219, y + 65),
        f"{score.dx_score or 0}/{score.level_dx_score}",
        font=font(15, FONT_NUM),
        fill=DIFF_TEXT_COLORS[diff],
        anchor="mm",
    )
    draw.text(
        (x + 93, y + 65),
        f"{score.level_value} -> {int(score.dx_rating or 0)}",
        font=font(15, FONT_NUM),
        fill=DIFF_TEXT_COLORS[diff],
        anchor="lm",
    )


async def draw_b50_nb(
    player_name: str,
    rating: int,
    rating_b35: int,
    rating_b15: int,
    scores_b35: list[ScoreExtend],
    scores_b15: list[ScoreExtend],
    *,
    player: Player | None = None,
    qqid: int | None = None,
    service: str | None = None,
    theme: str = "prism_plus",
) -> Image.Image:
    """NB 版 B50 大图（player 携带落雪名片信息，service 为绑定源键）。"""
    pic = assets.static_path() / "mai" / "pic"
    im = Image.open(pic / theme / "b50.png").convert("RGBA")
    draw = ImageDraw.Draw(im)

    await _draw_header(
        im,
        draw,
        player_name=player_name,
        player=player,
        qqid=qqid,
        rating=rating,
        rating_b35=rating_b35,
        rating_b15=rating_b15,
        theme=theme,
    )

    # 成绩行：b35 从 y=235、b15 从 y=1085，均 5 列、行距 114（Hoshino 布局）
    for data, initial_y in ((scores_b35, 235), (scores_b15, 1085)):
        for num, score in enumerate(data):
            row, col = divmod(num, 5)
            x = 16 + col * 276
            y = initial_y + row * 114
            draw_score_row(im, draw, x, y, score, theme)

    service_name = SERVICE_DISPLAY.get(service or "", "")
    footer_color = FOOTER_COLORS.get(theme, FOOTER_COLORS["prism_plus"])
    draw.text(
        (700, 1570),
        credit_text(service_name or None),
        font=font(22, FONT_HAN),
        fill=footer_color,
        anchor="mm",
        stroke_width=5,
        stroke_fill=(255, 255, 255, 255),
    )
    return im


async def best50_bytes(
    player_name: str,
    rating: int,
    rating_b35: int,
    rating_b15: int,
    scores_b35: list[ScoreExtend],
    scores_b15: list[ScoreExtend],
    *,
    player: Player | None = None,
    qqid: int | None = None,
    service: str | None = None,
    theme: str = "prism_plus",
) -> bytes:
    return image_to_bytes(
        await draw_b50_nb(
            player_name,
            rating,
            rating_b35,
            rating_b15,
            scores_b35,
            scores_b15,
            player=player,
            qqid=qqid,
            service=service,
            theme=theme,
        )
    )
