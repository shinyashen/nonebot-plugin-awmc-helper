"""落雪站内谱面预览的 simai 解析器（chart-canvas bundle 逐语句移植）。

与 ``core/simai.py`` 的分工：``simai.py`` 是指令层物量对拍用的解析器
（MaiConverter 内核，按谱面 token 顺序出 note，一条 ``*`` 分段=多个 note）；
本模块只服务谱面预览渲染——站内渲染器的 note 模型与时间基准必须逐字段一致，
否则滑轨/星星/连击时序都会错：

- 一个 token 的 ``*`` 分段 = **同一个 note 的多条并行动径**（``allSlideSegments``，
  共享 token 时刻与各自的 delay/duration）；``[]`` 内多段 = 单路径多段（``wn``）；
- 所有 note 与 bpm 事件的时刻整体 **+4 拍**（1 小节 lead-in：``measure += 1,
  timing += 4, timingMs += 240000/firstBpm``，L9112/L9258），bpm/divisor 事件
  表各 unshift 一条 timing 0 的基线事件——站内播放器在 lead-in 期间空场，
  音频从 chart beat 4 起播（chartpage.js 的 ``$e(bpm)=240000/bpm`` 与
  ``tt()``/syncFrame 逻辑）；
- ``timingMs`` 由所在 token 的 bpm 累加（``xn`` 的 ``l``），而非用全局时间轴重算。

源码行号均指 ``local/reference/lxns-renderer/chart-canvas.pretty.js``。
"""

from __future__ import annotations

import re
import math
from dataclasses import field, dataclass

# ---------------------------------------- 数据结构（L7031-7142）


@dataclass
class SlideSegment:
    """滑轨单段（wn 输出项：type/startPos/endPos/midPos）。"""

    type: str
    start_pos: int
    end_pos: int
    mid_pos: int | None = None


@dataclass
class SlidePart:
    """滑轨单条路径（``*`` 分隔的每段 = 一条并行动径）。"""

    segments: list[SlideSegment]
    delay_ms: float
    duration_ms: float
    is_break: bool


@dataclass
class ChartNote:
    """站内 note 对象（字段对齐 JS note，类型名沿用 JS type 字符串）。"""

    type: str
    """tap / break / simultaneous / hold-start / hold-end /
    slide / touch / touch-hold-start / touch-hold-end"""

    position: int | str
    timing: float
    timing_ms: float
    measure: int = 0
    position_in_measure: int = 0
    bpm: float = 0.0
    # hold / touch-hold
    duration: float = 0.0
    duration_ms: float = 0.0
    hold_start_timing: float | None = None
    is_break_hold: bool = False
    has_firework: bool = False
    # slide（parts 即 JS 的 allSlideSegments/allDelayMs/allDurationMs/allSlideBreaks）
    parts: list[SlidePart] = field(default_factory=list)
    is_headless: bool = False
    headless_mode: str | None = None
    has_tap_head: bool = False
    is_start_break: bool = False
    is_ex: bool = False
    is_split_slide: bool = False
    # tap
    is_star: bool = False
    is_spinning_star: bool = False
    has_delay_marker: bool = False


@dataclass
class Timeline:
    """时间轴（Re 类 L7047-7097）。"""

    default_bpm: float
    bpm_points: list[tuple[float, float, float]]  # (timing, bpm, cumulativeMs)
    divisor_events: list[tuple[float, int]]  # (timing, divisor)

    def ms_from_beat(self, beat: float) -> float:
        point = None
        for p in self.bpm_points:
            if p[0] <= beat:
                point = p
            else:
                break
        if point is None:
            return 60000.0 * beat / self.default_bpm
        timing, bpm, cumulative = point
        return cumulative + (beat - timing) * 60000.0 / bpm

    def beat_from_ms(self, ms: float) -> float:
        point = None
        for p in self.bpm_points:
            if p[2] <= ms:
                point = p
            else:
                break
        if point is None:
            return ms * self.default_bpm / 60000.0
        timing, bpm, cumulative = point
        return timing + (ms - cumulative) * bpm / 60000.0

    def bpm_at_beat(self, beat: float) -> float:
        bpm = self.default_bpm
        for timing, value, _cum in self.bpm_points:
            if timing <= beat:
                bpm = value
            else:
                break
        return bpm

    def divisor_at_beat(self, beat: float) -> int:
        div = 4
        for timing, value in self.divisor_events:
            if timing <= beat:
                div = value
            else:
                break
        return div


@dataclass
class SiteChart:
    """站内 chart 对象（yn 输出子集）。"""

    difficulty: int
    title: str
    artist: str
    designer: str
    bpm: float
    first_ms: float | None
    notes: list[ChartNote]
    timeline: Timeline
    measures: int
    available_difficulties: set[int]

    @property
    def total_beats(self) -> int:
        return self.measures * 4


# ---------------------------------------- 元数据（bn L9289）


def _read_metadata(line: str, meta: dict) -> None:
    eq = line.find("=")
    if eq == -1:
        return
    key = line[1:eq].strip()
    value = line[eq + 1 :].strip()
    if key in ("title", "artist", "des", "bpm", "first") or key.startswith(
        ("des_", "lv_")
    ):
        meta[key] = value


# ---------------------------------------- 段解析（wn/Tn L9670）

_SHAPE_CHARS = "-><^vpqszVw"


def _parse_segments(shape_text: str, start_pos: int, bpm: float) -> list[SlideSegment]:
    """wn（L9670）：把一条路径形状串拆成段（含 V 折线拆两段）。"""
    segments: list[SlideSegment] = []
    index = 0
    current = start_pos
    text = shape_text
    while index < len(text):
        char = text[index]
        shape: str | None = None
        if index + 1 < len(text) and text[index + 1] == char and char in "pq":
            shape = char + char
            index += 2
        elif char in _SHAPE_CHARS:
            shape = char
            index += 1
        else:
            index += 1
            continue
        if shape == "V":
            digits = ""
            while index < len(text) and text[index].isdigit():
                digits += text[index]
                index += 1
            if index < len(text) and text[index] == "[":
                close = text.find("]", index)
                if close != -1:
                    index = close + 1
            if len(digits) >= 2:
                mid = int(digits[0])
                end = int(digits[1:])
                first_ok = mid == (current + 5) % 8 + 1
                second_ok = mid == (current + 1) % 8 + 1
                forward = ((end - current) % 8 + 8) % 8
                backward = (8 - forward) % 8
                if (first_ok and 1 <= forward <= 4) or (
                    second_ok and 1 <= backward <= 4
                ):
                    segments.append(SlideSegment("V", current, end, mid))
                else:
                    # 非法折线：拆成两段直线（wn 的兜底分支）
                    segments.append(SlideSegment("-", current, mid))
                    segments.append(SlideSegment("-", mid, end))
                current = end
            continue
        digits = ""
        while index < len(text) and text[index].isdigit():
            digits += text[index]
            index += 1
        while index < len(text) and text[index] in "bxBX":
            index += 1
        if index < len(text) and text[index] == "[":
            close = text.find("]", index)
            if close != -1:
                index = close + 1
                while index < len(text) and text[index] in "bxBX":
                    index += 1
        if digits:
            end = int(digits)
            segments.append(SlideSegment(shape, current, end))
            current = end
    return segments


# ---------------------------------------- token 解析（Cn L9427）

_HOLD_RE = re.compile(
    r"^(\d+)[hbx]{1,3}\[(?:([\d.]+):([\d.]+)|#([\d.]+))\][bx]*$", re.IGNORECASE
)
_SLIDE_RE = re.compile(r"^(\d+)([bx?!@]*[-><^vpqszVw*]+.*)$", re.IGNORECASE)
_TOUCH_RE = re.compile(
    r"^([ABCDE])(\d*)([hbfx]*)(?:\[(?:([\d.]+):([\d.]+)|#([\d.]+))\])?$", re.IGNORECASE
)
_TAP_RE = re.compile(r"^(\d+)([bx$]*)$", re.IGNORECASE)
_DUR_MULTI_RE = re.compile(r"\[[\d.:#+]*\]")


def _segment_duration(value: str, num: str, den: str, bpm: float) -> float:
    """Sn（L9423）：时长（拍）——``[a:b]`` = 4/b*a；``[#c]`` = c*bpm/60。"""
    if value:
        return 4 / float(value) * float(num)
    return float(den) * bpm / 60


def _parse_token(
    token: str,
    timing: float,
    timing_ms: float,
    measure: int,
    position_in_measure: int,
    bpm: float,
    simultaneous: bool,
    has_delay_marker: bool,
) -> list[ChartNote]:
    r"""Cn（L9427）：解析单个 token（``/`` 已拆开、去 ``\` `` 前缀后的一个 item）。"""
    notes: list[ChartNote] = []
    base_ms = timing_ms + (1.0 if has_delay_marker else 0.0)

    hold = _HOLD_RE.match(token)
    if hold:
        position = int(hold.group(1))
        duration = _segment_duration(hold.group(2), hold.group(3), hold.group(4), bpm)
        if 1 <= position <= 8:
            lower = token.lower()
            is_break = "b" in lower
            is_ex = "x" in lower
            notes.append(
                ChartNote(
                    type="hold-start-simultaneous" if simultaneous else "hold-start",
                    position=position,
                    timing=timing,
                    timing_ms=base_ms,
                    measure=measure,
                    position_in_measure=position_in_measure,
                    bpm=bpm,
                    duration=duration,
                    duration_ms=60000.0 * duration / bpm,
                    is_break_hold=is_break,
                    is_ex=is_ex,
                    has_delay_marker=has_delay_marker,
                )
            )
            notes.append(
                ChartNote(
                    type="hold-end-simultaneous" if simultaneous else "hold-end",
                    position=position,
                    timing=timing + duration,
                    timing_ms=base_ms + 60000.0 * duration / bpm,
                    measure=int((timing + duration) // 4),
                    position_in_measure=int(((timing + duration) % 4) / 4 * 512),
                    bpm=bpm,
                    hold_start_timing=timing,
                    is_break_hold=is_break,
                    is_ex=is_ex,
                )
            )
            return notes

    slide = _SLIDE_RE.match(token)
    if slide and re.search(r"[-><^vpqszVw]", slide.group(2), re.IGNORECASE):
        position = int(slide.group(1))
        body = slide.group(2)
        first_shape = re.search(r"[-><^vpqszVw*]", body, re.IGNORECASE)
        prefix = body[: first_shape.start()].lower() if first_shape else ""
        headless_mode = "pop" if "!" in prefix else "fade" if "?" in prefix else None
        has_tap_head = headless_mode is None and "@" in prefix
        is_start_break = "b" in prefix
        is_ex = "x" in token.lower()
        parts_text = body.split("*")
        parts: list[SlidePart] = []
        for part_text in parts_text:
            part_break = bool(
                re.search(r"[-><^vpqszVw]\d*b", part_text, re.IGNORECASE)
            ) or bool(re.search(r"\]b", part_text, re.IGNORECASE))
            if len(_DUR_MULTI_RE.findall(part_text)) > 1:
                parsed = _parse_multi_duration_path(position, part_text, bpm)
                parts.append(
                    SlidePart(
                        segments=parsed[0],
                        delay_ms=60000.0 / bpm,
                        duration_ms=parsed[1],
                        is_break=part_break,
                    )
                )
                continue
            duration_beats = 1.0
            custom_delay: float | None = None
            custom_duration_sec: float | None = None
            custom_length: float | None = None
            match_ab = re.search(r"\[([\d.]+)##([\d.]+)\]", part_text)
            match_b = re.search(r"\[#([\d.]+)\]", part_text)
            if match_ab:
                custom_delay = float(match_ab.group(1))
                custom_duration_sec = float(match_ab.group(2))
                duration_beats = custom_duration_sec
            elif match_b:
                custom_duration_sec = float(match_b.group(1))
                duration_beats = custom_duration_sec
            else:
                match = re.search(
                    r"\[(?:([\d.]+)#)?([\d.]+):([\d.]+)(?:##([\d.]+))?\]", part_text
                )
                if match:
                    if match.group(1):
                        custom_delay = float(match.group(1))
                    duration_beats = 4 / float(match.group(2)) * float(match.group(3))
                    if match.group(4):
                        custom_length = float(match.group(4))
            if match_ab and custom_duration_sec is not None:
                # [a##b]：a 秒延迟 + b 秒时长（JS L9504-9506 / L9513 n*1e3、i*1e3）
                duration_ms = custom_duration_sec * 1000.0
                delay_ms = (
                    custom_delay * 1000.0 if custom_delay is not None else 60000.0 / bpm
                )
            elif custom_duration_sec is not None:
                # [#b]：仅绝对秒时长（JS L9507 / L9513）
                duration_ms = custom_duration_sec * 1000.0
                delay_ms = 60000.0 / bpm
            else:
                duration_ms = (
                    custom_length * 1000.0
                    if custom_length is not None
                    else 60000.0 * duration_beats / bpm
                )
                if custom_delay is not None:
                    # [X#Y:Z]：X 是等价 BPM，延迟 = 60000/X ms（JS L9513 u=6e4/n）
                    delay_ms = 60000.0 / custom_delay
                else:
                    delay_ms = 60000.0 / bpm
            stripped = re.sub(
                r"\[(?:(?:[\d.]+#)?[\d.]+:[\d.]+(?:##[\d.]+)?|[\d.]+##[\d.]+|[#][\d.]+)\]",
                "",
                part_text,
                flags=re.IGNORECASE,
            )
            stripped = re.sub(r"[bx$]", "", stripped, flags=re.IGNORECASE)
            segments = _parse_segments(stripped, position, bpm)
            parts.append(
                SlidePart(
                    segments=segments,
                    delay_ms=delay_ms,
                    duration_ms=duration_ms,
                    is_break=part_break,
                )
            )
        if 1 <= position <= 8 and parts:
            notes.append(
                ChartNote(
                    type="slide",
                    position=position,
                    timing=timing,
                    timing_ms=base_ms,
                    measure=measure,
                    position_in_measure=position_in_measure,
                    bpm=bpm,
                    parts=parts,
                    is_headless=headless_mode is not None,
                    headless_mode=headless_mode,
                    has_tap_head=has_tap_head,
                    is_start_break=is_start_break,
                    is_ex=is_ex,
                    is_split_slide=len(parts) > 1,
                    has_delay_marker=has_delay_marker,
                )
            )
            return notes

    if len(token) >= 2 and token.isdigit():
        for char in token:
            value = int(char)
            if not 1 <= value <= 8:
                break
        else:
            for char in token:
                notes.append(
                    ChartNote(
                        type="simultaneous",
                        position=int(char),
                        timing=timing,
                        timing_ms=base_ms,
                        measure=measure,
                        position_in_measure=position_in_measure,
                        bpm=bpm,
                        has_delay_marker=has_delay_marker,
                    )
                )
            return notes

    touch = _TOUCH_RE.match(token)
    if touch:
        sensor = touch.group(1).upper()
        number = int(touch.group(2)) if touch.group(2) else None
        flag = (touch.group(3) or "").lower()
        valid = (
            (sensor == "C" and (number is None or number in (1, 2)))
            if sensor == "C"
            else (number is not None and 1 <= number <= 8)
        )
        if valid:
            name = sensor if sensor == "C" else f"{sensor}{number}"
            is_hold = "h" in flag
            has_firework = "f" in flag
            if is_hold and (touch.group(4) or touch.group(6)):
                duration = _segment_duration(
                    touch.group(4), touch.group(5), touch.group(6), bpm
                )
                duration_ms = 60000.0 * duration / bpm
                notes.append(
                    ChartNote(
                        type="touch-hold-start",
                        position=name,
                        timing=timing,
                        timing_ms=base_ms,
                        measure=measure,
                        position_in_measure=position_in_measure,
                        bpm=bpm,
                        duration=duration,
                        duration_ms=duration_ms,
                        has_firework=has_firework,
                        has_delay_marker=has_delay_marker,
                    )
                )
                end_beats = timing + duration
                notes.append(
                    ChartNote(
                        type="touch-hold-end",
                        position=name,
                        timing=end_beats,
                        timing_ms=base_ms + duration_ms,
                        measure=int(end_beats // 4),
                        position_in_measure=int((end_beats % 4) / 4 * 512),
                        bpm=bpm,
                        hold_start_timing=timing,
                        has_firework=has_firework,
                    )
                )
            else:
                notes.append(
                    ChartNote(
                        type="touch",
                        position=name,
                        timing=timing,
                        timing_ms=base_ms,
                        measure=measure,
                        position_in_measure=position_in_measure,
                        bpm=bpm,
                        has_firework=has_firework,
                        has_delay_marker=has_delay_marker,
                    )
                )
            return notes

    tap = _TAP_RE.match(token)
    if tap:
        position = int(tap.group(1))
        flags = tap.group(2).lower()
        if 1 <= position <= 8:
            is_break = "b" in flags
            notes.append(
                ChartNote(
                    type="break"
                    if is_break
                    else "simultaneous"
                    if simultaneous
                    else "tap",
                    position=position,
                    timing=timing,
                    timing_ms=base_ms,
                    measure=measure,
                    position_in_measure=position_in_measure,
                    bpm=bpm,
                    is_star=" $" in f" {flags}" or "$" in flags,
                    is_spinning_star=flags.count("$") >= 2,
                    is_ex="x" in flags,
                    has_delay_marker=has_delay_marker,
                )
            )
    return notes


def _parse_multi_duration_path(
    start_pos: int, part_text: str, bpm: float
) -> tuple[list[SlideSegment], float]:
    """单 token 内多段路径（``1-2[1:1]-3[1:1]``）→ (段表, 总时长 ms)。

    wn 对每段解析出累计 duration；这里沿用其语义：段时长按各 ``[a:b]`` 累加，
    ``[#x]`` 为绝对秒，其余为 4/b*a 拍。
    """
    segments: list[SlideSegment] = []
    total_beats = 0.0
    total_ms = 0.0
    index = 0
    current = start_pos
    text = part_text
    while index < len(text):
        char = text[index]
        shape: str | None = None
        if index + 1 < len(text) and text[index + 1] == char and char in "pq":
            shape = char + char
            index += 2
        elif char in _SHAPE_CHARS:
            shape = char
            index += 1
        else:
            index += 1
            continue
        digits = ""
        while index < len(text) and text[index].isdigit():
            digits += text[index]
            index += 1
        while index < len(text) and text[index] in "bxBX":
            index += 1
        duration_beats = 1.0
        duration_ms = 60000.0 / bpm
        if index < len(text) and text[index] == "[":
            close = text.find("]", index)
            if close != -1:
                inner = text[index + 1 : close]
                multi = re.match(r"^([\d.]+)##([\d.]+)$", inner)
                single_sec = re.match(r"^#([\d.]+)$", inner)
                if multi:
                    duration_ms = float(multi.group(2)) * 1000.0
                    duration_beats = duration_ms * bpm / 60000.0
                elif single_sec:
                    duration_ms = float(single_sec.group(1)) * 1000.0
                    duration_beats = duration_ms * bpm / 60000.0
                else:
                    normal = re.match(
                        r"^(?:([\d.]+)#)?([\d.]+):([\d.]+)(?:##([\d.]+))?$", inner
                    )
                    if normal:
                        duration_beats = (
                            4 / float(normal.group(2)) * float(normal.group(3))
                        )
                        duration_ms = (
                            float(normal.group(4)) * 1000.0
                            if normal.group(4)
                            else 60000.0 * duration_beats / bpm
                        )
                index = close + 1
                while index < len(text) and text[index] in "bxBX":
                    index += 1
        if digits:
            end = int(digits)
            segments.append(SlideSegment(shape or "-", current, end))
            total_beats += duration_beats
            total_ms += duration_ms
            current = end
    _ = total_beats
    return segments, total_ms


# ---------------------------------------- 行扫描（xn L9333）

_WS = " \t\r\n"


def _parse_statements(
    text: str, bpm: float
) -> tuple[list[ChartNote], list[tuple[float, float]], list[tuple[float, int]], float]:
    """xn（L9333）：逐 token 扫描，返回 (notes, bpmEvents, divisorEvents, firstBpm)。"""
    notes: list[ChartNote] = []
    bpm_events: list[tuple[float, float]] = []
    divisor_events: list[tuple[float, int]] = []
    current_bpm = bpm
    first_bpm: float | None = None
    divisor = 4
    timing = 0.0
    timing_ms = 0.0
    index = 0

    def skip_ws(pos: int) -> int:
        while pos < len(text) and text[pos] in _WS:
            pos += 1
        return pos

    while True:
        index = skip_ws(index)
        if index >= len(text):
            break
        token = ""
        token_start = index
        while index < len(text) and text[index] != ",":
            char = text[index]
            if char in _WS:
                index += 1
                continue
            if char == "(":
                match = re.match(r"^\((\d+(?:\.\d+)?)\)", text[index:])
                if match:
                    current_bpm = float(match.group(1))
                    if first_bpm is None:
                        first_bpm = current_bpm
                    bpm_events.append((timing, current_bpm))
                    index += len(match.group(0))
                    continue
            if char == "{":
                match = re.match(r"^\{(\d+(?:\.\d+)?)\}", text[index:])
                if match:
                    value = float(match.group(1))
                    if value != divisor:
                        divisor = value
                        divisor_events.append((timing, int(divisor)))
                    index += len(match.group(0))
                    continue
            if char == "<":
                match = re.match(
                    r"^<HS\*([-+]?\d*\.?\d+)>", text[index:], re.IGNORECASE
                )
                if match:
                    index += len(match.group(0))
                    continue
            token += char
            index += 1
        if index == token_start and index < len(text) and text[index] != ",":
            index += 1
            continue
        step = 4 / divisor
        if token.strip():
            measure = int(timing // 4)
            position_in_measure = int((timing % 4) / 4 * 512)
            items: list[str] = []
            buffer = ""
            for char in token:
                if char == "`":
                    if buffer.strip():
                        items.append(buffer.strip())
                    buffer = "`"
                elif char == "/":
                    if buffer.strip():
                        items.append(buffer.strip())
                    buffer = ""
                else:
                    buffer += char
            if buffer.strip():
                items.append(buffer.strip())
            simultaneous = len(items) > 1
            for item in items:
                text_item = item.strip()
                if not text_item:
                    continue
                delay_marker = text_item.startswith("`")
                if delay_marker:
                    text_item = text_item[1:]
                notes.extend(
                    _parse_token(
                        text_item,
                        timing,
                        timing_ms,
                        measure,
                        position_in_measure,
                        current_bpm,
                        simultaneous,
                        delay_marker,
                    )
                )
        timing += step
        timing_ms += 60000.0 * step / current_bpm
        if index < len(text) and text[index] == ",":
            index += 1
    return (
        notes,
        bpm_events,
        divisor_events,
        first_bpm if first_bpm is not None else bpm,
    )


# ---------------------------------------- 完成时刻表（prepareRenderNotes L8251）


def note_completion_times(notes: list[ChartNote]) -> list[float]:
    """连击计数（noteCompletionTimes）：判定/长条头尾/滑轨头与各段末/touch。"""
    times: list[float] = []
    for note in notes:
        kind = note.type
        if kind in (
            "tap",
            "break",
            "simultaneous",
            "hold-end",
            "hold-end-simultaneous",
            "touch",
            "touch-hold-end",
        ) or (kind == "slide" and not note.is_headless):
            times.append(note.timing_ms)
        if kind == "slide":
            for part in note.parts:
                times.append(note.timing_ms + part.delay_ms + part.duration_ms)
    times.sort()
    return times


def break_completion_times(notes: list[ChartNote]) -> list[float]:
    """BREAK 计数（breakCompletionTimes）：break tap/hold 头/slide 头与 break 段末。"""
    times: list[float] = []
    for note in notes:
        kind = note.type
        if (
            kind == "break"
            or (kind == "slide" and note.is_start_break)
            or (
                kind in ("hold-start", "hold-start-simultaneous") and note.is_break_hold
            )
        ):
            times.append(note.timing_ms)
        if kind == "slide":
            for part in note.parts:
                if part.is_break:
                    times.append(note.timing_ms + part.delay_ms + part.duration_ms)
    times.sort()
    return times


def break_noex_completion_times(notes: list[ChartNote]) -> list[float]:
    """「无保护」计数（breakNoExCompletionTimes）：非 EX 的 break 判定。

    break 判定共四类：break tap / break star head（slide 前缀 b）/
    break hold 头 / **break slide 本体**（slide 结尾巴 b，路径判定）。
    本函数统计其中非 EX 的全部事件（含 slide 本体），因此
    ``BREAK 总数 = 无保护 + EX``（12025：146 = 119 + 27）。
    站内 JS 的 d 数组只 push 三类“头”，漏掉 break slide 本体，
    会在含「路径 break」的谱面上少算（JS 12025 为 114）。
    """
    times: list[float] = []
    for note in notes:
        kind = note.type
        if note.is_ex:
            continue
        if (
            kind == "break"
            or (kind == "slide" and note.is_start_break)
            or (
                kind in ("hold-start", "hold-start-simultaneous") and note.is_break_hold
            )
        ):
            times.append(note.timing_ms)
        if kind == "slide":
            for part in note.parts:
                if part.is_break:
                    times.append(note.timing_ms + part.delay_ms + part.duration_ms)
    times.sort()
    return times


# ---------------------------------------- 文件解析（yn L9178）


def parse_site_chart(text: str, difficulty: int | None = None) -> dict[int, SiteChart]:
    """解析 simai 全文 → {难度: SiteChart}（难度编号同 &inote_N；1-6）。"""
    lines = [line[:-1] if line.endswith("\r") else line for line in text.split("\n")]
    meta: dict[str, str] = {}
    inotes: dict[int, list[str]] = {}
    current: int | None = None
    for line in lines:
        stripped = line.strip()
        match = re.match(r"^&inote_(\d+)=(.*)$", stripped, re.IGNORECASE)
        if match:
            current = int(match.group(1))
            inotes[current] = [match.group(2)] if match.group(2) else []
            continue
        if current is not None:
            if stripped.startswith("&") and not stripped.startswith("&inote"):
                current = None
                _read_metadata(stripped, meta)
            elif not stripped.startswith("&"):
                inotes[current].append(line)
            continue
        if stripped.startswith("&"):
            _read_metadata(stripped, meta)
    charts: dict[int, SiteChart] = {}
    for suffix in sorted(inotes, reverse=True):
        if difficulty is not None and suffix != difficulty:
            continue
        body = "\n".join(inotes[suffix])
        chart = _build_chart(body, suffix, meta)
        charts[suffix] = chart
    return charts


def _build_chart(body: str, suffix: int, meta: dict[str, str]) -> SiteChart:
    """yn 后段（L9231-9282）：求解 bpm/first → 解析正文 → +4 拍 lead-in 偏移。"""
    parsed_bpm = float(meta.get("bpm", 0) or 0)
    if not parsed_bpm:
        match = re.search(r"\((\d+(?:\.\d+)?)\)", body)
        if match:
            parsed_bpm = float(match.group(1))
    if not parsed_bpm:
        raise ValueError("simai 文件缺少 BPM 声明")
    notes, bpm_events, divisor_events, first_bpm = _parse_statements(body, parsed_bpm)
    first_ms = None
    if meta.get("first"):
        try:
            first_ms = float(meta["first"]) * 1000.0
        except ValueError:
            first_ms = None
    # 站内把所有 note/bpm/divisor 事件整体 +4 拍（1 小节 lead-in），
    # 对应 bpm 事件的 timingMs 偏移 240000/firstBpm（L9112/L9258）。
    lead_ms = 240000.0 / first_bpm
    max_measure = 0
    max_timing = 0.0
    for note in notes:
        note.measure += 1
        note.timing += 4
        note.timing_ms += lead_ms
        if note.type == "hold-end" or note.type == "hold-end-simultaneous":
            max_measure = max(max_measure, note.measure)
        if note.hold_start_timing is not None:
            note.hold_start_timing += 4
        if note.measure > max_measure:
            max_measure = note.measure
        end = note.timing
        if note.type in ("hold-start", "hold-start-simultaneous"):
            end = note.timing + note.duration
        elif note.type == "slide":
            if note.parts:
                end = note.timing + max(
                    p.delay_ms + p.duration_ms for p in note.parts
                ) / (60000.0 / note.bpm)
        elif note.type == "touch-hold-start":
            end = note.timing + note.duration
        if end > max_timing:
            max_timing = end
    shifted_bpm = [(timing + 4, value) for timing, value in bpm_events]
    shifted_bpm.insert(0, (0.0, first_bpm))
    shifted_divisor = [(timing + 4, value) for timing, value in divisor_events]
    shifted_divisor.insert(0, (0.0, 4))
    shifted_divisor.sort(key=lambda item: item[0])
    timeline = Timeline(
        default_bpm=first_bpm,
        bpm_points=_create_bpm_points(first_bpm, shifted_bpm),
        divisor_events=shifted_divisor,
    )
    measures = max(max_measure, math.ceil(max_timing / 4)) + 2
    designer = meta.get(f"des_{suffix}", meta.get("des", ""))
    return SiteChart(
        difficulty=suffix,
        title=meta.get("title", ""),
        artist=meta.get("artist", ""),
        designer=designer,
        bpm=first_bpm,
        first_ms=first_ms,
        notes=notes,
        timeline=timeline,
        measures=measures,
        available_difficulties=set(),
    )


def _create_bpm_points(
    default_bpm: float, bpm_events: list[tuple[float, float]]
) -> list[tuple[float, float, float]]:
    """createBpmPoints（L7074）。"""
    if not bpm_events:
        return []
    sorted_events = sorted(bpm_events, key=lambda item: item[0])
    points: list[tuple[float, float, float]] = []
    previous_timing = 0.0
    previous_bpm = default_bpm
    cumulative = 0.0
    for timing, bpm in sorted_events:
        cumulative += (timing - previous_timing) * 60000.0 / previous_bpm
        points.append((timing, bpm, cumulative))
        previous_timing = timing
        previous_bpm = bpm
    return points
