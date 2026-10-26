"""谱面预览音频合成：打击音效 + 开篇空小节节拍器。

对齐站内播放器的打击音效实现（``chunk-BS312iYd.js`` 的 AnswerSound 管理器，
L7430-8000）：按 note 类型把 11 个 wav 采样混成一条音轨（answer 层 / judge
层），再与整曲音乐在 mux 阶段混合。站内音量默认 music=0.8、sound(answer)=0.6、
judge=1.0，因此相对音乐的系数是 answer 0.75、judge 1.25（与站内口径一致）。

节拍器：舞萌谱面开头的空小节是节拍器报拍（站内播放器只放歌不带）。这里在
第一个判定点之前按 BPM 每拍合成短促 click，空小节的小节首重音。
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
) -> None:
    """把采样叠加到音轨（max_ms 非 None 时截断并做 8ms 释放淡出）。"""
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
        release = min(length, int(_RELEASE_MS / 1000.0 * rate))
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
    loop_start = 50.0  # 站内 touchHoldLoopStartSec ≈ (50+8)/1000
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


def _click(accent: bool) -> np.ndarray:
    """节拍器 click：短促正弦 + 指数衰减（小节首重音更高更响）。"""
    rate = MIX_RATE
    freq = 1568.0 if accent else 1046.5
    length = int(0.045 * rate)
    t = np.arange(length, dtype=np.float32) / rate
    env = np.exp(-t / 0.010).astype(np.float32)
    tone = np.sin(2 * math.pi * freq * t).astype(np.float32)
    return tone * env * (0.30 if accent else 0.19)


def _add_metronome(mix: np.ndarray, first_ms: float, beat_ms: float) -> None:
    """第一个判定点之前的空小节按拍合成节拍器（小节首重音）。"""
    click = _click(False)
    accent = _click(True)
    measure_ms = beat_ms * 4.0
    t = 0.0
    while t < first_ms - 1e-6:
        pos = round(t / 1000.0 * MIX_RATE)
        if pos < 0 or pos >= mix.size:
            pos = 0
        sample = accent if abs(t % measure_ms) < 1e-6 else click
        end = min(mix.size, pos + sample.size)
        mix[pos:end] += sample[: end - pos]
        t += beat_ms


def build_effect_track(
    chart: SiteChart,
    sounds: dict[str, bytes] | None,
    *,
    metronome: bool = True,
) -> tuple[bytes, float] | None:
    """合成打击音效 + 节拍器音轨。

    返回 ``(wav 字节, 幅度还原系数)``；没有可合成内容时返回 None。系数用于
    混音阶段补偿「为避免 16bit 截断做的整体缩放」（ffmpeg ``volume=``）。
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

    lead_ms = 240000.0 / chart.bpm
    # 节拍器一直拍到第一个判定点：开头可能不止一个空小节（11355 是 2 个）。
    note_times = [n.timing_ms for n in chart.notes]
    first_ms = min(note_times) if note_times else lead_ms
    beat_ms = 60000.0 / chart.bpm
    events = _collect_events(chart) if buffers else []
    if events:
        _fill_gaps(events)
    last = max((e["time_ms"] for e in events), default=0.0)
    total_ms = max(last + _TAIL_S * 1000.0, first_ms + 1000.0)
    if metronome:
        total_ms = max(total_ms, first_ms + 1000.0)
    mix = np.zeros(int(total_ms / 1000.0 * MIX_RATE) + 1, dtype=np.float32)

    answer = buffers.get("answer")
    tap = buffers.get("tap")
    touch = buffers.get("touch")
    for event in events:
        t = event["time_ms"]
        if event["base"] and answer is not None:
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

    if metronome:
        _add_metronome(mix, first_ms, beat_ms)

    peak = float(np.abs(mix).max()) if mix.size else 0.0
    if peak <= 1e-6:
        return None
    scale = 1.0
    if peak > 0.99:
        scale = peak / 0.99
        mix /= scale
    return _encode_wav(mix), scale
