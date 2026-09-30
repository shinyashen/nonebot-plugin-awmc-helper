"""表格编排域：评价计划判定、等级条目查询、牌子完成表与进度总览。

被 matchers 的各表格指令 handler 共用；不注册 matcher。
"""

from nonebot_plugin_alconna.uniseg import UniMessage

from ...constants import LEVEL_LIST, PLATE_CHARS, chart_display_id
from ...core.combo import (
    ComboEmpty,
    plan_of,
    combo_chart_entries,
    combo_filtered_scores,
)
from ...core.score import score_service
from ...core.songs import song_service
from ...core.types import FCType, FSType
from ...core.utils import slow_notice
from ...core.plates import (
    PLATE_KINDS,
    PLATE_KIND_ALIAS_CHARS,
    PLATE_VERSION_ALIAS_CHARS,
    in_plate_scope,
    major_type_of_plate,
    plate_version_range,
)
from ...core.binding import service_display
from ...core.sources import Capability
from ...core.render.score import DrawScore, score_list_height
from ...core.render.plate_progress import plate_progress_bytes

# 评价计划 → 判定函数（achievement / fc / fs）
PLANS: dict[str, str] = {
    "sssp": "rate:100.5",
    "sss": "rate:100",
    "ssp": "rate:99.5",
    "ss": "rate:99",
    "sp": "rate:98",
    "s": "rate:97",
    "ap": "fc:ap",
    "fc": "fc:fc",
    "fs": "fs:fs",
    "fdx": "fs:fsd",
    "fcp": "fc:fcp",
    "fsp": "fs:fsp",
}

_LEVEL_ORDER = {lv: i for i, lv in enumerate(LEVEL_LIST)}
"""标级 → 序号（替代循环内 LEVEL_LIST.index 的 O(n) 查找）。"""


def _plan_checker(plan: str):
    """评价计划 → 判定谓词（说明文案无消费方，进度卡标题自拼）。"""
    kind, value = PLANS[plan].split(":")
    if kind == "rate":
        return lambda ach, fc, fs: (ach or 0) >= float(value)
    if kind == "fc":
        order = {
            "fc": (FCType.FC, FCType.FCP, FCType.AP, FCType.APP),
            "fcp": (FCType.FCP, FCType.AP, FCType.APP),
            "ap": (FCType.AP, FCType.APP),
        }
        allow = order[value]
        return lambda ach, fc, fs: fc in allow
    allow_fs = {
        "fs": (FSType.FS, FSType.FSP, FSType.FSD, FSType.FSDP),
        "fsp": (FSType.FSP, FSType.FSD, FSType.FSDP),
        "fsd": (FSType.FSD, FSType.FSDP),
    }
    allow = allow_fs[value]
    return lambda ach, fc, fs: fs in allow


async def _level_entries(level: str) -> list[tuple]:
    """全库指定标级的谱面条目（定数表/完成表/推分计划三处查询共用）。"""
    from ...core.render.table_template import filter_level

    return filter_level(await song_service.get_all(), level)


async def _plate_completion_sheet(binding, version: str, kind: str, page: int) -> None:
    """完成表（NB DrawPlateTable：底图 + 达成章 + 各槽位计数与进度条）。"""
    from ...core.render import table_template

    # 数据源门禁先行（NET 不开放牌子）：与牌子进度总览同语义，避免「范围
    # 内没有谱面」之类的次要文案盖过能力边界提示
    if not score_service.supports(binding.service, Capability.PLATES):
        from ...core.sources import source_of

        raise source_of(binding.service).unsupported(Capability.PLATES)
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
    scores = await score_service.get_scores_all(binding, notify_slow=slow_notice())
    png = await table_template.draw_plate_table_with_fallback(
        version,
        kind,
        scores.scores,
        entries,
        page=page,
        song_service=song_service,
    )
    if png is None:
        await UniMessage.text(" 完成表底图生成失败，请稍后再试").finish(at_sender=True)
    await UniMessage.image(raw=png).finish(at_sender=True)


async def _plate_progress_overview(
    binding, plates, version: str, kind: str, page: int
) -> None:
    """进度总览（R7：NB DrawPlateProgress 版式总览图）。"""
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
    service = service_display(binding)
    png = plate_progress_bytes(
        version,
        kind,
        service=service,
        slots=slots,
        total_count=len(info),
        completed_count=completed_count,
    )
    await UniMessage.image(raw=png).finish(at_sender=True)


# ---------------------------------------------------------------- 条件化进度/分数列表

_PLATE_KIND_CHARS = frozenset((*PLATE_KINDS, *PLATE_KIND_ALIAS_CHARS))
"""牌种字符集（含繁体/和制；形状检测用）。"""

_PLATE_SHAPE_VERSION = frozenset(f"{PLATE_CHARS}{PLATE_VERSION_ALIAS_CHARS}")
"""版本字字符集（含繁体/和制；形状检测用）。"""


def _plate_shape(text: str) -> "tuple[str, str] | None":
    """条件串的**牌组合形状**：(版本字, 牌种字)。

    只判形状不判合法性——「真将进度」这类用户显然想要牌子的输入保持旧
    拒绝文案（牌单点破），只有非牌形状的文本才继续条件分解管线。合法性
    由调用方经 :func:`is_valid_plate` 校验。
    """
    if not text:
        return None
    if text.endswith("舞舞"):
        ver, kind = text[:-2], "舞舞"
    elif (kind := text[-1:]) in _PLATE_KIND_CHARS:
        ver = text[:-1]
    else:
        return None
    if not ver or any(ch not in _PLATE_SHAPE_VERSION for ch in ver):
        return None
    return ver, kind


async def combo_progress_card(
    binding, conds, cond_text: str, category: str | None, page: int
) -> None:
    """条件化进度（R4 版式泛化，NB draw_plan/draw_category 同布局）。

    条件 → 谱面集（core.combo §5 选谱启发式）→ 逐谱面按玩家成绩盖章分三段
    （已完成/未完成/未游玩）；判型由条件集推导（:func:`plan_of`，无达标型
    条件 → 达成率 ≥80% 评级章）。
    """
    entries = await combo_chart_entries(conds, binding)
    if isinstance(entries, ComboEmpty):
        await UniMessage.text(f" {entries.message}").finish(at_sender=True)
    scores = await score_service.get_scores_all(binding, notify_slow=slow_notice())
    score_map = {(s.id, s.type, s.level_index): s for s in scores.scores}
    checker, plan, kind = plan_of(conds)

    completed: list = []
    unfinished: list = []
    notplayed: list[tuple[int, int, float]] = []
    for song, d in entries:
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
        await UniMessage.text(" 没有符合条件的谱面").finish(at_sender=True)

    # NB 排序：按计划类型取值降序（fc/fs 枚举值越大越好，rate 按达成率）
    def _sort_key(sc):
        if kind == "rate":
            return sc.achievements or 0
        if kind == "fc":
            return sc.fc.value if sc.fc else -1
        return sc.fs.value if sc.fs else -1

    completed.sort(key=_sort_key, reverse=True)
    unfinished.sort(key=_sort_key, reverse=True)
    notplayed.sort(key=lambda x: x[2], reverse=True)

    service = service_display(binding)

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
            cond_text, completed, c_y, unfinished, u_y, notplayed, plan, comp_limit
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


async def combo_score_list_card(binding, conds, cond_text: str, page: int) -> None:
    """条件化分数列表（R5 行卡版式泛化，80/页）：条件 → 成绩集（键集全量
    过滤、无选谱收缩），按达成率降序分页。"""
    filtered = await combo_filtered_scores(conds, binding, notify_slow=slow_notice())
    if isinstance(filtered, ComboEmpty):
        await UniMessage.text(f" {filtered.message}").finish(at_sender=True)
    matched = sorted(filtered, key=lambda s: s.achievements or 0, reverse=True)
    if not matched:
        await UniMessage.text(" 没有找到符合条件的成绩").finish(at_sender=True)
    end_page = max(1, -(-len(matched) // 80))
    real = min(max(page, 1), end_page)
    # NB 高度公式已下沉 core（pc 列表等第三方扩展共用）
    plc = score_list_height(len(matched), real, end_page)
    service = service_display(binding)
    card = DrawScore(280 + plc, service=service)
    png = card.draw_score_list(cond_text, matched, real, end_page)
    await UniMessage.image(raw=png).finish(at_sender=True)
