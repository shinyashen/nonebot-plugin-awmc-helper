"""牌子完成表（NB core/image/plate_table.py::DrawPlateTable 的移植）。

打开预渲染底图（``plate_table/{版本}{牌种}.png``，舞/霸为 ``舞-1/2.png``），
叠加：总进度条与完成数、达成谱面章（者/将盖评级章，极/神盖连击章，
舞舞盖 Sync 章 + t_0..t_4 分槽小标）、各难度分组计数与进度条。
"""

from PIL import Image, ImageDraw
from maimai_py import Song, FCType, RateType, LevelIndex, SongDifficulty

from . import table_template
from .fonts import FONT_RODIN, font
from .tools import WHITE, TEXT_BLUE, ID_TEXT_COLORS, scale_output, image_to_bytes
from .assets import assets
from ..plates import plate_score_ok, major_type_of_plate
from ...constants import SYNC_FILE, COMBO_FILE
from .table_layout import (
    PLATE_COLS,
    PLATE_START_X,
    PLATE_START_Y,
    PLATE_COL_STEP,
    PLATE_ROW_STEP,
    PLATE_GROUP_GAP,
    slot_rep,
    level_page_key,
)
from .plate_progress import progress_header


def _plate_icon(kind: str, score):
    """达成章：者/将盖评级章（compute_rating onlyrate），其余盖连击/Sync 章。"""
    if kind in ("者", "将"):
        rate = RateType._from_achievement(score.achievements or 0)
        return assets.rate_badge(rate, "prism_plus", (80, 36)), (0, 22)
    if kind == "极":
        key = score.fc.name.lower() if score.fc else "fc"
        return assets.play_bonus(COMBO_FILE.get(key, "FC"), (60, 60)), (10, 12)
    if kind == "神":
        ok_ap = score.fc is not None and score.fc.value <= FCType.APP.value
        return assets.play_bonus("APp" if ok_ap else "AP", (60, 60)), (10, 12)
    # 舞舞
    name = "FSDp"
    if score.fs is not None:
        name = SYNC_FILE.get(score.fs.name.lower(), "FSD")
    return assets.play_bonus(name, (60, 60)), (10, 12)


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
    # 槽等级分组（舞/霸 ReM 曲用 ReM 槽等级）——代表谱面（组标级与组内排序键）
    # 与模板 _plate_grid 同用 table_layout.slot_rep，保证叠章与底图格子对位
    all_slots: dict[int, list[SongDifficulty]] = {}
    for song, diff in entries:
        all_slots.setdefault(song.id, []).append(diff)

    song_rep: dict[int, SongDifficulty] = {}
    for song_id, diffs in all_slots.items():
        re_m = (
            next((d for d in diffs if d.level_index.value == 4), None)
            if is_wu
            else None
        )
        master = next(
            (d for d in diffs if d.level_index == LevelIndex.MASTER), diffs[0]
        )
        song_rep[song_id] = slot_rep(master, re_m, use_remaster=is_wu)
    song_level = {sid: rep.level for sid, rep in song_rep.items()}
    # 槽数按曲动态（Hoshino _process_plate_table_wu_data 同款：`slot_size =
    # 5 if sid in wu_re_id_set else 4`）——舞/霸的 ReM 槽只对**实际拥有白谱**
    # 的曲存在；固定 5 槽会让无白谱曲的槽 4 恒 None、整曲永不标记（2026-09-30
    # 用户实测：只有白谱曲被标记）
    song_slot_size: dict[int, int] = {
        sid: 5 if (is_wu and any(d.level_index.value == 4 for d in diffs)) else 4
        for sid, diffs in all_slots.items()
    }
    played: dict[str, dict[int, list]] = {}
    for song_id, level in song_level.items():
        played.setdefault(level, {}).setdefault(
            song_id, [None] * song_slot_size[song_id]
        )
    for score in play_result:
        if score.type != major or score.level_index.value >= slot_num:
            continue
        if score.id not in song_level:
            continue
        slots = played[song_level[score.id]].setdefault(
            score.id, [None] * song_slot_size[score.id]
        )
        slots[score.level_index.value] = score

    qualified_count = 0
    slot_counts = [0] * slot_num
    slot_total = [0] * slot_num
    qualified_slots_of: dict[int, list[int]] = {}

    # 全牌统计先于分页（Hoshino process：completed/slot_counts 均为整牌口径，
    # 跨舞/霸两页合计；网格与 t 形小标只画当前页）
    for group in played.values():
        for song_id, slots in group.items():
            qualified_slots = [
                i for i, s in enumerate(slots) if plate_score_ok(kind, s)
            ]
            qualified_slots_of[song_id] = qualified_slots
            for i, s in enumerate(slots):
                slot_total[i] += 1
                if i in qualified_slots:
                    slot_counts[i] += 1
            if len(qualified_slots) == len(slots):
                qualified_count += 1

    # 舞/霸双页：与模板分页同规则（lv≥13 第 1 页，<13 第 2 页）
    if is_wu:
        played = {
            lv: group
            for lv, group in played.items()
            if (level_page_key(lv) >= 13) == (page <= 1)
        }
    # 组序（等级降序）与组内序（代表谱面定数降序）同模板 _plate_grid，
    # 否则叠章与底图格子错位
    played = dict(
        sorted(
            (
                (
                    lv,
                    dict(
                        sorted(
                            group.items(),
                            key=lambda kv: song_rep[kv[0]].level_value,
                            reverse=True,
                        )
                    ),
                )
                for lv, group in played.items()
            ),
            key=lambda kv: level_page_key(kv[0]),
            reverse=True,
        )
    )

    dr = ImageDraw.Draw(im)
    # 头部白色大面板：舞/霸用 wu 变体（Hoshino _plate_progress_wu_bg；非舞是
    # plate_progress.png，progress_bg.png 是进度总览每槽的底部小条，勿混用）
    im.alpha_composite(
        assets.pic("plate_progress_wu.png" if is_wu else "plate_progress.png"),
        (175, 20),
    )
    # 牌头走 Assets.plate_version（含简→繁转换与缓存；本地手拼文件名
    # 不做版本字转换，晓/樱/堇/辉/华 及一切「极」牌的繁体文件名永远打不开）
    bg = assets.plate_version(version, kind)
    if bg is not None:
        im.alpha_composite(bg.resize((1000, 161)), (200, 45))

    # 完成小标按槽位数取（非舞四槽牌种用不到 t_4，不白载一张素材）
    finished_marks = [assets.pic(f"t_{i}.png") for i in range(slot_num)]

    # 网格几何直接用 table_layout 原名常量（第六轮审查：删 COL_STEP 等本地
    # 别名——ROW_COUNT 名实不符「实为列数」，随删除消除）
    current_y = PLATE_START_Y
    for level, songs_slots in played.items():
        rows = (len(songs_slots) - 1) // PLATE_COLS + 1
        for idx, (song_id, slots) in enumerate(songs_slots.items()):
            row, col = divmod(idx, PLATE_COLS)
            x = PLATE_START_X + col * PLATE_COL_STEP
            y = current_y + row * PLATE_ROW_STEP
            qualified_slots = qualified_slots_of[song_id]
            # 大章 = 该曲**最后一槽**（动态 4/5，Hoshino `len(results)-1` 同款）：
            # 无白谱曲的最后一槽是 Master（index 3），固定槽 4 会永不画章
            best_index = len(slots) - 1
            if best_index in qualified_slots:
                best = slots[best_index]
                if best is not None:
                    icon, offset = _plate_icon(kind, best)
                    if icon is not None:  # 评级章素材缺失时跳过盖章（不画半截底）
                        im.alpha_composite(assets.pic("complete_2.png"), (x + 1, y + 1))
                        im.alpha_composite(icon, (x + offset[0], y + offset[1]))
            for s_idx in qualified_slots:
                mark = finished_marks[s_idx]
                if is_wu and len(slots) == 5:
                    im.alpha_composite(
                        mark.resize((14, 14)), (x + 1 + 16 * s_idx, y + 64)
                    )
                else:
                    im.alpha_composite(mark, (x + 4 + 19 * s_idx, y + 63))
        current_y += rows * PLATE_ROW_STEP + PLATE_GROUP_GAP

    # 头部计数与进度条（与进度总览同源组件）
    progress_header(im, dr, qualified_count, len(song_level))

    # 各难度分组计数（模板按等级分组；此处按槽位统计，与 NB slot_counts 一致）。
    # 布局分两套（Hoshino DrawPlateTable.__init__）：非舞 320/253/条宽 230；
    # 舞/霸五档 292/204/条宽 176（progress_small_wu），否则第 5 列出画面
    stats_start_y = 300
    if is_wu:
        stats_start_x, stats_gap_x = 292, 204
        bar_width, text_x, bar_x = 176, 89, 88
        progress_small = assets.pic("progress_small_wu.png")
    else:
        stats_start_x, stats_gap_x = 320, 253
        bar_width, text_x, bar_x = 230, 115, 115
        progress_small = assets.pic("progress_small.png")
    for li in range(slot_num):
        x = stats_start_x + li * stats_gap_x
        count, total = slot_counts[li], slot_total[li]
        group_progress = count / total if total else 0
        if group_progress:
            im.alpha_composite(
                progress_small.crop(
                    (
                        0,
                        0,
                        int(bar_width * group_progress),
                        min(46, progress_small.height),
                    )
                ),
                (x - bar_x, 326),
            )
        dr.text(
            (x, stats_start_y),
            str(count),
            font=font(40, FONT_RODIN),
            fill=ID_TEXT_COLORS[li],
            anchor="mm",
            stroke_width=4,
            stroke_fill=WHITE,
        )
        dr.text(
            (x + text_x, stats_start_y + 20),
            f"/{total}",
            font=font(14, FONT_RODIN),
            fill=ID_TEXT_COLORS[li],
            anchor="rd",
            stroke_width=3,
            stroke_fill=WHITE,
        )
        dr.text(
            (x + text_x, 343),
            f"{round(group_progress * 100, 2)}%",
            font=font(20, FONT_RODIN),
            fill=TEXT_BLUE,
            anchor="rm",
            stroke_width=2,
            stroke_fill=WHITE,
        )

    return image_to_bytes(scale_output(im))
