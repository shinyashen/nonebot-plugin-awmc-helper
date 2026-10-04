"""表格版式布局：模板生成与叠章渲染共用的几何常量与分组（单一事实来源）。

历史上模板侧（table_template）与叠章侧（rating_table / plate_table_draw）
各写一份坐标，改一处即错位；两侧行为必须由本模块同一组常量驱动。
"""

from typing import TypeVar
from collections.abc import Iterator, Sequence

from maimai_py import Song, SongDifficulty

T = TypeVar("T")

# 定数表 lv7–14：85px 格、14 列、起点 x=140、首组 y=450、组间附加 30px
RATING_GRID_STEP = 85
RATING_START_X = 140
RATING_START_Y = 450
RATING_COLS = 14
RATING_GROUP_GAP = 30


def grid_geometry(by_level: bool) -> tuple[int, int]:
    """条件版网格几何（列数, 起点 x）：标级大类 13 列 + x=180（标签让位），
    定数节 14 列 + x=140。模板生成（table_template）与叠章（rating_table）
    两侧共用，防「同序契约」靠注释约束的两处手写漂移。"""
    if by_level:
        return RATING_COLS - 1, PLATE_START_X
    return RATING_COLS, RATING_START_X


# 定数表 lv15：3 列大格（425×450），起点 (100, 500)
LV15_COLS = 3
LV15_START_X = 100
LV15_START_Y = 500
LV15_COL_STEP = 425
LV15_ROW_STEP = 450

# 牌子完成表：96px 横向列距、纵向行距 96、组间附加 30，起点 (180, 490)
# （全部对齐 Hoshino _draw_plate 原版间距）
PLATE_COL_STEP = 96
PLATE_ROW_STEP = 96
PLATE_START_X = 180
PLATE_START_Y = 490
PLATE_COLS = 12
PLATE_GROUP_GAP = 30


def group_by_ds(
    entries: Sequence[tuple[Song, SongDifficulty]],
) -> dict[str, list[tuple[Song, SongDifficulty]]]:
    """按定数串分组（"13.0"/"13.6"），节序按定数降序（NB by_level_list 同序）。"""
    grouped: dict[str, list[tuple[Song, SongDifficulty]]] = {}
    for song, diff in entries:
        if diff.level_value < 7:
            continue
        grouped.setdefault(f"{diff.level_value:.1f}", []).append((song, diff))
    return {k: grouped[k] for k in sorted(grouped, key=float, reverse=True)}


def group_by_level(
    entries: Sequence[tuple[Song, SongDifficulty]],
) -> dict[str, list[tuple[Song, SongDifficulty]]]:
    """按标级大类分组（"14+"/"14" 各一组；2026-10-01 拍板：跨等级条件完成表
    一个等级大类全部合在一起，组内**不再细分定数小数节**），节序
    ``level_page_key`` 降序、组内定数降序。

    仅跨等级条件版完成表/定数表使用；单等级条件走文件底图
    （``group_by_ds`` 定数节，与旧版式一致）。
    """
    grouped: dict[str, list[tuple[Song, SongDifficulty]]] = {}
    for song, diff in entries:
        if diff.level_value < 7:
            continue
        grouped.setdefault(diff.level, []).append((song, diff))
    for group in grouped.values():
        group.sort(key=lambda pair: pair[1].level_value, reverse=True)
    return {k: grouped[k] for k in sorted(grouped, key=level_page_key, reverse=True)}


def slot_rep(
    master: SongDifficulty,
    remaster: SongDifficulty | None,
    *,
    use_remaster: bool,
) -> SongDifficulty:
    """完成表槽位代表谱面（分组标级与组内排序键的**唯一依据**，Hoshino
    get_ds_sort_key 同语义）：舞/霸（``use_remaster``）且组内有 Re:MASTER
    谱时用 ReM 谱，否则用 MASTER 谱。

    模板生成（``table_template._plate_grid``）与叠章
    （``plate_table_draw``）必须同用本函数，否则底图格子与叠章错位。
    """
    if use_remaster and remaster is not None:
        return remaster
    return master


def level_page_key(lv: str) -> float:
    """舞/霸分页与组序键：标级串 → 可比较浮点（+ 记 0.3，小于下一整数档，
    与「先按整数、再按是否有 +」的元组序同序）。"""
    return float(lv.rstrip("+")) + (0.3 if lv.endswith("+") else 0.0)


def slot_level_of(
    song: Song,
    diff: SongDifficulty,
    remaster_entries: Sequence[tuple[Song, SongDifficulty]] | None,
) -> str:
    """谱面在完成表中的槽位标级：舞/霸的 ReM 曲用 ReM 槽等级。"""
    if remaster_entries:
        re_m = next(
            (
                d
                for s2, d in remaster_entries
                if s2.id == song.id and d.level_index.value == 4
            ),
            None,
        )
        return slot_rep(diff, re_m, use_remaster=True).level
    return diff.level


# B50 式成绩行卡网格（best50 大图与 score 列表共用，Hoshino 布局）
SCORE_ROW_START_X = 16
SCORE_ROW_COL_STEP = 276
SCORE_ROW_GAP = 114
SCORE_ROW_COLS = 5

# b50 标准版式 b35/b15 分区锚点（b50.png 1400×1600 底图上的行区起点 y；
# b40 收窄版式的同族锚点见 best50.B40_B15_TOP）
B50_B35_TOP = 235
B50_B15_TOP = 1085


# ---------------------------------------------------------------------------
# 分组网格循环脚手架（四处消费点共用：table_template._rating_grid/_plate_grid、
# rating_table._draw_rating_core、plate_progress.plate_progress_bytes）
# ---------------------------------------------------------------------------


def grid_rows(count: int, cols: int) -> int:
    """``count`` 个条目按 ``cols`` 列网格排布占的行数（0 个 → 0 行）。

    分组网格行数算式单源（ ``(count - 1) // cols + 1`` ），底图高度预算与
    盖章/绘制侧的组推进共用，防两处手写漂移。
    """
    return (count - 1) // cols + 1 if count else 0


def grid_height(count: int, *, cols: int, row_step: int, group_gap: int) -> int:
    """一组 ``count`` 个条目的网格占高：``grid_rows × row_step + group_gap``。

    画布高度预算（求和）与组推进（前一组占高）共用。⚠️ plate_progress 的
    折叠网格（超 51 个提前断行）组占高按**实际画出行数**（≤ 本式）自算，
    不合用本函数。
    """
    return grid_rows(count, cols) * row_step + group_gap


def iter_grid(
    entries: Sequence[T],
    *,
    cols: int,
    start_x: int,
    start_y: int,
    row_step: int,
    col_step: int | None = None,
) -> Iterator[tuple[int, int, T, int]]:
    """单组条目的网格坐标生成器：行列序逐格 yield ``(x, y, entry, 组内序号)``。

    四处分组网格循环共用的几何算式单源：``row, col = divmod(序号, cols)``、
    ``x = start_x + col × col_step``、``y = start_y + row × row_step``。
    底图（模板侧）与叠章（盖章侧）同用本生成器保证逐格对位；调用方以
    :func:`grid_height` 推进组起点 y（详见其 Fold 例外备注）。
    """
    if col_step is None:
        col_step = row_step
    for num, entry in enumerate(entries):
        row, col = divmod(num, cols)
        yield start_x + col * col_step, start_y + row * row_step, entry, num
