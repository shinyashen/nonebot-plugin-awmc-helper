"""simai 谱面文本解析器（谱面预览数据层，落雪 assets2 simai 格式）。

只做「文本 → 带时间戳的 note 序列」，不含渲染。片段语法解析直接采用
MaiConverter 的 lark 解析器（vendor 于 ``core/_vendor/maiconverter_simai``，
MIT），本模块负责：

- 头部字段与各难度 ``&inote_{n}`` 正文切分；
- 落雪方言预处理：``E`` 截断、``*`` 缺省的相邻形状链补插星号
  （如 ``3x>1v4[4:3]`` → ``3x>1*v4[4:3]``，实测仅个别谱出现）；
- 时钟推进与秒时刻换算（语义照抄上游 ``SimaiChart.from_str``：每步推进
  ``1/细分`` 小节、滑键头部自动补 tap 并按步内同位去重、``?``/``!``/``$``
  为 tapless/无判定、``[X#Y:Z]`` 等效 BPM 缩放、`` ` `` 微小节偏移）；
- each 配色判定（同步非 touch note ≥2）与物量计数（对拍水鱼物量口径）。

已知边界：解析失败的 token 记入 ``SimaiDifficulty.warnings`` 跳过，不抛
异常（预览是尽力而为的展示功能）。
"""

import re
from dataclasses import field, dataclass

from ._vendor.maiconverter_simai import parse_fragment

_RE_BPM = re.compile(r"\((\d+(?:\.\d+)?)\)")
_RE_SUB = re.compile(r"\{(\d+)\}")
_RE_INOTE_SPLIT = re.compile(r"&inote_(\d)=\n?")
_RE_HEADER = re.compile(r"&(\w+)=(.*)")

# 形状对：形状 + 结束位（V 形额外带一位 reflect，单独处理）。
# 注意 ``/`` 与 ``^`` 在落雪库中不作滑键形状出现（``/`` 是并发分隔符，
# 由 lark 语法按最长匹配天然区分），但保留 ``^`` 以兼容上游语法。
_SHAPE_PAIR = re.compile(r"(pp|qq|V|v|p|q|s|w|z|>|<|\^|-)([0-8])")
# 形状对之后的「对后修饰」（上游连接滑语法里位于形状前的集合）
_POST_PAIR_MODS = re.compile(r"[b@x?$!]+")

# 滑键默认起手延迟（上游 add_slide 口径：0.25 小节，随等效 BPM 缩放）
_SLIDE_DELAY_MEASURE = 0.25
# 反引号伪 each 的步内偏移（上游口径：≈ma2 384 分辨率 1 tick）
_BACKTICK_OFFSET = 0.0027


@dataclass
class SimaiTap:
    """单击点（含滑键头部自动补的 tap）。"""

    time: float
    position: int  # 0-7 内部位（0 = 按钮 1）
    is_break: bool = False
    is_ex: bool = False
    is_each: bool = False
    visual_only: bool = False  # $ / ? / !（无判定）


@dataclass
class SimaiHold:
    """长条（按钮位按住一段时长）。"""

    time: float
    duration: float
    position: int
    is_break: bool = False
    is_ex: bool = False
    is_each: bool = False
    visual_only: bool = False


@dataclass
class SimaiSlide:
    """滑键段（``*`` 链每段独立，time = 头部 tap 时刻；时刻均为秒）。"""

    time: float
    duration: float
    delay: float  # 移动箭头相对头部的延迟秒数（先有连接线的阶段）
    start_pos: int
    end_pos: int
    shape: str  # - ^ < > s z v w p q pp qq V
    reflect_pos: int | None = None  # 仅 V 形：中继位
    is_break: bool = False
    is_ex: bool = False
    is_each: bool = False
    tapless: bool = False  # 头部不出 tap


@dataclass
class SimaiTouch:
    """touch 点（传感器 A-E；duration>0 为 touch 长条）。"""

    time: float
    sensor: str  # A-E
    position: int | None  # 0-7；None = 整圈/特殊位（C0-C2 记 0-2）
    fire: bool = False
    duration: float = 0.0
    is_each: bool = False


SimaiNote = SimaiTap | SimaiHold | SimaiSlide | SimaiTouch


@dataclass
class SimaiDifficulty:
    """单难度解析结果（时刻均已换算为秒）。"""

    suffix: int  # simai 难度后缀（2=BASIC … 6=Re:MASTER；宴谱 1/2 等）
    notes: list[SimaiNote] = field(default_factory=list)
    bpm_events: list[tuple[float, float]] = field(default_factory=list)  # (秒, BPM)
    chart_end: float = 0.0  # 谱面正文走完时刻（秒，含尾部空拍）
    warnings: list[str] = field(default_factory=list)

    @property
    def note_end(self) -> float:
        """最后一个 note 的结束时刻（hold/touch 长条/滑键含持续段）。"""
        end = 0.0
        for n in self.notes:
            if isinstance(n, (SimaiHold, SimaiSlide, SimaiTouch)):
                end = max(end, n.time + n.duration)
            else:
                end = max(end, n.time)
        return end

    def counts(self) -> dict[str, int]:
        """物量计数（对拍水鱼物量口径）：

        - tap 列 = 非破坏 tap（含滑键头补 tap，含 ``$`` 无判定）；
        - hold/slide/touch 列 = 全量（slide 按段计）；
        - break 列 = 全部带 ``b`` 标记的 note（滑键按 token 计 1 次）。
        """
        result = {"tap": 0, "hold": 0, "slide": 0, "touch": 0, "break": 0}
        for n in self.notes:
            if isinstance(n, SimaiTap):
                if not n.is_break:
                    result["tap"] += 1
            elif isinstance(n, SimaiHold):
                result["hold"] += 1
            elif isinstance(n, SimaiSlide):
                result["slide"] += 1
            elif isinstance(n, SimaiTouch):
                # touch 长条归 hold 列（水鱼物量口径），touch 列只计 touch 点
                if n.duration:
                    result["hold"] += 1
                else:
                    result["touch"] += 1
            if getattr(n, "is_break", False):
                result["break"] += 1
        return result


@dataclass
class SimaiSong:
    """单文件（单谱面类型）解析结果。"""

    title: str = ""
    artist: str = ""
    genre: str = ""
    version: str = ""
    music_id: int | None = None
    bpm: float = 0.0
    pv_start: float | None = None  # 官方试听段起点（秒）
    pv_end: float | None = None
    difficulties: dict[int, SimaiDifficulty] = field(default_factory=dict)


def _normalize_chain(step: str) -> str:
    """补插缺省的 ``*`` 连接符（落雪方言）。

    规则：一个完整「形状+位」对之后、中间无时长括号地紧跟下一形状对时，
    在两对之间补插 ``*``（``3x>1v4[4:3]`` → ``3x>1*v4[4:3]``）。V 形按
    V+reflect+end 两位消费；段尾 ``]b`` 类修饰由语法的 slide_postfix 处理；
    「]b*链」形态把段级修饰挪进后继链段（``7bv1[8:1]b*v5`` →
    ``7bv1[8:1]*bv5``）。
    """
    step = re.sub(r"\]([b@x?$!]+)\*", r"]*\1", step)
    out: list[str] = []
    i = 0
    n = len(step)
    prev_pair_end = -1  # 上一形状对结束位置（-1 = 不在链中）
    while i < n:
        m = _SHAPE_PAIR.match(step, i)
        if m:
            if i == prev_pair_end:
                out.append("*")
            shape = m.group(1)
            j = m.end()
            if shape == "V" and j < n and step[j].isdigit():
                j += 1  # V 的 reflect 位
            out.append(step[i:j])
            i = j
            prev_pair_end = i
            continue
        ch = step[i]
        if ch in "[,":
            prev_pair_end = -1  # 时长括号/并发分隔打断链判定
        out.append(ch)
        i += 1
    return "".join(out)


@dataclass
class _RawNote:
    """小节坐标系的中间 note（转秒前的内部形态）。"""

    kind: str  # tap / hold / slide / touch
    measure: float
    duration: float = 0.0
    data: dict = field(default_factory=dict)


def _parse_body(body: str, suffix: int, base_bpm: float) -> SimaiDifficulty:
    """解析单难度 inote 正文：小节时钟推进 → 事件收集 → 秒换算。"""
    diff = SimaiDifficulty(suffix=suffix)
    measure = 0.0
    bpm = base_bpm if base_bpm > 0 else 120.0
    bpm_events: list[tuple[float, float]] = [(0.0, bpm)]
    divisor = 1

    for raw_step in body.split(","):
        step = raw_step.strip()
        if step == "E":
            # 终止标记独占一步（注意不可子串截断——touch 的 E 传感器会误杀）
            break
        step = step.rstrip("/")  # 步尾悬空并发分隔（实测 B7/ 形态）
        if not step:
            measure += 1 / divisor
            continue

        # (BPM) 与 {n} 可与 note 同现；先剥离（均从本步起生效）
        if m := _RE_BPM.search(step):
            new_bpm = float(m.group(1))
            if new_bpm != bpm:
                bpm_events.append((measure, new_bpm))
                bpm = new_bpm
            step = step[: m.start()] + step[m.end() :]
        if m := _RE_SUB.search(step):
            divisor = max(1, int(m.group(1)))
            step = step[: m.start()] + step[m.end() :]
        step = step.strip()

        try:
            events = parse_fragment(_normalize_chain(step))
        except Exception as e:  # 语法错误 / 未知 token：跳步记 warning
            diff.warnings.append(
                f"后缀 {suffix} @ {measure:.4f} 小节：{step!r}（{type(e).__name__}）"
            )
            measure += 1 / divisor
            continue

        head_positions: set[int] = set()  # 本步已出 tap 的位（滑键头去重）
        offset = 0.0
        raw_notes: list[_RawNote] = []
        for event in events:
            etype = event["type"]
            if etype == "bpm":
                new_bpm = event["value"]
                if new_bpm != bpm:
                    bpm_events.append((measure, new_bpm))
                    bpm = new_bpm
                continue
            if etype == "divisor":
                divisor = int(event["value"])
                continue

            modifier = event.get("modifier", "")
            visual = any(c in modifier for c in "?!$")
            is_break = "b" in modifier
            is_ex = "x" in modifier
            at = measure + offset
            offset = offset + _BACKTICK_OFFSET if "`" in modifier else 0.0

            if etype == "tap":
                raw_notes.append(
                    _RawNote(
                        "tap",
                        at,
                        data={
                            "position": event["button"],
                            "is_break": is_break,
                            "is_ex": is_ex,
                            "visual_only": visual,
                        },
                    )
                )
                head_positions.add(event["button"])
            elif etype == "hold":
                raw_notes.append(
                    _RawNote(
                        "hold",
                        at,
                        duration=event["duration"] or 0.0,
                        data={
                            "position": event["button"],
                            "is_break": is_break,
                            "is_ex": is_ex,
                            "visual_only": visual,
                        },
                    )
                )
                head_positions.add(event["button"])
            elif etype == "slide":
                is_chain = "*" in modifier
                # 链段不出头 tap（水鱼物量口径：仅首段/独立滑出 tap）
                tapless = visual or is_chain or event["start_button"] in head_positions
                raw_notes.append(
                    _RawNote(
                        "slide",
                        at,
                        duration=event["duration"] or 0.0,
                        data={
                            "start_pos": event["start_button"],
                            "end_pos": event["end_button"],
                            "shape": event["pattern"],
                            "reflect_pos": event["reflect_position"],
                            "is_break": is_break,
                            "is_ex": is_ex,
                            "tapless": tapless,
                            "equivalent_bpm": event["equivalent_bpm"],
                        },
                    )
                )
                if not tapless:
                    # 头 tap 不继承 break（水鱼物量口径：b 标记按 token 计 1 次，
                    # 滑键本的破坏不计在头部 tap 上）
                    raw_notes.append(
                        _RawNote(
                            "tap",
                            at,
                            data={
                                "position": event["start_button"],
                                "is_break": False,
                                "is_ex": is_ex,
                                "visual_only": False,
                            },
                        )
                    )
                    head_positions.add(event["start_button"])
            elif etype == "touch_tap":
                raw_notes.append(
                    _RawNote(
                        "touch",
                        at,
                        data={
                            "sensor": event["region"],
                            "position": event["location"],
                            "fire": "f" in modifier,
                        },
                    )
                )
            elif etype == "touch_hold":
                raw_notes.append(
                    _RawNote(
                        "touch",
                        at,
                        duration=event["duration"] or 0.0,
                        data={
                            "sensor": event["region"],
                            "position": event["location"],
                            "fire": "f" in modifier,
                        },
                    )
                )

        # each 判定：同步非 touch note 数 ≥2（滑键头补 tap 参与计数）
        non_touch = [r for r in raw_notes if r.kind != "touch"]
        is_each = len(non_touch) >= 2
        for raw in raw_notes:
            raw.data["is_each"] = is_each if raw.kind != "touch" else False
            diff.notes.extend(_build_note(raw, bpm))
        measure += 1 / divisor

    # 小节 → 秒换算（BPM 分段积分）
    to_seconds = _make_seconds_fn(bpm_events)
    diff.bpm_events = [(to_seconds(m), b) for m, b in bpm_events]
    for n in diff.notes:
        _convert_note_times(n, to_seconds)
    diff.chart_end = to_seconds(measure)
    return diff


def _build_note(raw: _RawNote, bpm: float) -> list[SimaiNote]:
    """内部形态 → 数据类 note（小节坐标，含滑键头 tap 的生成）。"""
    d = raw.data
    if raw.kind == "tap":
        return [
            SimaiTap(
                time=raw.measure,
                position=d["position"],
                is_break=d["is_break"],
                is_ex=d["is_ex"],
                is_each=d["is_each"],
                visual_only=d["visual_only"],
            )
        ]
    if raw.kind == "hold":
        return [
            SimaiHold(
                time=raw.measure,
                duration=raw.duration,
                position=d["position"],
                is_break=d["is_break"],
                is_ex=d["is_ex"],
                is_each=d["is_each"],
                visual_only=d["visual_only"],
            )
        ]
    if raw.kind == "slide":
        delay = _SLIDE_DELAY_MEASURE
        if eq := d["equivalent_bpm"]:
            delay *= bpm / eq
        notes = [
            SimaiSlide(
                time=raw.measure,
                duration=raw.duration,
                delay=delay,
                start_pos=d["start_pos"],
                end_pos=d["end_pos"],
                shape=d["shape"],
                reflect_pos=d["reflect_pos"],
                is_break=d["is_break"],
                is_ex=d["is_ex"],
                is_each=d["is_each"],
                tapless=d["tapless"],
            )
        ]
        return notes
    return [
        SimaiTouch(
            time=raw.measure,
            sensor=d["sensor"],
            position=d["position"],
            fire=d["fire"],
            duration=raw.duration,
            is_each=d["is_each"],
        )
    ]


def _make_seconds_fn(bpm_events: list[tuple[float, float]]):
    """构造 小节 → 秒 的分段换算函数（BPM 变化点分段积分）。"""

    def to_seconds(target: float) -> float:
        total = 0.0
        for idx, (m0, bpm0) in enumerate(bpm_events):
            m1 = bpm_events[idx + 1][0] if idx + 1 < len(bpm_events) else target
            if target <= m0:
                break
            span = min(target, m1) - m0
            if span > 0:
                total += span * 4.0 * 60.0 / bpm0
        return total

    return to_seconds


def _convert_note_times(note: SimaiNote, to_seconds) -> None:
    """note 时刻/时长/延迟 由小节换算为秒（原位修改）。"""
    start_m = note.time
    note.time = to_seconds(start_m)
    if isinstance(note, (SimaiHold, SimaiSlide, SimaiTouch)):
        if note.duration:
            note.duration = max(0.0, to_seconds(start_m + note.duration) - note.time)
        if isinstance(note, SimaiSlide):
            note.delay = max(0.0, to_seconds(start_m + note.delay) - note.time)


_HEADER_INT_KEYS = {"music_id"}
_HEADER_FLOAT_KEYS = {"bpm", "pvstart", "pvend"}
_HEADER_TEXT_KEYS = ("title", "artist", "genre", "version")
# simai 头部键 → SimaiSong 字段（pvstart/pvend 字段名带下划线）
_HEADER_KEY_MAP = {"pvstart": "pv_start", "pvend": "pv_end"}


def parse_simai(text: str) -> SimaiSong:
    """解析落雪 simai 全曲文本。

    头部 ``&key=value`` 行 + 各难度 ``&inote_{n}=`` 多行正文（至下一 ``&``
    行止）。容错口径：缺头部字段给默认值，正文解析失败记 warning 跳步。
    """
    song = SimaiSong()
    parts = _RE_INOTE_SPLIT.split(text)
    for key, value in _RE_HEADER.findall(parts[0]):
        value = value.strip()
        try:
            if key in _HEADER_INT_KEYS:
                setattr(song, key, int(value))
            elif key in _HEADER_FLOAT_KEYS:
                setattr(song, _HEADER_KEY_MAP.get(key, key), float(value))
            elif key in _HEADER_TEXT_KEYS:
                setattr(song, key, value)
        except ValueError:
            continue

    for i in range(1, len(parts) - 1, 2):
        suffix = int(parts[i])
        body = re.split(r"\n&", parts[i + 1])[0]
        if not body.strip():
            continue
        song.difficulties[suffix] = _parse_body(body, suffix, song.bpm)
    return song
