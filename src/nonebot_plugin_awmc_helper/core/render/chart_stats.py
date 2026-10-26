"""谱面统计（合并转发歌曲信息节点用）：谱面真实时长、各类型音符与 EX 计数。"""

from __future__ import annotations

from dataclasses import dataclass

from .chart_notes import SiteChart

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
    counts: dict[str, int]
    ex_counts: dict[str, int]

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    @property
    def ex_total(self) -> int:
        return sum(self.ex_counts.values())


def compute_chart_stats(chart: SiteChart) -> ChartStats:
    """从 SiteChart 统计各类音符/EX 数量与谱面真实时长（不含音源时长）。"""
    counts = dict.fromkeys(_GROUPS, 0)
    ex_counts = dict.fromkeys(_GROUPS, 0)
    for note in chart.notes:
        group = _TYPE_GROUP.get(note.type)
        if group is None:
            continue
        counts[group] += 1
        if note.is_ex:
            ex_counts[group] += 1
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
