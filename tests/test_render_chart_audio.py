"""打击音效/节拍器音轨合成测试（``core/render/chart_audio.py``）。"""

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


def test_metronome_clicks_before_first_note(ca, chart):
    """无音效素材时也能生成节拍器；click 落在拍点。"""
    result = ca.build_effect_track(chart, None, metronome=True)
    assert result is not None
    wav, scale = result
    assert scale == 1.0
    track = ca._decode_wav(wav)
    beat_ms = 60000.0 / chart.bpm
    first_ms = min(n.timing_ms for n in chart.notes)
    for k in range(int(first_ms // beat_ms)):
        start = round(k * beat_ms / 1000.0 * ca.MIX_RATE)
        window = track[start : start + int(0.05 * ca.MIX_RATE)]
        assert float(np.abs(window).max()) > 0.05
    # 关掉节拍器后没有可合成内容
    assert ca.build_effect_track(chart, None, metronome=False) is None


def test_hit_sounds_at_note_times(ca, chart):
    """有素材时：第一个判定点处出现打击音，且音轨覆盖整曲。"""
    result = ca.build_effect_track(chart, _fake_sounds(ca), metronome=False)
    assert result is not None
    wav, _scale = result
    track = ca._decode_wav(wav)
    first = min(n.timing_ms for n in chart.notes)
    start = round(first / 1000.0 * ca.MIX_RATE)
    window = track[max(0, start - 100) : start + int(0.1 * ca.MIX_RATE)]
    assert float(np.abs(window).max()) > 0.1
    assert track.size > int(first / 1000.0 * ca.MIX_RATE)


def test_wav_roundtrip(ca):
    body = np.linspace(-0.5, 0.5, 1000, dtype=np.float32)
    back = ca._decode_wav(ca._encode_wav(body))
    assert back.shape == body.shape
    assert np.allclose(back, body, atol=1e-3)
