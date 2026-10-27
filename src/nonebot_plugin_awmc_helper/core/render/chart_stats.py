"""谱面统计（合并转发歌曲信息节点用）：谱面真实时长、各类型音符与 EX 计数。

EX 口径（萌娘百科「Maimai系列」EX-NOTE 条）：EX 音符 = 只有 CRITICAL
PERFECT / MISS 判定的音符，包含 EX-TAP（**含 SLIDE 的星星头部**）、EX-HOLD、
EX-BREAK；**SLIDE 本体 / TOUCH（含烟花）没有 EX**。因此 SLIDE 的 ``is_ex``
归到 TAP 的 EX 计数（星星头按 TAP 判定），SLIDE 自身 EX 恒为 0。
"""

from __future__ import annotations

from dataclasses import dataclass

from maimai_py import SongDifficulty

from .chart_notes import (
    SiteChart,
    break_completion_times,
    break_noex_completion_times,
)

# 站内 note 类型 → 展示类型（hold-end/touch-hold-end 不计入音符总量）
_TYPE_GROUP = {
    "tap": "TAP",
    "simultaneous": "TAP",
    "break": "BREAK",
    "hold-start": "HOLD",
    "hold-start-simultaneous": "HOLD",
    "slide": "SLIDE",
    "touch": "TOUCH",
    "touch-hold-start": "TOUCH",
}
_GROUPS = ("TAP", "HOLD", "SLIDE", "TOUCH", "BREAK")


@dataclass(frozen=True)
class ChartStats:
    """谱面统计结果。"""

    chart_ms: float
    measures: int
    counts: dict[str, int]  # 与谱面信息一致（官方 tap/hold/slide/touch/break）
    ex_counts: dict[str, int]

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    @property
    def ex_total(self) -> int:
        return sum(self.ex_counts.values())


def compute_chart_stats(
    chart: SiteChart, difficulty: SongDifficulty | None = None
) -> ChartStats:
    """统计各类型音符/EX 数量与谱面真实时长（不含音源时长）。

    ``difficulty`` 传入时优先用官方谱面信息（水鱼/落雪 ``tap_num`` 等）作为
    各类型数量：官方口径把 SLIDE 星星头计入 TAP（例：11355 MASTER
    tap 551 = 我方纯 tap 456 + 95 个非无头 slide），用户要求与谱面信息
    对齐。EX 数量官方无此字段：普通类型按解析 note 统计（非 break slide 的
    EX 归 TAP）；BREAK 用与 HUD 一致的完成事件差额统计，保证
    「BREAK 总数 = 无保护 + EX」自洽（12025：146 = 119 + 27）。
    """
    ex_counts: dict[str, int] = dict.fromkeys(_GROUPS, 0)
    for note in chart.notes:
        group = _TYPE_GROUP.get(note.type)
        if group is None or not note.is_ex:
            continue
        if group == "SLIDE":
            # 星星头是 TAP 判定；但 break slide 属于 EX-BREAK（下方按
            # 完成事件差额统计），不能重复计到 EX-TAP。
            is_break_slide = note.is_start_break or any(
                part.is_break for part in note.parts
            )
            if not is_break_slide:
                ex_counts["TAP"] += 1
        elif group == "TOUCH":
            continue  # TOUCH（含烟花）没有 EX 判定
        elif group == "BREAK":
            continue  # EX break 含 break slide 各段，按完成事件差额统计
        elif group == "HOLD" and note.is_break_hold:
            continue  # EX break hold 属于 BREAK 事件
        else:
            ex_counts[group] += 1
    # BREAK 判定 = 头（break tap / star head / hold 头）+ break slide 本体
    # （结尾巴 b 的路径判定）。EX-BREAK 用完成事件差额统计，覆盖两类；
    # 站内 JS 的「无保护」只算头、漏了 slide 本体，此处按完整语义计。
    ex_counts["BREAK"] = max(
        0,
        len(break_completion_times(chart.notes))
        - len(break_noex_completion_times(chart.notes)),
    )

    counts: dict[str, int] = dict.fromkeys(_GROUPS, 0)
    official: dict[str, int] | None = None
    if difficulty is not None:
        official = {
            "TAP": difficulty.tap_num,
            "HOLD": difficulty.hold_num,
            "SLIDE": difficulty.slide_num,
            "TOUCH": difficulty.touch_num,
            "BREAK": difficulty.break_num,
        }
    if official is not None and sum(official.values()) > 0:
        counts.update(official)
    else:
        # 官方物量缺失（如部分宴谱 buddy）：回退解析口径
        for note in chart.notes:
            group = _TYPE_GROUP.get(note.type)
            if group is not None:
                counts[group] += 1
    chart_ms = chart.timeline.ms_from_beat(float(chart.total_beats))
    return ChartStats(
        chart_ms=chart_ms,
        measures=chart.measures,
        counts=counts,
        ex_counts=ex_counts,
    )


def format_chart_duration(ms: float) -> str:
    """毫秒 → ``m:ss.s``（谱面时长，保留 0.1s）。"""
    ms = max(0.0, ms)
    total_tenths = round(ms / 100.0)
    minutes, rest = divmod(total_tenths, 600)
    seconds, tenths = divmod(rest, 10)
    return f"{minutes}:{seconds:02d}.{tenths}"


def format_note_distribution(stats: ChartStats) -> str:
    """各类型音符数 + EX 个数（如 ``TAP 201（EX 12）``）。"""
    parts = []
    for group in _GROUPS:
        count = stats.counts[group]
        ex = stats.ex_counts[group]
        parts.append(f"{group} {count}（EX {ex}）" if ex else f"{group} {count}")
    return " · ".join(parts)
