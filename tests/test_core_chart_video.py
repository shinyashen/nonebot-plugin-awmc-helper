"""core/render/chart_video 渲染测试（PIL 移植版）。

插件模块按项目测试惯例在用例内 import；渲染冒烟需 ffmpeg（缺失跳过）。
"""

import math
import shutil
from pathlib import Path

import pytest

SNAPSHOTS = Path(__file__).parent / "data" / "snapshots" / "simai"


@pytest.fixture
def cv():
    from nonebot_plugin_awmc_helper.core.render import chart_video as mod

    return mod


@pytest.fixture
def simai():
    from nonebot_plugin_awmc_helper.core.simai import parse_simai

    return parse_simai


def test_button_angles(cv):
    """按钮角公式：-π/2+π/8+(k-1)π/4，pos1 在右上。"""
    a1 = cv._button_angle(1)
    assert a1 == pytest.approx(-math.pi / 2 + math.pi / 8)
    # pos4 = 下偏右（67.5°）；pos5 = 下偏左（112.5°）——8 键位无正下键
    assert cv._button_angle(4) == pytest.approx(math.pi * 3 / 8)
    assert cv._button_angle(5) == pytest.approx(math.pi * 5 / 8)


def test_note_position_travel(cv):
    """运动：前半窗 0.25R 驻留淡入 → 后半窗到 R → tap 判定后消失。"""
    note = cv._Note("tap", 6, 6734.7)
    half = cv._HALF_APPROACH
    # 前半窗起点（接近窗开端）：驻留 0.25R、alpha=0
    p = cv._note_position(note, 6734.7 - cv._APPROACH_MS + 1, 300, 300, 270)
    assert math.hypot(p.x - 300, p.y - 300) == pytest.approx(270 * 0.25, abs=2)
    assert p.alpha == pytest.approx(0.0, abs=0.01)
    # 判定时刻：正好在 R
    p = cv._note_position(note, 6734.7, 300, 300, 270)
    assert math.hypot(p.x - 300, p.y - 300) == pytest.approx(270, abs=1)
    # 判定后：tap 不可见
    p = cv._note_position(note, 6734.7 + 50, 300, 300, 270)
    assert not p.visible


def test_hold_flicker_range(cv):
    assert cv._hold_flicker(-1) == 1.0
    v = cv._hold_flicker(123.0)
    assert 0.82 <= v <= 1.0


def test_shape_map_circle(cv):
    """ce()：同位 > 不镜像（flat 起点），跨距映射 circle1-8。"""
    m = cv._SlideRenderer._shape_map(">", 1, 1, None)
    assert m == ("circle1", False)
    m = cv._SlideRenderer._shape_map("-", 1, 4, None)
    assert m == ("line4", False)  # dist=((4-1)%8+8)%8+1=4


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="需要 ffmpeg")
def test_render_smoke(cv, simai, tmp_path: Path):
    chart = simai((SNAPSHOTS / "8.txt").read_text()).difficulties[5]
    out = cv.render_chart_video(
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
def test_render_with_audio(cv, simai, tmp_path: Path):
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
    out = cv.render_chart_video(
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
