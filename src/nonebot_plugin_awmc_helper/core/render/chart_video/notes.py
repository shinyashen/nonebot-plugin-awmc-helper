"""渲染 note 数据类与 SiteChart → 渲染 note 序列映射。

字段语义对齐站内 JS note 对象（preparedRenderNotes 输入）。
"""

from dataclasses import field, dataclass

from ..chart_notes import ChartNote, SiteChart, SlidePart


@dataclass
class _Note:
    """渲染 note（由 SiteChart 映射；字段语义对齐 JS note 对象）。"""

    kind: str  # tap / hold / slide / touch / touch-hold
    pos: int  # 按钮 1-8（touch 为 0）
    timing: float  # ms（含站内 +4 拍 lead-in）
    is_break: bool = False
    is_each: bool = False  # 渲染层重算的同刻旗标（calculateSimultaneousCounts）
    is_ex: bool = False
    duration: float = 0.0  # hold/touch-hold ms
    # slide：并行动径（JS allSlideSegments/allDelayMs/allDurationMs/allSlideBreaks）
    parts: list[SlidePart] = field(default_factory=list)
    has_tap_head: bool = False
    headless_mode: str | None = None
    # tap 的星形变体（`$` 修饰，宴谱常见）
    is_star: bool = False
    is_spinning_star: bool = False
    has_delay_marker: bool = False
    fire: bool = False
    sensor: str = ""
    touch_num: int | None = None
    is_headless: bool = False
    visual_only: bool = False
    src_index: int = 0  # Le()：touch 部件排序用（全体 note 序）
    simul_slide_count: int = 1  # 同刻 slide 数（轨道/移动星配色专用，L8123）


def _touch_position_name(note: _Note) -> str:
    """JS note.position 字符串：中心为 "C"，其余 "A1".."E8"。"""
    if (note.sensor or "C") == "C" or note.touch_num is None:
        return "C"
    return f"{note.sensor}{note.touch_num}"


@dataclass
class _Pos:
    x: float
    y: float
    alpha: float
    visible: bool


def _collect_hit_notes(chart: SiteChart) -> list[tuple[int, float, bool]]:
    """hitEffectNotes（L8268）：(按钮位, 时刻 ms, 是否星形打击特效)。

    JS 口径：带数字位的 note 中排除 touch/touch-hold/hold-start/headless slide；
    星形 = break tap / `$` 星 tap / break hold 的 hold-end / break slide 头。
    """
    items: list[tuple[int, float, bool]] = []
    for note in chart.notes:
        if note.type in (
            "tap",
            "break",
            "simultaneous",
            "hold-end",
            "hold-end-simultaneous",
            "slide",
        ):
            if note.type == "slide" and note.is_headless:
                continue
            if not isinstance(note.position, int):
                continue
            is_star = (
                note.type == "break"
                or (note.type in ("tap", "simultaneous") and note.is_star)
                or note.is_break_hold
                or (note.type == "slide" and note.is_start_break)
            )
            items.append((note.position, note.timing_ms, is_star))
    return items


def _map_notes(chart: SiteChart) -> list[_Note]:
    """SiteChart → 渲染 note 序列（字段/顺序对齐 JS preparedRenderNotes 输入）。"""
    notes: list[_Note] = []
    hold_starts: dict[tuple[int, float], ChartNote] = {}
    # Le()（L7031）：touch 部件排序用的 sourceNoteIndex（hold-end 类不计）
    source_index: dict[int, int] = {}
    counter = 0
    for note in chart.notes:
        if note.type in ("hold-end", "hold-end-simultaneous", "touch-hold-end"):
            continue
        if note.type in ("touch", "touch-hold-start"):
            source_index[id(note)] = counter
        counter += 1

    for note in chart.notes:
        kind = note.type
        if kind in ("hold-start", "hold-start-simultaneous"):
            hold_starts[(int(note.position), round(note.timing, 3))] = note
        elif kind in ("hold-end", "hold-end-simultaneous"):
            start_key = (
                int(note.position),
                round(note.hold_start_timing or 0.0, 3),
            )
            start = hold_starts.pop(start_key, None)
            if start is None:
                continue
            notes.append(
                _Note(
                    "hold",
                    int(note.position),
                    start.timing_ms,
                    is_break=start.is_break_hold,
                    is_ex=start.is_ex,
                    duration=note.timing_ms - start.timing_ms,
                )
            )
        elif kind == "slide":
            notes.append(
                _Note(
                    "slide",
                    int(note.position),
                    note.timing_ms,
                    is_break=note.is_start_break,
                    is_ex=note.is_ex,
                    parts=[
                        SlidePart(
                            segments=part.segments,
                            delay_ms=part.delay_ms,
                            duration_ms=part.duration_ms,
                            is_break=part.is_break,
                        )
                        for part in note.parts
                    ],
                    has_tap_head=note.has_tap_head,
                    headless_mode=note.headless_mode,
                    is_headless=note.is_headless,
                    has_delay_marker=note.has_delay_marker,
                )
            )
        elif kind in ("tap", "break", "simultaneous"):
            notes.append(
                _Note(
                    "tap",
                    int(note.position),
                    note.timing_ms,
                    is_break=kind == "break",
                    is_ex=note.is_ex,
                    is_star=note.is_star,
                    is_spinning_star=note.is_spinning_star,
                    has_delay_marker=note.has_delay_marker,
                )
            )
        elif kind in ("touch", "touch-hold-start"):
            position = str(note.position)
            sensor = position[:1]
            number = None
            if sensor != "C" and len(position) > 1:
                try:
                    number = int(position[1:])
                except ValueError:
                    number = None
            duration = 0.0
            if kind == "touch-hold-start":
                duration = note.duration_ms
            notes.append(
                _Note(
                    "touch-hold" if kind == "touch-hold-start" else "touch",
                    0,
                    note.timing_ms,
                    duration=duration,
                    fire=note.has_firework,
                    sensor=sensor,
                    touch_num=number,
                    src_index=source_index.get(id(note), 0),
                )
            )
    return notes
