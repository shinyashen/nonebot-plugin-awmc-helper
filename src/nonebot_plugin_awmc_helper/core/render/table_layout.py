"""表格版式布局：模板生成与叠章渲染共用的几何常量与分组（单一事实来源）。

历史上模板侧（table_template）与叠章侧（rating_table / plate_table_draw）
各写一份坐标，改一处即错位；两侧行为必须由本模块同一组常量驱动。
"""

from collections.abc import Sequence

from maimai_py import Song, SongDifficulty

# 定数表 lv7–14：85px 格、14 列、起点 x=140、首组 y=450、组间附加 30px
RATING_GRID_STEP = 85
RATING_START_X = 140
RATING_START_Y = 450
RATING_COLS = 14
RATING_GROUP_GAP = 30

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
