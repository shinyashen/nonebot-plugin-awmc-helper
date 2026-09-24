"""牌子完成表（NB core/image/plate_table.py::DrawPlateTable 的移植）。

打开预渲染底图（``plate_table/{版本}{牌种}.png``，舞/霸为 ``舞-1/2.png``），
叠加：总进度条与完成数、达成谱面章（者/将盖评级章，极/神盖连击章，
舞舞盖 Sync 章 + t_0..t_4 分槽小标）、各难度分组计数与进度条。
"""

from PIL import Image
from maimai_py import Song, FCType, FSType, RateType, LevelIndex, SongDifficulty

from . import table_template
from .fonts import FONT_RODIN, font
from .tools import TEXT_BLUE, ID_TEXT_COLORS
from .assets import assets
from ..plates import major_type_of_plate
from ...constants import RATE_FILE
from .table_layout import (
    PLATE_COLS,
    PLATE_START_X,
    PLATE_START_Y,
    PLATE_GRID_STEP,
)
from .plate_progress import progress_header

# 完成表网格几何：单一事实来源 table_layout
GRID_STEP, START_X, START_Y, ROW_COUNT = (
    PLATE_GRID_STEP,
    PLATE_START_X,
    PLATE_START_Y,
    PLATE_COLS,
)


def _qualified(kind: str, score) -> bool:
    """单谱面是否达成牌子要求（maimai_py 判牌语义同款）。"""
    if score is None:
        return False
    if kind == "者":
        return (score.achievements or 0) >= 80
    if kind == "将":
        return (score.achievements or 0) >= 100
    if kind == "极":
        return score.fc is not None and score.fc.value <= FCType.FC.value
    if kind == "神":
        return score.fc is not None and score.fc.value <= FCType.AP.value
    if kind == "舞舞":
        return score.fs is not None and score.fs.value <= FSType.FSD.value
    return False


def _plate_icon(kind: str, score):
    """达成章：者/将盖评级章（compute_rating onlyrate），其余盖连击/Sync 章。"""
    if kind in ("者", "将"):
        rate = RateType._from_achievement(score.achievements or 0).name
        name = RATE_FILE.get(rate, "D")
        return (
            assets.pic(f"UI_TTR_Rank_{name}.png", "prism_plus").resize((80, 36)),
            (0, 22),
        )
    if kind == "极":
        key = score.fc.name.lower() if score.fc else "fc"
        name = {"fc": "FC", "fcp": "FCp", "ap": "AP", "app": "APp"}.get(key, "FC")
        return assets.pic(f"UI_CHR_PlayBonus_{name}.png").resize((60, 60)), (10, 12)
    if kind == "神":
        ok_ap = score.fc is not None and score.fc.value <= FCType.APP.value
        name = "APp" if ok_ap else "AP"
        return assets.pic(f"UI_CHR_PlayBonus_{name}.png").resize((60, 60)), (10, 12)
    # 舞舞
    name = "FSDp"
    if score.fs is not None:
        name = {"fs": "FS", "fsp": "FSp", "fsd": "FSD", "fsdp": "FSDp"}.get(
            score.fs.name.lower(), "FSD"
        )
    return assets.pic(f"UI_CHR_PlayBonus_{name}.png").resize((60, 60)), (10, 12)


def draw_plate_table(
    version: str,
    kind: str,
    play_result: list,
    entries: list[tuple[Song, SongDifficulty]],
    *,
    page: int = 1,
) -> bytes | None:
    """绘制牌子完成表。``entries`` 为牌子范围内主类型谱面（与模板同源）。"""
    is_wu = version in ("舞", "霸")
    major = major_type_of_plate(version)
    slot_num = 5 if is_wu else 4
    plate_name = f"{version}-{page}" if is_wu else f"{version}{kind}"

    path = table_template.plate_table_dir() / f"{plate_name}.png"
    if not path.exists():
        return None
    im = Image.open(path).convert("RGBA")

    # 数据组装（NB _process_plate_table_data 同构）：**一格一曲**，按 Master
    # 槽等级分组（舞/霸 ReM 曲用 ReM 槽等级）——分组/排序与模板 _plate_grid
    # 逐条一致，否则叠章与底图格子错位
    all_slots: dict[int, list[SongDifficulty]] = {}
    for song, diff in entries:
        all_slots.setdefault(song.id, []).append(diff)

    def _chart_level(song_id: int) -> str:
        diffs = all_slots[song_id]
        if is_wu:
            re_m = next((d for d in diffs if d.level_index.value == 4), None)
            if re_m is not None:
                return re_m.level
        master = next(
            (d for d in diffs if d.level_index == LevelIndex.MASTER), diffs[0]
        )
        return master.level

    song_level = {sid: _chart_level(sid) for sid in all_slots}
    played: dict[str, dict[int, list]] = {}
    for song_id, level in song_level.items():
        played.setdefault(level, {}).setdefault(song_id, [None] * slot_num)
    for score in play_result:
        if score.type != major or score.level_index.value >= slot_num:
            continue
        if score.id not in song_level:
            continue
        slots = played[song_level[score.id]].setdefault(score.id, [None] * slot_num)
        slots[score.level_index.value] = score

    # 舞/霸双页：与模板分页同规则（lv≥13 第 1 页，<13 第 2 页）
    if is_wu:

        def _lv_key(lv: str) -> float:
            return float(lv.rstrip("+")) + (0.3 if lv.endswith("+") else 0.0)

        played = {
            lv: group
            for lv, group in played.items()
            if (_lv_key(lv) >= 13) == (page <= 1)
        }
    # 组序（等级降序）与组内序（定数降序）对齐模板 _plate_grid
    played = dict(
        sorted(
            (
                (
                    lv,
                    dict(
                        sorted(
                            group.items(),
                            key=lambda kv: next(
                                (
                                    d.level_value
                                    for d in all_slots[kv[0]]
                                    if d.level_index == LevelIndex.MASTER
                                ),
                                0,
                            ),
                            reverse=True,
                        )
                    ),
                )
                for lv, group in played.items()
            ),
            key=lambda kv: (float(kv[0].rstrip("+")), kv[0].endswith("+")),
            reverse=True,
        )
    )

    qualified_count = 0
    slot_counts = [0] * slot_num
    slot_total = [0] * slot_num
    qualified_slots_of: dict[int, list[int]] = {}

    from PIL import ImageDraw

    dr = ImageDraw.Draw(im)
    # 头部白色大面板（Hoshino _plate_progress_bg = plate_progress.png；
    # progress_bg.png 是进度总览每槽的底部小条，误用致面板缺失、白条外露）
    im.alpha_composite(assets.pic("plate_progress.png"), (175, 20))
    # 牌头走 Assets.plate_version（含简→繁转换与缓存；本地手拼文件名
    # 不做版本字转换，晓/樱/堇/辉/华 及一切「极」牌的繁体文件名永远打不开）
    bg = assets.plate_version(version, kind)
    if bg is not None:
        im.alpha_composite(bg.resize((1000, 161)), (200, 45))

    finished_marks = [assets.pic(f"t_{i}.png") for i in range(5)]

    current_y = START_Y
    for level, songs_slots in played.items():
        for song_id, slots in songs_slots.items():
            qualified_slots = [i for i, s in enumerate(slots) if _qualified(kind, s)]
            qualified_slots_of[song_id] = qualified_slots
            for i, s in enumerate(slots):
                slot_total[i] += 1
                if i in qualified_slots:
                    slot_counts[i] += 1
            if len(qualified_slots) == len(slots):
                qualified_count += 1

        rows = (len(songs_slots) - 1) // ROW_COUNT + 1
        for idx, (song_id, slots) in enumerate(songs_slots.items()):
            row, col = divmod(idx, ROW_COUNT)
            x = START_X + col * GRID_STEP
            y = current_y + row * GRID_STEP
            qualified_slots = qualified_slots_of[song_id]
            if slot_num - 1 in qualified_slots:
                best = slots[slot_num - 1]
                if best is not None:
                    im.alpha_composite(assets.pic("complete_2.png"), (x + 1, y + 1))
                    icon, offset = _plate_icon(kind, best)
                    im.alpha_composite(icon, (x + offset[0], y + offset[1]))
            for s_idx in qualified_slots:
                mark = finished_marks[s_idx]
                if is_wu and len(slots) == 5:
                    im.alpha_composite(
                        mark.resize((14, 14)), (x + 1 + 16 * s_idx, y + 64)
                    )
                else:
                    im.alpha_composite(mark, (x + 4 + 19 * s_idx, y + 63))
        current_y += rows * GRID_STEP + 30

    # 头部计数与进度条（与进度总览同源组件）
    progress_header(im, dr, qualified_count, len(song_level))

    # 各难度分组计数（模板按等级分组；此处按槽位统计，与 NB slot_counts 一致）
    stats_start_y = 300
    stats_gap_x = 253
    stats_start_x = 320
    for li in range(slot_num):
        x = stats_start_x + li * stats_gap_x
        count, total = slot_counts[li], slot_total[li]
        group_progress = count / total if total else 0
        if group_progress:
            small = assets.pic("progress_small.png")
            im.alpha_composite(
                small.crop((0, 0, int(230 * group_progress), min(46, small.height))),
                (x - 115, 326),
            )
        dr.text(
            (x, stats_start_y),
            str(count),
            font=font(40, FONT_RODIN),
            fill=ID_TEXT_COLORS[li],
            anchor="mm",
            stroke_width=4,
            stroke_fill=(255, 255, 255, 255),
        )
        dr.text(
            (x + 115, stats_start_y + 20),
            f"/{total}",
            font=font(14, FONT_RODIN),
            fill=ID_TEXT_COLORS[li],
            anchor="rd",
            stroke_width=3,
            stroke_fill=(255, 255, 255, 255),
        )
        dr.text(
            (x + 115, 343),
            f"{round(group_progress * 100, 2)}%",
            font=font(20, FONT_RODIN),
            fill=TEXT_BLUE,
            anchor="rm",
            stroke_width=2,
            stroke_fill=(255, 255, 255, 255),
        )

    im = im.resize(
        (round(im.size[0] * 0.8), round(im.size[1] * 0.8)), Image.Resampling.LANCZOS
    )
    from .tools import image_to_bytes

    return image_to_bytes(im)
