"""core/render/chart_video 渲染测试（ffmpeg 冒烟 + 滑键路径几何）。

插件模块按项目测试惯例在用例内 import（nonebot 于 session fixture 初始化）。
"""

import math
import shutil
import itertools
from pathlib import Path

import pytest

SNAPSHOTS = Path(__file__).parent / "data" / "snapshots" / "simai"
CX, CY, R = 360.0, 367.0, 234.0  # 720 画布布局常量（与 _Canvas 一致的量级）


@pytest.fixture
def chart_video():
    from nonebot_plugin_awmc_helper.core.render import chart_video as mod

    return mod


@pytest.fixture
def simai():
    from nonebot_plugin_awmc_helper.core.simai import parse_simai

    return parse_simai


def test_slide_path_straight_endpoints(chart_video, simai):
    """`-` 直线滑路径端点 = 起/终按钮位。"""
    chart = simai((SNAPSHOTS / "8.txt").read_text())
    slide = next(
        n
        for n in chart.difficulties[5].notes
        if (
            isinstance(n, chart_video.SimaiSlide)
            and n.shape == "-"
            and n.start_pos != n.end_pos
        )
    )
    path = chart_video._slide_path(slide, CX, CY, R)
    assert len(path) == 2
    for pos, pt in ((slide.start_pos, path[0]), (slide.end_pos, path[1])):
        compass = math.radians(337.5 + pos * 45.0)
        assert pt == pytest.approx(
            (CX + R * math.sin(compass), CY - R * math.cos(compass)), abs=0.5
        )


def test_slide_path_circle_same_position_is_full_loop(chart_video, simai):
    """同位圆滑路径应为整圈（跨度 ≈ 2π；取材 700.txt 真实谱 `1<1[4:3]`）。"""
    chart = simai((SNAPSHOTS / "700.txt").read_text())
    slide = next(
        n
        for n in chart.difficulties[5].notes
        if isinstance(n, chart_video.SimaiSlide) and n.start_pos == n.end_pos
    )
    path = chart_video._slide_path(slide, CX, CY, R)
    length = sum(math.dist(a, b) for a, b in itertools.pairwise(path))
    assert length == pytest.approx(2 * math.pi * R, rel=0.05)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="需要 ffmpeg")
def test_render_smoke(chart_video, simai, tmp_path: Path):
    """真实谱面 2.5s 渲染冒烟：产出非空 mp4。"""
    chart = simai((SNAPSHOTS / "8.txt").read_text()).difficulties[5]
    out = chart_video.render_chart_video(
        chart,
        title="True Love Song",
        level_label="12",
        suffix=5,
        audio=None,
        out_path=tmp_path / "preview.mp4",
        limit_seconds=2.5,
    )
    assert out.exists()
    assert out.stat().st_size > 10_000


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="需要 ffmpeg")
def test_render_with_audio_mux(chart_video, simai, tmp_path: Path):
    """带音频渲染：ffmpeg 合成成功（音频为生成的静音 mp3）。"""
    import subprocess

    audio = tmp_path / "silence.mp3"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=stereo",
            "-t",
            "3",
            "-b:a",
            "32k",
            str(audio),
        ],
        check=True,
    )
    chart = simai((SNAPSHOTS / "8.txt").read_text()).difficulties[2]
    out = chart_video.render_chart_video(
        chart,
        title="True Love Song",
        level_label="5",
        suffix=2,
        audio=audio.read_bytes(),
        out_path=tmp_path / "mux.mp4",
        limit_seconds=2.5,
    )
    assert out.exists()
    assert out.stat().st_size > 10_000
