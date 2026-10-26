"""谱面预览片段范围：解析、按小节对齐、越界保护。

用户输入的是**视频时间轴**的秒数（如 ``0:35-1:20``）——与成片播放时间一致
（视频 0 秒 = 站内 chart beat 0）。渲染前按 4 拍/小节对齐到最近的小节边界：
起点向上取整、终点向下取整，保证片段首尾都落在小节线上。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .chart_notes import SiteChart

_SIDE = r"(?:\d{1,3}:[0-5]?\d|\d+(?:\.\d+)?)"
_RANGE_RE = re.compile(rf"^({_SIDE})?-({_SIDE})?$")


@dataclass(frozen=True)
class ChartRange:
    """对齐后的小节范围（chart 显示时间轴毫秒，左闭右开）。"""

    start_ms: float
    end_ms: float

    @property
    def duration_ms(self) -> float:
        return self.end_ms - self.start_ms


def format_time(ms: float) -> str:
    """毫秒 → ``m:ss``（向上取整到秒，用于提示文案）。"""
    total = max(0, round(ms / 1000.0))
    return f"{total // 60}:{total % 60:02d}"


def _side_seconds(text: str | None) -> float | None:
    if not text:
        return None
    if ":" in text:
        minute, second = text.split(":", 1)
        return int(minute) * 60.0 + int(second)
    return float(text)


def parse_range_token(token: str) -> tuple[float | None, float | None] | None:
    """解析范围 token（秒）：``0:35-1:20`` / ``-1:20`` / ``0:35-``。

    ``-`` 前后可省略一侧（表示从头开始 / 放到结尾）；``-`` 本身无效。
    """
    match = _RANGE_RE.match(token.strip())
    if match is None:
        return None
    start = _side_seconds(match.group(1))
    end = _side_seconds(match.group(2))
    if start is None and end is None:
        return None
    return start, end


def measure_times_ms(chart: SiteChart) -> list[float]:
    """所有小节边界（显示时间轴毫秒），含 0 与曲末。"""
    times = [
        chart.timeline.ms_from_beat(float(beat))
        for beat in range(0, chart.total_beats + 1, 4)
    ]
    return times


def measure_duration_ms(chart: SiteChart, at_ms: float) -> float:
    """一段位置所在小节的时长（该处 BPM 下 4 拍；BPM 变化时按当前段）。"""
    beat = chart.timeline.beat_from_ms(at_ms)
    return chart.timeline.ms_from_beat(beat + 4.0) - at_ms


def add_preroll(
    chart: SiteChart, clip: ChartRange, measures: int
) -> tuple[float, float, float]:
    """片段前置空小节：返回 (成片起点, 音符开始渲染点, 成片终点)。

    前置段只有音乐与空场地（不渲染音符/特效），长度 = ``measures`` 个小节；
    起点不足时钳到 0。
    """
    if measures <= 0:
        return clip.start_ms, clip.start_ms, clip.end_ms
    span = measure_duration_ms(chart, clip.start_ms) * measures
    start = max(0.0, clip.start_ms - span)
    return start, clip.start_ms, clip.end_ms


def snap_range(
    chart: SiteChart, start_s: float | None, end_s: float | None
) -> ChartRange:
    """把秒范围按小节对齐（越界钳制；无完整小节时抛 ValueError）。

    ``start_s``/``end_s`` 任一为 None 表示从头 / 到结尾。
    """
    total_ms = chart.timeline.ms_from_beat(float(chart.total_beats))
    requested_start = 0.0 if start_s is None else start_s * 1000.0
    requested_end = total_ms if end_s is None else end_s * 1000.0
    if requested_end <= requested_start:
        raise ValueError("片段范围需满足「开始 < 结束」，例如 0:35-1:20")
    if requested_start < 0:
        requested_start = 0.0
    if requested_start >= total_ms:
        raise ValueError(f"片段起点超出歌曲长度（整曲约 {format_time(total_ms)}）")
    requested_end = min(requested_end, total_ms)
    bounds = measure_times_ms(chart)
    # 起点向上取整、终点向下取整到小节边界
    start_ms = next((t for t in bounds if t >= requested_start - 1e-6), bounds[-1])
    end_ms = max((t for t in bounds if t <= requested_end + 1e-6), default=bounds[0])
    if end_ms <= start_ms:
        raise ValueError(
            "片段范围内没有完整小节，请把范围放宽到至少一个小节"
            f"（约 {round((bounds[1] - bounds[0]) / 1000.0, 1)} 秒）"
        )
    if start_ms >= total_ms:
        raise ValueError("片段起点超出歌曲长度")
    return ChartRange(start_ms=start_ms, end_ms=min(end_ms, total_ms))
