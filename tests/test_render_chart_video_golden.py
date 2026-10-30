"""谱面预览渲染黄金帧基线（chart_video 结构拆分的零行为变更验证）。

5 张真实快照谱面 × 每谱 8 个固定时点，用 ``_ChartRenderer.render_frame``
在默认超采样（2.0）下出帧、PNG 编码后取 SHA256，与
``tests/data/golden/chart_video_frames.json`` 清单逐帧比对。

清单由 ``local/scratch/golden_frames.py --update`` 重新生成（同时把 PNG
落到 local/scratch/golden/ 供人工查看）。重构期间任何一帧哈希变化都必须
停下查明原因——渲染代码只许搬移、不许改行为；若确属浮点锯齿级差异，
需改用逐像素容差断言并在清单 note 里说明。
"""

import io
import json
import hashlib
from pathlib import Path

import pytest

GOLDEN_JSON = Path(__file__).parent / "data" / "golden" / "chart_video_frames.json"

# 谱面集：任务指定的三张（11355 MASTER / 8 MASTER / 100199 宴谱）+ 两张
# 补特效覆盖（10665 MASTER：touch hold 进度环+花火；111355 DX：星形 tap+花火）。
_CHART_SPECS = (
    ("11355", 5),
    ("8", 5),
    ("100199", 2),
    ("10665", 5),
    ("111355", 2),
)
_FRAMES_PER_CHART = 8


def _snapshot_path(chart_id: str) -> Path:
    return Path(__file__).parent / "data" / "snapshots" / "simai" / f"{chart_id}.txt"


def _parse_chart(chart_id: str, suffix: int):
    from nonebot_plugin_awmc_helper.core.render.chart_notes import parse_site_chart

    text = _snapshot_path(chart_id).read_text()
    return parse_site_chart(text, difficulty=suffix)[suffix]


def _offset(t: float | None, ms: float) -> float | None:
    return None if t is None else t + ms


def golden_times(chart) -> list[float]:
    """从真实 note 数据确定性推导每谱时点（优先级序取前 8、间隔 <100ms 去重）。

    覆盖目标：tap 判定界 / 接近窗 / hold 进行中 / slide 移动中 / wifi 段 /
    touch 命中特效 / touch hold 进度环 / 花火两阶段 / break 打击特效 / 结尾。
    """
    taps = [n for n in chart.notes if n.type in ("tap", "break", "simultaneous")]
    ex_taps = [n for n in taps if n.is_ex]
    holds = [n for n in chart.notes if n.type == "hold-start"]
    slides = [n for n in chart.notes if n.type == "slide"]
    touches = [n for n in chart.notes if n.type == "touch"]
    tholds = [n for n in chart.notes if n.type == "touch-hold-start"]
    fires = [n for n in chart.notes if n.has_firework]
    breaks = [n for n in chart.notes if n.type == "break"]

    def middle(values: list[float]) -> float | None:
        if not values:
            return None
        return sorted(values)[len(values) // 2]

    def slide_mid(n) -> float:
        part = n.parts[0]
        return n.timing_ms + part.delay_ms + part.duration_ms / 2

    first_tap = min((n.timing_ms for n in taps), default=0.0)
    last = max((n.timing_ms for n in chart.notes), default=0.0)
    wifi = [
        n
        for n in slides
        if n.parts and n.parts[0].segments and n.parts[0].segments[0].type == "w"
    ]

    candidates = [
        first_tap,  # tap 判定界（判定后立即消失）
        first_tap - 300.0,  # 接近窗中段
        middle([n.timing_ms + n.duration_ms / 2 for n in holds]),  # hold 进行中
        middle([slide_mid(n) for n in slides]),  # slide 移动中
        middle([slide_mid(n) for n in wifi]),  # wifi 段
        _offset(middle([n.timing_ms for n in touches]), 100.0),  # touch 命中特效
        middle([n.timing_ms + n.duration_ms / 2 for n in tholds]),  # 进度环
        _offset(middle([n.timing_ms for n in ex_taps]), -300.0),  # EX 环接近期
        middle([n.timing_ms for n in ex_taps]),  # EX 环判定界
        _offset(middle([n.timing_ms for n in fires]), 300.0),  # 花火前段（矢量楔）
        _offset(middle([n.timing_ms for n in fires]), 900.0),  # 花火后段（切洞）
        _offset(middle([n.timing_ms for n in breaks]), 200.0),  # break 打击特效
        last + 500.0,  # 结尾状态
    ]
    times: list[float] = []
    for t in candidates:
        if t is None or t < 0:
            continue
        if any(abs(t - kept) < 100.0 for kept in times):
            continue
        times.append(t)
        if len(times) >= _FRAMES_PER_CHART:
            break
    return times


def render_golden_frames(chart, times: list[float]) -> list[bytes]:
    """逐时点渲染 PNG（一个谱面共用一个渲染器，帧序=时点序）。"""
    from nonebot_plugin_awmc_helper.core.render.chart_video import _ChartRenderer

    renderer = _ChartRenderer(chart)
    out: list[bytes] = []
    for now_ms in times:
        frame = renderer.render_frame(now_ms, 30)
        buf = io.BytesIO()
        frame.save(buf, format="PNG")
        out.append(buf.getvalue())
    return out


def test_chart_video_golden_frames():
    """逐帧 SHA256 比对黄金帧清单；任何变化都是渲染行为变了。

    基线在「有 static 素材包字体」的开发机生成；CI 没有 static/（不入库），
    字体回落 load_default 会整体改变帧内容——素材缺失时跳过（不是基线
    失效，是环境不可比）。
    """
    from nonebot_plugin_awmc_helper.config import plugin_config

    font_path = (
        Path(plugin_config.awmc_static_path) / "font" / "ResourceHanRoundedCN-Bold.ttf"
    )
    if not font_path.exists():
        pytest.skip(f"static 素材包缺失（{font_path}），黄金帧仅在素材齐备的环境比对")
    if not GOLDEN_JSON.exists():
        pytest.fail(
            "黄金帧清单缺失：先在仓库根目录运行 "
            "local/scratch/golden_frames.py --update 生成基线"
        )
    manifest = json.loads(GOLDEN_JSON.read_text(encoding="utf-8"))
    mismatches: list[str] = []
    for spec in manifest["charts"]:
        chart = _parse_chart(spec["id"], spec["suffix"])
        key = f"{spec['id']}#{spec['suffix']}"
        times = manifest["times"][key]
        expected_hashes = manifest["hashes"][key]
        for now_ms, png, expected in zip(
            times, render_golden_frames(chart, times), expected_hashes
        ):
            got = hashlib.sha256(png).hexdigest()
            if got != expected:
                mismatches.append(
                    f"{key}@{now_ms:g}ms 期望 {expected[:16]} 实得 {got[:16]}"
                )
    assert not mismatches, (
        "黄金帧哈希变化——渲染行为在重构中被改动，逐条排查：\n" + "\n".join(mismatches)
    )
