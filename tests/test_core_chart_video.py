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
def site():
    from nonebot_plugin_awmc_helper.core.render.chart_notes import parse_site_chart

    return parse_site_chart


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
def test_render_smoke(cv, site, tmp_path: Path):
    chart = site((SNAPSHOTS / "8.txt").read_text(), difficulty=5)[5]
    out = cv.render_chart_video(
        chart,
        audio=None,
        out_path=tmp_path / "preview.mp4",
        limit_seconds=2.5,
    )
    assert out.exists()
    assert out.stat().st_size > 10_000


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="需要 ffmpeg")
def test_render_parallel_workers(cv, site, tmp_path: Path):
    """多进程分帧渲染路径（fork 不可用时内部自动退回串行，仍应产出视频）。"""
    chart = site((SNAPSHOTS / "8.txt").read_text(), difficulty=5)[5]
    out = cv.render_chart_video(
        chart,
        audio=None,
        out_path=tmp_path / "parallel.mp4",
        limit_seconds=2.0,
        workers=2,
    )
    assert out.exists()
    assert out.stat().st_size > 10_000


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="需要 ffmpeg")
def test_render_quality_knob(cv, site, tmp_path: Path):
    """低画质档（1× 超采样 + 24fps + 单进程）可正常产出视频。"""
    chart = site((SNAPSHOTS / "8.txt").read_text(), difficulty=3)[3]
    out = cv.render_chart_video(
        chart,
        audio=None,
        out_path=tmp_path / "low.mp4",
        limit_seconds=1.5,
        supersample=1.0,
        fps=24,
        workers=1,
    )
    assert out.exists()
    assert out.stat().st_size > 5_000


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="需要 ffmpeg")
def test_render_with_audio(cv, site, tmp_path: Path):
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
    chart = site((SNAPSHOTS / "8.txt").read_text(), difficulty=2)[2]
    out = cv.render_chart_video(
        chart,
        audio=audio.read_bytes(),
        out_path=tmp_path / "mux.mp4",
        limit_seconds=2.5,
    )
    assert out.exists()
    assert out.stat().st_size > 10_000


def test_normalize_hi_speed(cv):
    """谱面流速校验：3–9.75 步长 0.25，非法回退默认 7.0。"""
    for value in (7, 7.25, 3.0, 9.75):
        assert cv.normalize_hi_speed(value) == float(value)
    for bad in (0, 0.25, 9.3, 10, -3, "x", None):
        assert cv.normalize_hi_speed(bad) == 7.0


def test_resolve_ffmpeg_missing(cv, monkeypatch):
    """PATH 无 ffmpeg 且未配置 → 面向用户的 ValueError（含配置项提示）。"""
    import shutil

    monkeypatch.delenv("AWMC_FFMPEG", raising=False)
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(cv.FFmpegUnavailableError) as exc_info:
        cv.resolve_ffmpeg(None)
    message = str(exc_info.value)
    assert "aws" not in message  # 不泄漏无关内容
    assert "awmc_chart_preview_ffmpeg" in message
    assert "ffmpeg" in message


def test_resolve_ffmpeg_explicit_path(cv, tmp_path):
    """显式配置可执行文件路径 → 直接使用。"""
    fake = tmp_path / "ffmpeg"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    assert cv.resolve_ffmpeg(str(fake)) == str(fake)


def test_check_ffmpeg_reports_usable_and_missing(cv, monkeypatch):
    """探测：完整构建 usable；缺 libx264/loudnorm 时报 missing 条目。"""
    from types import SimpleNamespace

    monkeypatch.setattr(cv, "resolve_ffmpeg", lambda _ffmpeg=None: "/usr/bin/ffmpeg")

    def fake_run(cmd, **kwargs):
        if "-version" in cmd:
            return SimpleNamespace(
                returncode=0, stdout="ffmpeg version 6.1.1 Copyright\n", stderr=""
            )
        if "-encoders" in cmd:
            return SimpleNamespace(
                returncode=0, stdout=" V....D libx264  H.264\n A....D aac  AAC\n"
            )
        return SimpleNamespace(
            returncode=0, stdout=" T.. amix  Audio mix\n .. loudnorm  EBU R128\n"
        )

    monkeypatch.setattr("subprocess.run", fake_run)
    status = cv.check_ffmpeg("/usr/bin/ffmpeg")
    assert status.usable
    assert "6.1.1" in status.version

    def fake_run_missing(cmd, **kwargs):
        if "-version" in cmd:
            return SimpleNamespace(
                returncode=0, stdout="ffmpeg version 5.0\n", stderr=""
            )
        if "-encoders" in cmd:
            return SimpleNamespace(returncode=0, stdout=" A....D aac  AAC\n")
        return SimpleNamespace(returncode=0, stdout=" T.. amix  Audio mix\n")

    monkeypatch.setattr("subprocess.run", fake_run_missing)
    status = cv.check_ffmpeg("/usr/bin/ffmpeg")
    assert not status.usable
    assert any("libx264" in item for item in status.missing)
    assert any("loudnorm" in item for item in status.missing)


def test_speed_mapping_majdata(cv):
    """流速按 Majdata 标定：7.0 → 音符 630.6ms / touch 500.5ms（见常量注释）。"""
    import pytest

    try:
        cv._apply_hi_speed(7.0)
        assert cv._APPROACH_MS == pytest.approx(630.56, abs=0.05)
        assert cv._HALF_APPROACH == pytest.approx(315.28, abs=0.05)
        assert cv._TOUCH_APPROACH_MS == pytest.approx(500.48, abs=0.05)
        assert cv._SPEED_SETTING == 7.0
        cv._apply_hi_speed(6.0)
        assert cv._APPROACH_MS == pytest.approx(719.28, abs=0.05)
        assert cv._TOUCH_APPROACH_MS == pytest.approx(579.85, abs=0.05)
    finally:
        cv._apply_hi_speed(cv._DEFAULT_HI_SPEED)


def test_parse_site_chart_buddy_suffix(site):
    """&inote_102（buddy 2P）也要能解析：历史上正则只认单个数字。"""
    text = (
        "&bpm=120\n"
        "&inote_2=\n(120){1},1,2,\n(120){2},3,4,\n"
        "&inote_102=\n(120){1},5,6,\n(120){2},7,8,\n"
    )
    charts = site(text)
    assert set(charts) == {2, 102}
    assert len(charts[2].notes) == 4
    assert len(charts[102].notes) == 4


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="需要 ffmpeg")
def test_render_buddy_two_panels(cv, site, tmp_path: Path):
    """buddy 宴谱：1200x600，左侧 1P、右侧 2P（两张谱面独立渲染）。"""
    import subprocess

    text = (
        "&bpm=120\n"
        "&inote_2=\n(120){1},1,2,\n(120){2},3,4,\n"
        "&inote_102=\n(120){1},5,6,\n(120){2},7,8,\n"
    )
    chart_1p = site(text, difficulty=2)[2]
    chart_2p = site(text, difficulty=102)[102]
    out = cv.render_chart_video(
        chart_1p,
        audio=None,
        out_path=tmp_path / "buddy.mp4",
        limit_seconds=1.0,
        supersample=1.0,
        workers=1,
        buddy_chart=chart_2p,
    )
    assert out.exists()
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0",
            str(out),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert probe.stdout.strip() == "1200,600"
