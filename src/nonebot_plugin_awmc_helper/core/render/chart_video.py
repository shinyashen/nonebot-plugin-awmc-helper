"""谱面预览视频渲染（PIL 逐帧 + ffmpeg 合成）。

输入解析后的 :class:`~nonebot_plugin_awmc_helper.core.simai.SimaiDifficulty`
（秒时刻 note 序列）与整曲 mp3 字节，产出 h264+aac 的 mp4。画面为自上而下的
maimai 圆盘：note 从盘外沿落向判定圈（固定接近时长），滑键沿路径画连接线与
移动箭头，长条在按钮位画流动条带，touch 在传感器区闪花。

几何为社区预览器级别的近似（圆滑走同心弧、w 钩形走贝塞尔），追求「看得清、
对得上拍」，不复刻机内特效。帧数据经 rawvideo 管道喂 ffmpeg，不落中间帧文件。
"""

import math
import shutil
import subprocess
from pathlib import Path
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

from .fonts import font
from ..simai import SimaiTap, SimaiHold, SimaiSlide, SimaiTouch, SimaiDifficulty

# 配色（maimai 风格）：单点粉红 / each 黄 / break 金 / touch 青
_COLOR_SINGLE = (255, 92, 126)
_COLOR_EACH = (255, 196, 64)
_COLOR_BREAK = (255, 226, 130)
_COLOR_TOUCH = (86, 220, 235)
_BG = (10, 10, 18)
_RING = (235, 235, 245)

# 难度标签色（BASIC→Re:MASTER，对齐游戏难度色系）
_LEVEL_COLORS = {
    2: (255, 105, 86),
    3: (255, 184, 64),
    4: (176, 88, 255),
    5: (214, 60, 168),
    6: (176, 128, 255),
}
_LEVEL_NAMES = {2: "BASIC", 3: "ADVANCED", 4: "EXPERT", 5: "MASTER", 6: "Re:MASTER"}

# note 接近时长（秒）：固定值保证任何 BPM 下可读；spawn = 判定圈外一圈
_APPROACH = 0.9


def _ensure_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("未找到 ffmpeg，谱面预览渲染不可用（请安装 ffmpeg）")


def _pos_angle(pos: int) -> float:
    """按钮位 0-7 → 罗盘方位角弧度（337.5°+45°k，北=上；罗盘角增大 = 屏幕顺时针）。"""
    return math.radians(337.5 + pos * 45.0)


def _compass_xy(cx: float, cy: float, r: float, angle: float) -> tuple[float, float]:
    """罗盘角 → 屏幕坐标点（y 向下：(sinθ, -cosθ)）。"""
    return (cx + r * math.sin(angle), cy - r * math.cos(angle))


def _dir_of(pos: int) -> tuple[float, float]:
    return _compass_xy(0.0, 0.0, 1.0, _pos_angle(pos))


def _arc_points(
    cx: float,
    cy: float,
    radius: float,
    a0: float,
    a1: float,
    *,
    ccw: bool,
    steps: int = 48,
) -> list[tuple[float, float]]:
    """罗盘角圆弧采样（ccw=True 逆时针走弧；同位滑由调用方给整圈跨度）。"""
    if ccw:
        while a1 > a0 - 1e-9:
            a1 -= 2 * math.pi
    else:
        while a1 < a0 + 1e-9:
            a1 += 2 * math.pi
    return [
        _compass_xy(cx, cy, radius, a0 + (a1 - a0) * k / steps)
        for k in range(steps + 1)
    ]


def _quad_bezier(p0, ctrl, p1, steps: int = 48) -> list[tuple[float, float]]:
    return [
        (
            (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * ctrl[0] + t**2 * p1[0],
            (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * ctrl[1] + t**2 * p1[1],
        )
        for t in (k / steps for k in range(steps + 1))
    ]


def _slide_path(
    slide: SimaiSlide, cx: float, cy: float, r_ring: float
) -> list[tuple[float, float]]:
    """滑键形状 → 路径采样点列（起点→终点）。几何为近似，见模块文档。"""
    p0 = _compass_xy(cx, cy, r_ring, _pos_angle(slide.start_pos))
    p1 = _compass_xy(cx, cy, r_ring, _pos_angle(slide.end_pos))
    a0 = _pos_angle(slide.start_pos)
    a1 = _pos_angle(slide.end_pos)
    shape = slide.shape

    if shape == "-":
        return [p0, p1]
    if shape in (">", "<"):
        # 圆滑：沿判定圈同半径圆弧走（同位=整圈）
        ccw = shape == "<"
        if slide.start_pos == slide.end_pos:
            a1 = a0 + (0.999 if not ccw else -0.999) * 2 * math.pi
        return _arc_points(cx, cy, r_ring, a0, a1, ccw=ccw, steps=72)
    if shape in ("p", "q", "pp", "qq"):
        # 小圆（p/q，过中心区）/ 大圆（pp/qq，盘外沿）
        big = shape in ("pp", "qq")
        ccw = shape.startswith("q")
        radius = r_ring * (1.32 if big else 0.45)
        if slide.start_pos == slide.end_pos:
            a1 = a0 + (0.999 if not ccw else -0.999) * 2 * math.pi
        return _arc_points(cx, cy, radius, a0, a1, ccw=ccw, steps=72)
    if shape == "v":
        return [p0, (cx, cy), p1]
    if shape == "V":
        via = slide.reflect_pos
        if via is not None:
            return [p0, _compass_xy(cx, cy, r_ring, _pos_angle(via)), p1]
        return [p0, p1]
    if shape == "s":
        # 中心圆：以弦中点为圆心、半弦长为半径的半圆（盘心必在圆上，
        # 用叉积选经过盘心一侧的半圆）
        mx, my = (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2
        radius = max(math.dist(p0, p1) / 2, 1.0)
        m0 = math.atan2(p0[1] - my, p0[0] - mx)
        cross = (p1[0] - p0[0]) * (cy - my) - (p1[1] - p0[1]) * (cx - mx)
        a_end = m0 + (math.pi if cross > 0 else -math.pi)
        steps = 48
        return [
            (
                mx + radius * math.cos(m0 + (a_end - m0) * k / steps),
                my + radius * math.sin(m0 + (a_end - m0) * k / steps),
            )
            for k in range(steps + 1)
        ]
    if shape == "w":
        # 钩形：先外抛后回勾的二次贝塞尔
        d0 = _dir_of(slide.start_pos)
        d1 = _dir_of(slide.end_pos)
        ctrl = (
            cx + d0[0] * r_ring * 1.45 - d1[0] * r_ring * 0.18,
            cy + d0[1] * r_ring * 1.45 - d1[1] * r_ring * 0.18,
        )
        return _quad_bezier(p0, ctrl, p1)
    if shape in ("z", "^"):
        # 锯齿（z，向内折）/ 外凸弧（^，向上凸）：弦中点法向偏移折线
        mx, my = (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        norm = math.hypot(dx, dy) or 1.0
        k = (-0.5 if shape == "z" else 0.45) * r_ring
        mid = (mx - dy / norm * k, my + dx / norm * k)
        return [p0, mid, p1]
    return [p0, p1]


def _note_color(note: SimaiTap | SimaiHold | SimaiSlide) -> tuple[int, int, int]:
    if getattr(note, "is_break", False):
        return _COLOR_BREAK
    if getattr(note, "is_each", False):
        return _COLOR_EACH
    return _COLOR_SINGLE


def _draw_tap_note(
    draw: ImageDraw.ImageDraw,
    x: float,
    y: float,
    radius: float,
    color: tuple[int, int, int],
    *,
    is_break: bool = False,
    is_ex: bool = False,
    alpha: float = 1.0,
) -> None:
    """maimai 风圆环 note：实心外圈 + 中心孔 + 白描边（EX 白圈、break 光晕）。"""
    if alpha <= 0:
        return
    if is_break:
        glow_r = radius * 1.5
        draw.ellipse(
            (x - glow_r, y - glow_r, x + glow_r, y + glow_r),
            fill=(*color, int(50 * alpha)),
        )
    rim = (255, 255, 255) if is_ex else (250, 250, 252)
    draw.ellipse(
        (x - radius, y - radius, x + radius, y + radius),
        fill=(*color, int(235 * alpha)),
        outline=(*rim, int(255 * alpha)),
        width=max(2, int(radius * 0.12)),
    )
    hole = radius * 0.42
    draw.ellipse(
        (x - hole, y - hole, x + hole, y + hole), fill=(*_BG, int(255 * alpha))
    )


def _draw_star(
    draw: ImageDraw.ImageDraw,
    x: float,
    y: float,
    radius: float,
    color: tuple[int, int, int],
    alpha: float = 1.0,
) -> None:
    """破坏滑终点星形。"""
    pts = []
    for k in range(10):
        r = radius if k % 2 == 0 else radius * 0.45
        a = math.pi / 2 + k * math.pi / 5
        pts.append((x + r * math.cos(a), y - r * math.sin(a)))
    draw.polygon(
        pts, fill=(*color, int(235 * alpha)), outline=(255, 255, 255, int(220 * alpha))
    )


@dataclass
class _SlideRender:
    """滑键的预计算路径。"""

    slide: SimaiSlide
    path: list[tuple[float, float]]
    cum: list[float]  # 累计弧长（箭头沿路径匀速）


class _Canvas:
    """静态底图（一次绘制逐帧 copy）+ 布局常量。"""

    def __init__(self, size: int, title: str, level_label: str, suffix: int) -> None:
        self.size = size
        self.cx = size / 2
        self.cy = size / 2 + size * 0.01
        self.r_ring = size * 0.325
        self.r_note = size * 0.052
        self.r_spawn = self.r_ring * 1.35
        img = Image.new("RGB", (size, size), _BG)
        draw = ImageDraw.Draw(img, "RGBA")
        # 盘外参考圈
        for rr, alpha in ((self.r_ring * 1.55, 16), (self.r_ring * 1.15, 22)):
            draw.ellipse(
                (self.cx - rr, self.cy - rr, self.cx + rr, self.cy + rr),
                outline=(120, 130, 170, alpha),
                width=2,
            )
        # 判定圈与 8 个按钮位
        draw.ellipse(
            (
                self.cx - self.r_ring,
                self.cy - self.r_ring,
                self.cx + self.r_ring,
                self.cy + self.r_ring,
            ),
            outline=(*_RING, 200),
            width=3,
        )
        for pos in range(8):
            bx, by = _compass_xy(self.cx, self.cy, self.r_ring, _pos_angle(pos))
            draw.ellipse(
                (bx - 9, by - 9, bx + 9, by + 9), outline=(*_RING, 170), width=3
            )
        draw.ellipse(
            (self.cx - 5, self.cy - 5, self.cx + 5, self.cy + 5),
            fill=(150, 155, 185, 120),
        )
        # HUD：标题（左上）与难度标签（右上）
        f_title: ImageFont.FreeTypeFont = font(int(size * 0.030))
        f_level: ImageFont.FreeTypeFont = font(int(size * 0.036))
        draw.text(
            (size * 0.035, size * 0.018), title, font=f_title, fill=(232, 232, 240)
        )
        name = _LEVEL_NAMES.get(suffix, f"DIFF_{suffix}")
        label = f"{name}  {level_label}".strip()
        color = _LEVEL_COLORS.get(suffix, _RING)
        lw = draw.textlength(label, font=f_level)
        draw.text(
            (size - size * 0.035 - lw, size * 0.014),
            label,
            font=f_level,
            fill=(*color, 255),
        )
        self.img = img

    def progress_bar(self, draw: ImageDraw.ImageDraw, ratio: float) -> None:
        size = self.size
        y = size - size * 0.028
        x0, x1 = size * 0.06, size * 0.94
        draw.rectangle((x0, y, x1, y + 5), fill=(70, 72, 92, 255))
        draw.rectangle(
            (x0, y, x0 + (x1 - x0) * min(1.0, max(0.0, ratio)), y + 5),
            fill=(124, 129, 255, 255),
        )


def render_chart_video(
    chart: SimaiDifficulty,
    *,
    title: str,
    level_label: str,
    suffix: int,
    audio: bytes | None,
    out_path: Path,
    fps: int = 30,
    size: int = 720,
    limit_seconds: float | None = None,
) -> Path:
    """渲染谱面预览 mp4（阻塞、CPU 密集，调用方放线程池）。

    ``limit_seconds`` 截断渲染时长（试听段/限时模式）；音频为 None 时出无声视频。
    """
    _ensure_ffmpeg()
    canvas = _Canvas(size, title, level_label, suffix)
    slides = sorted(
        (
            _SlideRender(n, _slide_path(n, canvas.cx, canvas.cy, canvas.r_ring), [])
            for n in chart.notes
            if isinstance(n, SimaiSlide)
        ),
        key=lambda s: s.slide.time,
    )
    for sr in slides:
        cum = [0.0]
        for a, b in zip(sr.path, sr.path[1:]):
            cum.append(cum[-1] + math.dist(a, b))
        sr.cum = cum

    total = max(1.0, chart.note_end + 1.0)
    if limit_seconds is not None:
        total = min(total, limit_seconds)
    n_frames = int(total * fps)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{size}x{size}",
        "-r",
        str(fps),
        "-i",
        "pipe:",
    ]
    audio_file: Path | None = None
    if audio:
        audio_file = out_path.with_suffix(".audio.tmp")
        audio_file.write_bytes(audio)
        cmd += ["-i", str(audio_file), "-c:a", "aac", "-b:a", "160k", "-shortest"]
    cmd += [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "21",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(out_path),
    ]

    notes = sorted(chart.notes, key=lambda n: n.time)
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    assert proc.stdin is not None
    try:
        for frame_idx in range(n_frames):
            now = frame_idx / fps
            img = canvas.img.copy()
            draw = ImageDraw.Draw(img, "RGBA")
            for n in notes:
                if isinstance(n, SimaiTouch):
                    if n.time - 0.5 <= now <= n.time + 0.6:
                        _draw_touch(draw, canvas, n, now)
                elif isinstance(n, SimaiHold):
                    if n.time - _APPROACH <= now <= n.time + n.duration + 0.3:
                        _draw_hold(draw, canvas, n, now)
                    if n.time - _APPROACH <= now <= n.time + 0.12:
                        _draw_falling(draw, canvas, n, now)
                elif isinstance(n, SimaiTap):
                    if n.time - _APPROACH <= now <= n.time + 0.12:
                        _draw_falling(draw, canvas, n, now)
            for sr in slides:
                if (
                    sr.slide.time - _APPROACH
                    <= now
                    <= sr.slide.time + sr.slide.duration + 0.4
                ):
                    _draw_slide(draw, canvas, sr, now)
            canvas.progress_bar(draw, now / total)
            proc.stdin.write(img.tobytes())
    finally:
        try:
            proc.stdin.close()
        except BrokenPipeError:
            pass
        code = proc.wait()
        if audio_file is not None:
            audio_file.unlink(missing_ok=True)
    if code != 0:
        raise RuntimeError(f"ffmpeg 渲染失败（exit {code}），输出见 {out_path}")
    return out_path


def _fall_radius(canvas: _Canvas, hit: float, now: float) -> float:
    """下落 note 当前半径：盘外沿 → 判定圈线性接近。"""
    ratio = max(0.0, min(1.0, (hit - now) / _APPROACH))
    return canvas.r_ring + (canvas.r_spawn - canvas.r_ring) * ratio


def _draw_falling(
    draw: ImageDraw.ImageDraw, canvas: _Canvas, note: SimaiTap | SimaiHold, now: float
) -> None:
    hit = note.time
    alpha = 1.0 if now <= hit else 1.0 - (now - hit) / 0.12
    r = _fall_radius(canvas, hit, now)
    bx, by = _compass_xy(canvas.cx, canvas.cy, r, _pos_angle(note.position))
    _draw_tap_note(
        draw,
        bx,
        by,
        canvas.r_note,
        _note_color(note),
        is_break=getattr(note, "is_break", False),
        is_ex=getattr(note, "is_ex", False),
        alpha=alpha,
    )


def _draw_hold(
    draw: ImageDraw.ImageDraw, canvas: _Canvas, note: SimaiHold, now: float
) -> None:
    """长条条带：按钮位 ±15° 的弧带，进行中的已过段淡除（扫除感）。"""
    color = _note_color(note)
    span = math.radians(15)
    a = _pos_angle(note.position)
    width = max(6, int(canvas.r_note * 0.5))
    pts = _arc_points(
        canvas.cx, canvas.cy, canvas.r_ring, a - span, a + span, ccw=False, steps=12
    )
    draw.line(pts, fill=(*color, 110), width=width, joint="curve")
    if now >= note.time and note.duration > 0:
        done = min(1.0, (now - note.time) / note.duration)
        pts_done = _arc_points(
            canvas.cx,
            canvas.cy,
            canvas.r_ring,
            a - span,
            a - span + 2 * span * done,
            ccw=False,
            steps=8,
        )
        draw.line(pts_done, fill=(*_BG, 150), width=width)
    for cap in (a - span, a + span):
        x, y = _compass_xy(canvas.cx, canvas.cy, canvas.r_ring, cap)
        draw.ellipse(
            (x - width / 2, y - width / 2, x + width / 2, y + width / 2),
            fill=(*color, 150),
        )


def _draw_slide(
    draw: ImageDraw.ImageDraw, canvas: _Canvas, sr: _SlideRender, now: float
) -> None:
    s = sr.slide
    color = _note_color(s)
    if now >= s.time:
        # 连接线：头部判定后出现
        draw.line(sr.path, fill=(*color, 90), width=4)
    t0 = s.time + s.delay
    t1 = s.time + max(s.duration, s.delay + 0.05)
    if not (t0 <= now <= t1 + 0.1):
        return
    u = min(1.0, max(0.0, (now - t0) / (t1 - t0)))
    target = sr.cum[-1] * u
    i = 1
    while i < len(sr.cum) - 1 and sr.cum[i] < target:
        i += 1
    seg0, seg1 = sr.cum[i - 1], sr.cum[i]
    f = 0.0 if seg1 <= seg0 else (target - seg0) / (seg1 - seg0)
    ax0, ay0 = sr.path[i - 1]
    ax1, ay1 = sr.path[i]
    x, y = ax0 + (ax1 - ax0) * f, ay0 + (ay1 - ay0) * f
    tx, ty = ax1 - ax0, ay1 - ay0
    norm = math.hypot(tx, ty) or 1.0
    tx, ty = tx / norm, ty / norm
    size = canvas.r_ring * 0.075
    perp = (-ty, tx)
    for trail in (0.0, 0.45, 0.9):  # 三重雪佛龙拖尾
        ux, uy = x - tx * trail * size * 1.6, y - ty * trail * size * 1.6
        pts = [
            (ux + tx * size * 0.7, uy + ty * size * 0.7),
            (ux + perp[0] * size * 0.55, uy + perp[1] * size * 0.55),
            (ux - tx * size * 0.35, uy - ty * size * 0.35),
            (ux - perp[0] * size * 0.55, uy - perp[1] * size * 0.55),
        ]
        draw.polygon(pts, fill=(*color, int(230 * (1.0 - trail * 0.4))))
    if u >= 1.0:
        # 抵达闪效（破坏滑出星）
        ex, ey = sr.path[-1]
        if s.is_break:
            _draw_star(draw, ex, ey, canvas.r_note * 0.5, color)
        else:
            r_flash = 10 + 26 * min(1.0, (now - t1) / 0.1)
            draw.ellipse(
                (ex - r_flash, ey - r_flash, ex + r_flash, ey + r_flash),
                outline=(*color, 200),
                width=3,
            )


def _draw_touch(
    draw: ImageDraw.ImageDraw, canvas: _Canvas, note: SimaiTouch, now: float
) -> None:
    """touch：传感器区菱形花 + 命中扩散六边形（f 花火）。"""
    color = _COLOR_TOUCH
    zone_r = canvas.r_ring * {
        "A": 0.84,
        "B": 0.66,
        "C": 0.48,
        "D": 0.30,
        "E": 0.16,
    }.get(note.sensor, 0.5)
    if note.position is None:
        x, y = canvas.cx, canvas.cy
        rot = 0.0
    else:
        rot = _pos_angle(note.position)
        x, y = _compass_xy(canvas.cx, canvas.cy, zone_r, rot)
    if now <= note.time:
        grow = max(0.0, 1.0 - (note.time - now) / 0.5)
        r = 8 + 14 * grow
        alpha = int(120 + 100 * grow)
    else:
        fade = max(0.0, 1.0 - (now - note.time) / 0.4)
        r = 22
        alpha = int(200 * fade)
    if alpha <= 0:
        return
    pts = [(x, y - r), (x + r, y), (x, y + r), (x - r, y)]
    # 菱形随扇区角度旋转对齐
    ca, sa = math.cos(rot + math.pi / 8), math.sin(rot + math.pi / 8)
    pts = [
        ((px - x) * ca - (py - y) * sa + x, (px - x) * sa + (py - y) * ca + y)
        for px, py in pts
    ]
    draw.polygon(pts, outline=(*color, alpha), width=3)
    if note.fire or now >= note.time:
        burst = (now - note.time) / 0.45
        if 0 <= burst <= 1:
            rr = 14 + 46 * burst
            hex_pts = [
                (
                    x + rr * math.cos(k * math.pi / 3 + math.pi / 6),
                    y + rr * math.sin(k * math.pi / 3 + math.pi / 6),
                )
                for k in range(6)
            ]
            draw.polygon(hex_pts, outline=(*color, int(220 * (1 - burst))), width=3)
