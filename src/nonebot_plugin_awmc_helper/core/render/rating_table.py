"""等级完成表（NB core/image/rating_table.py::DrawRatingTable 的移植）。

打开预渲染底图（``rating_table/{level}.png``），绘制统计头（通关数、
S~SSS+ 评级分布、Sync/FC 分布）并逐谱面盖章：普通模式盖评级章（达成
≥100 金框）、FC/AP 计划盖 FC 章连击徽章；lv15 走三列大图布局。
缺失该谱面成绩的谱面不盖章。
"""

from PIL import Image
from maimai_py import Song, FCType, FSType, RateType, SongDifficulty

from . import table_template
from .fonts import FONT_NUM, FONT_RODIN, font
from .tools import TEXT_BLUE
from .assets import assets
from ...constants import RATE_FILE, SYNC_FILE, COMBO_FILE
from .table_layout import (
    LV15_COLS,
    RATING_COLS,
    LV15_START_X,
    LV15_START_Y,
    LV15_COL_STEP,
    LV15_ROW_STEP,
    RATING_START_X,
    RATING_START_Y,
    RATING_GRID_STEP,
    RATING_GROUP_GAP,
    group_by_ds,
)
from .table_template import FONT_BLUE

# NB constants 同源：统计键序与阈值表
STATISTICS_KEYS = [
    "clear",
    "s",
    "sp",
    "ss",
    "ssp",
    "sss",
    "sssp",
    "sync",
    "fc",
    "fcp",
    "ap",
    "app",
    "fs",
    "fsp",
    "fsd",
    "fsdp",
]
RANK_SP = [
    "d",
    "c",
    "b",
    "bb",
    "bbb",
    "a",
    "aa",
    "aaa",
    "s",
    "sp",
    "ss",
    "ssp",
    "sss",
    "sssp",
]
COMBO_SP = ["fc", "fcp", "ap", "app"]
SYNC_D_SP = ["fs", "fsp", "fsd", "fsdp"]

_COMPLETED_BG = "complete_1.png"
_UNFINISHED_BG = "unfinished_1.png"


def _rate_only(ds: float, ach: float) -> str:
    """达成率 → 评级键（小写，NB compute_rating(onlyrate=True) 同义）。"""
    return RateType._from_achievement(ach).name.lower()


class _Stats:
    def __init__(self) -> None:
        self.data = dict.fromkeys(STATISTICS_KEYS, 0)

    def add_rank(self, rate: str) -> None:
        if rate in RANK_SP[-6:]:
            for r in RANK_SP[-6:][: RANK_SP[-6:].index(rate) + 1]:
                self.data[r] += 1

    def add_combo(self, fc: FCType) -> None:
        key = fc.name.lower()
        if key in COMBO_SP:
            for c in COMBO_SP[: COMBO_SP.index(key) + 1]:
                self.data[c] += 1

    def add_sync(self, fs: FSType) -> None:
        key = fs.name.lower()
        if key == "sync":
            self.data["sync"] += 1
        elif key in SYNC_D_SP:
            for s in SYNC_D_SP[: SYNC_D_SP.index(key) + 1]:
                self.data[s] += 1


def draw_rating_table(
    level: str,
    plan: str | None,
    play_result: list,
    entries: list[tuple[Song, SongDifficulty]],
    *,
    theme: str = "prism_plus",
) -> bytes | None:
    """绘制等级完成表。

    ``plan``：None/达成率计划 → 评级章模式；fc/fcp/ap → 连击章模式（NB
    plan=True）；fs/fdx/fsp → Sync 章模式（NB 未支持，按连击章模式自然扩展）。
    ``play_result``：玩家全量成绩（ScoreExtend 列表）。底图缺失返回 None。
    """
    path = table_template.rating_table_dir() / f"{level}.png"
    if not path.exists():
        return None
    im = Image.open(path).convert("RGBA")

    from PIL import ImageDraw

    dr = ImageDraw.Draw(im)
    combo_mode = plan in ("fc", "fcp", "ap")
    sync_mode = plan in ("fs", "fdx", "fsp")

    stats = _Stats()
    played: dict[tuple[int, int], object] = {}
    total_count = 0
    for _song, diff in entries:
        total_count += 1
    for score in play_result:
        if score.level != level:
            continue
        played[(score.id, score.level_index.value)] = score
        ach = score.achievements or 0
        if ach >= 80:
            stats.data["clear"] += 1
        stats.add_rank(_rate_only(score.level_value or 0, ach))
        if score.fc:
            stats.add_combo(score.fc)
        if score.fs:
            stats.add_sync(score.fs)

    # 标题 + 统计头（普通分支坐标；lv15 由模板自身布局承载，统计同位）
    title_y = 160
    dr.text(
        (495, title_y),
        "Level.",
        font=font(70, FONT_RODIN),
        fill=FONT_BLUE,
        anchor="ld",
        stroke_width=8,
        stroke_fill=(255, 255, 255, 255),
    )
    dr.text(
        (750, title_y),
        level,
        font=font(100, FONT_RODIN),
        fill=FONT_BLUE,
        anchor="ld",
        stroke_width=8,
        stroke_fill=(255, 255, 255, 255),
    )

    im.alpha_composite(assets.pic("complete.png"), (251, 190))
    dr.text(
        (394, 238),
        f"{stats.data['clear']}/{total_count}",
        font=font(30, FONT_NUM),
        fill=TEXT_BLUE,
        anchor="mm",
        stroke_width=5,
        stroke_fill=(255, 255, 255, 255),
    )
    for n, key in enumerate(STATISTICS_KEYS[1:]):
        if n < 6:
            x, y = 534 + (n % 6) * 102, 238
        else:
            x, y = 292 + ((n - 6) % 9) * 102, 323
        dr.text(
            (x, y),
            str(stats.data[key]),
            font=font(30, FONT_NUM),
            fill=TEXT_BLUE,
            anchor="mm",
            stroke_width=2,
            stroke_fill=(255, 255, 255, 255),
        )

    # 逐谱面盖章（按模板生成时的分组与排序：_group_by_ds 降序 / lv15 特例）
    qualified: list[float] = []

    def stamp_rank(x: int, y: int, ds: float, score) -> None:
        ach = score.achievements or 0
        qualified.append(ach)
        im.alpha_composite(
            assets.pic(_COMPLETED_BG if ach >= 100 else _UNFINISHED_BG), (x + 1, y + 1)
        )
        rate = RATE_FILE[RateType._from_achievement(ach).name]
        p = assets.static_path() / "mai" / "pic" / theme / f"UI_TTR_Rank_{rate}.png"
        if p.exists():
            im.alpha_composite(
                assets.pic(f"UI_TTR_Rank_{rate}.png", theme).resize((78, 35)),
                (x, y + 20),
            )

    def stamp_combo(x: int, y: int, score) -> None:
        if not score.fc:
            return
        qualified.append(COMBO_SP.index(score.fc.name.lower()))
        im.alpha_composite(assets.pic(_COMPLETED_BG), (x + 1, y + 1))
        im.alpha_composite(
            assets.pic(
                f"UI_MSS_MBase_Icon_{_combo_file(score.fc.name.lower())}.png"
            ).resize((50, 50)),
            (x + 15, y + 13),
        )

    def stamp_sync(x: int, y: int, score) -> None:
        if not score.fs or score.fs.name.lower() == "sync":
            return
        qualified.append(SYNC_D_SP.index(score.fs.name.lower()))
        im.alpha_composite(assets.pic(_COMPLETED_BG), (x + 1, y + 1))
        im.alpha_composite(
            assets.pic(
                f"UI_MSS_MBase_Icon_{_sync_file(score.fs.name.lower())}.png"
            ).resize((50, 50)),
            (x + 15, y + 13),
        )

    if level == "15":
        ordered = sorted(entries, key=lambda pair: pair[1].level_value, reverse=True)
        for i, (song, diff) in enumerate(ordered):
            row, col = divmod(i, LV15_COLS)
            x = LV15_START_X + col * LV15_COL_STEP
            y = LV15_START_Y + row * LV15_ROW_STEP
            score = played.get((song.id, diff.level_index.value))
            if score is None:
                continue
            if not combo_mode and not sync_mode:
                stamp_rank(x, y, diff.level_value, score)
            elif combo_mode:
                stamp_combo(x, y, score)
            else:
                stamp_sync(x, y, score)
    else:
        groups = group_by_ds(entries)
        current_y = RATING_START_Y
        for ds in groups:
            charts = groups[ds]
            for num, (song, diff) in enumerate(charts):
                row, col = divmod(num, RATING_COLS)
                x = RATING_START_X + col * RATING_GRID_STEP
                y = current_y + row * RATING_GRID_STEP
                score = played.get((song.id, diff.level_index.value))
                if score is None:
                    continue
                if combo_mode:
                    stamp_combo(x, y, score)
                elif sync_mode:
                    stamp_sync(x, y, score)
                else:
                    stamp_rank(x, y, diff.level_value, score)
            rows = (len(charts) - 1) // RATING_COLS + 1
            current_y += rows * RATING_GRID_STEP + RATING_GROUP_GAP

    # 全曲达成徽章（NB _calc_achievements_fc：增量阈值全部满足时挂 Allclear 图）
    thresholds = (
        list(range(4))
        if combo_mode
        else list(range(len(SYNC_D_SP)))
        if sync_mode
        else list(range(len(RANK_SP[-6:])))
    )
    if total_count and len(qualified) == total_count:
        r = -1
        for t in thresholds:
            if all(q >= t for q in qualified):
                r += 1
            else:
                break
        if r != -1:
            if combo_mode:
                name = _combo_file(COMBO_SP[r])
            elif sync_mode:
                name = _sync_allclear(SYNC_D_SP[r])
            else:
                name = RATE_FILE.get(RANK_SP[-6:][r].upper(), "")
            p = (
                assets.static_path()
                / "mai"
                / "pic"
                / f"UI_MSS_Allclear_Icon_{name}.png"
            )
            if p.exists():
                im.alpha_composite(Image.open(p).convert("RGBA"), (40, 40))

    im = im.resize(
        (round(im.size[0] * 0.8), round(im.size[1] * 0.8)), Image.Resampling.LANCZOS
    )
    from .tools import image_to_bytes

    return image_to_bytes(im)


def _combo_file(key: str) -> str:
    # UI_MSS_MBase_Icon_：FC/FCp/AP/APp（映射单源 constants）
    return COMBO_FILE[key]


def _sync_file(key: str) -> str:
    return SYNC_FILE[key]


def _sync_allclear(key: str) -> str:
    # NB SYNC_MAP：fsd→FSD、fsdp→FSDp（Allclear 图无 FSp 档，回落 FSD 形态）
    return {"fs": "FS", "fsp": "FS", "fsd": "FSD", "fsdp": "FSDp"}[key]
