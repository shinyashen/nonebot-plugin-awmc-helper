"""linfo 定数历史卡渲染测试（2026-10-16 新增）。

样例复用 mocks 真实快照构造（199 チルノ DX MASTER）；变化点序列取自
maimaiinfo 真实形态（旧框末值折 DX 基线 + DX 世代调整点）。断言以渲染
可执行 + PNG 头为主，视觉细节靠本地人工预览（与牌子/分数线渲染同口径）。
"""

import pytest
from mocks import sample_songs, requires_assets

# ネコ日和。紫真实形态的同构样例：8 节点（旧框末值折 DX 基线 + 7 次调整）
POINTS_MULTI_ROW = [
    (20000, 11.7),
    (20500, 12.2),
    (21000, 12.8),
    (21500, 13.0),
    (22500, 13.3),
    (23000, 13.5),
    (24000, 13.6),
    (24500, 13.5),
]
# 单行上限形态：5 节点
POINTS_SINGLE_ROW = [
    (20000, 10.6),
    (20500, 10.8),
    (21000, 11.1),
    (22500, 11.8),
    (25000, 12.6),
]


@pytest.fixture
def chart_199_dx_master():
    from maimai_py import SongType, LevelIndex

    song = next(s for s in sample_songs() if s.id == 199)
    diff = next(
        d
        for d in song.get_difficulties(SongType.DX)
        if d.level_index == LevelIndex.MASTER
    )
    return song, diff


def _render(song, diff, points, *, jp=False, theme="prism_plus"):
    from nonebot_plugin_awmc_helper.core.render.linfo import linfo_card

    return linfo_card(song, diff, points, cn_current=25500, jp=jp, theme=theme)


@requires_assets
def test_linfo_card_multi_row(chart_199_dx_master):
    """8 节点（全库最坏形态）：两行排布渲染可执行。"""
    png = _render(*chart_199_dx_master, POINTS_MULTI_ROW)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


@requires_assets
def test_linfo_card_single_row(chart_199_dx_master):
    """5 节点单行上限形态渲染可执行。"""
    png = _render(*chart_199_dx_master, POINTS_SINGLE_ROW)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


@requires_assets
def test_linfo_card_jp_view(chart_199_dx_master):
    """日服视图口径：现行 = 最新值，无「日服最新」标记（渲染分支可执行）。"""
    png = _render(*chart_199_dx_master, POINTS_MULTI_ROW, jp=True)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


@requires_assets
def test_linfo_card_cn_divergence(chart_199_dx_master):
    """国服视图 + 日服先行（末节点 > 国服当前版本）：双标记分支可执行。"""
    points = [
        (20000, 11.5),
        (20500, 12.0),
        (21000, 12.3),
        (22500, 12.8),
        (23000, 13.0),
        (25000, 13.1),
        (25500, 13.3),
    ]
    png = _render(*chart_199_dx_master, points)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


@requires_assets
def test_linfo_card_circle_theme(chart_199_dx_master):
    """circle 粉底主题（自适应高度底图）渲染可执行。"""
    png = _render(*chart_199_dx_master, POINTS_SINGLE_ROW, theme="circle")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_linfo_card_rejects_single_point(chart_199_dx_master):
    """单点历史（DX 世代未调整）应由调用方走纯文本分支，渲染层拒收。"""
    with pytest.raises(AssertionError):
        _render(*chart_199_dx_master, [(20000, 12.5)])
