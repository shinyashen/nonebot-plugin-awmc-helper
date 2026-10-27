"""core/render/chart_notes 站内解析器测试（对拍基线：11355 MASTER）。

断言取自 2026-10-26 站内 HUD 实测：连击总数 772、BREAK 11、无保护 11，
以及 note 时刻表整体比 simai 原始谱面 +4 拍（1 小节 lead-in）。
"""

from pathlib import Path

import pytest

SNAPSHOTS = Path(__file__).parent / "data" / "snapshots" / "simai"


@pytest.fixture
def notes():
    from nonebot_plugin_awmc_helper.core.render.chart_notes import parse_site_chart

    text = (SNAPSHOTS / "11355.txt").read_text()
    return parse_site_chart(text, difficulty=5)[5]


def test_structure(notes):
    from collections import Counter

    counts = Counter(note.type for note in notes.notes)
    assert counts["slide"] == 96
    assert counts["tap"] + counts["simultaneous"] + counts["break"] == 466
    assert counts["hold-start"] + counts["hold-start-simultaneous"] == 84
    assert counts["touch"] == 30
    assert notes.bpm == 147.0


def test_completion_tables(notes):
    from nonebot_plugin_awmc_helper.core.render.chart_notes import (
        note_completion_times,
        break_completion_times,
        break_noex_completion_times,
    )

    assert len(note_completion_times(notes.notes)) == 772
    assert len(break_completion_times(notes.notes)) == 11
    assert len(break_noex_completion_times(notes.notes)) == 11  # 非 EX break


def test_lead_in_shift(notes):
    """所有 note +4 拍；首个 note 在 beat 8（原谱 beat 4）——站内 HUD 实证。"""
    first = min(notes.notes, key=lambda n: n.timing)
    assert first.timing == pytest.approx(8.0)
    # lead-in = 240000/bpm 毫秒：timingMs 与 timeline 一致
    assert first.timing_ms == pytest.approx(notes.timeline.ms_from_beat(8.0), abs=1e-6)
    assert notes.timeline.ms_from_beat(4.0) == pytest.approx(240000.0 / 147.0)


def test_hud_beat_roundtrip(notes):
    timeline = notes.timeline
    for beat in (0.0, 4.0, 8.03, 154.04, 301.12):
        assert timeline.beat_from_ms(timeline.ms_from_beat(beat)) == pytest.approx(
            beat, abs=1e-6
        )


def test_break_noex_counts_slide_body_judgements():
    """无保护统计全部非 EX break 判定（含结尾巴 b 的 slide 本体）。

    站内 JS 的 d 只算头、漏 slide 本体；本实现按完整语义计入
    （12025：无保护 119、EX 27、总数 146 自洽）。
    """
    from nonebot_plugin_awmc_helper.core.render.chart_notes import (
        parse_site_chart,
        break_completion_times,
        break_noex_completion_times,
    )

    text = "&bpm=120\n&inote_2=\n(120){1},1-3b[4:1],\n(120){2},1b-3[4:1],\n"
    chart = parse_site_chart(text, difficulty=2)[2]
    assert len(break_completion_times(chart.notes)) == 2
    # 第一张 slide 本体是 break、第二张 star 头是 break → 两条都算无保护
    assert len(break_noex_completion_times(chart.notes)) == 2
