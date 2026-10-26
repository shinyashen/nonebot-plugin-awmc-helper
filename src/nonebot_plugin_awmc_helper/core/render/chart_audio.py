"""谱面预览音频合成：站内打击音效。

对齐站内播放器的 AnswerSound 管理器（``chunk-BS312iYd.js`` L7430-8000）：
按 note 类型把 11 个 wav 采样混成一条音轨（answer 层 / judge 层），再与整曲
音乐在 mux 阶段混合。站内音量默认 music=0.8、sound(answer)=0.6、judge=1.0，
因此相对音乐的系数是 answer 0.75、judge 1.25。

时间口径：站内 schedule() 的播放时刻 = 音符时刻 + timingOffsetMs（默认
-50ms，即**提前 50ms** 触发，用于抵消输出/采样延迟），由 ``offset_ms`` 控制。
"""

from __future__ import annotations

import io
import math
import wave
from typing import Any

import numpy as np

from .chart_notes import SiteChart

MIX_RATE = 48000
"""音效轨采样率（站内 wav 均为 48kHz，与 mp3 由 ffmpeg 重采样到同率混音）。"""

_MUSIC_GAIN = 0.8  # 站内默认 musicVolume
_ANSWER_GAIN = 0.6 / _MUSIC_GAIN  # 站内默认 soundVolume，换算成相对音乐
_JUDGE_GAIN = 1.0 / _MUSIC_GAIN  # 站内默认 judgeVolume
_RELEASE_MS = 8  # 站内 judge 层截断释放（In）
_TAIL_S = 4.0  # 最长采样时长（firework 3.9s）

# 站内 wav 采样名（answer.wav 是每个基础音都叠的底音）
SOUND_NAMES = (
    "answer",
    "tap",
    "touch",
    "break",
    "slide",
    "cheer",
    "ex",
    "firework",
    "break_slide",
    "break_slide_cheer",
    "touch_hold",
)


def _decode_wav(data: bytes) -> np.ndarray:
    """wav 字节 → float32 单声道（PCM16；多声道取均值）。"""
    with wave.open(io.BytesIO(data)) as w:
        frames = w.readframes(w.getnframes())
        channels = w.getnchannels()
        width = w.getsampwidth()
    if width == 2:
        arr = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 1:
        arr = (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128) / 128
    elif width == 4:
        arr = np.frombuffer(frames, dtype="<i4").astype(np.float32) / 2147483648.0
    elif width == 3:
        raw = np.frombuffer(frames, dtype=np.uint8).reshape(-1, 3)
        vals = (
            raw[:, 0].astype(np.int32)
            | (raw[:, 1].astype(np.int32) << 8)
            | (raw[:, 2].astype(np.int32) << 16)
        )
        vals = np.where(vals >= 1 << 23, vals - (1 << 24), vals)
        arr = vals.astype(np.float32) / 8388608.0
    else:
        raise ValueError(f"不支持的 wav 位深：{width}")
    if channels > 1:
        arr = arr.reshape(-1, channels).mean(axis=1)
    return arr


def _encode_wav(samples: np.ndarray, rate: int = MIX_RATE) -> bytes:
    """float32 [-1,1] → 16bit 单声道 wav 字节。"""
    pcm = np.clip(samples, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


class _Events:
    """按毫秒时间合并同刻音效标记（对齐站内 Yn() 的合成事件）。"""

    def __init__(self) -> None:
        self._by_time: dict[str, dict[str, Any]] = {}

    def at(self, time_ms: float) -> dict[str, Any]:
        key = f"{time_ms:.3f}"
        event = self._by_time.get(key)
        if event is None:
            event = {
                "time_ms": time_ms,
                "base": False,
                "tap_judge": False,
                "break_judge": False,
                "ex_judge": False,
                "slide": False,
                "break_slide": False,
                "break_slide_cheer": False,
                "touch": False,
                "firework": False,
                "touch_hold": False,
                "touch_hold_ms": 0.0,
                "hold_end": False,
                "touch_hold_end": False,
                "touch_hold_end_firework": False,
            }
            self._by_time[key] = event
        return event

    def sorted(self) -> list[dict[str, Any]]:
        return sorted(self._by_time.values(), key=lambda e: e["time_ms"])


def _collect_events(chart: SiteChart) -> list[dict[str, Any]]:
    """chart.notes → 音效事件（口径同站内 Yn()，L7447）。"""
    events = _Events()
    for note in chart.notes:
        kind = note.type
        if kind == "slide":
            if not note.is_headless:
                event = events.at(note.timing_ms)
                event["base"] = True
                if note.is_start_break:
                    event["break_judge"] = True
                elif note.is_ex:
                    event["ex_judge"] = True
                else:
                    event["tap_judge"] = True
            for part in note.parts:
                at = events.at(note.timing_ms + part.delay_ms)
                at["slide"] = True
                if part.is_break:
                    at["break_slide"] = True
                    after = events.at(note.timing_ms + part.delay_ms + part.duration_ms)
                    after["break_slide_cheer"] = True
        elif kind == "break":
            event = events.at(note.timing_ms)
            event["base"] = True
            event["break_judge"] = True
        elif kind in ("tap", "simultaneous"):
            event = events.at(note.timing_ms)
            event["base"] = True
            if note.is_ex:
                event["ex_judge"] = True
            else:
                event["tap_judge"] = True
        elif kind in ("hold-start", "hold-start-simultaneous"):
            event = events.at(note.timing_ms)
            event["base"] = True
            if note.is_break_hold:
                event["break_judge"] = True
            elif note.is_ex:
                event["ex_judge"] = True
            else:
                event["tap_judge"] = True
        elif kind in ("hold-end", "hold-end-simultaneous"):
            events.at(note.timing_ms)["hold_end"] = True
        elif kind == "touch":
            event = events.at(note.timing_ms)
            if note.has_firework:
                event["firework"] = True
            else:
                event["touch"] = True
        elif kind == "touch-hold-start":
            event = events.at(note.timing_ms)
            event["touch"] = True
            event["touch_hold"] = True
            event["touch_hold_ms"] = max(event["touch_hold_ms"], note.duration_ms)
        elif kind == "touch-hold-end":
            event = events.at(note.timing_ms)
            if note.has_firework:
                event["touch_hold_end_firework"] = True
            else:
                event["touch_hold_end"] = True
    return events.sorted()


def _should_play_answer(event: dict[str, Any]) -> bool:
    """站内 shouldPlaySound：底层 answer 音在基础音/触控/长按结束等都会响。"""
    return bool(
        event["base"]
        or event["touch"]
        or event["firework"]
        or event["hold_end"]
        or event["touch_hold_end"]
        or event["touch_hold_end_firework"]
    )


def _fill_gaps(events: list[dict[str, Any]]) -> None:
    """同类型音效到下一次的间隔（站内截断用，L7500-7530）。"""
    gaps = {
        "break_judge": "break_gap",
        "slide": "slide_gap",
        "ex_judge": "ex_gap",
        "firework": "firework_gap",
        "break_slide": "break_slide_gap",
        "break_slide_cheer": "break_slide_cheer_gap",
        "touch_hold": "touch_hold_gap",
    }
    next_time = dict.fromkeys(gaps.values(), math.inf)
    for event in reversed(events):
        for flag, gap_name in gaps.items():
            event[gap_name] = next_time[gap_name] - event["time_ms"]
        if event["break_judge"]:
            next_time["break_gap"] = event["time_ms"]
        if event["slide"]:
            next_time["slide_gap"] = event["time_ms"]
        if event["ex_judge"]:
            next_time["ex_gap"] = event["time_ms"]
        if event["firework"] or event["touch_hold_end_firework"]:
            next_time["firework_gap"] = event["time_ms"]
        if event["break_slide"]:
            next_time["break_slide_gap"] = event["time_ms"]
        if event["break_slide_cheer"]:
            next_time["break_slide_cheer_gap"] = event["time_ms"]
        if event["touch_hold"]:
            next_time["touch_hold_gap"] = event["time_ms"]


def _add_segment(
    mix: np.ndarray,
    sample: np.ndarray,
    start_ms: float,
    gain: float,
    *,
    max_ms: float | None = None,
    release_ms: float = _RELEASE_MS,
) -> None:
    """把采样叠加到音轨（max_ms 非 None 时截断，末尾做 release_ms 淡出）。"""
    rate = MIX_RATE
    start = round(start_ms / 1000.0 * rate)
    if start < 0 or start >= mix.size:
        return
    crop = sample
    length = crop.size
    if max_ms is None or not math.isfinite(max_ms):
        max_ms = None
    else:
        length = min(length, int(max_ms / 1000.0 * rate))
    length = min(length, mix.size - start)
    if length <= 0:
        return
    seg = crop[:length].astype(np.float32, copy=True)
    if max_ms is not None and length < crop.size:
        release = min(length, int(release_ms / 1000.0 * rate))
        if release > 0:
            seg[-release:] *= np.linspace(1.0, 0.0, release, dtype=np.float32)
    mix[start : start + length] += seg * gain


def _add_touch_hold(
    mix: np.ndarray,
    sample: np.ndarray,
    start_ms: float,
    duration_ms: float,
    gain: float,
) -> None:
    """touch-hold 长按音：截取循环段平铺填满按住时长（两端 8ms 淡入淡出）。"""
    rate = MIX_RATE
    start = round(start_ms / 1000.0 * rate)
    length = round(duration_ms / 1000.0 * rate)
    if start < 0 or length <= 0:
        return
    loop_start = 58.0  # 站内 touchHoldLoopStartSec = (50+8)/1000s
    loop = sample[int(loop_start / 1000.0 * rate) :]
    if loop.size == 0:
        return
    reps = math.ceil(length / loop.size)
    seg = np.tile(loop, reps)[:length].astype(np.float32, copy=True)
    fade = min(length, int(_RELEASE_MS / 1000.0 * rate))
    if fade > 0:
        seg[:fade] *= np.linspace(0.0, 1.0, fade, dtype=np.float32)
        seg[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)
    end = min(mix.size, start + length)
    mix[start:end] += seg[: end - start] * gain


def build_effect_track(
    chart: SiteChart,
    sounds: dict[str, bytes] | None,
    *,
    offset_ms: float = -50.0,
) -> tuple[bytes, float] | None:
    """合成打击音效音轨。

    返回 ``(wav 字节, 幅度还原系数)``；没有可合成内容时返回 None。系数用于
    混音阶段补偿「为避免 16bit 截断做的整体缩放」（ffmpeg ``volume=``）。
    ``offset_ms`` 对应站内 timingOffsetMs（负数=提前触发）。
    """
    buffers: dict[str, np.ndarray] = {}
    if sounds:
        for name in SOUND_NAMES:
            data = sounds.get(name)
            if data:
                try:
                    buffers[name] = _decode_wav(data)
                except (ValueError, wave.Error):
                    continue

    events = _collect_events(chart) if buffers else []
    if events:
        _fill_gaps(events)
    last = max((e["time_ms"] for e in events), default=0.0)
    total_ms = last + _TAIL_S * 1000.0
    mix = np.zeros(int(total_ms / 1000.0 * MIX_RATE) + 1, dtype=np.float32)

    answer = buffers.get("answer")
    tap = buffers.get("tap")
    touch = buffers.get("touch")
    for index, event in enumerate(events):
        # 站内：音符时刻 + timingOffsetMs（默认 -50ms 提前），不得早于 0。
        t = max(0.0, event["time_ms"] + offset_ms)
        if answer is not None and _should_play_answer(event):
            # 站内 answerBuffer 在「下一事件 < 40ms」时只播 max(3·gap, 30)ms
            # （playBufferAt 再叠加 $=50ms），稀疏处播完整 0.4s。
            next_gap = 1e9
            if index + 1 < len(events):
                next_gap = events[index + 1]["time_ms"] - event["time_ms"]
            if next_gap < 40.0:
                _add_segment(
                    mix,
                    answer,
                    t,
                    _ANSWER_GAIN,
                    max_ms=50.0 + max(3.0 * next_gap, 30.0),
                    release_ms=0.0,  # 站内 answerBuffer 的 releaseMs=0（硬切）
                )
            else:
                _add_segment(mix, answer, t, _ANSWER_GAIN)
        if (event["tap_judge"] or event["hold_end"]) and tap is not None:
            _add_segment(mix, tap, t, _JUDGE_GAIN)
        if event["ex_judge"] and "ex" in buffers:
            _add_segment(
                mix,
                buffers["ex"],
                t,
                _JUDGE_GAIN,
                max_ms=event["ex_gap"] + 50.0,
            )
        if event["break_judge"]:
            for name in ("break", "cheer"):
                if name in buffers:
                    _add_segment(
                        mix,
                        buffers[name],
                        t,
                        _JUDGE_GAIN,
                        max_ms=event["break_gap"] + 50.0,
                    )
        if (event["touch"] or event["touch_hold_end"]) and touch is not None:
            _add_segment(mix, touch, t, _JUDGE_GAIN)
        if (
            event["firework"] or event["touch_hold_end_firework"]
        ) and "firework" in buffers:
            _add_segment(
                mix,
                buffers["firework"],
                t,
                _JUDGE_GAIN,
                max_ms=event["firework_gap"] + 50.0,
            )
        if event["touch_hold"] and "touch_hold" in buffers:
            _add_touch_hold(
                mix,
                buffers["touch_hold"],
                t,
                event["touch_hold_ms"],
                _JUDGE_GAIN,
            )
        if event["slide"] and "slide" in buffers:
            _add_segment(
                mix,
                buffers["slide"],
                t,
                _JUDGE_GAIN,
                max_ms=event["slide_gap"] + 50.0,
            )
        if event["break_slide"] and "break_slide" in buffers:
            _add_segment(
                mix,
                buffers["break_slide"],
                t,
                _JUDGE_GAIN,
                max_ms=event["break_slide_gap"] + 50.0,
            )
        if event["break_slide_cheer"] and "break_slide_cheer" in buffers:
            _add_segment(
                mix,
                buffers["break_slide_cheer"],
                t,
                _JUDGE_GAIN,
                max_ms=event["break_slide_cheer_gap"] + 50.0,
            )

    peak = float(np.abs(mix).max()) if mix.size else 0.0
    if peak <= 1e-6:
        return None
    scale = 1.0
    if peak > 0.99:
        scale = peak / 0.99
        mix /= scale
    return _encode_wav(mix), scale
