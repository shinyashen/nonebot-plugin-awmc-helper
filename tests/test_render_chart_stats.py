"""谱面统计：物量/EX 口径（EX 含 SLIDE 星星头归 TAP，烟花/TOUCH 不算）。"""

from pathlib import Path

import pytest

SNAPSHOTS = Path(__file__).parent / "data" / "snapshots" / "simai"


@pytest.fixture(scope="module")
def chart_11355_master():
    from nonebot_plugin_awmc_helper.core.render.chart_notes import parse_site_chart

    charts = parse_site_chart((SNAPSHOTS / "11355.txt").read_text(), difficulty=5)
    return charts[5]


def test_chart_stats_counts_and_ex(chart_11355_master):
    """11355 MASTER：数量对齐官方谱面信息，EX 归属（SLIDE 星星头归 TAP）。"""
    from types import SimpleNamespace

    from nonebot_plugin_awmc_helper.core.render.chart_stats import (
        compute_chart_stats,
        format_note_distribution,
    )

    # 官方谱面信息：tap 551 / hold 84 / slide 96 / touch 30 / break 11
    official = SimpleNamespace(
        tap_num=551, hold_num=84, slide_num=96, touch_num=30, break_num=11
    )
    stats = compute_chart_stats(chart_11355_master, official)
    assert stats.total == 772  # 与谱面信息（HUD 物量）一致
    assert stats.counts["TAP"] == 551
    assert stats.counts["BREAK"] == 11
    assert stats.ex_total == 66
    assert stats.counts["SLIDE"] == 96
    assert stats.ex_counts["SLIDE"] == 0  # SLIDE 本体无 EX（星星头归 TAP）
    assert stats.ex_counts["TAP"] == 65  # 54 EX tap + 11 EX slide head
    assert stats.ex_counts["HOLD"] == 1
    assert stats.ex_counts["TOUCH"] == 0  # 烟花/TOUCH 不算 EX
    text = format_note_distribution(stats)
    assert "TAP 551（EX 65）" in text
    assert "SLIDE 96" in text
    assert "SLIDE 96（EX" not in text


def test_break_ex_completion_difference():
    """BREAK EX 用完成事件差额：总数 = 无保护 + EX（含 slide 本体 break）。"""
    from types import SimpleNamespace

    from nonebot_plugin_awmc_helper.core.render.chart_notes import (
        parse_site_chart,
        break_completion_times,
        break_noex_completion_times,
    )
    from nonebot_plugin_awmc_helper.core.render.chart_stats import (
        compute_chart_stats,
    )

    text = (
        "&bpm=120\n"
        "&inote_2=\n(120){1},1xb-3[4:1],\n"
        "(120){2},1b,2b,\n(120){3},4bxh[1:1],\n"
    )
    chart = parse_site_chart(text, difficulty=2)[2]
    total = len(break_completion_times(chart.notes))
    noex = len(break_noex_completion_times(chart.notes))
    # 官方 break_num 取 HUD 的完成事件总数（12025 为 146）
    diff = SimpleNamespace(
        tap_num=0, hold_num=0, slide_num=0, touch_num=0, break_num=total
    )
    stats = compute_chart_stats(chart, diff)
    assert stats.counts["BREAK"] == total
    assert stats.ex_counts["BREAK"] == 2  # EX star head + EX break hold 头
    assert stats.counts["BREAK"] - stats.ex_counts["BREAK"] == noex
    assert stats.ex_counts["TAP"] == 0  # break slide 的 EX 不重复计入 TAP
