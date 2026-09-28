"""表格编排域：评价计划判定、等级条目查询、牌子完成表与进度总览。

被 matchers 的各表格指令 handler 共用；不注册 matcher。
"""

from nonebot_plugin_alconna.uniseg import UniMessage

from ...constants import LEVEL_LIST, chart_display_id
from ...core.score import score_service
from ...core.songs import song_service
from ...core.types import FCType, FSType
from ...core.utils import slow_notice
from ...core.plates import in_plate_scope, major_type_of_plate, plate_version_range
from ...core.binding import service_display
from ...core.render.plate_progress import plate_progress_bytes

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

_LEVEL_ORDER = {lv: i for i, lv in enumerate(LEVEL_LIST)}
"""标级 → 序号（替代循环内 LEVEL_LIST.index 的 O(n) 查找）。"""


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


async def _level_entries(level: str) -> list[tuple]:
    """全库指定标级的谱面条目（定数表/完成表/推分计划三处查询共用）。"""
    from ...core.render.table_template import filter_level

    return filter_level(await song_service.get_all(), level)


async def _plate_completion_sheet(binding, version: str, kind: str, page: int) -> None:
    """完成表（NB DrawPlateTable：底图 + 达成章 + 各槽位计数与进度条）。"""
    from ...core.render import table_template

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
