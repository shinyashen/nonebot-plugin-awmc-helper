"""打击音效音轨合成测试（``core/render/chart_audio.py``）。"""

from pathlib import Path

import numpy as np
import pytest

SNAPSHOTS = Path(__file__).parent / "data" / "snapshots" / "simai"


@pytest.fixture
def ca():
    from nonebot_plugin_awmc_helper.core.render import chart_audio as mod

    return mod


@pytest.fixture(scope="module")
def chart():
    from nonebot_plugin_awmc_helper.core.render.chart_notes import parse_site_chart

    text = (SNAPSHOTS / "11355.txt").read_text()
    return parse_site_chart(text, difficulty=5)[5]


def _tone(ca, seconds: float = 0.05, freq: float = 880.0) -> bytes:
    rate = ca.MIX_RATE
    n = int(seconds * rate)
    t = np.arange(n, dtype=np.float32) / rate
    return ca._encode_wav((0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32))


def _fake_sounds(ca) -> dict[str, bytes]:
    return {name: _tone(ca) for name in ca.SOUND_NAMES}


def _peak_at(track: np.ndarray, ca, start_ms: float, length_ms: float = 80.0) -> float:
    start = max(0, round(start_ms / 1000.0 * ca.MIX_RATE))
    window = track[start : start + int(length_ms / 1000.0 * ca.MIX_RATE)]
    return float(np.abs(window).max()) if window.size else 0.0


def test_hit_sounds_follow_first_note(ca, chart):
    """音频层：第一个判定点处出现音效，且提前 offset（站内默认 -50ms）。"""
    result = ca.build_effect_track(chart, _fake_sounds(ca), offset_ms=-50.0)
    assert result is not None
    wav, _scale = result
    track = ca._decode_wav(wav)
    first = min(n.timing_ms for n in chart.notes)
    assert _peak_at(track, ca, first - 50.0) > 0.1
    assert track.size > int(first / 1000.0 * ca.MIX_RATE)


def test_sound_offset_shifts_attack(ca, chart):
    """offset 参数可整体平移音效（-50 与 0 相差约 50ms）。"""
    sounds = {"tap": _tone(ca)}
    first = min(n.timing_ms for n in chart.notes)
    positions = []
    for offset in (-50.0, 0.0):
        wav, _scale = ca.build_effect_track(chart, sounds, offset_ms=offset)
        track = ca._decode_wav(wav)
        lo = round((first - 120) / 1000.0 * ca.MIX_RATE)
        hi = round((first + 120) / 1000.0 * ca.MIX_RATE)
        seg = np.abs(track[lo:hi])
        attack = lo + int(np.argmax(seg > 0.05))
        positions.append(attack / ca.MIX_RATE * 1000.0)
    assert positions[0] < positions[1]
    assert 40.0 <= positions[1] - positions[0] <= 60.0


def test_hold_end_gets_answer_sound(ca, chart):
    """站内 shouldPlaySound：hold 结束也会叠 answer 层（只有 answer 采样时也响）。"""
    events = ca._collect_events(chart)
    hold_ends = [e for e in events if e["hold_end"]]
    assert hold_ends, "11355 应包含 hold-end 事件"
    wav, _scale = ca.build_effect_track(
        chart, {"answer": _tone(ca)}, offset_ms=0.0
    )
    track = ca._decode_wav(wav)
    assert _peak_at(track, ca, hold_ends[0]["time_ms"]) > 0.05


def test_no_sounds_returns_none(ca, chart):
    assert ca.build_effect_track(chart, None) is None
    assert ca.build_effect_track(chart, {}) is None


def test_wav_roundtrip(ca):
    body = np.linspace(-0.5, 0.5, 1000, dtype=np.float32)
    back = ca._decode_wav(ca._encode_wav(body))
    assert back.shape == body.shape
    assert np.allclose(back, body, atol=1e-3)
