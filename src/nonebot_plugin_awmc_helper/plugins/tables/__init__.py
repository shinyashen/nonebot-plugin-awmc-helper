"""awmc.tables：定数表 / 完成表 / 牌子 / 进度 / 分数列表。

指令（对齐原版 maimaiDX）：
- `<等级>定数表`（如 13+定数表）
- `<等级><评价>完成表`（如 13fc完成表 / 14sssp完成表）
- `<等级><评价>进度 [页]`（如 13fc进度）
- `<版本><牌种>完成表 / 进度`（如 真将完成表 / 樱极进度）
- `牌子条件`
- `<等级|定数>分数列表 [页]`
- `更新定数表 / 更新完成表`（SUPERUSER，预渲染底图；查询时叠加成绩）
"""

from nonebot import on_regex, on_command, on_fullmatch
from nonebot.params import RegexGroup
from nonebot.plugin import PluginMetadata
from nonebot.permission import SUPERUSER
from nonebot_plugin_uninfo import Session, UniSession
from nonebot_plugin_alconna.uniseg import UniMessage

from ...config import plugin_config
from ...constants import (
    LEVEL_LIST,
    PLATE_CHARS,
    SERVICE_DISPLAY,
    chart_display_id,
)
from ...core.score import UserScoreError, score_service
from ...core.songs import song_service
from ...core.types import FCType, FSType, SongType
from ...core.utils import handle_errors
from ...core.plates import in_plate_scope, major_type_of_plate, plate_version_range
from ...core.binding import session_keys, binding_service
from ...core.render.score import DrawScore
from ...core.render.tools import text_to_image, image_to_bytes
from ...core.render.plate_progress import plate_progress_bytes

__plugin_meta__ = PluginMetadata(
    name="awmc.tables",
    description="舞萌DX 完成度表格：定数表/完成表/牌子/进度/分数列表",
    usage="13+定数表｜13fc完成表｜13fc进度｜真将完成表｜牌子条件｜13+分数列表",
    type="application",
    homepage="https://github.com/shinyashen/nonebot-plugin-awmc-helper",
)

from nonebot import get_driver as _get_driver


@_get_driver().on_startup
async def _warn_missing_templates() -> None:
    """NB 方案：底图未生成时启动告警（提示 SUPERUSER 执行更新指令）。"""
    if not plugin_config.awmc_startup_tasks:
        return
    from nonebot import logger

    from ...core.render.table_template import plate_table_dir, rating_table_dir

    if not rating_table_dir().exists() or not any(rating_table_dir().iterdir()):
        logger.warning("定数表底图未生成，请 SUPERUSER 执行「更新定数表」")
    if not plate_table_dir().exists() or not any(plate_table_dir().iterdir()):
        logger.warning("完成表底图未生成，请 SUPERUSER 执行「更新完成表」")


# 评价计划 → 判定函数（achievement / fc / fs）
PLANS: dict[str, str] = {
    "sssp": "rate:100.5",
    "sss": "rate:100",
    "ssp": "rate:99.5",
    "ss": "rate:99",
    "sp": "rate:98",
    "s": "rate:97",
    "spp": "rate:97",
    "ap": "fc:ap",
    "fc": "fc:fc",
    "fs": "fs:fs",
    "fdx": "fs:fsd",
    "fcp": "fc:fcp",
    "fsp": "fs:fsp",
}

# 牌种正则（牌子字符 PLATE_CHARS 与 core 预渲染共用 constants 一份）
PLATE_KINDS = "舞舞|将|者|极|神"

_LEVEL_ORDER = {lv: i for i, lv in enumerate(LEVEL_LIST)}
"""标级 → 序号（替代循环内 LEVEL_LIST.index 的 O(n) 查找）。"""


async def _level_entries(level: str) -> list[tuple]:
    """全库指定标级的谱面条目（定数表/完成表/推分计划三处查询共用）。"""
    return [
        (song, d)
        for song in await song_service.get_all()
        for d in song.get_difficulties()
        if d.type != SongType.UTAGE and d.level == level
    ]


LEVEL_RE = r"([0-9]+\+?)"
DS_RE = r"([0-9]+(?:\.[0-9]+)?\+?)"
PLAN_RE = r"(sssp|sss|ssp|ss|spp|sp|s|ap|fcp|fc|fsp|fs|fdx)"

ds_table_cmd = on_regex(rf"^{LEVEL_RE}定数表$", block=True)
score_table_cmd = on_regex(rf"^{LEVEL_RE}{PLAN_RE}\+?完成表$", block=True)
progress_cmd = on_regex(
    rf"^{LEVEL_RE}{PLAN_RE}\+?(已完成|未完成|未开始|未游玩)?进度\s?([0-9]+)?$",
    block=True,
)
plate_cmd = on_regex(
    rf"^([{PLATE_CHARS}])({PLATE_KINDS})(完成表|进度)\s?([0-9]+)?$",
    block=True,
)
plate_help = on_fullmatch("牌子条件", block=True)
score_list_cmd = on_regex(rf"^{DS_RE}\s?分数列表\s?([0-9]+)?$", block=True)
update_rating = on_command("更新定数表", permission=SUPERUSER, block=True)
update_plate = on_command("更新完成表", permission=SUPERUSER, block=True)


def _plan_checker(plan: str):
    """评价计划 → (判定, 说明)。"""
    kind, value = PLANS[plan].split(":")
    if kind == "rate":
        return (lambda ach, fc, fs: (ach or 0) >= float(value)), f"达成率 ≥ {value}%"
    if kind == "fc":
        order = {
            "fc": (FCType.FC, FCType.FCP, FCType.AP, FCType.APP),
            "fcp": (FCType.FCP, FCType.AP, FCType.APP),
            "ap": (FCType.AP, FCType.APP),
        }
        allow = order[value]
        return (lambda ach, fc, fs: fc in allow), {
            "fc": "Full Combo",
            "fcp": "Full Combo+",
            "ap": "All Perfect",
        }[value]
    allow_fs = {
        "fs": (FSType.FS, FSType.FSP, FSType.FSD, FSType.FSDP),
        "fsp": (FSType.FSP, FSType.FSD, FSType.FSDP),
        "fsd": (FSType.FSD, FSType.FSDP),
    }
    if kind == "fs":
        allow = allow_fs[value]
        return (lambda ach, fc, fs: fs in allow), "Full Sync+"
    allow = allow_fs[value]
    names = {"fdx": "Full Sync DX"}
    return (lambda ach, fc, fs: fs in allow), names.get(value, value.upper())


@ds_table_cmd.handle()
@handle_errors("生成定数表失败")
async def _(
    groups: tuple = RegexGroup(),
):
    """定数表（R8，NB DrawRatingTable(level_text=True) 版式网格图）。"""
    from ...core.render import table_template

    (level,) = groups
    entries = await _level_entries(level)
    if not entries:
        await UniMessage.text(f" 没有找到等级为「{level}」的谱面").finish(
            at_sender=True
        )
    png = table_template.rating_table_text_bytes(level, entries)
    await UniMessage.image(raw=png).finish(at_sender=True)


@score_table_cmd.handle()
@handle_errors("生成完成表失败", except_with_message=(UserScoreError,))
async def _(session: Session = UniSession(), groups: tuple = RegexGroup()):
    """等级完成表（NB DrawRatingTable 移植）：模板 + 统计头 + 逐谱面盖章。

    计划映射：fc/fcp/ap → 连击章模式（NB plan=True）；fs 族 → Sync 章
    （NB 未支持，按连击章模式扩展）；达成率计划/无计划 → 评级章模式
    （NB plan=False，盖章为各谱面实际评级）。
    """
    from ...core.render import table_template
    from ...core.render.rating_table import draw_rating_table

    level, plan = groups
    binding = await binding_service.ensure(*session_keys(session))
    entries = await _level_entries(level)
    if not entries:
        await UniMessage.text(f" 没有找到等级为「{level}」的谱面").finish(
            at_sender=True
        )
    scores = await score_service.get_scores_all(binding)

    theme = binding.theme or "prism_plus"
    png = draw_rating_table(level, plan, scores.scores, entries, theme=theme)
    if png is None:
        # 底图缺失：现场按 NB 布局生成（不落盘）后重试
        await table_template.generate_rating_template(level, song_service)
        png = draw_rating_table(level, plan, scores.scores, entries, theme=theme)
        if png is None:
            await UniMessage.text(" 定数表底图生成失败，请稍后再试").finish(
                at_sender=True
            )
    await UniMessage.image(raw=png).finish(at_sender=True)


@progress_cmd.handle()
@handle_errors("生成进度失败", except_with_message=(UserScoreError,))
async def _(session: Session = UniSession(), groups: tuple = RegexGroup()):
    """等级进度（R4，NB DrawScore.draw_plan/draw_category 版式）。

    - `13fc进度`：三段总览（已完成 30/未完成 30/未游玩 100 网格）；
    - `13fc已完成进度 [页]` / `未完成进度 [页]`：80/页成绩行卡；
    - `13fc未游玩进度`：未游玩封面网格。
    """
    level, plan, category, page_raw = groups
    page = int(page_raw) if page_raw else 1
    checker, _plan_name = _plan_checker(plan)
    binding = await binding_service.ensure(*session_keys(session))
    scores = await score_service.get_scores_all(binding)
    score_map = {(s.id, s.type, s.level_index): s for s in scores.scores}

    completed: list = []
    unfinished: list = []
    notplayed: list[tuple[int, int, float]] = []
    for song, d in await _level_entries(level):
        # NB by_plan 含 SD+DX 全部谱面（与本插件完成表口径一致），宴谱除外
        sc = score_map.get((song.id, d.type, d.level_index))
        if sc is None:
            # 未游玩网格显示游戏内 per-type id（DX 曲 10231 形状，NB 同款）
            notplayed.append(
                (chart_display_id(song, d), d.level_index.value, d.level_value)
            )
        elif checker(sc.achievements, sc.fc, sc.fs):
            completed.append(sc)
        else:
            unfinished.append(sc)
    total = len(completed) + len(unfinished) + len(notplayed)
    if total == 0:
        await UniMessage.text(f"  没有找到等级为「{level}」的谱面").finish(
            at_sender=True
        )

    # NB 排序：按计划类型取值降序（fc/fs 枚举值越大越好，rate 按达成率）
    kind = PLANS[plan].split(":")[0]

    def _sort_key(sc):
        if kind == "rate":
            return sc.achievements or 0
        if kind == "fc":
            return sc.fc.value if sc.fc else -1
        return sc.fs.value if sc.fs else -1

    completed.sort(key=_sort_key, reverse=True)
    unfinished.sort(key=_sort_key, reverse=True)
    notplayed.sort(key=lambda x: x[2], reverse=True)

    service = SERVICE_DISPLAY.get(binding.service, binding.service)

    def played_rows(count: int) -> int:
        return max(4, -(-count // 5))

    if category is None:
        # 三段总览（comp_limit 语义对齐 NB：仅完成时放宽到 60）
        comp_limit = 60 if not unfinished and not notplayed else 30
        c_y = played_rows(len(completed[:comp_limit])) * 109 + 140
        u_y = played_rows(len(unfinished[:30])) * 109 + 140
        n_y = max(4, -(-len(notplayed[:100]) // 20)) * 65 + 140
        card = DrawScore(150 + c_y + u_y + n_y, service=service)
        png = card.draw_plan(
            level, completed, c_y, unfinished, u_y, notplayed, plan, comp_limit
        )
    elif category in ("已完成", "未完成"):
        data = completed if category == "已完成" else unfinished
        total_pages = max(1, -(-(len(data)) // 80))
        real = min(max(page, 1), total_pages)
        display = data[(real - 1) * 80 : real * 80]
        y_size = played_rows(len(display)) * 109
        card = DrawScore(240 + y_size + 120, service=service)
        png = card.draw_category(
            "completed" if category == "已完成" else "unfinished",
            data,
            real,
            total_pages,
        )
    else:
        y_size = max(4, -(-len(notplayed) // 20)) * 65
        card = DrawScore(max(240 + y_size + 120, 600), service=service)
        png = card.draw_category("notplayed", notplayed)
    await UniMessage.image(raw=png).finish(at_sender=True)


@plate_cmd.handle()
@handle_errors("查询牌子失败", except_with_message=(UserScoreError,))
async def _(session: Session = UniSession(), groups: tuple = RegexGroup()):
    version, kind, mode, page_raw = groups
    binding = await binding_service.ensure(*session_keys(session))
    page = int(page_raw) if page_raw else 1
    plates = await score_service.get_plates(binding, f"{version}{kind}")
    if mode == "完成表":
        # NB DrawPlateTable：底图 + 达成章 + 各槽位计数与进度条
        from ...core.render import table_template
        from ...core.render.plate_table_draw import draw_plate_table

        major = major_type_of_plate(version)
        rng = plate_version_range(version)
        entries = []
        if rng is not None:
            lo, hi = rng
            for song in await song_service.get_all():
                for d in song.get_difficulties():
                    if in_plate_scope(song, d, lo, hi, major):
                        entries.append((song, d))
        if not entries:
            await UniMessage.text(" 该牌子范围内没有谱面").finish(at_sender=True)
        scores = await score_service.get_scores_all(binding)
        png = draw_plate_table(version, kind, scores.scores, entries, page=page)
        if png is None:
            # 底图缺失：现场按 NB 布局生成（不落盘）后重试
            await table_template.generate_plate_template(version, kind, song_service)
            png = draw_plate_table(version, kind, scores.scores, entries, page=page)
            if png is None:
                await UniMessage.text(" 完成表底图生成失败，请稍后再试").finish(
                    at_sender=True
                )
        await UniMessage.image(raw=png).finish(at_sender=True)
    # 进度（R7：NB DrawPlateProgress 版式总览图）
    cleared_plates = await plates.get_cleared()
    remained = await plates.get_remained()

    # song_id → (song, 剩余槽集, 达成槽集)；remained ∪ cleared = 牌子范围内全部曲
    info: dict[int, tuple] = {}
    for p in remained:
        info[p.song.id] = (p.song, set(p.levels), set())
    for p in cleared_plates:
        if p.song.id in info:
            info[p.song.id][2].update(p.levels)
        else:
            info[p.song.id] = (p.song, set(), set(p.levels))

    is_wu = version in ("舞", "霸")
    slot_count = 5 if is_wu else 4
    # 牌子主类型：DX 世代牌推 DX 谱，旧作牌（含舞/霸）推 SD 谱——决定未达成
    # 网格的 per-type 游戏 id 与定数取哪侧谱面（判定下沉 core/plates）
    major_type = major_type_of_plate(version)
    slot_total = [0] * slot_count
    slot_cleared = [0] * slot_count
    remained_by_slot: list[list[tuple[int, int, float, str]]] = [
        [] for _ in range(slot_count)
    ]
    completed_count = 0
    for song, remaining, cleared_l in info.values():
        if not remaining:
            completed_count += 1
        for li in remaining | cleared_l:
            if li.value < slot_count:
                slot_total[li.value] += 1
        for li in cleared_l:
            if li.value < slot_count:
                slot_cleared[li.value] += 1
        for li in remaining:
            d = next(
                (
                    x
                    for x in song.get_difficulties()
                    if x.level_index == li and x.type == major_type
                ),
                None,
            )
            if d is not None:
                remained_by_slot[li.value].append(
                    (chart_display_id(song, d), li.value, d.level_value, d.level)
                )
    for slot in remained_by_slot:
        slot.sort(key=lambda x: -x[2])

    # 槽节倒序（Re:MASTER → Basic，NB 同款）；舞/霸按等级 13 分界分页（NB 同款）
    boundary = _LEVEL_ORDER["13"]
    slots = []
    for li in range(slot_count):
        items_full = remained_by_slot[li]
        if is_wu:
            if page <= 1:
                items = [it[:3] for it in items_full if _LEVEL_ORDER[it[3]] >= boundary]
            else:
                items = [it[:3] for it in items_full if _LEVEL_ORDER[it[3]] < boundary]
        else:
            items = [it[:3] for it in items_full]
        slots.append(
            {
                "level_index": li,
                "cleared": slot_cleared[li],
                "total": slot_total[li],
                "items": items,
            }
        )
    slots = slots[::-1]
    service = SERVICE_DISPLAY.get(binding.service, binding.service)
    png = plate_progress_bytes(
        version,
        kind,
        service=service,
        slots=slots,
        total_count=len(info),
        completed_count=completed_count,
    )
    await UniMessage.image(raw=png).finish(at_sender=True)


@plate_help.handle()
@handle_errors()
async def _():
    from ...constants import PLATE_KIND_ZH

    lines = ["牌子达成条件说明："]
    lines += [f"{kind}：{desc}" for kind, desc in PLATE_KIND_ZH.items()]
    lines.append("舞/霸：旧作（含 Re:MASTER 单列）全曲谱面")
    png = image_to_bytes(text_to_image("\n".join(lines)))
    await UniMessage.image(raw=png).finish(at_sender=True)


@score_list_cmd.handle()
@handle_errors("查询失败", except_with_message=(UserScoreError,))
async def _(session: Session = UniSession(), groups: tuple = RegexGroup()):
    """分数列表（R5，NB DrawScore.draw_score_list 行卡版式，80/页）。"""
    ds_raw, page_raw = groups
    page = int(page_raw) if page_raw else 1
    binding = await binding_service.ensure(*session_keys(session))
    scores = await score_service.get_scores_all(binding)
    if "." in ds_raw:  # 定数
        ds = float(ds_raw)
        matched = [s for s in scores.scores if abs(s.level_value - ds) < 0.05]
        title = ds_raw
    else:
        matched = [s for s in scores.scores if s.level == ds_raw]
        title = ds_raw
    matched.sort(key=lambda s: s.achievements or 0, reverse=True)
    if not matched:
        await UniMessage.text("  没有找到符合条件的成绩").finish(at_sender=True)
    end_page = max(1, -(-len(matched) // 80))
    real = min(max(page, 1), end_page)
    # NB 高度公式：非末页整 80 条 4 段；末页按实际条数算行数与段数
    to_page = 80 if real < end_page else (len(matched) % 80 or 80)
    line = (to_page + 4) // 5
    if real < end_page:
        plc = line * 109 + 130 * 4
    else:
        multiplier = (to_page + 19) // 20
        actual_line = 4 if to_page <= 20 else line
        plc = actual_line * 109 + 130 * multiplier
    service = SERVICE_DISPLAY.get(binding.service, binding.service)
    card = DrawScore(280 + plc, service=service)
    png = card.draw_score_list(title, matched, real, end_page)
    await UniMessage.image(raw=png).finish(at_sender=True)


@update_rating.handle()
@handle_errors("生成底图失败")
async def _():
    from ...core.render import table_template

    # 进度提示必须用 send：finish 会抛 FinishedException 终止 handler，后续逻辑不再执行
    await UniMessage.text(" 正在生成定数表底图，请稍候……").send(at_sender=True)
    total, failed = await table_template.refresh_all_rating_tables(song_service)
    extra = f"；失败 {len(failed)} 项：{'、'.join(failed)}" if failed else ""
    await UniMessage.text(f" 定数表底图生成完成（{total} 谱面次）{extra}。").finish(
        at_sender=True
    )


@update_plate.handle()
@handle_errors("生成底图失败")
async def _():
    from ...core.render import table_template

    # 进度提示必须用 send：finish 会抛 FinishedException 终止 handler，后续逻辑不再执行
    await UniMessage.text(" 正在生成完成表底图，需要一些时间，请稍候……").send(
        at_sender=True
    )
    total, failed = await table_template.refresh_all_plate_tables(song_service)
    extra = f"；失败 {len(failed)} 项：{'、'.join(failed)}" if failed else ""
    await UniMessage.text(f" 完成表底图生成完成（{total} 谱面次）{extra}。").finish(
        at_sender=True
    )
