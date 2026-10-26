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
