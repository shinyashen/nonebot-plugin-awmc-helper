"""片段范围解析与按小节对齐测试（``core/render/chart_range.py``）。"""

from pathlib import Path

import pytest

SNAPSHOTS = Path(__file__).parent / "data" / "snapshots" / "simai"


@pytest.fixture
def cr():
    from nonebot_plugin_awmc_helper.core.render import chart_range as mod

    return mod


@pytest.fixture(scope="module")
def chart():
    from nonebot_plugin_awmc_helper.core.render.chart_notes import parse_site_chart

    return parse_site_chart((SNAPSHOTS / "11355.txt").read_text(), difficulty=5)[5]


def test_parse_range_formats(cr):
    assert cr.parse_range_token("0:35-1:20") == (35.0, 80.0)
    assert cr.parse_range_token("35-80") == (35.0, 80.0)
    assert cr.parse_range_token("-1:20") == (None, 80.0)
    assert cr.parse_range_token("0:35-") == (35.0, None)
    assert cr.parse_range_token("1:05.5-2:00") is None  # 秒不支持小数冒号混写
    assert cr.parse_range_token("1:20") is None
    assert cr.parse_range_token("-") is None
    assert cr.parse_range_token("abc") is None


def test_measure_boundaries(cr, chart):
    bounds = cr.measure_times_ms(chart)
    assert bounds[0] == 0.0
    assert len(bounds) > 10
    assert bounds == sorted(bounds)
    # BPM147 一小节 4*60000/147 ≈ 1632.65ms
    assert abs((bounds[1] - bounds[0]) - 1632.65) < 1.0


def test_snap_mid_measure(cr, chart):
    """35-80 秒 → 起点向上、终点向下对齐到小节线。"""
    clip = cr.snap_range(chart, 35.0, 80.0)
    bounds = cr.measure_times_ms(chart)
    assert clip.start_ms in bounds
    assert clip.end_ms in bounds
    assert clip.start_ms >= 35000.0
    assert clip.end_ms <= 80000.0
    assert clip.end_ms - clip.start_ms >= bounds[1] - bounds[0]


def test_snap_exact_measure_keeps_boundary(cr, chart):
    bounds = cr.measure_times_ms(chart)
    clip = cr.snap_range(chart, bounds[3] / 1000.0, bounds[6] / 1000.0)
    assert clip.start_ms == pytest.approx(bounds[3])
    assert clip.end_ms == pytest.approx(bounds[6])


def test_snap_open_sides(cr, chart):
    """-1:20 表示从头；0:35- 表示到结尾。"""
    clip = cr.snap_range(chart, None, 80.0)
    assert clip.start_ms == 0.0
    bounds = cr.measure_times_ms(chart)
    assert clip.end_ms <= 80000.0
    clip2 = cr.snap_range(chart, 35.0, None)
    assert clip2.start_ms >= 35000.0
    assert clip2.end_ms == pytest.approx(bounds[-1])


def test_snap_clamps_bounds(cr, chart):
    total = chart.timeline.ms_from_beat(float(chart.total_beats))
    clip = cr.snap_range(chart, -5.0, total / 1000.0 + 100.0)
    assert clip.start_ms == 0.0
    assert clip.end_ms <= total + 1e-6


def test_snap_errors(cr, chart):
    with pytest.raises(ValueError, match="开始 < 结束"):
        cr.snap_range(chart, 60.0, 40.0)
    with pytest.raises(ValueError, match="没有完整小节"):
        cr.snap_range(chart, 0.2, 0.5)  # 不足一个小节
    with pytest.raises(ValueError, match="超出歌曲长度"):
        cr.snap_range(chart, 9999.0, 9999.0 + 10.0)  # 超出曲长


def test_format_time(cr):
    assert cr.format_time(0) == "0:00"
    assert cr.format_time(35000) == "0:35"
    assert cr.format_time(80000) == "1:20"
