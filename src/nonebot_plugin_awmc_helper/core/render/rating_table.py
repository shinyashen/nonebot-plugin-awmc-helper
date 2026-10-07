"""等级完成表（NB core/image/rating_table.py::DrawRatingTable 的移植）。

打开预渲染底图（``rating_table/{level}.png``），绘制统计头（通关数、
S~SSS+ 评级分布、Sync/FC 分布）并逐谱面盖章：普通模式盖评级章（达成
≥100 金框）、FC/AP 计划盖 FC 章连击徽章；lv15 走三列大图布局。
缺失该谱面成绩的谱面不盖章。
"""

from typing import Any
from collections.abc import Callable

from PIL import Image, ImageDraw
from maimai_py import Song, FCType, FSType, RateType, SongDifficulty

from . import table_template
from .fonts import FONT_NUM, font
from .tools import WHITE, TEXT_BLUE, scale_output, image_to_bytes
from .assets import assets
from ...constants import (
    RATE_FILE,
    SYNC_FILE,
    COMBO_FILE,
    DEFAULT_THEME,
    ACHIEVEMENT_LIST,
)
from .table_layout import (
    LV15_COLS,
    LV15_START_X,
    LV15_START_Y,
    LV15_COL_STEP,
    LV15_ROW_STEP,
    RATING_START_Y,
    RATING_GRID_STEP,
    RATING_GROUP_GAP,
    iter_grid,
    grid_height,
    group_by_ds,
    grid_geometry,
    group_by_level,
)

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
RANK_SP = ("s", "sp", "ss", "ssp", "sss", "sssp")
"""评级统计键（NB 同名表的 S 及以上段；d..aaa 段从未被统计使用）。"""
COMBO_SP = ["fc", "fcp", "ap", "app"]
SYNC_D_SP = ["fs", "fsp", "fsd", "fsdp"]

_COMPLETED_BG = "complete_1.png"
_UNFINISHED_BG = "unfinished_1.png"

# 全曲达成徽章的评级阈值（升序 S→SSSP）：系数表推导阈值的末六档
# （与 maimai_py RateType._from_achievement 的 S~SSSP 分界一致）
RANK_ACHIEVEMENT_THRESHOLDS = tuple(ACHIEVEMENT_LIST[-6:])


def _rate_only(ach: float) -> str:
    """达成率 → 评级键（小写，NB compute_rating(onlyrate=True) 同义）。"""
    return RateType._from_achievement(ach).name.lower()


class _Stats:
    def __init__(self) -> None:
        self.data = dict.fromkeys(STATISTICS_KEYS, 0)

    def add_rank(self, rate: str) -> None:
        if rate in RANK_SP:
            for r in RANK_SP[: RANK_SP.index(rate) + 1]:
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
    theme: str = DEFAULT_THEME,
    checker: "Callable[[Any, Any, Any], bool] | None" = None,
    im: "Image.Image | None" = None,
) -> bytes | None:
    """绘制等级完成表（条件化收编后保留：单等级条件走文件底图与 lv15 版式）。

    ``plan``：None/达成率计划 → 评级章模式；fc/fcp/ap → 连击章模式（NB
    plan=True）；fs/fdx/fsp → Sync 章模式（NB 未支持，按连击章模式自然扩展）。
    ``play_result``：玩家全量成绩（ScoreExtend 列表）。底图缺失返回 None。
    ``im``：调用方预生成的底图（日服视图现算分支，``use_file=False`` 链路）；
    缺省读预渲染文件。
    ``checker``：达标判定 ``(achievements, fc, fs) -> bool``（plan_of 产物），
    驱动盖章三态背景（QoL：达标白/不达标黑/未打无）；None 回退 ≥100 分界。
    """
    if im is None:
        path = table_template.rating_table_file(level)
        if not path.exists():
            return None
        im = Image.open(path).convert("RGBA")
    played = {
        (score.id, score.level_index.value): score
        for score in play_result
        if score.level == level
    }
    return _draw_rating_core(
        im,
        plan,
        played,
        entries,
        theme=theme,
        lv15=(level == "15"),
        header_text=level,
        header_prefix="Level.",
        suffix="完成表",
        checker=checker,
    )


def draw_rating_table_cond(
    im: Image.Image,
    plan: str | None,
    play_result: list,
    entries: list[tuple[Song, SongDifficulty]],
    *,
    header_text: str,
    header_prefix: "str | None" = None,
    theme: str = DEFAULT_THEME,
    checker: "Callable[[Any, Any, Any], bool] | None" = None,
    by_level: bool = True,
) -> bytes:
    """条件化完成表（P2-c）：调用方提供现算/文件底图，成绩按谱面键集过滤。

    ``header_text``：标题大字（单等级条件传等级串配 ``header_prefix="Level."``
    ——「Level. xx」整段日文字体口径，2026-10-01；其余传条件 label 串——
    规范化显示，评级档大写，无 Level. 前缀）。
    ``checker``：同 :func:`draw_rating_table`。``by_level`` 必须与**底图
    实际分组**一致（调用方按 single_level 是否为 None 传入）：跨等级条件
    底图=标级大类（True）；「单等级+判型」混合条件（如 14+sss+完成表）
    底图=定数节文件底图（False），硬编码 True 会叠章错位。
    """
    keys = {(song.id, d.level_index.value) for song, d in entries}
    played = {
        (score.id, score.level_index.value): score
        for score in play_result
        if (score.id, score.level_index.value) in keys
    }
    return _draw_rating_core(
        im,
        plan,
        played,
        entries,
        theme=theme,
        lv15=False,
        header_text=header_text,
        header_prefix=header_prefix,
        suffix="完成表",
        checker=checker,
        by_level=by_level,
    )


def _draw_rating_core(
    im: Image.Image,
    plan: str | None,
    played: dict,
    entries: list[tuple[Song, SongDifficulty]],
    *,
    theme: str,
    lv15: bool,
    header_text: str,
    header_prefix: "str | None" = "Level.",
    suffix: str = "",
    checker: "Callable[[Any, Any, Any], bool] | None" = None,
    by_level: bool = False,
) -> bytes:
    """盖章核心（等级版/条件版共用）：统计头 + 逐谱面盖章 + 全曲徽章。

    背景三态跟随 ``checker``（QoL 2026-09-30）：达标 → 白色半透明
    （complete_1）、有成绩不达标 → 黑色半透明（unfinished_1）、未打 → 无
    （底图原样）；``checker=None`` 回退旧 ≥100 分界。lv15 大格分支保持
    Hoshino 惯例不画底。``by_level``：条件版（跨等级）底图按标级大类分组 →
    盖章同序（``group_by_level``）；单等级版走 ``group_by_ds`` 定数节。
    ``suffix``：表型说明（「完成表」，随表头居中，2026-10-01 QoL）。
    """
    dr = ImageDraw.Draw(im)
    combo_mode = plan in ("fc", "fcp", "ap")
    sync_mode = plan in ("fs", "fdx", "fsp")

    def bg_of(score) -> str:
        if checker is not None:
            ok = checker(score.achievements, score.fc, score.fs)
        else:
            ok = (score.achievements or 0) >= 100
        return _COMPLETED_BG if ok else _UNFINISHED_BG

    stats = _Stats()
    total_count = len(entries)
    for score in played.values():
        ach = score.achievements or 0
        if ach >= 80:
            stats.data["clear"] += 1
        stats.add_rank(_rate_only(ach))
        if score.fc:
            stats.add_combo(score.fc)
        if score.fs:
            stats.add_sync(score.fs)

    # 标题 + 统计头（普通分支坐标；lv15 由模板自身布局承载，统计同位）
    # 条件版无 Level. 前缀（QoL：多余前缀去除）；表型说明随表头居中
    title_y = 160
    table_template.draw_level_header(
        dr, header_text, title_y, prefix=header_prefix, suffix=suffix
    )

    im.alpha_composite(assets.pic("complete.png"), (251, 190))
    dr.text(
        (394, 238),
        f"{stats.data['clear']}/{total_count}",
        font=font(30, FONT_NUM),
        fill=TEXT_BLUE,
        anchor="mm",
        stroke_width=5,
        stroke_fill=WHITE,
    )
    for n, key in enumerate(STATISTICS_KEYS[1:]):
        if n < 6:
            x, y = 534 + (n % 6) * 102, 238
        else:
            # n ∈ [6, 14] → n-6 ∈ [0, 8]，恰为 Sync/FC 行 9 列（原 % 9 恒无效）
            x, y = 292 + (n - 6) * 102, 323
        dr.text(
            (x, y),
            str(stats.data[key]),
            font=font(30, FONT_NUM),
            fill=TEXT_BLUE,
            anchor="mm",
            stroke_width=2,
            stroke_fill=WHITE,
        )

    # 逐谱面盖章（按模板生成时的分组与排序：group_by_ds 降序 / lv15 特例）
    qualified: list[float] = []

    def stamp_rank(x: int, y: int, score, *, lv15: bool = False) -> None:
        ach = score.achievements or 0
        qualified.append(ach)
        if lv15:
            # Hoshino lv15 大格分支：不画完成/未完成底，评级章原尺寸置中
            if rank := assets.rate_badge_of_achievement(ach, theme):
                im.alpha_composite(rank, (x + 55, y + 115))
            return
        im.alpha_composite(assets.pic(bg_of(score)), (x + 1, y + 1))
        if rank := assets.rate_badge_of_achievement(ach, theme, (78, 35)):
            im.alpha_composite(rank, (x, y + 20))

    def stamp_combo(x: int, y: int, score, *, lv15: bool = False) -> None:
        # 判型完成表 Hoshino 同款（2026-10-01 用户澄清）：只有**达标**谱面画
        # 白底 + 实际徽章，不达标/未打完全不显示（黑色半透明背景仅评级模式
        # 三态使用）。checker 缺省（第三方直调）回退「有 fc 即显示」。
        # ⚠️ checker 须自证 fc/fs 非 None（core.combo._fs_checker 有守卫）；
        # 宽松 checker 传 None 成绩进下方 .name 会 AttributeError
        if checker is not None:
            if not checker(score.achievements, score.fc, score.fs):
                return
        elif not score.fc:
            return
        qualified.append(COMBO_SP.index(score.fc.name.lower()))
        if lv15:
            # Hoshino lv15 计划分支：PlayBonus 大章 200×200，不画完成底
            name = COMBO_FILE[score.fc.name.lower()]
            if bonus := assets.pic_optional(f"UI_CHR_PlayBonus_{name}.png"):
                im.alpha_composite(bonus.resize((200, 200)), (x + 75, y + 80))
            return
        im.alpha_composite(assets.pic(_COMPLETED_BG), (x + 1, y + 1))
        if icon := assets.mss_icon(COMBO_FILE, score.fc, size=(50, 50)):
            im.alpha_composite(icon, (x + 15, y + 13))

    def stamp_sync(x: int, y: int, score, *, lv15: bool = False) -> None:
        # 同 stamp_combo：checker 达标才显示（Sync 档不达任何 fs 族 plan →
        # 不显示）
        if checker is not None:
            if not checker(score.achievements, score.fc, score.fs):
                return
        elif not score.fs or score.fs.name.lower() == "sync":
            return
        qualified.append(SYNC_D_SP.index(score.fs.name.lower()))
        # 扩展分支（NB 未支持 Sync 计划）：PlayBonus 大章无 Sync 档素材，
        # lv15 也只能用 50×50 小章；lv15 按 Hoshino 分支惯例不画完成底
        if not lv15:
            im.alpha_composite(assets.pic(_COMPLETED_BG), (x + 1, y + 1))
        if icon := assets.mss_icon(SYNC_FILE, score.fs, size=(50, 50)):
            im.alpha_composite(icon, (x + 15, y + 13))

    if lv15:
        # 同序契约：与 table_template._rating_grid_15 的底图摆放同一排序
        # （定数降序、稳定等值保序；Level.15 现全为 15.0，排序今日为无操作）
        ordered = sorted(entries, key=lambda pair: pair[1].level_value, reverse=True)
        for i, (song, diff) in enumerate(ordered):
            row, col = divmod(i, LV15_COLS)
            x = LV15_START_X + col * LV15_COL_STEP
            y = LV15_START_Y + row * LV15_ROW_STEP
            score = played.get((song.id, diff.level_index.value))
            if score is None:
                continue
            if not combo_mode and not sync_mode:
                stamp_rank(x, y, score, lv15=True)
            elif combo_mode:
                stamp_combo(x, y, score, lv15=True)
            else:
                stamp_sync(x, y, score, lv15=True)
    else:
        # 同序契约：底图分组随 by_level（条件版标级大类 / 单等级定数节），
        # 列数/列起点同步（标级大类 13 列 + x=180，标签让位）；逐格坐标与
        # 组推进与模板侧同走 iter_grid/grid_height 单源
        groups = group_by_level(entries) if by_level else group_by_ds(entries)
        cols, start_x = grid_geometry(by_level)
        current_y = RATING_START_Y
        for ds in groups:
            charts = groups[ds]
            for x, y, (song, diff), _num in iter_grid(
                charts,
                cols=cols,
                start_x=start_x,
                start_y=current_y,
                row_step=RATING_GRID_STEP,
            ):
                score = played.get((song.id, diff.level_index.value))
                if score is None:
                    continue
                if combo_mode:
                    stamp_combo(x, y, score)
                elif sync_mode:
                    stamp_sync(x, y, score)
                else:
                    stamp_rank(x, y, score)
            current_y += grid_height(
                len(charts),
                cols=cols,
                row_step=RATING_GRID_STEP,
                group_gap=RATING_GROUP_GAP,
            )

    # 全曲达成徽章（Hoshino _calc_achievements_fc 同构）：连击/Sync 计划的
    # qualified 存 COMBO_SP/SYNC_D_SP 下标，逐档 0..3；评级分支存原始达成率，
    # 阈值用 RANK_ACHIEVEMENT_THRESHOLDS 逐档（曾误用 range(6) 当阈值，
    # 全完成表恒判 SSSp）
    thresholds = (
        list(range(4))
        if combo_mode
        else list(range(len(SYNC_D_SP)))
        if sync_mode
        else list(RANK_ACHIEVEMENT_THRESHOLDS)
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
                name = COMBO_FILE[COMBO_SP[r]]
            elif sync_mode:
                name = _sync_allclear(SYNC_D_SP[r])
            else:
                name = RATE_FILE.get(RANK_SP[r].upper(), "")
            p = (
                assets.static_path()
                / "mai"
                / "pic"
                / f"UI_MSS_Allclear_Icon_{name}.png"
            )
            if p.exists():
                im.alpha_composite(assets.get(p), (40, 40))

    return image_to_bytes(scale_output(im))


def _sync_allclear(key: str) -> str:
    # NB SYNC_MAP：fsd→FSD、fsdp→FSDp（Allclear 图无 FSp 档，回落 FSD 形态；
    # 与 SYNC_FILE 的差异仅 fsp 档——那里是 FSp，此处无素材退 FS）
    return {"fs": "FS", "fsp": "FS", "fsd": "FSD", "fsdp": "FSDp"}[key]
