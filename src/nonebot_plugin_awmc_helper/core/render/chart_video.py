"""谱面预览视频渲染（落雪站内 canvas 渲染器的 PIL 逐语句移植）。

绘制逻辑对照 maimai.lxns.net 渲染器 bundle（beautify 存档
``local/reference/lxns-renderer/chart-canvas.pretty.js``，各方法标注 L* 行号；
逐函数对照档案见 ``local/reference/lxns-renderer/parity-notes.md``）。
note 数据用 ``chart_notes.parse_site_chart``（同一 bundle 的解析器逐语句移植），
不使用 ``core/simai.py``（那是物量对拍用的 MaiConverter 内核，note 切分口径
不同：一个 token 的 ``*`` 分段在站内是同一 note 的多条并行动径）。

站内页面实际生效的配置（chartpage.js 设置默认值 L881-902，勿用渲染器类默认值）：
hiSpeed=6（回调 setHiSpeed → ×0.6 = 3.6）、alwaysKeepHiSpeed=false、
slideDelay=0、**slideRotation=true**、mirrorMode=none、**judgmentLineDesign=simple**
（白色判定圈 + 8 按钮点，无 sensor 底图）、pinkSlideStart/normalColorBreakSlide/
highlightEx/ddr 均关、showBpm/showNoteTotal/showBreakCount/showFireworks/
showHitEffect 开；播放时钟在 chart beat 0 起、音频从 beat 4（1 小节 lead-in）
起播（chartpage.js syncFrame），因此视频前 4 拍为空场。

关键语义（均引自源码，勿凭直觉改）：
- 运动（calculateNotePosition L307）：接近窗前半程 0.25R 驻留、scale 0→1
  （**尺寸渐显而非 alpha**，tap sprite 显示尺寸 ×scale L449），后半程
  0.25R→R；tap 判定后立即消失；hold 头判定后驻留 R；
- 滑键头 = **星形头**（hasTapHead 仅 `@` 修饰，simai 数据恒无 → 星头，
  L8411）；`!`/`?`（headless）无头，星于起点按钮生长（renderSlideStar
  L6115）；slideRotation 开 → 接近期星头自旋（calculateStarRotation L6321）、
  移动星朝向路径切向 +π/2（getPathTangentAngle L6386）；
- slide 轨道透明度 ge()（L5527）：普通段与 wifi 段（视作 fading 档）分别取，
  slideDelay=0；单路径可含多段（[] 内多段），`*` 分段为并行动径；
- tap = 四象限线性渐变环 + 双白环 + 黑描边 + 中心点（drawTapNoteVector L495）；
  EX 覆盖环画在 scale=1 的 sprite 内（1.19×f），显示再 ×scale → 终效 1.19·f·s；
- hold = 双六棱柱：EX 覆盖环顶点序 O,k,F,N,L,A（L6519）、六面渐变轴
  **os→oe 外边端点**（L6567）、进行中白色呼吸 Se 作用于渐变两端与白边、
  头部光晕 E 表径向渐变（drawActiveHeadGlow L6584）；
- touch = 四瓣**环形**渐变花瓣（外三角正向 + 内三角反向 = nonzero 空心环，
  L6807）带投影（sb 层），touch hold 有四色进度环 gauge（L6818）与
  部件级分层排序（ke/Ae L6672：区序 B,E,A,D,C + we() 键 + De 哨兵）；
- 按压涟漪 = tt 16 停点径向渐变环带 [0.8r,1.2r]（drawRippleRing L7225）
  screen 混合，非描边圆；touch hold 松手有 hexagon 打击特效（L8549）；
- touch 命中 = lighter 中心环（ft 62 停点）+ 双层星爆（Ft 类 L7509）；
- 花火（`!` fire touch 专属，Wt L7665）：15 色楔形 + 光球 + >0.6s 切洞；
- 帧序（renderFrame L7997 / renderNotes L8104）：clear → 判定线 → 花火 →
  HUD → clip 圆 → 轨道(slides **倒序**) → 星(倒序) → 接近指示(hold/slide
  弧+全对 GOLD 连接线) → tap 弧批 → 分层头(**taps 升序→holds 升序→slides
  升序**，L8271-8283) → touch 边框/部件 → 涟漪/touch hold 松手/命中/打击。

帧在 2× 超采样画布绘制（等价站内 G=2 backing）后缩至 600²，rawvideo 管道
喂 ffmpeg 合成 h264+aac mp4（音频前补 1 小节静音，与视频时间轴一致）。
"""

import math
import zlib
import threading
from pathlib import Path
from dataclasses import field, dataclass

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .fonts import font as _load_font
from .chart_notes import (
    ChartNote,
    SiteChart,
    SlidePart,
    SlideSegment,
    note_completion_times,
    break_completion_times,
    break_noex_completion_times,
)
from ._lxns_slide_data import SLIDE_POINTS, SLIDE_KEYFRAMES

# ------------------------------------------------- 常量（JS L15-60 / I 表 L66）
_LOGICAL = 600.0
_SS = 2  # 超采样（站内 G=2 backing）
_RADIUS = _LOGICAL * 0.45
_CENTER = _LOGICAL / 2
_BASE_APPROACH_MS = 2250.0
_HI_SPEED = 6.0 * 0.6  # setHiSpeed 隐藏系数（L7894 默认 6*N）
_APPROACH_MS = _BASE_APPROACH_MS / _HI_SPEED
_HALF_APPROACH = _APPROACH_MS / 2
_R_NOTE = 1 / 12.5
_R_INNER = 0.65
_C_TRAVEL = 0.25
_DOT = 9 / 300
_STROKE = 2 / 300
_JLINE = 3 / 300
_ARROW_BACK = 44.6 / 300
_ARROW_LEN = 12.2 / 300
_ARROW_W = 16.6 / 300
_ARROW_TIP = 9.5 / 300
_SHADOW_OFF = 5 / 300  # 箭头黑影偏移（源码 a = 5/300·radius）
_STAR = 1.6 / 10.42
_B_HOLD = 1.5
_INNER_HEX = 0.62
_T_GLOW = 0.06
_FLICK_PERIOD = 16000 / 60
_FLICK_FLOOR = 0.82
_HIT_MS = 450.0
_EX_ALPHA = 1.19
_P_MIX = 0.18
_TOUCH_OUT = 1 / 6.25
_TOUCH_IN = 1 / 12.5
_STAR_HEAD = 1.15 * 1.25
_STAR_MIN = 0.5  # JS `_`：生长星最小尺寸比例（L6131 u = _+(1-_)*r）
_SLIDE_DELAY = 0.0  # 站内 config.slideDelay 默认 0（me() L5519 入参）
_WIFI_HALF_ANGLE = 67.4 * math.pi / 180
_TOUCH_FLICK_MS = 150.0
_DELAY_FADE = 50.0  # de（L5510）：临近判定跳满档的窗口
_TRAIL_RAMP = 200.0  # fe（L5511）：轨道淡入爬坡时长

C_BLACK = (0, 0, 0)
C_WHITE = (255, 255, 255)
C_TAP_DOT = (255, 105, 180)
C_SIMUL_DOT = (255, 215, 0)
C_BREAK_DOT = (255, 140, 0)
C_TAP_GRAD = ((253, 25, 125), (255, 125, 213))
C_SIMUL_GRAD = ((252, 182, 0), (255, 254, 0))
C_BREAK_GRAD = ((255, 179, 71), (255, 102, 0))
C_SLIDE_CYAN = (1, 251, 253)
C_SLIDE_ARROW = (255, 144, 1)
C_SLIDE_SIMUL = (255, 237, 36)
C_SLIDE_PINK = (255, 105, 180)
C_TOUCH_CYAN = (0, 255, 255)
C_TOUCH_BLUE = (0, 128, 255)
C_TOUCH_SIMUL_Y = (255, 255, 0)
C_TOUCH_SIMUL_G = (255, 215, 0)
C_TOUCH_CENTER = (0, 191, 255)
C_TOUCH_CENTER_S = (255, 255, 0)
C_HIT_GOLD = (245, 234, 114)
C_RIPPLE = (255, 227, 54)  # $e = (1, .889, .213)
C_EX_NORMAL = (255, 180, 210, 204)
C_EX_BREAK = (255, 200, 120, 204)
C_EX_SIMUL = (255, 245, 150, 204)
C_EX_SLIDE = (100, 230, 230, 204)
C_HUD_GRAY = (148, 163, 184)
C_FPS_GREEN = (34, 197, 94)
C_FPS_YELLOW = (234, 179, 8)
C_FPS_RED = (239, 68, 68)
C_ORANGE = (255, 165, 0)
# touch hold 花瓣渐变四停点 Pe（L6737）与进度环四象限色 Fe（L6743）
C_TOUCHHOLD_PETALS = (
    ((255, 85, 17), (247, 70, 1), (231, 66, 1), (255, 246, 242)),
    ((248, 222, 0), (246, 237, 0), (236, 244, 2), (255, 251, 239)),
    ((30, 180, 118), (18, 168, 106), (10, 160, 98), (236, 255, 248)),
    ((0, 170, 248), (0, 151, 246), (2, 137, 244), (239, 252, 255)),
)
C_TOUCHHOLD_GAUGE = ((233, 85, 19), (250, 237, 0), (13, 172, 103), (44, 166, 224))

# ---- 按压涟漪（it 类 Ge/Ke/Ye/Xe/Je/qe/Ze/Qe/et/tt/nt，L7143-7176）----
_RIPPLE_GE = 16000 / 60 / 1000
_RIPPLE_KE = (0.0, 4 / 60, 8 / 60)
_RIPPLE_QE = 11 / 60
_RIPPLE_JE = 1.0
_RIPPLE_YE = 0.1355
_RIPPLE_XE = 0.735
_RIPPLE_ZE = 0.8  # 环带内缘比例
_RIPPLE_QE_OUT = 1.2  # 环带外缘比例
_RIPPLE_ET = 0.58  # 色向白偏移系数
_RIPPLE_MIN_ALPHA = 0.02  # nt
_RIPPLE_PROFILE = (  # tt：16 停点轮廓（位置, 强度）
    (0.0, 0.0),
    (0.063, 0.01),
    (0.125, 0.08),
    (0.188, 0.17),
    (0.25, 0.33),
    (0.313, 0.52),
    (0.375, 0.68),
    (0.438, 0.85),
    (0.5, 1.0),
    (0.563, 0.88),
    (0.625, 0.71),
    (0.688, 0.56),
    (0.75, 0.37),
    (0.813, 0.2),
    (0.875, 0.09),
    (0.938, 0.03),
    (1.0, 0.01),
)

# ---- touch 命中特效（Ft 类，L7350-7454）----
_TOUCHHIT_LIFETIME = 0.5  # mt
_TOUCHHIT_RING_MAX = 540.0  # gt
_TOUCHHIT_RING_BASE = 2.5 * 70.0  # ht = 175
_TOUCHHIT_STAR_SCALE = 1.4  # Et
_TOUCHHIT_SPRITE_SIZE = 32.0  # Tt
_TOUCHHIT_BURSTS = (
    # (数量, 起始半径, 峰值半径, 漂移时长, 漂移距离)
    (8, 0.5 * 70.0, 0.3 * 70.0, 0.0, 0.0),
    (8, 0.6 * 70.0, 0.6 * 70.0, 700.0, 140.0),
)
_TOUCHHIT_RING_SPLINE = (  # Dt
    (0.0, 0.0, 10.02783, 10.02783),
    (0.0734887, 0.482223, 2.93804, 2.93804),
    (0.3209003, 0.834577, 0.592962, 0.592962),
    (1.0, 1.0, 0.180534, 0.180534),
)
_TOUCHHIT_RING_ALPHA = (  # Ot
    (0.0, 1.0),
    (5484 / 65535, 1.0),
    (18591 / 65535, 0.141176),
    (31029 / 65535, 0.031373),
    (45607 / 65535, 0.0),
    (1.0, 0.0),
)
_TOUCHHIT_STAR_SPLINE = (  # kt
    (0.0, 0.755245, 2.38315, 2.38315),
    (0.127286, 1.0, 0.061337, 0.061337),
    (0.272332, 0.892134, -0.927502, -0.927502),
    (0.540943, 0.811183, -0.114026, -0.114026),
    (1.0, 0.0, 0.036313, 0.036313),
)
_TOUCHHIT_DRIFT_SPLINE = ((0.0, 1.0, -2.726415, -2.726415), (1.0, 0.0, 0.0, 0.0))  # At
_TOUCHHIT_GLOW = (  # ft：62 停点径向轮廓
    0.666,
    0.667,
    0.671,
    0.679,
    0.685,
    0.693,
    0.7,
    0.709,
    0.718,
    0.727,
    0.736,
    0.744,
    0.753,
    0.763,
    0.772,
    0.781,
    0.791,
    0.8,
    0.809,
    0.818,
    0.826,
    0.835,
    0.843,
    0.851,
    0.858,
    0.866,
    0.872,
    0.879,
    0.887,
    0.896,
    0.905,
    0.913,
    0.922,
    0.93,
    0.938,
    0.946,
    0.953,
    0.959,
    0.964,
    0.969,
    0.974,
    0.977,
    0.981,
    0.984,
    0.986,
    0.987,
    0.989,
    0.993,
    0.998,
    1.0,
    0.896,
    0.584,
    0.374,
    0.248,
    0.155,
    0.087,
    0.038,
    0.008,
    0.002,
    0.0,
    0.0,
    0.0,
)

# ---- 花火（Wt 类，L7655-7660）----
_FIREWORK_MS = 1333.0  # It
_FIREWORK_WEDGE_SCALE = 5 / 4.5 * 1.05  # Lt
_FIREWORK_R = 5.0  # Rt（楔形长度参数）
_FIREWORK_SMALL = 2.5  # zt：矢量楔阈值
_FIREWORK_PHASE = 0.6  # Bt：两阶段切换
_FIREWORK_LATE = 1.1  # Vt
_FIREWORK_COLORS = (  # Ut：15 色楔形
    (255, 179, 186),
    (255, 208, 168),
    (255, 250, 184),
    (216, 240, 168),
    (184, 224, 176),
    (168, 224, 204),
    (168, 200, 232),
    (184, 176, 232),
    (208, 176, 232),
    (240, 176, 216),
    (255, 201, 168),
    (255, 239, 168),
    (200, 232, 168),
    (168, 224, 188),
    (168, 204, 232),
)

# ---- touch 部件排序（ke/Ae，L6672-6684）----
_TOUCH_ZONES = "BEADC"
_TOUCH_SORT_SENTINELS = (  # De：槽间哨兵键
    2148237322,
    2148237312,
    2148237312,
    2147844096,
    2147844096,
    2147844098,
    2147844098,
    2147844096,
    2147844096,
    2147844096,
    2147844098,
    2147844098,
    2147844096,
    2147844096,
    2147844096,
    2147844096,
    2147844098,
    2147844098,
    2147844096,
    2147844106,
    2147844096,
    2147844096,
    2147844096,
    2147844096,
    2147844096,
    2147844096,
    2147844096,
    2147942399,
    2147942399,
    2147516416,
    2147516416,
)
# touch hold 进度环几何（K 表，L6744；单位为 480 设计空间）
_GAUGE_GEO = {
    "canvas_half": 99.75,
    "inner_axis": 64.0,
    "inner_diagonal": 50.5,
    "outer_axis": 97.75,
    "outer_diagonal": 82.75,
}
# wifi chevron 间距缓动表（y，L32）
_WIFI_EASE = (0, 0.094, 0.178, 0.266, 0.355, 0.462, 0.57, 0.674, 0.779, 0.884, 1)
_WIFI_WIDTH_BASE = 19.2 / 300  # v
_QT = 2 * math.pi / 1000  # 星星 tap 自旋角速度（L7824 qt）


def _sort_key(layer: int, order: int) -> int:
    """we()（L6604）：部件排序键。"""
    return ((layer + 32768) << 16 | (order + 32768) & 0xFFFF) & 0xFFFFFFFF


def _lerp_stops(stops: tuple, t: float) -> float:
    """线性插值多停点表（Mt L7472）。"""
    if t <= stops[0][0]:
        return stops[0][1]
    if t >= stops[-1][0]:
        return stops[-1][1]
    for i in range(len(stops) - 1):
        p0, p1 = stops[i], stops[i + 1]
        if p0[0] <= t <= p1[0]:
            span = p1[0] - p0[0] or 1.0
            return p0[1] + (p1[1] - p0[1]) * (t - p0[0]) / span
    return stops[-1][1]


def _hermite(tbl: tuple, t: float) -> float:
    """hermite 样条插值（jt L7456，带斜率停点）。"""
    if t <= tbl[0][0]:
        return tbl[0][1]
    if t >= tbl[-1][0]:
        return tbl[-1][1]
    for i in range(len(tbl) - 1):
        r, nx = tbl[i], tbl[i + 1]
        if not (r[0] <= t <= nx[0]):
            continue
        span = nx[0] - r[0] or 1.0
        o = (t - r[0]) / span
        s = o * o
        c = s * o
        return (
            (2 * c - 3 * s + 1) * r[1]
            + (c - 2 * s + o) * r[3] * span
            + (-2 * c + 3 * s) * nx[1]
            + (c - s) * nx[2] * span
        )
    return tbl[-1][1]


def _hash01(seed: int) -> float:
    """Nt()（L7486）：星爆随机（LCG），seed 取 32 位有符号。"""
    t = (seed * 1664525 + 1013904223) & 0xFFFFFFFF
    t = (t ^ (t >> 16)) & 0xFFFFFFFF
    return t / 4294967296.0


def _drift_dist(elapsed_s: float, u: float, d: float) -> float:
    """Pt()（L7496）：星爆漂移距离（At 样条衰减积分，上界钳制）。"""
    if elapsed_s <= 0:
        return 0.0
    steps = max(6, math.ceil(elapsed_s * 60))
    dt = elapsed_s / steps
    a = u
    total = 0.0
    for k in range(steps):
        t = min(1.0, (k + 0.5) * dt / _TOUCHHIT_LIFETIME)
        a = min(a, max(0.0, _hermite(_TOUCHHIT_DRIFT_SPLINE, t) * d))
        total += a * dt
    return total


def _rounded_tri_points(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    radius: float,
) -> list[tuple[float, float]]:
    """drawRoundedTriangle：每角按半径切角，二次曲线（控制点=角点）过角。"""
    pts = (p0, p1, p2)
    out: list[tuple[float, float]] = []
    for i in range(3):
        cur = pts[i]
        prev = pts[i - 1]
        nxt = pts[(i + 1) % 3]
        din = math.hypot(prev[0] - cur[0], prev[1] - cur[1]) or 1.0
        dout = math.hypot(nxt[0] - cur[0], nxt[1] - cur[1]) or 1.0
        rr = min(radius, din / 2, dout / 2)
        a_pt = (
            cur[0] + (prev[0] - cur[0]) / din * rr,
            cur[1] + (prev[1] - cur[1]) / din * rr,
        )
        b_pt = (
            cur[0] + (nxt[0] - cur[0]) / dout * rr,
            cur[1] + (nxt[1] - cur[1]) / dout * rr,
        )
        out.append(a_pt)
        for k in (1, 2, 3):
            t = k / 4
            out.append(
                (
                    (1 - t) ** 2 * a_pt[0] + 2 * (1 - t) * t * cur[0] + t**2 * b_pt[0],
                    (1 - t) ** 2 * a_pt[1] + 2 * (1 - t) * t * cur[1] + t**2 * b_pt[1],
                )
            )
        out.append(b_pt)
    return out


def _mirror_pair(pos: int) -> int:
    """ae()：圆滑镜像位（re 表）。"""
    return (-1, 1, 8, 7, 6, 5, 4, 3, 2)[pos]


def _button_angle(pos: int) -> float:
    """getButtonAngle（L262）：-π/2 + π/8 + (pos-1)·π/4。"""
    return -math.pi / 2 + math.pi / 8 + (pos - 1) * math.pi / 4


def _mix(c1: tuple, c2: tuple, t: float) -> tuple:
    return tuple(round(a + (b - a) * t) for a, b in zip(c1[:3], c2[:3]))


def _brightness(c: tuple, t: float) -> tuple:
    return tuple(max(0, min(255, round(v * t))) for v in c[:3])


def _grad_pair(is_break: bool, is_each: bool) -> tuple[tuple, tuple]:
    """H()（L167）：ddr 关闭时 break/each/normal 三组渐变对。"""
    if is_break:
        return C_BREAK_GRAD
    if is_each:
        return C_SIMUL_GRAD
    return C_TAP_GRAD


def _dot_color(is_break: bool, is_each: bool) -> tuple:
    if is_break:
        return C_BREAK_DOT
    if is_each:
        return C_SIMUL_DOT
    return C_TAP_DOT


def _hold_flicker(elapsed_ms: float) -> float:
    """Se()（L6420）：进行中 hold 白边亮度呼吸。"""
    if elapsed_ms < 0:
        return 1.0
    table = (
        0.714,
        0.51,
        0.286,
        0.095,
        0.0,
        0.01,
        0.095,
        0.293,
        0.552,
        0.762,
        0.917,
        0.976,
        1.0,
        0.995,
        0.969,
        0.917,
    )
    n = len(table)
    pos = elapsed_ms % _FLICK_PERIOD / _FLICK_PERIOD * n
    i = int(pos)
    frac = pos - i
    a = table[i % n]
    b = table[(i + 1) % n]
    return _FLICK_FLOOR + (a + (b - a) * frac) * (1 - _FLICK_FLOOR)


@dataclass
class _Note:
    """渲染 note（由 SiteChart 映射；字段语义对齐 JS note 对象）。"""

    kind: str  # tap / hold / slide / touch / touch-hold
    pos: int  # 按钮 1-8（touch 为 0）
    timing: float  # ms（含站内 +4 拍 lead-in）
    is_break: bool = False
    is_each: bool = False  # 渲染层重算的同刻旗标（calculateSimultaneousCounts）
    is_ex: bool = False
    duration: float = 0.0  # hold/touch-hold ms
    # slide：并行动径（JS allSlideSegments/allDelayMs/allDurationMs/allSlideBreaks）
    parts: list[SlidePart] = field(default_factory=list)
    has_tap_head: bool = False
    headless_mode: str | None = None
    # tap 的星形变体（`$` 修饰，宴谱常见）
    is_star: bool = False
    is_spinning_star: bool = False
    has_delay_marker: bool = False
    fire: bool = False
    sensor: str = ""
    touch_num: int | None = None
    is_headless: bool = False
    visual_only: bool = False
    src_index: int = 0  # Le()：touch 部件排序用（全体 note 序）
    simul_slide_count: int = 1  # 同刻 slide 数（轨道/移动星配色专用，L8123）


def _touch_position_name(note: _Note) -> str:
    """JS note.position 字符串：中心为 "C"，其余 "A1".."E8"。"""
    if (note.sensor or "C") == "C" or note.touch_num is None:
        return "C"
    return f"{note.sensor}{note.touch_num}"


@dataclass
class _Pos:
    x: float
    y: float
    alpha: float
    visible: bool


def _note_position(
    note: _Note, now_ms: float, cx: float, cy: float, radius: float
) -> _Pos:
    """calculateNotePosition（L307）。tap 判定后立即消失；hold 头驻留 R。

    alpha 字段对应 JS 的 scale：前半程由 0→1，用于 note 尺寸缩放。
    """
    ang = _button_angle(note.pos)
    dt = note.timing - now_ms
    is_hold = note.kind == "hold"
    end_guard = -note.duration if is_hold else 0.0
    if dt > _APPROACH_MS or dt < end_guard:
        return _Pos(0.0, 0.0, 0.0, False)
    if dt > _HALF_APPROACH:
        d = radius * _C_TRAVEL
        alpha = 1 - (dt - _HALF_APPROACH) / _HALF_APPROACH
    elif dt >= 0:
        e = 1 - dt / _HALF_APPROACH
        d = radius * (_C_TRAVEL + 0.75 * e)
        alpha = 1.0
    elif is_hold:
        d = radius
        alpha = 1.0
    else:
        return _Pos(0.0, 0.0, 0.0, False)
    return _Pos(cx + math.cos(ang) * d, cy + math.sin(ang) * d, alpha, True)


class _Canvas:
    """单帧画布：logical 600 × SS 超采样，ImageDraw(RGBA) 直接混色。"""

    def __init__(self, transparent: bool = False):
        self.size = int(_LOGICAL * _SS)
        fill = (0, 0, 0, 0) if transparent else (*C_BLACK, 255)
        self.img = Image.new("RGBA", (self.size, self.size), fill)
        self.d = ImageDraw.Draw(self.img, "RGBA")
        self.radius = _RADIUS
        self.cx = _CENTER
        self.cy = _CENTER
        self.sw = _STROKE * _RADIUS  # note 描边宽（logical px）

    def s(self, v: float) -> float:
        return v * _SS

    def pts(self, points: list[tuple[float, float]]) -> list[tuple[float, float]]:
        return [(x * _SS, y * _SS) for x, y in points]

    def stroke_circle(self, x: float, y: float, r: float, color, width: float) -> None:
        rr = self.s(r)
        x, y = self.s(x), self.s(y)
        self.d.ellipse(
            (x - rr, y - rr, x + rr, y + rr),
            outline=color,
            width=max(1, round(self.s(width))),
        )

    def fill_circle(self, x: float, y: float, r: float, color) -> None:
        rr = self.s(r)
        x, y = self.s(x), self.s(y)
        self.d.ellipse((x - rr, y - rr, x + rr, y + rr), fill=color)

    def polygon(
        self,
        points: list[tuple[float, float]],
        fill=None,
        outline=None,
        width: float = 1.0,
    ) -> None:
        pts = self.pts(points)
        self.d.polygon(pts, fill=fill, outline=None, width=0)
        if outline:
            # canvas stroke 以路径为中心（±width/2）；Pillow 的 polygon outline
            # 是向内画。改用 line 闭合描边以保持与站内一致的外观尺寸
            w = max(1, round(self.s(width)))
            self.d.line([*pts, pts[0]], fill=outline, width=w, joint="curve")

    def line(self, points: list[tuple[float, float]], color, width: float) -> None:
        self.d.line(
            self.pts(points),
            fill=color,
            width=max(1, round(self.s(width))),
            joint="curve",
        )

    def arc_points(
        self, cx: float, cy: float, r: float, a0: float, a1: float
    ) -> list[tuple[float, float]]:
        """ctx.arc 的折线近似（逻辑坐标；调用方用 c.pts()/c.line() 缩放到画布）。"""
        rr = r
        span = a1 - a0
        n = max(2, math.ceil(abs(span) / (math.pi / 24)))
        return [
            (
                cx + rr * math.cos(a0 + span * k / n),
                cy + rr * math.sin(a0 + span * k / n),
            )
            for k in range(n + 1)
        ]


def _linear_gradient(
    size: tuple[int, int],
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    c0: tuple,
    c1: tuple,
) -> Image.Image:
    """createLinearGradient 等价（RGBA，不透明）。"""
    w, h = size
    ax, ay = x1 - x0, y1 - y0
    len2 = ax * ax + ay * ay or 1.0
    yy, xx = np.mgrid[0:h, 0:w]
    t = np.clip(((xx - x0) * ax + (yy - y0) * ay) / len2, 0, 1)[..., None]
    a = np.array((*c0[:3], 255), dtype=float)
    b = np.array((*c1[:3], 255), dtype=float)
    return Image.fromarray((a * (1 - t) + b * t).astype(np.uint8), "RGBA")


def _circle_mask(size: int) -> Image.Image:
    """播放场 clip 圆掩码（ctx.clip: arc(center, logicalSize/2)，按尺寸缓存）。"""

    key = size
    cached = _CLIP_MASK_CACHE.get(key)
    if cached is None:
        yy, xx = np.mgrid[0:size, 0:size]
        circle = np.hypot(xx - size / 2, yy - size / 2) <= size / 2 - 1
        cached = Image.fromarray((circle * 255).astype(np.uint8), "L")
        _CLIP_MASK_CACHE[key] = cached
    return cached


def _clip_layer(
    layer: Image.Image, box: tuple | None = None, frame_size: int | None = None
) -> Image.Image:
    """特效层按 clip 圆裁剪 alpha（JS 特效全部画在 ctx.clip 之内）。

    掩码圆心在整帧画布中心；区域层需传 box 与 frame_size，取整帧掩码的
    对应窗口（不能以区域自身中心重新生成圆）。
    """
    from PIL import ImageChops

    out = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    if box is None:
        # 只处理非空区域：整帧 darker() 是每帧特效的主要开销之一
        bbox = layer.getbbox()
        if bbox is None:
            return out
        sub = layer.crop(bbox)
        mask = _circle_mask(layer.width).crop(bbox)
        sub.putalpha(ImageChops.darker(sub.getchannel("A"), mask))
        out.paste(sub, bbox)
        return out
    else:
        frame = frame_size if frame_size else box[2] - box[0]
        mask = _circle_mask(frame).crop((box[0], box[1], box[2], box[3]))
    out = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    out.paste(layer, (0, 0))
    out.putalpha(ImageChops.darker(out.getchannel("A"), mask))
    return out


def _screen_composite(base: Image.Image, layer: Image.Image, box: tuple) -> None:
    """globalCompositeOperation='screen'（不透明底）：out = dst + a·src·(1-dst)。"""
    x0, y0 = box[0], box[1]
    layer = _clip_layer(layer, box, base.width)
    dst = np.asarray(base.crop(box).convert("RGB"), dtype=np.int32)
    src = np.asarray(layer.convert("RGBA"), dtype=np.int32)
    a = src[..., 3:4] / 255.0
    out = dst + (a * src[..., :3] * (255 - dst) // 255).astype(np.int32)
    base.paste(Image.fromarray(out.clip(0, 255).astype(np.uint8), "RGB"), (x0, y0))


def _additive_composite(base: Image.Image, layer: Image.Image, box: tuple) -> None:
    """globalCompositeOperation='lighter'（不透明底）：out = dst + a·src。"""
    x0, y0 = box[0], box[1]
    layer = _clip_layer(layer, box, base.width)
    dst = np.asarray(base.crop(box).convert("RGB"), dtype=np.int32)
    src = np.asarray(layer.convert("RGBA"), dtype=np.int32)
    out = dst + (src[..., 3:4] * src[..., :3] // 255)
    base.paste(Image.fromarray(out.clip(0, 255).astype(np.uint8), "RGB"), (x0, y0))


def _poly_mask(
    size: int | tuple[int, int],
    outer: list[tuple[float, float]],
    inner: list[tuple[float, float]] | None = None,
) -> Image.Image:
    """外正向 + 内反向 = nonzero 环形填充的 L 掩码。"""
    w, h = size if isinstance(size, tuple) else (size, size)
    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    md.polygon(outer, fill=255)
    if inner:
        md.polygon(inner, fill=0)
    return mask


_CLIP_MASK_CACHE: dict[int, Image.Image] = {}


class _Sprites:
    """缓存 sprite（tap 环 / touch 花瓣 / 打击特效 / gauge），等价 JS sprite cache。"""

    def __init__(self):
        self.taps: dict[tuple, Image.Image] = {}
        self.petals: dict[tuple, Image.Image] = {}
        self.hits: dict[str, Image.Image] = {}

    # ---- tap 基础 sprite（getTapSprite L476，scale=1 几何；EX 环另画）
    def tap(self, pos: int, is_break: bool, is_each: bool, is_ex: bool) -> Image.Image:
        """sprite 几何固定 scale=1，显示端按 note scale 缩放（renderTapNote L439）。

        EX 版本跳过外黑环（drawTapNoteVector L507 的 `c ||` 条件），EX 覆盖环
        因几何随 scale 二次缩放（sprite 内 1.19×scale × 显示 scale）单独绘制。
        """
        key = (pos, is_break, is_each, is_ex)
        cached = self.taps.get(key)
        if cached:
            return cached
        f = _RADIUS * _R_NOTE * 1.36  # d
        p = f * _R_INNER
        sw = _STROKE * _RADIUS
        # 显示半宽 = getTapSpriteCropHalf L451（EX 环 1.19f 与黑环 f+1.5sw 取大）
        half = (max(f * _EX_ALPHA, f + sw / 2) if is_ex else f + sw * 1.5) + 1.0
        size = math.ceil(half * 2 * _SS)
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img, "RGBA")
        cx = cy = size / 2
        c0, c1 = _grad_pair(is_break, is_each)

        def ring_px(r: float, color, width: float) -> None:
            rr = r * _SS
            d.ellipse(
                (cx - rr, cy - rr, cx + rr, cy + rr),
                outline=color,
                width=max(1, round(width * _SS)),
            )

        def sector(
            r0: float, r1: float, a0: float, a1: float
        ) -> list[tuple[float, float]]:
            out = [
                (
                    cx + math.cos(a0 + (a1 - a0) * k / 12) * r1 * _SS,
                    cy + math.sin(a0 + (a1 - a0) * k / 12) * r1 * _SS,
                )
                for k in range(13)
            ]
            inn = [
                (
                    cx + math.cos(a1 - (a1 - a0) * k / 12) * r0 * _SS,
                    cy + math.sin(a1 - (a1 - a0) * k / 12) * r0 * _SS,
                )
                for k in range(13)
            ]
            return out + inn

        # 四象限渐变环（renderDirectionalTapRing L423）：渐变轴 = 扇区起/止两条
        # 半径在 mid 半径处的连线（切向，不加 π/4）
        rot = _button_angle(pos)
        mid = (p + f) / 2
        for k in range(4):
            a0 = rot + k * math.pi / 2
            a1 = a0 + math.pi / 2
            g0 = (
                cx + math.cos(a0) * mid * _SS,
                cy + math.sin(a0) * mid * _SS,
            )
            g1 = (
                cx + math.cos(a1) * mid * _SS,
                cy + math.sin(a1) * mid * _SS,
            )
            path = sector(p, f, a0, a1)
            bbox = (
                int(min(x for x, _ in path) - 2),
                int(min(y for _, y in path) - 2),
                int(max(x for x, _ in path) + 2),
                int(max(y for _, y in path) + 2),
            )
            bbox = (
                max(0, bbox[0]),
                max(0, bbox[1]),
                min(size, bbox[2]),
                min(size, bbox[3]),
            )
            if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
                continue
            mask = Image.new("L", (bbox[2] - bbox[0], bbox[3] - bbox[1]), 0)
            ImageDraw.Draw(mask).polygon(
                [(x - bbox[0], y - bbox[1]) for x, y in path], fill=255
            )
            grad = _linear_gradient(
                mask.size,
                g0[0] - bbox[0],
                g0[1] - bbox[1],
                g1[0] - bbox[0],
                g1[1] - bbox[1],
                _mix(c0, C_WHITE, _P_MIX),
                c1,
            )
            img.paste(grad, (bbox[0], bbox[1]), mask)
        # 黑描边（外环仅非 EX）+ 白双环 + 中心点（drawTapNoteVector L507-511）
        if not is_ex:
            ring_px(f + sw, C_BLACK, sw)
        if p - sw > 0:
            ring_px(p - sw, C_BLACK, sw)
        ring_px(f, C_WHITE, sw)
        ring_px(p, C_WHITE, sw)
        dot = f * 0.15
        d.ellipse(
            (cx - dot * _SS, cy - dot * _SS, cx + dot * _SS, cy + dot * _SS),
            fill=_dot_color(is_break, is_each),
        )
        self.taps[key] = img
        return img

    # ---- 打击特效 sprite（getHitEffectShapeSprite L7322）
    def hit(self, shape: str) -> Image.Image:
        cached = self.hits.get(shape)
        if cached:
            return cached
        d_size = _RADIUS * _R_NOTE * 1.36 * 1.5  # 形状半径 d
        c_blur = _RADIUS * (4 / 300 * 0.7)  # at
        l_blur = c_blur * 2.4  # at×ot
        # JS getHitEffectShapeSprite L7329-7332：l = at·R·ot·X，pad = s + u + l/X·ct + 1
        half = d_size + _STROKE * _RADIUS + l_blur * 2.5 + 1
        size = math.ceil(half * 2 * _SS)
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        cx = cy = size / 2
        rr = d_size * _SS
        lw = max(2, round(_STROKE * _RADIUS * 2 * _SS))
        if shape == "star":
            # starSubPath L7276：5 角星，首顶点角 3π/2+π ≡ π/2（朝下）
            pts = []
            ang = math.pi / 2
            for _ in range(5):
                pts.append((cx + math.cos(ang) * rr, cy + math.sin(ang) * rr))
                ang += math.pi / 5
                pts.append(
                    (cx + math.cos(ang) * rr * 0.5, cy + math.sin(ang) * rr * 0.5)
                )
                ang += math.pi / 5
            path = [*pts, pts[0]]
        else:
            # hexagonSubPath L7267：6 顶点，起始角 0
            path = [
                (
                    cx + rr * math.cos(k * math.pi / 3),
                    cy + rr * math.sin(k * math.pi / 3),
                )
                for k in range(6)
            ]
            path.append(path[0])

        def stroke_layer() -> Image.Image:
            lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ImageDraw.Draw(lay).line(
                path, fill=(*C_HIT_GOLD, 255), width=lw, joint="curve"
            )
            return lay

        # 辉光层：α.55 + blur(l)（L7338 st/ot），主层：α1 + blur(c)
        glow = stroke_layer().filter(ImageFilter.GaussianBlur(l_blur * _SS))
        ga = glow.getchannel("A").point(lambda v: int(v * 0.55))
        glow.putalpha(ga)
        img.alpha_composite(glow)
        main = stroke_layer().filter(ImageFilter.GaussianBlur(c_blur * _SS))
        img.alpha_composite(main)
        self.hits[shape] = img
        return img

    # ---- touch 花瓣 sprite（getTouchPetalSprite L6758：sb/f/w 三层）
    def petal(self, layer_kind: str, kind: str, k: int) -> Image.Image:
        """layer_kind: sb（投影+黑边）/ f（渐变填充）/ w（白边）。

        kind: h=touch hold / s=同刻 / n=普通；k=花瓣序 0-3。
        sprite 坐标系与 JS 一致：花瓣中心在原点，tip 方向随 k/isHold。
        """
        key = (layer_kind, kind, k)
        cached = self.petals.get(key)
        if cached:
            return cached
        sw = _STROKE * _RADIUS
        half = (
            _RADIUS * _TOUCH_IN * 1.3
            + sw * 3
            + _RADIUS * (8 / 300)
            + _RADIUS * (2 / 300)
        )  # getTouchSpriteHalf L6755
        size = math.ceil(half * 2 * _SS)
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img, "RGBA")
        cx = cy = size / 2
        is_hold = kind == "h"
        p = (-math.pi / 4, math.pi / 4, 3 * math.pi / 4, -3 * math.pi / 4)[k] + (
            0 if is_hold else -math.pi / 4
        )
        m = _RADIUS * _TOUCH_IN * 1.3
        h_ang = p + math.pi
        tip = (cx + math.cos(h_ang) * m * _SS, cy + math.sin(h_ang) * m * _SS)
        left = (
            cx + math.cos(p + math.pi / 2) * m * _SS,
            cy + math.sin(p + math.pi / 2) * m * _SS,
        )
        right = (
            cx + math.cos(p - math.pi / 2) * m * _SS,
            cy + math.sin(p - math.pi / 2) * m * _SS,
        )
        corner = _RADIUS * (8 / 300) * _SS
        outer = _rounded_tri_points(tip, left, right, corner)
        # 内三角（非 hold）：顶点向质心收缩 40%，圆角 0.4×
        gx = (tip[0] + left[0] + right[0]) / 3
        gy = (tip[1] + left[1] + right[1]) / 3
        inner_pts = [
            (gx + (q[0] - gx) * 0.4, gy + (q[1] - gy) * 0.4) for q in (tip, left, right)
        ]
        inner = _rounded_tri_points(*inner_pts, corner * 0.4)

        if layer_kind == "sb":
            # 投影（shadow 层 L6798）：canvas shadowBlur=8/300·R 是设备像素
            # （不受 CTM 影响、且 blur≈2σ），offset=2/300·R；换到 2× 超采样画布
            blur_r = _RADIUS * (8 / 300) * _SS / 2
            off = _RADIUS * (2 / 300) * _SS
            sil = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ImageDraw.Draw(sil).polygon(outer, fill=(0, 0, 0, 255))
            shadow = sil.filter(ImageFilter.GaussianBlur(blur_r))
            shadow.putalpha(shadow.getchannel("A").point(lambda v: int(v * 0.5)))
            img.alpha_composite(shadow, (round(off), round(off)))
            d.line(
                [*outer, outer[0]],
                fill=(*C_BLACK, 255),
                width=round(sw * 3 * _SS),
                joint="curve",
            )
            if not is_hold:
                d.line(
                    [*inner, inner[0]],
                    fill=(*C_BLACK, 255),
                    width=round(sw * 3 * _SS),
                    joint="curve",
                )
        elif layer_kind == "f":
            bbox = (
                max(0, int(min(x for x, _ in outer) - 2)),
                max(0, int(min(y for _, y in outer) - 2)),
                min(size, int(max(x for x, _ in outer) + 3)),
                min(size, int(max(y for _, y in outer) + 3)),
            )
            if bbox[2] > bbox[0] and bbox[3] > bbox[1]:
                # 非空心语义：外三角填充；空心（非 hold）：外正 + 内反 = 环
                mask = _poly_mask(
                    (bbox[2] - bbox[0], bbox[3] - bbox[1]),
                    [(x - bbox[0], y - bbox[1]) for x, y in outer],
                    None if is_hold else [(x - bbox[0], y - bbox[1]) for x, y in inner],
                )
                if is_hold:
                    stops = (
                        (0.0, C_TOUCHHOLD_PETALS[k][0]),
                        (0.35, C_TOUCHHOLD_PETALS[k][1]),
                        (0.72, C_TOUCHHOLD_PETALS[k][2]),
                        (1.0, C_TOUCHHOLD_PETALS[k][3]),
                    )
                    g0, g1 = (cx, cy), tip
                elif kind == "s":
                    stops = ((0.0, C_TOUCH_SIMUL_Y), (1.0, C_TOUCH_SIMUL_G))
                    g0, g1 = (cx, cy), tip
                else:
                    stops = ((0.0, C_TOUCH_CYAN), (1.0, C_TOUCH_BLUE))
                    g0, g1 = (cx, cy), tip
                grad = _multi_stop_gradient(
                    mask.size,
                    g0[0] - bbox[0],
                    g0[1] - bbox[1],
                    g1[0] - bbox[0],
                    g1[1] - bbox[1],
                    stops,
                )
                img.paste(grad, (bbox[0], bbox[1]), mask)
        else:  # w
            d.line(
                [*outer, outer[0]],
                fill=(*C_WHITE, 255),
                width=max(1, round(sw * _SS)),
                joint="curve",
            )
            if not is_hold:
                d.line(
                    [*inner, inner[0]],
                    fill=(*C_WHITE, 255),
                    width=max(1, round(sw * _SS)),
                    joint="curve",
                )
        self.petals[key] = img
        return img


def _multi_stop_gradient(
    size: tuple[int, int],
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    stops: tuple,
) -> Image.Image:
    """多停点线性渐变（createLinearGradient + addColorStop 序列）。"""
    w, h = size
    ax, ay = x1 - x0, y1 - y0
    len2 = ax * ax + ay * ay or 1.0
    yy, xx = np.mgrid[0:h, 0:w]
    t = np.clip(((xx - x0) * ax + (yy - y0) * ay) / len2, 0, 1)
    lut = np.zeros((256, 3), dtype=np.uint8)
    for i in range(len(stops) - 1):
        (ta, ca), (tb, cb) = stops[i], stops[i + 1]
        lo, hi = round(ta * 255), round(tb * 255)
        for kk in range(lo, hi + 1):
            f = (kk - lo) / max(hi - lo, 1)
            lut[kk] = [ca[j] + (cb[j] - ca[j]) * f for j in range(3)]
    lut[0], lut[255] = stops[0][1], stops[-1][1]
    idx = np.round(t * 255).astype(np.int32).clip(0, 255)
    arr = np.dstack([lut[idx], np.full((h, w), 255, np.uint8)])
    return Image.fromarray(arr, "RGBA")


class _Renderer:
    """渲染器（U 基类 + 各渲染器合流；方法注释 L* = JS 源码行号）。"""

    def __init__(
        self,
        notes: list[_Note],
        site: SiteChart,
        hit_notes: list[tuple[int, float, bool]],
    ):
        self.notes = notes
        self.site = site
        self.sw = _STROKE * _RADIUS  # note 描边宽（logical px）
        self.slides = [n for n in notes if n.kind == "slide"]
        self.holds = [n for n in notes if n.kind == "hold"]
        self.taps = [n for n in notes if n.kind == "tap"]
        self.touches = [n for n in notes if n.kind in ("touch", "touch-hold")]
        # hitEffectNotes（L8268）：带数字位的非 touch/非 hold-start 事件
        # （tap/break/simultaneous、hold-end、非 headless slide），按时刻升序
        self.hit_notes = sorted(hit_notes, key=lambda item: item[1])
        # 同刻分组（calculateSimultaneousCounts L8191，按 timingMs.toFixed(3)）
        self._simul_groups: dict[float, list[_Note]] = {}
        for n in notes:
            if n.has_delay_marker:  # JS L8193：反引号 pseudo-each 不参与同刻分组
                continue
            self._simul_groups.setdefault(round(n.timing, 3), []).append(n)
        # touch-hold-end 是独立 note（L8193 不跳过），参与同刻计数
        for note in self.touches:
            if note.kind != "touch-hold" or note.duration <= 0:
                continue
            end = round(note.timing + note.duration, 3)
            self._simul_groups.setdefault(end, []).append(_Note("touch-end", 0, 0.0))
        for group in self._simul_groups.values():
            plain = [
                n
                for n in group
                if (n.kind == "tap" and not n.is_break)
                or n.kind in ("hold", "slide", "touch", "touch-hold", "touch-end")
            ]
            breaks = [
                n for n in group if n.is_break and n.kind in ("tap", "hold", "slide")
            ]
            is_simul = len(plain) >= 2 or (len(breaks) >= 1 and len(plain) >= 1)
            slide_count = sum(1 for n in group if n.kind == "slide")
            for n in group:
                n.is_each = is_simul
                n.simul_slide_count = slide_count
        # 完成时刻表（noteCompletionTimes/breakCompletionTimes L8251）
        self.completions = note_completion_times(site.notes)
        self.break_completions = break_completion_times(site.notes)
        self.noex_break_completions = break_noex_completion_times(site.notes)
        self.sprites = _Sprites()
        self._fonts: dict[int, object] = {}

    # ------------------------------------------------------------ 通用
    def button_position(self, pos: int) -> tuple[float, float]:
        a = _button_angle(pos)
        return _CENTER + math.cos(a) * _RADIUS, _CENTER + math.sin(a) * _RADIUS

    def count_done(self, times: list[float], ms: float) -> int:
        import bisect

        return bisect.bisect_right(times, ms)

    # ------------------------------------------------------------ tap（ne 类）
    def draw_tap(self, c: _Canvas, note: _Note, pos: _Pos) -> None:
        """renderTapNote L439：sprite 显示尺寸 ×scale（接近期缩小，非 alpha）。"""
        x, y = c.s(pos.x), c.s(pos.y)
        if note.is_ex:
            # EX 覆盖环画在 scale=1 的 sprite 内（半径 f..1.19f），sprite 整体再按
            # note scale 缩放（renderTapNote L439-449）→ 显示环 = [f·s, 1.19f·s]
            f = _RADIUS * _R_NOTE * 1.36
            s = pos.alpha
            r0, r1 = f * s, _EX_ALPHA * f * s
            if r1 > r0:
                color = (
                    C_EX_BREAK
                    if note.is_break
                    else C_EX_SIMUL
                    if note.is_each
                    else C_EX_NORMAL
                )
                lay = Image.new("RGBA", c.img.size, (0, 0, 0, 0))
                ImageDraw.Draw(lay).ellipse(
                    (x - r1 * _SS, y - r1 * _SS, x + r1 * _SS, y + r1 * _SS),
                    fill=color,
                )
                hole = Image.new("L", c.img.size, 0)
                ImageDraw.Draw(hole).ellipse(
                    (x - r0 * _SS, y - r0 * _SS, x + r0 * _SS, y + r0 * _SS),
                    fill=255,
                )
                lay.putalpha(ImageChops_darker(lay.getchannel("A"), hole))
                c.img.alpha_composite(lay)
        sprite = self.sprites.tap(note.pos, note.is_break, note.is_each, note.is_ex)
        if abs(pos.alpha - 1) > 1e-3:
            new_size = max(2, round(sprite.width * pos.alpha))
            if new_size % 2 != sprite.width % 2:
                new_size += 1
            sprite = sprite.resize((new_size, new_size), Image.BILINEAR)
        c.img.alpha_composite(
            sprite, (int(x - sprite.width / 2), int(y - sprite.height / 2))
        )

    def draw_approach_arc(
        self, c: _Canvas, pos_btn: int, dist: float, color: tuple
    ) -> None:
        """renderApproachArc（L333，批量版 L345 同绘制）：±π/8 主弧 + 4 段
        渐隐尾，线宽 sw（全宽）。"""
        angle = _button_angle(pos_btn)
        seg = math.pi / 8 / 4
        for k in range(4, -1, -1):
            alpha = 0.4 if k == 0 else 0.4 * (1 - k / 5)
            col = (*color, int(255 * alpha))
            if k == 0:
                c.line(
                    c.arc_points(
                        c.cx, c.cy, dist, angle - math.pi / 8, angle + math.pi / 8
                    ),
                    col,
                    self.sw,
                )
            else:
                c.line(
                    c.arc_points(
                        c.cx,
                        c.cy,
                        dist,
                        angle - math.pi / 8 - k * seg,
                        angle - math.pi / 8 - (k - 1) * seg,
                    ),
                    col,
                    self.sw,
                )
                a0 = angle + math.pi / 8 + (k - 1) * seg
                c.line(
                    c.arc_points(c.cx, c.cy, dist, a0, angle + math.pi / 8 + k * seg),
                    col,
                    self.sw,
                )

    def draw_tap_approach_arcs(self, c: _Canvas, taps: list[_Note], now: float) -> None:
        """renderTapApproachArcs L8506：tap 批量彗星尾弧（颜色 L8532）。"""
        for n in taps:
            if n.timing <= now:
                continue
            pos = _note_position(n, now, c.cx, c.cy, c.radius)
            if not pos.visible:
                continue
            color = (
                C_BREAK_DOT
                if n.is_break
                else C_SIMUL_DOT
                if n.is_each
                else C_SLIDE_CYAN
                if n.is_star
                else C_TAP_DOT
            )
            self.draw_approach_arc(
                c, n.pos, math.hypot(pos.x - c.cx, pos.y - c.cy), color
            )

    def draw_connector(self, c: _Canvas, pos_a: int, pos_b: int, dist: float) -> None:
        """renderSimultaneousConnector L378：min→max 角弧（|Δ|=4 整圆），GOLD。"""
        a0 = _button_angle(pos_a)
        a1 = _button_angle(pos_b)
        delta = pos_b - pos_a
        while delta > 4:
            delta -= 8
        while delta < -4:
            delta += 8
        col = (*C_SIMUL_DOT, 178)  # α0.7
        lay = Image.new("RGBA", c.img.size, (0, 0, 0, 0))
        ld = ImageDraw.Draw(lay, "RGBA")
        if abs(delta) == 4:
            pts = c.arc_points(c.cx, c.cy, dist, 0, math.tau)
        else:
            lo, hi = (a0, a1) if delta > 0 else (a1, a0)
            if hi < lo:
                hi += math.tau
            pts = c.arc_points(c.cx, c.cy, dist, lo, hi)
        ld.line(c.pts(pts), fill=col, width=max(1, round(c.s(self.sw))), joint="curve")
        c.img.alpha_composite(lay)

    # ------------------------------------------------------------ hold（Ce 类 L6429）
    def draw_hold(self, c: _Canvas, note: _Note, now: float) -> None:
        head = _note_position(note, now, c.cx, c.cy, c.radius)
        tail_note = _Note("tap", note.pos, note.timing + note.duration)
        tail = _note_position(tail_note, now, c.cx, c.cy, c.radius)
        angle = _button_angle(note.pos)
        m = c.radius * _R_NOTE * _B_HOLD
        h = m * head.alpha if head.visible else 0.0
        # 尾宽 j：头未进入后半接近窗时渐长（renderHold L6464 j 逻辑，基于头 timing）
        start_dt = note.timing - now
        j = 1.0
        if start_dt > _HALF_APPROACH:
            j = max(0.0, 1 - (start_dt - _HALF_APPROACH) / _HALF_APPROACH)
        m_tail = m * j
        if head.visible:
            hx, hy = head.x, head.y
        else:
            hx, hy = (
                c.cx + math.cos(angle) * c.radius * _C_TRAVEL,
                c.cy + math.sin(angle) * c.radius * _C_TRAVEL,
            )
        if tail.visible:
            tx, ty = tail.x, tail.y
        else:
            tx, ty = (
                c.cx + math.cos(angle) * c.radius * _C_TRAVEL,
                c.cy + math.sin(angle) * c.radius * _C_TRAVEL,
            )
        c0, c1 = _grad_pair(note.is_break, note.is_each)
        active = head.visible and note.timing <= now < note.timing + note.duration

        def hv(cx0, cy0, half, base_offset):
            base = angle + base_offset
            return (cx0 + math.cos(base) * half, cy0 + math.sin(base) * half)

        OH = hv(hx, hy, h, 0)
        K = hv(hx, hy, h, math.pi / 3)
        A2 = hv(hx, hy, h, -math.pi / 3)
        N = hv(tx, ty, m_tail, math.pi)
        F = hv(tx, ty, m_tail, math.pi - math.pi / 3)
        L = hv(tx, ty, m_tail, math.pi + math.pi / 3)
        z = hv(hx, hy, h * _INNER_HEX, 0)
        B = hv(hx, hy, h * _INNER_HEX, math.pi / 3)
        A3 = hv(hx, hy, h * _INNER_HEX, -math.pi / 3)
        w1 = hv(tx, ty, m_tail * _INNER_HEX, math.pi)
        w2 = hv(tx, ty, m_tail * _INNER_HEX, math.pi - math.pi / 3)
        w3 = hv(tx, ty, m_tail * _INNER_HEX, math.pi + math.pi / 3)
        outer = [OH, K, F, N, L, A2]
        inner = [z, B, w2, w1, w3, A3]
        if note.is_ex:
            # EX 覆盖环顶点序 O,k,F,N,L,A（L6519）
            overlay = (
                C_EX_BREAK
                if note.is_break
                else C_EX_SIMUL
                if note.is_each
                else C_EX_NORMAL
            )
            t19 = _EX_ALPHA
            ex = [
                hv(hx, hy, h * t19, 0),
                hv(hx, hy, h * t19, math.pi / 3),
                hv(tx, ty, m_tail * t19, math.pi - math.pi / 3),
                hv(tx, ty, m_tail * t19, math.pi),
                hv(tx, ty, m_tail * t19, math.pi + math.pi / 3),
                hv(hx, hy, h * t19, -math.pi / 3),
            ]
            c.polygon(ex, fill=overlay)
        bw = self.sw * 3
        if not note.is_ex:
            c.polygon(outer, outline=C_BLACK, width=bw)
        c.polygon(inner, outline=C_BLACK, width=bw)
        # 六面渐变：轴 os→oe（外边端点），颜色受呼吸 p() 调制（L6527/6567）
        flick = _hold_flicker(now - note.timing) if active else 1.0
        g_start = _brightness(_mix(c0, C_WHITE, _P_MIX), flick)
        g_end = _brightness(c1, flick)
        sides = [
            (OH, K, z, B),
            (K, F, B, w2),
            (F, N, w2, w1),
            (N, L, w1, w3),
            (L, A2, w3, A3),
            (A2, OH, A3, z),
        ]
        for os_, oe_, is_, ie_ in sides:
            self._quad_gradient(c, os_, oe_, is_, ie_, g_start, g_end)
        white = _brightness(C_WHITE, flick)
        c.polygon(outer, outline=white, width=self.sw)
        c.polygon(inner, outline=white, width=self.sw)
        if active:
            self._head_glow(c, hx, hy, c0)
        else:
            c.fill_circle(hx, hy, h * 0.15, c0)
        # 尾端点（尾进入后半接近窗后出现，填 p(r[0])，L6575-6580）
        if (
            tail.visible
            and note.duration
            and note.timing + note.duration - now <= _HALF_APPROACH
        ):
            c.fill_circle(tx, ty, m_tail * 0.15, _brightness(c0, flick))
        # 进行中按压涟漪在特效阶段统一绘制（renderNotes L8138，见 render_frame）

    def _quad_gradient(self, c: _Canvas, os_, oe_, is_, ie_, c0, c1) -> None:
        path = c.pts([os_, oe_, ie_, is_])
        bbox = (
            int(min(x for x, _ in path) - 2),
            int(min(y for _, y in path) - 2),
            int(max(x for x, _ in path) + 2),
            int(max(y for _, y in path) + 2),
        )
        bbox = (
            max(0, bbox[0]),
            max(0, bbox[1]),
            min(c.img.width, bbox[2]),
            min(c.img.height, bbox[3]),
        )
        if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            return
        mask = Image.new("L", (bbox[2] - bbox[0], bbox[3] - bbox[1]), 0)
        ImageDraw.Draw(mask).polygon(
            [(x - bbox[0], y - bbox[1]) for x, y in path], fill=255
        )
        gx0, gy0 = c.s(os_[0]), c.s(os_[1])  # 轴 = 外边两端点（L6567）
        gx1, gy1 = c.s(oe_[0]), c.s(oe_[1])
        grad = _linear_gradient(
            mask.size,
            gx0 - bbox[0],
            gy0 - bbox[1],
            gx1 - bbox[0],
            gy1 - bbox[1],
            c0,
            c1,
        )
        c.img.paste(grad, (bbox[0], bbox[1]), mask)

    def _head_glow(self, c: _Canvas, x: float, y: float, color: tuple) -> None:
        """drawActiveHeadGlow（L6584）：E 表径向渐变。"""
        table = (
            (0, 0.99, 1.11, 1.29),
            (0.1, 0.99, 0.98, 1.04),
            (0.2, 0.99, 0.79, 0.72),
            (0.3, 0.98, 0.63, 0.62),
            (0.4, 0.93, 0.49, 0.62),
            (0.5, 0.76, 0.28, 0.43),
            (0.6, 0.54, 0.08, 0.13),
            (0.7, 0.41, 0.02, 0.05),
            (0.8, 0.3, 0.01, 0.04),
            (0.9, 0.24, 0.02, 0.05),
        )
        r = c.s(_T_GLOW * c.radius)
        if r < 1:
            return
        size = int(r * 2) + 2
        yy, xx = np.mgrid[0:size, 0:size]
        dist = np.hypot(xx - size / 2, yy - size / 2) / r
        arr = np.zeros((size, size, 4), dtype=np.uint8)
        for i in range(len(table) - 1):
            t0, r0, g0, b0 = table[i]
            t1, r1, g1, b1 = table[i + 1]
            msk = (dist >= t0) & (dist <= t1)
            frac = (dist[msk] - t0) / (t1 - t0 + 1e-9)
            arr[msk, 0] = np.round(color[0] * (r0 + (r1 - r0) * frac)).clip(0, 255)
            arr[msk, 1] = np.round(color[1] * (g0 + (g1 - g0) * frac)).clip(0, 255)
            arr[msk, 2] = np.round(color[2] * (b0 + (b1 - b0) * frac)).clip(0, 255)
        # 表覆盖 0..0.9；0.9..1.0 rgb 与 α 同步向 0 插值（canvas rgba stop 语义）
        fade = np.clip((1.0 - dist) / 0.1, 0.0, 1.0)
        fade = np.where(dist <= 0.9, 1.0, fade)
        arr[..., :3] = (arr[..., :3].astype(np.float32) * fade[..., None]).astype(
            np.uint8
        )
        arr[..., 3] = np.round(255 * fade).astype(np.uint8)
        glow = Image.fromarray(arr, "RGBA")
        c.img.alpha_composite(glow, (int(x * _SS - size / 2), int(y * _SS - size / 2)))

    # ------------------------------------------------ 按压涟漪（it 类 L7186）
    def draw_press_ripple(
        self,
        c: _Canvas,
        x: float,
        y: float,
        start_ms: float,
        end_ms: float,
        now: float,
    ) -> None:
        """renderPressRippleAt + drawRipples + drawRippleRing（L7196-7241）。

        每环 = [0.8r, 1.2r] 径向渐变环带（tt 16 停点轮廓，色向白偏移
        et·v·l²），screen 混合——非描边圆。
        """
        if now < start_ms or now >= end_ms or c.radius <= 0:
            return
        elapsed = (now - start_ms) / 1000
        rings = []
        k0 = math.floor((elapsed - _RIPPLE_QE) / _RIPPLE_GE)
        k1 = math.floor(elapsed / _RIPPLE_GE)
        for k in range(k0, k1 + 1):
            for offset in _RIPPLE_KE:
                n = elapsed - (k * _RIPPLE_GE + offset)
                if 0 <= n < _RIPPLE_QE:
                    radius = (_RIPPLE_YE + _RIPPLE_XE * n) * c.radius
                    alpha = min(1.0, (_RIPPLE_QE - n) / (1 / 6)) * _RIPPLE_JE
                    if radius >= 0.75 and alpha > _RIPPLE_MIN_ALPHA:
                        rings.append((radius, alpha))
        if not rings:
            return
        rings.sort(key=lambda t: -t[0])
        # 逐环 screen 叠加（JS drawRipples L7203-7241：环之间会重叠，叠加是
        # 1-(1-a1)(1-a2)，不能合并成单层取 max）
        cx, cy = c.s(x), c.s(y)
        base = np.array(C_RIPPLE, dtype=np.float32)
        for radius, alpha in rings:
            r_in = radius * _RIPPLE_ZE
            r_out = radius * _RIPPLE_QE_OUT
            if r_out - r_in < 0.5:
                continue
            region = int(r_out * _SS) + 4
            x0, y0 = int(max(0, cx - region)), int(max(0, cy - region))
            x1, y1 = (
                int(min(c.img.width, cx + region)),
                int(min(c.img.height, cy + region)),
            )
            if x1 <= x0 or y1 <= y0:
                continue
            w, h = x1 - x0, y1 - y0
            yy, xx = np.mgrid[0:h, 0:w]
            dist = np.hypot(xx - (cx - x0), yy - (cy - y0)) / _SS  # logical
            t = (dist - r_in) / (r_out - r_in)
            msk = (t >= 0) & (t <= 1)
            if not msk.any():
                continue
            v = np.zeros_like(t)
            v[msk] = [_lerp_stops(_RIPPLE_PROFILE, tv) for tv in t[msk]]
            n_off = _RIPPLE_ET * v * alpha * alpha  # l = a/Je = alpha
            a_px = v * alpha
            col = np.zeros((h, w, 3), dtype=np.float32)
            for ch in range(3):
                col[..., ch] = base[ch] + (255 - base[ch]) * n_off
            arr = np.dstack(
                [
                    col.clip(0, 255).astype(np.uint8),
                    (a_px.clip(0, 1) * 255).astype(np.uint8),
                ]
            )
            _screen_composite(c.img, Image.fromarray(arr, "RGBA"), (x0, y0, x1, y1))


def ImageChops_darker(a: Image.Image, b: Image.Image) -> Image.Image:
    from PIL import ImageChops

    return ImageChops.darker(a, b)


class _SlideRenderer:
    """slide 渲染（xe 类 L5557 + renderSlidePath L5670 + drawStar L6193）。"""

    def __init__(self, renderer: _Renderer):
        self.r = renderer
        self._chain_cache: dict[tuple, list] = {}
        self._lut_cache: dict[tuple, list] = {}
        self._metrics_cache: dict[tuple, float] = {}

    # ---- getBarChain（L5580）：段 → 旋转后锚点列 [{x,y,angle}]
    def bar_chain(self, seg: SlideSegment) -> list | None:
        shape_op, start, end, mid = seg.type, seg.start_pos, seg.end_pos, seg.mid_pos
        key = (shape_op, start, end, mid)
        cached = self._chain_cache.get(key)
        if cached is not None:
            return cached
        mapped = self._shape_map(shape_op, start, end, mid)
        if not mapped:
            self._chain_cache[key] = None
            return None
        shape, mirror = mapped
        pts = SLIDE_POINTS.get(shape)
        if not pts:
            self._chain_cache[key] = None
            return None
        # rot = (startPos − +!mirror)·π/4（L5582）：非镜像减 1、镜像减 0
        rot = (start - (0 if mirror else 1)) * math.pi / 4
        cos_i, sin_i = math.cos(rot), math.sin(rot)
        is_circle = shape.startswith("circle")
        is_line = shape.startswith("line")
        x0 = -pts[0][0] if mirror else pts[0][0]
        y0 = pts[0][1]
        dx = (-pts[-1][0] if mirror else pts[-1][0]) - x0
        dy = pts[-1][1] - y0
        v = dx * dx + dy * dy or 1.0
        chain = []
        for px, py, pr in pts:
            nx = -px if mirror else px
            ny = py
            if is_circle:
                ang = round(math.atan2(ny, nx) * 32 / math.pi) * (math.pi / 32)
                nx, ny = math.cos(ang), math.sin(ang)
            elif is_line:
                t = ((nx - x0) * dx + (ny - y0) * dy) / v
                nx, ny = x0 + t * dx, y0 + t * dy
            angle = (math.pi - pr if mirror else pr) + rot
            chain.append(
                {
                    "x": _CENTER + (nx * cos_i - ny * sin_i) * _RADIUS,
                    "y": _CENTER + (nx * sin_i + ny * cos_i) * _RADIUS,
                    "angle": angle,
                }
            )
        if (shape_op in ("p", "q", "pp", "qq") or is_circle) and len(chain) >= 2:
            self._apply_game_arrow_angles(chain)
        self._chain_cache[key] = chain
        return chain

    @staticmethod
    def _shape_map(shape: str, start: int, end: int, mid: int | None):
        """ce()（L553）：形状串 → (ue 表键, 是否镜像)。"""
        dist = ((end - start) % 8 + 8) % 8 + 1
        if shape == "-":
            if dist < 3 or dist > 7:
                return None
            e = 10 - dist if dist > 5 else dist
            return (f"line{e}", e != dist)
        if shape == ">":
            flat = start in (1, 2, 7, 8)
            return (
                (f"circle{dist}", False)
                if flat
                else (f"circle{_mirror_pair(dist)}", True)
            )
        if shape == "<":
            flat = start in (1, 2, 7, 8)
            return (
                (f"circle{_mirror_pair(dist)}", True)
                if flat
                else (f"circle{dist}", False)
            )
        if shape == "^":
            if dist in (1, 5):
                return None
            return (
                (f"circle{dist}", False)
                if dist < 5
                else (f"circle{_mirror_pair(dist)}", True)
            )
        if shape == "v":
            return None if dist == 5 else (f"v{dist}", False)
        if shape == "pp":
            return (f"ppqq{dist}", False)
        if shape == "qq":
            return (f"ppqq{_mirror_pair(dist)}", True)
        if shape == "p":
            return (f"pq{dist}", False)
        if shape == "q":
            return (f"pq{_mirror_pair(dist)}", True)
        if shape == "s":
            return ("s", False) if dist == 5 else None
        if shape == "z":
            return ("s", True) if dist == 5 else None
        if shape == "V":
            if mid is None:
                return None
            e = (start + 5) % 8 + 1
            n = (start + 1) % 8 + 1
            if mid == e and 2 <= dist <= 5:
                return (f"L{dist}", False)
            a = (8 - (dist - 1)) % 8 + 1
            if mid == n and 2 <= a <= 5:
                return (f"L{a}", True)
            return None
        if shape == "w":
            return ("wifi", False)
        return None

    @staticmethod
    def _angle_delta(a: float, b: float) -> float:
        d = (b - a) % math.tau
        if d > math.pi:
            d -= math.tau
        if d < -math.pi:
            d += math.tau
        return d

    def _lerp_angle(self, a: float, b: float, t: float) -> float:
        return a + self._angle_delta(a, b) * t

    def _apply_game_arrow_angles(self, chain: list[dict]) -> None:
        """applyGameArrowAngles L5625：p/q/pp/qq/circle 的弦切角 lerp。"""
        n = len(chain)
        raw = [0.0] * n
        for i in range(n - 1):
            raw[i] = math.atan2(
                chain[i + 1]["y"] - chain[i]["y"], chain[i + 1]["x"] - chain[i]["x"]
            )
        raw[n - 1] = raw[n - 2]
        chain[0]["angle"] = raw[0]
        for i in range(1, n - 1):
            # JS L5630：以上一个**已更新**角度为基准插值，不是原表角度
            chain[i]["angle"] = self._lerp_angle(chain[i - 1]["angle"], raw[i], 0.5)
        chain[n - 1]["angle"] = raw[n - 1]

    # ---- getHiddenCount（L5882）：le 关键帧 → 已被星星吃掉的箭头数
    def hidden_count(self, seg: SlideSegment, progress: float) -> int:
        mapped = self._shape_map(seg.type, seg.start_pos, seg.end_pos, seg.mid_pos)
        kf = SLIDE_KEYFRAMES.get(mapped[0], [0]) if mapped else [0]
        if len(kf) < 2:
            return 0
        index = kf[min(len(kf) - 1, int(progress * (len(kf) - 1)))]
        if seg.type == "w" and progress > 0:
            return index + 1
        return index

    # ---- getVisibleBarsForSegment（L5915）：可见箭头列（外推优先）
    def visible_bars(
        self, seg: SlideSegment, hidden: int, extend: bool
    ) -> list[dict] | None:
        if seg.type == "w":
            return None
        chain = self.bar_chain(seg)
        if not chain:
            return None
        use_chain_angle = seg.type != "-"
        bars: list[dict] = []
        o = len(chain) - 1
        if extend and len(chain) >= 2 and hidden < len(chain):
            last, prev = chain[o], chain[o - 1]
            nx, ny = 2 * last["x"] - prev["x"], 2 * last["y"] - prev["y"]
            if math.hypot(nx - _CENTER, ny - _CENTER) <= _RADIUS:
                ang = (
                    last["angle"]
                    if use_chain_angle
                    else math.atan2(last["y"] - prev["y"], last["x"] - prev["x"])
                )
                bars.append({"x": nx, "y": ny, "angle": ang})
        for i in range(o, hidden - 1, -1):
            if use_chain_angle:
                ang = chain[i]["angle"]
            else:
                lo, hi = chain[max(0, i - 1)], chain[min(o, i + 1)]
                ang = math.atan2(hi["y"] - lo["y"], hi["x"] - lo["x"])
            bars.append({"x": chain[i]["x"], "y": chain[i]["y"], "angle": ang})
        return bars

    # ---- buildArrowPaths（L6032）：单支箭头 主/影/左半
    @staticmethod
    def arrow_paths(bar: dict) -> tuple[list, list, list] | None:
        t = _ARROW_BACK * _RADIUS
        n = _ARROW_LEN * _RADIUS
        r = _ARROW_W * _RADIUS
        i = _ARROW_TIP * _RADIUS
        a = _SHADOW_OFF * _RADIUS
        e, c = math.cos(bar["angle"]), math.sin(bar["angle"])
        lx, uy = bar["x"] + e * i, bar["y"] + c * i
        dx = lx - e * n / 2 + c * t / 2
        dy = uy - c * n / 2 - e * t / 2
        px, my = lx + e * n / 2, uy + c * n / 2
        hx = lx - e * n / 2 - c * t / 2
        gy = uy - c * n / 2 + e * t / 2
        bis = _SlideRenderer._bisector(dx, dy, px, my, hx, gy, r)
        if not bis:
            return None
        bx, by = bis
        b2x, b2y = bx + hx - px, by + gy - my
        s2x, s2y = bx + dx - px, by + dy - my
        main = [(px, my), (hx, gy), (b2x, b2y), (bx, by), (s2x, s2y), (dx, dy)]
        shadow = [
            (px - e * a, my - c * a),
            (hx - e * a, gy - c * a),
            (b2x - e * a, b2y - c * a),
            (bx - e * a, by - c * a),
            (s2x - e * a, s2y - c * a),
            (dx - e * a, dy - c * a),
        ]
        left = [(px, my), (bx, by), (b2x, b2y), (hx, gy)]
        return main, shadow, left

    @staticmethod
    def _bisector(x1, y1, x2, y2, x3, y3, offset):
        s = math.hypot(x1 - x2, y1 - y2)
        c = math.hypot(x3 - x2, y3 - y2)
        if s == 0 or c == 0:
            return None
        dot = max(
            -1.0, min(1.0, ((x1 - x2) * (x3 - x2) + (y1 - y2) * (y3 - y2)) / (s * c))
        )
        u = math.sqrt((1 - dot) / 2)
        if u <= 0.001:
            return None
        mx, my = (x1 + x3) / 2, (y1 + y3) / 2
        p = math.hypot(mx - x2, my - y2)
        if p == 0:
            return None
        g = offset / u
        return (x2 + (mx - x2) / p * g, y2 + (my - y2) / p * g)

    # ---- getPointOnSegment（L6338）：节点索引空间参数化
    def point_on_segment(self, seg: SlideSegment, t: float) -> tuple[float, float]:
        chain = self.bar_chain(seg)
        if chain and len(chain) >= 2:
            start = self.r.button_position(seg.start_pos)
            end = self.r.button_position(seg.end_pos)
            a = len(chain) + 2
            o = max(0.0, min(a - 1.0, t * (a - 1)))
            i = min(a - 2, int(o))
            f = o - i

            def node(k: int) -> tuple[float, float]:
                if k == 0:
                    return start
                if k == a - 1:
                    return end
                return (chain[k - 1]["x"], chain[k - 1]["y"])

            u, d = node(i), node(i + 1)
            return (u[0] + (d[0] - u[0]) * f, u[1] + (d[1] - u[1]) * f)
        start = self.r.button_position(seg.start_pos)
        end = self.r.button_position(seg.end_pos)
        return (
            start[0] + (end[0] - start[0]) * t,
            start[1] + (end[1] - start[1]) * t,
        )

    # ---- buildArcLut（L5952）：64 均匀参数采样 + 弧长累计
    def segment_lut(self, seg: SlideSegment, steps: int = 64) -> list[dict]:
        key = (seg.type, seg.start_pos, seg.end_pos, seg.mid_pos)
        cached = self._lut_cache.get(key)
        if cached:
            return cached
        lut = []
        acc = 0.0
        prev = None
        for k in range(steps + 1):
            p = self.point_on_segment(seg, k / steps)
            if prev is not None:
                acc += math.dist(prev, p)
            lut.append({"x": p[0], "y": p[1], "s": acc})
            prev = p
        self._lut_cache[key] = lut
        return lut

    @staticmethod
    def _sample_lut(lut: list[dict], target: float) -> tuple[float, float]:
        if target <= 0:
            return lut[0]["x"], lut[0]["y"]
        last = lut[-1]
        if target >= last["s"]:
            return last["x"], last["y"]
        for i in range(len(lut) - 1):
            if lut[i]["s"] <= target <= lut[i + 1]["s"]:
                span = lut[i + 1]["s"] - lut[i]["s"]
                f = (target - lut[i]["s"]) / span if span > 0 else 0.0
                return (
                    lut[i]["x"] + (lut[i + 1]["x"] - lut[i]["x"]) * f,
                    lut[i]["y"] + (lut[i + 1]["y"] - lut[i]["y"]) * f,
                )
        return last["x"], last["y"]

    def segment_length(self, seg: SlideSegment) -> float:
        key = (seg.type, seg.start_pos, seg.end_pos, seg.mid_pos)
        cached = self._metrics_cache.get(key)
        if cached is None:
            cached = self.segment_lut(seg)[-1]["s"]
            self._metrics_cache[key] = cached
        return cached

    # ---- getSlidePathMetrics（L5706）：多段路径按弧长的进度区间
    def path_ranges(
        self, segments: list[SlideSegment]
    ) -> list[tuple[float, float]] | None:
        lengths = [self.segment_length(s) for s in segments]
        total = sum(lengths)
        if total <= 0:
            return None
        ranges = []
        acc = 0.0
        for length in lengths:
            ranges.append((acc / total, (acc + length) / total))
            acc += length
        return ranges

    def point_along(
        self, t: float, segments: list[SlideSegment]
    ) -> tuple[float, float]:
        """getPointAlongPath（L6362）：跨段弧长插值。"""
        if not segments:
            return (_CENTER, _CENTER)
        luts = [self.segment_lut(s) for s in segments]
        lengths = [lut[-1]["s"] for lut in luts]
        total = sum(lengths)
        if total == 0:
            return self.r.button_position(segments[0].start_pos)
        target = t * total
        acc = 0.0
        for lut, length in zip(luts, lengths):
            if acc + length >= target:
                return self._sample_lut(lut, target - acc)
            acc += length
        return self.r.button_position(segments[-1].end_pos)

    # ---- 轨道透明度 ge()（L5527；config.slideDelay=0）
    def _star_delay(self, note: _Note) -> float:
        """me()（L5514）：轨道淡入起点。"""
        r = round((_SLIDE_DELAY + 1) * 10)
        return note.timing - _APPROACH_MS + _APPROACH_MS * r / 21

    def track_alpha(self, note: _Note, now: float, fading: bool) -> float:
        """ge()（L5527）：fading=wifi 段（视作 split 档）。"""
        r = self._star_delay(note)
        if fading and now <= r:
            return 0.0
        if now + _DELAY_FADE >= note.timing:
            return (120 / 255) if fading else 1.0
        i = now - r
        if i <= 0:
            return 0.0
        if fading and i > _TRAIL_RAMP:
            return 0.5 if (note.timing - now + _TRAIL_RAMP <= now) else 120 / 255
        a = note.timing - r
        if fading or a >= _TRAIL_RAMP:
            return min(0.5, 0.5 * i / _TRAIL_RAMP)
        return max(0.0, min(0.5, 0.5 * (1 - (note.timing - now) / i)))

    # ---- getRenderPathIndexes + renderSlide（L5556/5656）：去重 + wifi 先行
    def ordered_parts(self, note: _Note) -> list[SlidePart]:
        seen: set[tuple] = set()
        unique: list[SlidePart] = []
        for part in note.parts:
            signature = (
                tuple(
                    (seg.type, seg.start_pos, seg.end_pos, seg.mid_pos)
                    for seg in part.segments
                ),
                round(part.delay_ms, 3),
                round(part.duration_ms, 3),
                part.is_break,
            )
            if signature in seen:
                continue
            seen.add(signature)
            unique.append(part)
        wifi = [p for p in unique if p.segments and p.segments[0].type == "w"]
        rest = [p for p in unique if not (p.segments and p.segments[0].type == "w")]
        return wifi + rest

    # ---- drawSegmentArrows（L5889）：影→主填充→break 左半→黑描边
    def draw_segment_arrows(
        self,
        c: _Canvas,
        seg: SlideSegment,
        progress: float,
        alpha: float,
        is_break: bool,
        is_simul: bool,
        extend: bool,
    ) -> None:
        if seg.type == "w":
            self.draw_wifi_bars(c, seg, progress, alpha, is_break, is_simul)
            return
        hidden = self.hidden_count(seg, progress)
        bars = self.visible_bars(seg, hidden, extend)
        if not bars:
            return
        is_break_track = is_break  # normalColorBreakSlide=false
        stroke = (
            C_BREAK_DOT
            if is_break_track
            else C_SLIDE_SIMUL
            if is_simul
            else C_SLIDE_CYAN
        )
        fill_main = C_SLIDE_ARROW if is_break_track else stroke
        fill_left = C_SLIDE_SIMUL if is_break_track else None
        for bar in bars:
            paths = self.arrow_paths(bar)
            if not paths:
                continue
            main, shadow, left = paths
            c.polygon(shadow, fill=(*C_BLACK, int(102 * alpha)))
            c.polygon(main, fill=(*fill_main, int(255 * alpha)))
            if fill_left:
                c.polygon(left, fill=(*fill_left, int(255 * alpha)))
            c.polygon(main, outline=(*C_BLACK, int(255 * alpha)), width=self.r.sw)

    # ---- renderWifiBars + drawWifiChevronsBatch（L5744/6070）
    def draw_wifi_bars(
        self,
        c: _Canvas,
        seg: SlideSegment,
        progress: float,
        alpha: float,
        is_break: bool,
        is_simul: bool,
    ) -> None:
        kf = SLIDE_KEYFRAMES.get("wifi", [0, 1, 4, 6, 11])
        last_kf = kf[-1]
        hidden = self.hidden_count(seg, progress)
        a0 = self.r.button_position(seg.start_pos)
        b0 = self.r.button_position(seg.end_pos)
        length = math.hypot(b0[0] - a0[0], b0[1] - a0[1])
        if length == 0:
            return
        ux, uy = (b0[0] - a0[0]) / length, (b0[1] - a0[1]) / length
        cos_d, sin_d = math.cos(_WIFI_HALF_ANGLE), math.sin(_WIFI_HALF_ANGLE)
        m0 = 0.075 * length
        h0 = 0.975 * length
        g0 = _RADIUS * 0.075
        color = C_BREAK_DOT if is_break else C_SLIDE_SIMUL if is_simul else C_SLIDE_CYAN
        # 影（后向偏移 5/300·R，α0.4×）与主链各合成一次：JS 把全部 chevron 累进
        # 同一个 Path2D 后一次性填充（L6106-6113），逐多边形填充会让重叠处变暗
        shadow_off = _RADIUS * (5 / 300)
        shadow_polys: list[list[tuple[float, float]]] = []
        main_polys: list[list[tuple[float, float]]] = []
        for e in range(last_kf - 1, hidden - 1, -1):
            t = e / (last_kf - 1)
            nd = m0 + (h0 - m0) * _WIFI_EASE[e]
            arm_len = cos_d * nd + g0 * (1 - t)
            width = _RADIUS * _WIFI_WIDTH_BASE * (0.7 + 0.3 * t)
            tip = (a0[0] + ux * nd, a0[1] + uy * nd)
            # 手臂定义在路径局部系（+x 前向），按路径方向旋转到世界系（L6106）
            al1x, al1y = -arm_len * cos_d, arm_len * sin_d
            al2x, al2y = -arm_len * cos_d, -arm_len * sin_d
            arm1 = (ux * al1x - uy * al1y, uy * al1x + ux * al1y)
            arm2 = (ux * al2x - uy * al2y, uy * al2x + ux * al2y)
            shadow_pts = self._wifi_chevron_points(
                tip, arm1, arm2, (ux, uy), width, shadow_off
            )
            if shadow_pts:
                shadow_polys.append(shadow_pts)
            main_pts = self._wifi_chevron_points(tip, arm1, arm2, (ux, uy), width, 0.0)
            if main_pts:
                main_polys.append(main_pts)
        for polys, col, shade in (
            (shadow_polys, C_BLACK, 0.4),
            (main_polys, color, 1.0),
        ):
            if not polys:
                continue
            xs = [p[0] for poly in polys for p in poly]
            ys = [p[1] for poly in polys for p in poly]
            x0 = max(0, int(min(xs) * _SS) - 2)
            y0 = max(0, int(min(ys) * _SS) - 2)
            x1 = min(c.img.width, int(max(xs) * _SS) + 3)
            y1 = min(c.img.height, int(max(ys) * _SS) + 3)
            if x1 <= x0 or y1 <= y0:
                continue
            mask = Image.new("L", (x1 - x0, y1 - y0), 0)
            md = ImageDraw.Draw(mask)
            for poly in polys:
                md.polygon(
                    [(px * _SS - x0, py * _SS - y0) for px, py in poly], fill=255
                )
            mask = mask.point(lambda v: int(v * alpha * shade))
            lay = Image.new("RGBA", (x1 - x0, y1 - y0), (*col, 255))
            lay.putalpha(mask)
            c.img.alpha_composite(lay, (x0, y0))

    def _wifi_chevron_points(
        self,
        tip: tuple[float, float],
        arm1: tuple[float, float],
        arm2: tuple[float, float],
        direction: tuple[float, float],
        width: float,
        offset: float,
    ) -> list[tuple[float, float]] | None:
        """drawWifiChevronsBatch 内联 s()（L6077）：箭尖后掠多边形。"""
        a1x, a1y = arm1
        a2x, a2y = arm2
        p = math.hypot(a1x, a1y)
        m = math.hypot(a2x, a2y)
        if p == 0 or m == 0:
            return None
        cos_ang = max(-1.0, min(1.0, (a1x * a2x + a1y * a2y) / (p * m)))
        g = math.sqrt((1 - cos_ang) / 2)
        if g <= 0.001:
            return None
        back = -((a1x * direction[0] + a1y * direction[1]) / p)
        if back <= 0:
            return None
        v = p / back
        shrink = max(0.0, v - width / g) / v
        apex = (tip[0] - direction[0] * v, tip[1] - direction[1] * v)

        def toward(pt: tuple[float, float]) -> tuple[float, float]:
            return (
                apex[0] + (pt[0] - apex[0]) * shrink,
                apex[1] + (pt[1] - apex[1]) * shrink,
            )

        w1 = (tip[0] + arm1[0], tip[1] + arm1[1])
        w2 = (tip[0] + arm2[0], tip[1] + arm2[1])
        sw1, stp, sw2 = toward(w1), toward(tip), toward(w2)
        pts = [w1, tip, w2, sw2, stp, sw1]
        if offset:
            pts = [
                (x - direction[0] * offset, y - direction[1] * offset) for x, y in pts
            ]
        return pts

    # ---- drawStar（L6184）：黑边→环形填充→白边→中心点
    def draw_star(
        self,
        c: _Canvas,
        x: float,
        y: float,
        size: float,
        color: tuple,
        rotation: float,
        is_ex: bool,
        alpha: float = 1.0,
    ) -> None:
        if alpha <= 0:
            return
        if alpha < 0.999:
            # JS ctx.globalAlpha 作用于整颗星（黑/白边、填充、中心点），
            # 不是只淡出填充——生长期的星整体半透明
            layer = _Canvas(transparent=True)
            self.draw_star(layer, x, y, size, color, rotation, is_ex, 1.0)
            layer.img.putalpha(
                layer.img.getchannel("A").point(lambda v: int(v * alpha))
            )
            c.img.alpha_composite(layer.img)
            return
        s = size
        cc = s * 0.5
        inner_s = s * 0.55
        u = cc * 0.55
        sw3 = self.r.sw * 3

        def star_pts(outer: float, inner: float, rot: float = 0.0):
            return [
                (
                    x
                    + math.cos(k * math.pi / 5 - math.pi / 2 + rot)
                    * (outer if k % 2 == 0 else inner),
                    y
                    + math.sin(k * math.pi / 5 - math.pi / 2 + rot)
                    * (outer if k % 2 == 0 else inner),
                )
                for k in range(10)
            ]

        # 黑边：外星仅非 EX（L6193 `if (!a)`）；内星恒画
        if not is_ex:
            c.polygon(star_pts(s, cc, rotation), outline=C_BLACK, width=sw3)
        c.polygon(star_pts(inner_s, u, rotation), outline=C_BLACK, width=sw3)
        # 填充 = 外正向 + 内反向（nonzero 环形，L6220-6228）
        path_o = c.pts(star_pts(s, cc, rotation))
        path_i = c.pts(star_pts(inner_s, u, rotation))
        bbox = (
            int(min(p[0] for p in path_o)),
            int(min(p[1] for p in path_o)),
            int(max(p[0] for p in path_o)) + 1,
            int(max(p[1] for p in path_o)) + 1,
        )
        bbox = (
            max(0, bbox[0]),
            max(0, bbox[1]),
            min(c.img.width, bbox[2]),
            min(c.img.height, bbox[3]),
        )
        if bbox[2] > bbox[0] and bbox[3] > bbox[1]:
            mask = _poly_mask(
                (bbox[2] - bbox[0], bbox[3] - bbox[1]),
                [(px - bbox[0], py - bbox[1]) for px, py in path_o],
                [(px - bbox[0], py - bbox[1]) for px, py in path_i],
            )
            fill_img = Image.new("RGBA", mask.size, (*color[:3], int(255 * alpha)))
            c.img.paste(fill_img, (bbox[0], bbox[1]), mask)
        # 白边 ×2 + 中心点
        c.polygon(star_pts(s, cc, rotation), outline=C_WHITE, width=self.r.sw)
        c.polygon(star_pts(inner_s, u, rotation), outline=C_WHITE, width=self.r.sw)
        c.fill_circle(x, y, size * 0.15, color)

    def draw_ex_star_ring(
        self,
        c: _Canvas,
        x: float,
        y: float,
        size: float,
        is_break: bool,
        is_each: bool,
        split: bool = False,
        rotation: float = 0.0,
    ) -> None:
        """renderExStarRing（L6249）：1.19× 十角环覆盖（pinkSlideStart 关）。

        ``rotation`` 与星体同角度：站内 JS 的环不参与 ctx.rotate，slideRotation
        开启时星体自旋而环不动，视觉上会看到「两颗错位的星」；这里让环跟随
        星体旋转，保持环-星同心（与站内视频的稳定观感一致）。
        """
        overlay = C_EX_BREAK if is_break else C_EX_SIMUL if is_each else C_EX_SLIDE
        scale = _EX_ALPHA
        outer, outer_mid = size * scale, size * 0.5 * scale
        inner, inner_mid = size, size * 0.5

        def pts(radius_outer: float, radius_inner: float, rot: float = 0.0):
            return [
                (
                    x
                    + math.cos(k * math.pi / 5 - math.pi / 2 + rot + rotation)
                    * (radius_outer if k % 2 == 0 else radius_inner),
                    y
                    + math.sin(k * math.pi / 5 - math.pi / 2 + rot + rotation)
                    * (radius_outer if k % 2 == 0 else radius_inner),
                )
                for k in range(10)
            ]

        rotations = [0.0, math.pi] if split else [0.0]
        for rot in rotations:
            path_o = c.pts(pts(outer, outer_mid, rot))
            path_i = c.pts(pts(inner, inner_mid, rot))
            bbox = (
                int(min(p[0] for p in path_o)),
                int(min(p[1] for p in path_o)),
                int(max(p[0] for p in path_o)) + 1,
                int(max(p[1] for p in path_o)) + 1,
            )
            bbox = (
                max(0, bbox[0]),
                max(0, bbox[1]),
                min(c.img.width, bbox[2]),
                min(c.img.height, bbox[3]),
            )
            if bbox[2] > bbox[0] and bbox[3] > bbox[1]:
                mask = _poly_mask(
                    (bbox[2] - bbox[0], bbox[3] - bbox[1]),
                    [(px - bbox[0], py - bbox[1]) for px, py in path_o],
                    [(px - bbox[0], py - bbox[1]) for px, py in path_i],
                )
                fill_img = Image.new("RGBA", mask.size, overlay)
                c.img.paste(fill_img, (bbox[0], bbox[1]), mask)

    # ---- getPathTangentAngle（L6386）：路径切向角（弧长 lerp）
    def path_tangent_angle(self, t: float, segments: list[SlideSegment]) -> float:
        total = 0.0
        lengths: list[float] = []
        angles: list[float] = []
        for seg in segments:
            chain = self.bar_chain(seg)
            if not chain:
                continue
            start = self.r.button_position(seg.start_pos)
            end = self.r.button_position(seg.end_pos)
            last = len(chain) - 1
            extended = [
                {"x": start[0], "y": start[1], "angle": chain[0]["angle"]},
                *chain,
                {"x": end[0], "y": end[1], "angle": chain[last]["angle"]},
            ]
            prev = None
            for point in extended:
                if prev is not None:
                    total += math.hypot(point["x"] - prev["x"], point["y"] - prev["y"])
                lengths.append(total)
                angles.append(point["angle"])
                prev = point
        if not angles:
            return 0.0
        if len(angles) == 1:
            return angles[0]
        target = max(0.0, min(1.0, t)) * total
        for i in range(len(lengths) - 1):
            if lengths[i] <= target <= lengths[i + 1]:
                span = lengths[i + 1] - lengths[i]
                return self._lerp_angle(
                    angles[i],
                    angles[i + 1],
                    (target - lengths[i]) / span if span > 0 else 0.0,
                )
        return angles[-1]

    # ---- calculateStarRotation（L6321）：接近期星头自旋角
    def calculate_star_rotation(self, note: _Note, now: float) -> float:
        if not note.parts:
            return 0.0
        part = note.parts[0]
        duration = part.duration_ms
        if not part.segments or not duration:
            return 0.0
        length = sum(self.segment_length(s) for s in part.segments)
        rate = length * (480.0 / _RADIUS) / math.pi / duration * 15 * 0.06
        per_ms = -min(rate, 1.08) * (math.pi / 180)
        start = note.timing - _APPROACH_MS
        return 0.0 if now < start else per_ms * (now - start)

    # ---- renderSlideStar（L6115）：起点生长 → 沿路径移动（每条并行动径一颗）
    def draw_slide_stars(self, c: _Canvas, note: _Note, now: float) -> None:
        if not note.parts or now < note.timing:
            return
        is_simul_slide = note.simul_slide_count >= 2 or len(note.parts) > 1
        for part in self.ordered_parts(note):
            start = note.timing + part.delay_ms
            if now > start + part.duration_ms:
                continue
            segments = part.segments
            if not segments:
                continue
            color = (
                C_BREAK_DOT
                if part.is_break
                else C_SLIDE_SIMUL
                if is_simul_slide
                else C_SLIDE_CYAN
            )
            if segments[0].type == "w":
                self._draw_wifi_stars(c, note, part, now, color)
                continue
            progress = 0.0
            if now >= start:
                progress = (
                    min(1.0, (now - start) / part.duration_ms)
                    if part.duration_ms > 0
                    else 1.0
                )
                px, py = self.point_along(progress, segments)
                size = _RADIUS * _STAR
                alpha = 1.0
            else:
                if note.headless_mode == "pop":
                    continue
                px, py = self.r.button_position(segments[0].start_pos)
                r = (
                    min(1.0, (now - note.timing) / part.delay_ms)
                    if part.delay_ms > 0
                    else 1.0
                )
                size = _RADIUS * _STAR * (_STAR_MIN + (1 - _STAR_MIN) * r)
                alpha = r
            rotation = (
                self.path_tangent_angle(progress if now >= start else 0.0, segments)
                + math.pi / 2
            )
            self.draw_star(c, px, py, size, color, rotation, note.is_ex, alpha)

    def _draw_wifi_stars(
        self,
        c: _Canvas,
        note: _Note,
        part: SlidePart,
        now: float,
        color: tuple,
    ) -> None:
        """renderWifiStars（L6140）：主路 + 两侧 ±1 位三线三星。"""
        segments = part.segments
        if not segments:
            return
        start = note.timing + part.delay_ms
        progress = 0.0
        if now >= start:
            progress = (
                min(1.0, (now - start) / part.duration_ms)
                if part.duration_ms > 0
                else 1.0
            )
        a0, b0 = segments[0].start_pos, segments[0].end_pos
        targets = [b0, (b0 - 1 - 1 + 8) % 8 + 1, (b0 - 1 + 1) % 8 + 1]
        for end in targets:
            p_start = self.r.button_position(a0)
            p_end = self.r.button_position(end)
            rotation = math.atan2(p_end[1] - p_start[1], p_end[0] - p_start[0]) + (
                math.pi / 2
            )
            if now < start:
                if note.headless_mode == "pop":
                    continue
                px, py = p_start
                r = (
                    min(1.0, (now - note.timing) / part.delay_ms)
                    if part.delay_ms > 0
                    else 1.0
                )
                size = _RADIUS * _STAR * (_STAR_MIN + (1 - _STAR_MIN) * r)
                alpha = r
            else:
                px = p_start[0] + (p_end[0] - p_start[0]) * progress
                py = p_start[1] + (p_end[1] - p_start[1]) * progress
                size = _RADIUS * _STAR
                alpha = 1.0
            self.draw_star(c, px, py, size, color, rotation, note.is_ex, alpha)

    # ---- 星星 tap 头（renderStarTapNote L8535）：`$` 修饰的 tap/break
    def draw_star_tap(self, c: _Canvas, note: _Note, pos: _Pos, now: float) -> None:
        size = c.radius / 12.5 * pos.alpha * 1.15 * 1.25
        color = (
            C_BREAK_DOT
            if note.is_break
            else C_SLIDE_SIMUL
            if note.is_each
            else C_SLIDE_CYAN
        )
        rotation = -now * _QT if note.is_spinning_star else 0.0
        if note.is_ex:
            self.draw_ex_star_ring(
                c, pos.x, pos.y, size, note.is_break, note.is_each, False, rotation
            )
        self.draw_star(c, pos.x, pos.y, size, color, rotation, note.is_ex)

    # ---- slide 起点星形头（renderSingleSlideStart L8411）
    def draw_slide_head(self, c: _Canvas, note: _Note, now: float) -> None:
        pos = _note_position(note, now, c.cx, c.cy, c.radius)
        if not pos.visible:
            return
        is_simul = note.is_each
        if note.has_tap_head:
            self.r.draw_tap(
                c,
                _Note(
                    "tap",
                    note.pos,
                    note.timing,
                    is_break=note.is_break,
                    is_ex=note.is_ex,
                ),
                pos,
            )
            return
        size = c.radius / 12.5 * pos.alpha * 1.15 * 1.25
        color = (
            C_BREAK_DOT
            if note.is_break
            else C_SLIDE_SIMUL
            if is_simul
            else C_SLIDE_CYAN
        )
        rotation = self.calculate_star_rotation(note, now)
        if note.is_ex:
            self.draw_ex_star_ring(
                c,
                pos.x,
                pos.y,
                size,
                note.is_break,
                is_simul,
                bool(note.parts and len(note.parts) > 1),
                rotation,
            )
        if len(note.parts) > 1:
            self._draw_split_slide_star(
                c, pos.x, pos.y, size, color, rotation, note.is_ex
            )
        else:
            self.draw_star(c, pos.x, pos.y, size, color, rotation, note.is_ex)

    def _draw_split_slide_star(
        self,
        c: _Canvas,
        x: float,
        y: float,
        size: float,
        color: tuple,
        rotation: float,
        is_ex: bool,
    ) -> None:
        """renderSplitSlideStar（L8427）：`*` 分裂滑轨的双星头（正交两枚十角环）。"""

        def star_pts(outer: float, inner: float, rot: float):
            return [
                (
                    x
                    + math.cos(k * math.pi / 5 + rot)
                    * (outer if k % 2 == 0 else inner),
                    y
                    + math.sin(k * math.pi / 5 + rot)
                    * (outer if k % 2 == 0 else inner),
                )
                for k in range(10)
            ]

        def ring_path(outer: float, inner_big: float, inner_small: float) -> list:
            path = star_pts(outer, inner_big, 0.0)
            path += star_pts(inner_big * 0.55, inner_small, 0.0)[::-1]
            return path

        if not is_ex:
            for rot in (-math.pi / 2, math.pi / 2):
                c.polygon(
                    star_pts(size, size * 0.5, rot + rotation),
                    outline=C_BLACK,
                    width=self.r.sw * 3,
                )
        # 填充：两个朝向的环（外正向 + 内 0.55 反向）
        for rot in (-math.pi / 2, math.pi / 2):
            outer_ring = [
                (
                    x
                    + math.cos(k * math.pi / 5 + rot + rotation)
                    * (size if k % 2 == 0 else size * 0.5),
                    y
                    + math.sin(k * math.pi / 5 + rot + rotation)
                    * (size if k % 2 == 0 else size * 0.5),
                )
                for k in range(10)
            ]
            inner_ring = [
                (
                    x
                    + math.cos(k * math.pi / 5 + rot + rotation)
                    * (size * 0.55 if k % 2 == 0 else size * 0.275),
                    y
                    + math.sin(k * math.pi / 5 + rot + rotation)
                    * (size * 0.55 if k % 2 == 0 else size * 0.275),
                )
                for k in range(10)
            ][::-1]
            path = [*c.pts(outer_ring), *c.pts(inner_ring)]
            mask = Image.new("L", (c.img.width, c.img.height), 0)
            ImageDraw.Draw(mask).polygon(path, fill=255)
            fill_img = Image.new("RGBA", c.img.size, (*color[:3], int(255 * 1.0)))
            c.img.paste(fill_img, (0, 0), mask)
        _ = ring_path
        for rot in (-math.pi / 2, math.pi / 2):
            c.polygon(
                star_pts(size, size * 0.5, rot + rotation),
                outline=C_WHITE,
                width=self.r.sw,
            )
        c.fill_circle(x, y, size * 0.15, color)


class _TouchRenderer:
    """touch 渲染（Ie 类 L6751 + 部件排序 ke/Ae L6672）。"""

    def __init__(self, renderer: _Renderer):
        self.r = renderer
        self._gauge_sprite: Image.Image | None = None

    def position(self, note: _Note) -> tuple[float, float]:
        """z()（L134）：传感器半径 M 表 + 角度（D/E 用按钮角，A/B 偏 22.5°）。"""
        sensor = note.sensor or "C"
        if sensor == "C":
            return _CENTER, _CENTER
        # JS z()（L125）：u = 位置数字，无数字时 0（裸区 B/A/D/E 合法写法）
        num = note.touch_num if note.touch_num is not None else 0
        ratio = {"A": 400 / 480, "B": 220 / 480, "D": 410 / 480, "E": 310 / 480}[sensor]
        base = (
            -math.pi / 2 + (num - 1) * math.pi / 4
            if sensor in ("D", "E")
            else -math.pi / 2 + math.pi / 8 + (num - 1) * math.pi / 4
        )
        d = _RADIUS * ratio
        return _CENTER + math.cos(base) * d, _CENTER + math.sin(base) * d

    # ---- 接近运动参数（renderTouch L6854 前半）
    def motion(self, note: _Note, now: float):
        is_hold = note.kind == "touch-hold"
        dt = note.timing - now
        approach = _APPROACH_MS * 0.9  # getTouchApproachTimeMs D=0.9
        life = (note.duration + 50) if is_hold else 50.0
        if dt > approach or dt < -life:
            return None
        fade = 1.0
        if dt > approach * 0.95:
            fade = 1 - (dt - approach * 0.95) / (approach * 0.05)
        flick = 1.0
        if 0 < dt < approach and approach - dt < _TOUCH_FLICK_MS:
            flick = (approach - dt) / _TOUCH_FLICK_MS
        p_out = _RADIUS * _TOUCH_OUT * 1.1
        p_in = _RADIUS * _TOUCH_IN * 1.3
        h = p_out
        if 0 < dt <= approach:
            e = 1 - dt / approach
            h = p_out - (p_out - p_in) * e * e * e * e
        elif dt <= 0:
            h = p_in
        return is_hold, dt, fade, flick, h

    # ---- gauge 进度环（getTouchHoldProgressSprite L6818 + L6918 楔形 clip）
    def gauge_sprite(self) -> Image.Image:
        if self._gauge_sprite is not None:
            return self._gauge_sprite
        r = _RADIUS / 480
        half = _GAUGE_GEO["canvas_half"] * r

        def ring_path(axis: float, diag: float) -> list[tuple[float, float]]:
            n = diag * math.sqrt(2) * r
            i = 2 * (n - axis * r)
            pts: list[tuple[float, float]] = []

            def q(cx0, cy0, cx1, cy1):
                for k in (1, 2, 3):
                    t = k / 4
                    pts.append(
                        (
                            (1 - t) ** 2 * cx0 + 2 * (1 - t) * t * cx1,
                            (1 - t) ** 2 * cy0 + 2 * (1 - t) * t * cy1,
                        )
                    )

            pts.append((-i, -n + i))
            q(-i, -n + i, 0, -n)
            pts.append((i, -n + i))
            pts.append((n - i, -i))
            q(n - i, -i, n, 0)
            pts.append((n - i, i))
            pts.append((i, n - i))
            q(i, n - i, 0, n)
            pts.append((-i, n - i))
            pts.append((-n + i, i))
            q(-n + i, i, -n, 0)
            pts.append((-n + i, -i))
            return pts

        size = math.ceil(half * 2 * _SS)
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        off = size / 2

        def to_px(pts):
            return [(px * _SS + off, py * _SS + off) for px, py in pts]

        outer = to_px(ring_path(_GAUGE_GEO["outer_axis"], _GAUGE_GEO["outer_diagonal"]))
        inner = to_px(ring_path(_GAUGE_GEO["inner_axis"], _GAUGE_GEO["inner_diagonal"]))
        mask = _poly_mask((size, size), outer, inner)
        n_max = _GAUGE_GEO["outer_diagonal"] * math.sqrt(2) * r
        for e in range(4):
            t = -math.pi / 4 + e * math.pi / 2
            ca, sa = math.cos(t), math.sin(t)
            g0 = (
                ca * _GAUGE_GEO["inner_diagonal"] * r,
                sa * _GAUGE_GEO["inner_diagonal"] * r,
            )
            g1 = (
                ca * _GAUGE_GEO["outer_diagonal"] * r,
                sa * _GAUGE_GEO["outer_diagonal"] * r,
            )
            col = C_TOUCHHOLD_GAUGE[e]
            stops = (
                (0.0, _mix(col, C_BLACK, 0.28)),
                (0.35, col),
                (0.72, _mix(col, C_WHITE, 0.32)),
                (1.0, _mix(col, C_WHITE, 0.08)),
            )
            grad = _multi_stop_gradient(
                (size, size),
                g0[0] * _SS + off,
                g0[1] * _SS + off,
                g1[0] * _SS + off,
                g1[1] * _SS + off,
                stops,
            )
            # 象限矩形裁剪（fillRect i>0?0:-n 等）
            x0q = 0 if ca > 0 else -n_max
            y0q = 0 if sa > 0 else -n_max
            quad_mask = Image.new("L", (size, size), 0)
            ImageDraw.Draw(quad_mask).rectangle(
                [
                    x0q * _SS + off,
                    y0q * _SS + off,
                    (x0q + n_max) * _SS + off,
                    (y0q + n_max) * _SS + off,
                ],
                fill=255,
            )

            quad = Image.composite(
                grad, Image.new("RGBA", (size, size), (0, 0, 0, 0)), quad_mask
            )
            img.alpha_composite(quad)
        # 环形裁剪
        ring = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        ring.paste(img, (0, 0), mask)
        self._gauge_sprite = ring
        return ring

    def draw_gauge(
        self, c: _Canvas, note: _Note, x: float, y: float, dt: float, alpha: float
    ) -> None:
        """touch hold 进行中进度环（L6919-6924）：楔形 clip 内画 gauge sprite。"""
        if dt >= 0 or note.duration <= 0:
            return
        progress = min(-dt / note.duration, 1.0)
        sprite = self.gauge_sprite()
        size = sprite.width
        # 楔形掩码：-π/2 → -π/2 + n·2π
        pie = Image.new("L", (size, size), 0)
        pd = ImageDraw.Draw(pie)
        cx = cy = size / 2
        a_end = -math.pi / 2 + progress * math.tau
        pts = [(cx, cy)]
        steps = max(2, int(progress * 64))
        for k in range(steps + 1):
            ang = -math.pi / 2 + (a_end + math.pi / 2) * k / max(steps, 1)
            pts.append((cx + math.cos(ang) * size, cy + math.sin(ang) * size))
        pd.polygon(pts, fill=255)
        layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        layer.paste(sprite, (0, 0), ImageChops_darker(sprite.getchannel("A"), pie))
        if alpha < 1:
            layer.putalpha(layer.getchannel("A").point(lambda v: int(v * alpha)))
        c.img.alpha_composite(layer, (int(c.s(x) - size / 2), int(c.s(y) - size / 2)))

    # ---- renderTouch（L6854）+ Ae 部件排序（L6726）
    def collect(self, c: _Canvas, note: _Note, now: float) -> tuple | None:
        """返回 (zone, num, is_hold, src, draw_part)；None = 窗外不可见。"""
        mv = self.motion(note, now)
        if mv is None:
            return None
        is_hold, dt, fade, flick, h = mv
        pos = self.position(note)
        kind = "h" if is_hold else ("s" if note.is_each else "n")
        quad = (-math.pi / 4, math.pi / 4, 3 * math.pi / 4, -3 * math.pi / 4)
        base_off = 0.0 if is_hold else -math.pi / 4
        petal_alpha = fade * flick
        petals = []
        for k in range(4):
            a = quad[k] + base_off
            pcx = pos[0] + math.cos(a) * h
            pcy = pos[1] + math.sin(a) * h
            petals.append((pcx, pcy))
        center_r = _RADIUS * (1 / 37.5) * 0.8
        zone = note.sensor or "C"
        num = (note.touch_num or 1) if zone != "C" else 1

        def draw_part(part: str) -> None:
            if part == "center":
                if fade <= 0:
                    return
                lay = Image.new("RGBA", c.img.size, (0, 0, 0, 0))
                ld = ImageDraw.Draw(lay, "RGBA")
                cx, cy = c.s(pos[0]), c.s(pos[1])
                rr = center_r * _SS
                lw3 = max(1, round(c.s(self.r.sw * 3)))
                lw1 = max(1, round(c.s(self.r.sw)))
                a255 = int(255 * fade)
                ld.ellipse(
                    (cx - rr, cy - rr, cx + rr, cy + rr),
                    outline=(*C_BLACK, a255),
                    width=lw3,
                )
                ld.ellipse(
                    (cx - rr, cy - rr, cx + rr, cy + rr),
                    fill=(
                        *(C_TOUCH_CENTER_S if note.is_each else C_TOUCH_CENTER),
                        a255,
                    ),
                )
                ld.ellipse(
                    (cx - rr, cy - rr, cx + rr, cy + rr),
                    outline=(*C_WHITE, a255),
                    width=lw1,
                )
                c.img.alpha_composite(lay)
            elif part.startswith("petal-"):
                k = int(part[-1])
                if petal_alpha <= 0:
                    return
                pcx, pcy = petals[k]
                for layer_kind in ("sb", "f", "w"):
                    sprite = self.r.sprites.petal(layer_kind, kind, k)
                    spr = sprite
                    if petal_alpha < 1:
                        spr = sprite.copy()
                        spr.putalpha(
                            spr.getchannel("A").point(lambda v: int(v * petal_alpha))
                        )
                    c.img.alpha_composite(
                        spr,
                        (
                            int(c.s(pcx) - spr.width / 2),
                            int(c.s(pcy) - spr.height / 2),
                        ),
                    )
            elif part == "gauge":
                if fade <= 0:
                    return
                self.draw_gauge(c, note, pos[0], pos[1], dt, fade)

        return (zone, num, is_hold, note.src_index, draw_part)

    @staticmethod
    def ordered_parts(entries: list) -> list:
        """ke.resolve + Ae（L6686-6733）：区序/槽序/键序全排序。

        entries 各项为 collect() 的 (zone, num, is_hold, src, draw)；
        返回按排序解得的 (draw, part) 序列。
        """

        def oe(keys: list, src: int, is_hold: bool) -> None:
            # Oe()（L6675）：同一 note 的全部部件共用一个 layer（touch-hold=4、
            # touch=3），部件顺序靠 order 偏移；无名占位在 layer 5
            layer = 4 if is_hold else 3

            def push(order: int, part: str | None) -> None:
                keys.append((_sort_key(layer, -src + order), src, part))

            push(3, "center")
            push(2, "petal-0")
            push(2 if is_hold else 1, "petal-1")
            push(1 if is_hold else 2, "petal-2")
            push(1, "petal-3")
            if not is_hold:
                push(4, None)
            keys.append((_sort_key(5, 2), src, None))
            if is_hold:
                push(-1, "gauge")

        out = []
        for zone in _TOUCH_ZONES:
            zone_entries = sorted(
                (e for e in entries if e[0] == zone),
                key=lambda e: (e[1], -e[3]),
            )
            if not zone_entries:
                continue
            by_src = {e[3]: e[4] for e in zone_entries}
            keys: list[tuple] = [(0, None, None), (0xFFFFFFFF, None, None)]
            slots = 1 if zone == "C" else 8
            for num in range(1, slots + 1):
                for e in zone_entries:
                    if zone == "C" or e[1] == num:
                        oe(keys, e[3], e[2])
                keys.extend((v, None, None) for v in _TOUCH_SORT_SENTINELS)
            keys.sort(key=lambda t: t[0])
            out.extend(
                (by_src[src], part) for _, src, part in keys if src is not None and part
            )
        return out

    # ---- renderTouchBorder（L6952）：同位 touch ≥2 的缺口方框
    def draw_border(self, c: _Canvas, note: _Note, count: int, now: float) -> None:
        if count < 2:
            return
        mv = self.motion(note, now)
        if mv is None:
            return
        _, dt, _, _, _ = mv
        if dt > _APPROACH_MS or dt < -50:
            return
        x, y = self.position(note)
        r_base = _RADIUS * (1 / 4.46) * 1.1 * 1.2
        corner_r = _RADIUS * _R_NOTE
        color = C_SIMUL_DOT if note.is_each else C_TOUCH_CYAN
        lay = Image.new("RGBA", c.img.size, (0, 0, 0, 0))
        ld = ImageDraw.Draw(lay, "RGBA")
        lw = max(1, round(3 * _RADIUS / 300 * _SS))
        for n in ((r_base * 1.2,) if count >= 3 else ()) + (r_base,):
            left, top = x - n / 2, y - n / 2
            right_, bottom = left + n, top + n
            gap = n * 0.3
            mid_l, mid_r = left + n / 2 - gap / 2, left + n / 2 + gap / 2
            mid_t, mid_b = top + n / 2 - gap / 2, top + n / 2 + gap / 2
            pts: list[tuple[float, float]] = []

            def seg(p0: tuple[float, float], p1: tuple[float, float]) -> None:
                pts.extend((p0, p1))

            def arc(cx0: float, cy0: float, a0: float, a1: float) -> None:
                for k in range(5):
                    t = a0 + (a1 - a0) * k / 4
                    pts.append(
                        (cx0 + corner_r * math.cos(t), cy0 + corner_r * math.sin(t))
                    )

            seg((left + corner_r, top), (mid_l, top))
            seg((mid_r, top), (right_ - corner_r, top))
            arc(right_ - corner_r, top + corner_r, -math.pi / 2, 0)
            seg((right_, top + corner_r), (right_, mid_t))
            seg((right_, mid_b), (right_, bottom - corner_r))
            arc(right_ - corner_r, bottom - corner_r, 0, math.pi / 2)
            seg((right_ - corner_r, bottom), (mid_r, bottom))
            seg((mid_l, bottom), (left + corner_r, bottom))
            arc(left + corner_r, bottom - corner_r, math.pi / 2, math.pi)
            seg((left, bottom - corner_r), (left, mid_b))
            seg((left, mid_t), (left, top + corner_r))
            arc(left + corner_r, top + corner_r, math.pi, math.pi * 1.5)
            ld.line(c.pts(pts), fill=(*color, 255), width=lw, joint="curve")
        c.img.alpha_composite(lay)


class _Fireworks:
    """花火（Wt 类 L7665）：fire touch（`!`）完成后的烟花。"""

    def __init__(self, renderer: _Renderer):
        self.r = renderer
        self._wedge: Image.Image | None = None
        self._touch = _TouchRenderer(renderer)

    def wedge_sprite(self) -> Image.Image:
        if self._wedge is not None:
            return self._wedge
        half = _RADIUS * _FIREWORK_WEDGE_SCALE
        size = math.ceil(half * 2 * _SS)
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        cx = cy = size / 2
        wedge_len = _RADIUS / 4.5 * _FIREWORK_R
        n = len(_FIREWORK_COLORS)
        half_ang = math.pi / n * 0.45
        alpha = int(255 * 0.589)
        for e in range(n):
            t = e / n * math.tau
            p0 = (cx, cy)
            p1 = (
                cx + math.cos(t - half_ang) * wedge_len * _SS,
                cy + math.sin(t - half_ang) * wedge_len * _SS,
            )
            p2 = (
                cx + math.cos(t + half_ang) * wedge_len * _SS,
                cy + math.sin(t + half_ang) * wedge_len * _SS,
            )
            # 径向渐变：0-0.9 实色 → 尖端透明
            pts = [p0, p1, p2]
            bbox = (
                int(min(p[0] for p in pts)) - 1,
                int(min(p[1] for p in pts)) - 1,
                int(max(p[0] for p in pts)) + 2,
                int(max(p[1] for p in pts)) + 2,
            )
            bbox = (
                max(0, bbox[0]),
                max(0, bbox[1]),
                min(size, bbox[2]),
                min(size, bbox[3]),
            )
            if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
                continue
            mask = Image.new("L", (bbox[2] - bbox[0], bbox[3] - bbox[1]), 0)
            ImageDraw.Draw(mask).polygon(
                [(px - bbox[0], py - bbox[1]) for px, py in pts], fill=255
            )
            col = _FIREWORK_COLORS[e]
            yy, xx = np.mgrid[0 : bbox[3] - bbox[1], 0 : bbox[2] - bbox[0]]
            dist = np.hypot(xx + bbox[0] - cx, yy + bbox[1] - cy) / (wedge_len * _SS)
            fade = np.clip((1.0 - dist) / 0.1, 0.0, 1.0)
            arr = np.dstack(
                [
                    np.full(dist.shape, col[0], np.uint8),
                    np.full(dist.shape, col[1], np.uint8),
                    np.full(dist.shape, col[2], np.uint8),
                    (fade * alpha).astype(np.uint8),
                ]
            )
            img.paste(Image.fromarray(arr, "RGBA"), (bbox[0], bbox[1]), mask)
        self._wedge = img
        return img

    def draw_balls(
        self, layer: Image.Image, x: float, y: float, t: float, additive: bool
    ) -> None:
        """drawFireworkBalls（L7741）：两层径向光球（lighter）。"""
        if t < 0:
            return
        i = _RADIUS / 4.5
        # (起亮时刻, 起始半径, 峰值透明度, ramp 时长, 衰减寿命, 衰减窗口, 色)
        specs = (
            (0.06, i * 1.3, 0.7, 0.04, 1.173, None, (255, 235, 200)),
            (0.04, i * 0.55, 0.6, 0.02, 0.51, 0.55, (255, 245, 220)),
        )
        balls = []
        for start, radius, peak, ramp, decay, window, color in specs:
            if t < start:
                m = t / start * radius
                p = peak * min(1.0, t / ramp)
            elif window is None or t < window:
                e = (t - start) / decay
                m = radius * (1 - e)
                p = peak * (1 - e)
            else:
                continue
            if p > 0 and m > 0:
                balls.append((m, p, color))
        if not balls:
            return
        max_r = max(m for m, *_ in balls)
        region = int(max_r * _SS) + 2
        cx, cy = x * _SS, y * _SS
        x0, y0 = int(max(0, cx - region)), int(max(0, cy - region))
        x1, y1 = int(min(layer.width, cx + region)), int(min(layer.height, cy + region))
        if x1 <= x0 or y1 <= y0:
            return
        w, h = x1 - x0, y1 - y0
        yy, xx = np.mgrid[0:h, 0:w]
        dist = np.hypot(xx - (cx - x0), yy - (cy - y0)) / _SS
        out = np.asarray(layer.crop((x0, y0, x1, y1)), dtype=np.float32)
        for m, p, c_in in balls:
            r_px = m
            t_arr = np.clip(dist / r_px, 0, 1)
            # canvas 径向渐变外圈 rgba(...,0)：预乘插值下 RGB 保持内色
            alpha = (1.0 - t_arr) * p
            col = np.empty((h, w, 3), dtype=np.float32)
            for ch in range(3):
                col[..., ch] = c_in[ch]
            src_a = (alpha * 255)[..., None]
            if additive:
                out[..., :3] += col * (src_a / 255.0)
                out[..., 3:4] = np.minimum(255, out[..., 3:4] + src_a)
            else:
                # source-over
                sa = src_a / 255.0
                out[..., :3] = col * sa + out[..., :3] * (1 - sa)
                out[..., 3:4] = src_a + out[..., 3:4] * (1 - sa)
        layer.paste(
            Image.fromarray(out.clip(0, 255).astype(np.uint8), "RGBA"),
            (x0, y0),
        )

    def render(self, c: _Canvas, touches: list[_Note], now: float) -> None:
        """renderTouchFireworks（L7777）：最新完成的 fire touch 单发烟花。"""
        latest = None
        for n in touches:
            end = n.timing + n.duration if n.kind == "touch-hold" else n.timing
            if end <= now and n.fire:
                if latest is None or end > latest[1]:
                    latest = (n, end)
        if not latest:
            return
        note, end = latest
        a = now - end
        if a >= _FIREWORK_MS:
            return
        o = a / 1000
        if o >= _FIREWORK_LATE:
            return
        if o < 0.1:
            s = 0.0
        elif o < 0.5:
            s = (1 - (1 - (o - 0.1) / 0.4) ** 3) * _FIREWORK_R
        else:
            s = _FIREWORK_R
        pos = self._touch.position(note)
        rot = a / _FIREWORK_MS * (72 * math.pi / 180)
        d = _RADIUS * _FIREWORK_WEDGE_SCALE
        if o <= _FIREWORK_PHASE:
            if 0 < s < _FIREWORK_SMALL:
                self._draw_wedges_vector(c, pos[0], pos[1], s, rot)
            elif s > 0:
                e = s / _FIREWORK_R * d
                self._blit_wedge(c, pos[0], pos[1], e, rot)
            self._balls_on_canvas(c, pos[0], pos[1], o - 0.1)
            return
        # 后半程：scratch 合成 + destination-out 切洞
        wedge = self.wedge_sprite()
        scratch = Image.new("RGBA", c.img.size, (0, 0, 0, 0))
        rotated = wedge.rotate(
            -math.degrees(rot),
            resample=Image.BILINEAR,
            center=(wedge.width / 2, wedge.height / 2),
        )
        scratch.alpha_composite(
            rotated,
            (
                int(c.s(pos[0]) - c.s(d)),
                int(c.s(pos[1]) - c.s(d)),
            ),
        )
        self.draw_balls(scratch, pos[0], pos[1], o - 0.1, additive=True)
        h = _RADIUS / 4.5 * s
        g = min(1.0, (o - _FIREWORK_PHASE) / 0.5)
        hole_in = h * (0.05 + g * g * (3 - 2 * g) * 0.95)
        v = hole_in + h * 0.5
        cut_alpha = min(1.0, g / 0.2)  # JS L7813：切洞强度渐入
        if v > 0 and cut_alpha > 0:
            yy, xx = np.mgrid[0 : scratch.height, 0 : scratch.width]
            dist = np.hypot(xx - c.s(pos[0]), yy - c.s(pos[1])) / _SS
            keep = np.ones_like(dist)
            m1 = dist <= hole_in
            keep[m1] = 0.0
            m2 = (dist > hole_in) & (dist < v)
            keep[m2] = (dist[m2] - hole_in) / (v - hole_in)
            alpha = np.asarray(scratch.getchannel("A"), dtype=np.float32) / 255.0
            keep = 1.0 - cut_alpha * (1.0 - keep)
            scratch.putalpha(
                Image.fromarray((alpha * keep * 255).astype(np.uint8), "L")
            )
        c.img.alpha_composite(_clip_layer(scratch))

    def _draw_wedges_vector(
        self, c: _Canvas, x: float, y: float, s: float, rot: float
    ) -> None:
        a = _RADIUS / 4.5 * s
        if a <= 0:
            return
        lay = Image.new("RGBA", c.img.size, (0, 0, 0, 0))
        n = len(_FIREWORK_COLORS)
        half_ang = math.pi / n * 0.45
        cx, cy = x * _SS, y * _SS
        for e in range(n):
            t = e / n * math.tau + rot
            pts = [
                (cx, cy),
                (
                    cx + math.cos(t - half_ang) * a * _SS,
                    cy + math.sin(t - half_ang) * a * _SS,
                ),
                (
                    cx + math.cos(t + half_ang) * a * _SS,
                    cy + math.sin(t + half_ang) * a * _SS,
                ),
            ]
            x0 = int(max(0, min(px for px, _ in pts) - 1))
            y0 = int(max(0, min(py for _, py in pts) - 1))
            x1 = int(min(lay.width, max(px for px, _ in pts) + 2))
            y1 = int(min(lay.height, max(py for _, py in pts) + 2))
            if x1 <= x0 or y1 <= y0:
                continue
            mask = Image.new("L", (x1 - x0, y1 - y0), 0)
            ImageDraw.Draw(mask).polygon(
                [(px - x0, py - y0) for px, py in pts], fill=255
            )
            yy, xx = np.mgrid[0 : y1 - y0, 0 : x1 - x0]
            dist = np.hypot(xx - (cx - x0), yy - (cy - y0)) / (a * _SS)
            fade = np.clip((1.0 - dist) / 0.1, 0.0, 1.0)
            arr = np.zeros((y1 - y0, x1 - x0, 4), dtype=np.uint8)
            arr[..., 0], arr[..., 1], arr[..., 2] = _FIREWORK_COLORS[e]
            arr[..., 3] = (fade * 0.589 * 255).astype(np.uint8)
            lay.paste(Image.fromarray(arr, "RGBA"), (x0, y0), mask)
        c.img.alpha_composite(lay)

    def _blit_wedge(self, c: _Canvas, x: float, y: float, e: float, rot: float) -> None:
        wedge = self.wedge_sprite()
        rotated = wedge.rotate(
            -math.degrees(rot),
            resample=Image.BILINEAR,
            center=(wedge.width / 2, wedge.height / 2),
        )
        ratio = c.s(e) * 2 / wedge.width
        scaled = rotated.resize(
            (max(2, int(rotated.width * ratio)),) * 2, Image.BILINEAR
        )
        c.img.alpha_composite(
            _clip_layer(scaled),
            (int(c.s(x) - scaled.width / 2), int(c.s(y) - scaled.height / 2)),
        )

    def _balls_on_canvas(self, c: _Canvas, x: float, y: float, t: float) -> None:
        lay = Image.new("RGBA", c.img.size, (0, 0, 0, 0))
        self.draw_balls(lay, x, y, t, additive=True)
        box = (0, 0, c.img.width, c.img.height)
        _additive_composite(c.img, lay, box)


class _TouchHitEffects:
    """touch 命中特效（Ft 类 L7509）：中心环 + 双层星爆（lighter）。"""

    def __init__(self, renderer: _Renderer):
        self.r = renderer
        self._star_sprites: list[Image.Image] | None = None
        self.color = (255, 255, 0)  # dt.perfect

    def star_sprites(self) -> list[Image.Image]:
        """getStarSprites（L7590）+ drawStarFrame（L7605）：4 帧 sparkle。"""
        if self._star_sprites is not None:
            return self._star_sprites
        size = math.ceil(_TOUCHHIT_SPRITE_SIZE * _TOUCHHIT_STAR_SCALE * 2 * _SS)
        base_r = _TOUCHHIT_SPRITE_SIZE
        col = self.color
        white = (255, 255, 241)  # Z({r:1,g:1,b:.94}) ≈ (255,255,241)

        def sparkle_path(outer: float, inner: float) -> list[tuple[float, float]]:
            """pathSparkle（L7639）：4 尖四角星（二次曲线过对角控制点）。"""
            cx = cy = size / 2
            pts = []
            for k in range(4):
                a0 = k * math.pi / 2 - math.pi / 2
                am = a0 + math.pi / 4
                a1 = a0 + math.pi / 2
                p0 = (cx + math.cos(a0) * outer, cy + math.sin(a0) * outer)
                p1 = (cx + math.cos(a1) * outer, cy + math.sin(a1) * outer)
                ctrl = (cx + math.cos(am) * inner, cy + math.sin(am) * inner)
                pts.append(p0)
                for t in (1, 2, 3):
                    tt = t / 4
                    pts.append(
                        (
                            (1 - tt) ** 2 * p0[0]
                            + 2 * (1 - tt) * tt * ctrl[0]
                            + tt**2 * p1[0],
                            (1 - tt) ** 2 * p0[1]
                            + 2 * (1 - tt) * tt * ctrl[1]
                            + tt**2 * p1[1],
                        )
                    )
            return pts

        sprites = []
        # 帧几何（drawStarFrame L7609-7618）
        frames = (
            (base_r * 1.08, base_r * 0.23, 1.0, "a"),  # 0：白心变体渐变
            (base_r, base_r * 0.23, 1.0, "b"),  # 1：常规渐变
            (base_r * 0.94, base_r * 0.19, 0.94, "b"),  # 2：缩小暗化
            (base_r, base_r * 0.2, 1.0, "stroke"),  # 3：双层描边
        )
        for outer, inner_r, alpha_mult, style in frames:
            img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            if style == "stroke":
                d = ImageDraw.Draw(img, "RGBA")
                path_o = sparkle_path(outer * 1.08 * _SS, inner_r * 1.08 * _SS)
                path_i = sparkle_path(outer * _SS, inner_r * _SS)
                d.line(
                    [*path_o, path_o[0]],
                    fill=(*col, int(255 * 0.22)),
                    width=max(1, round(outer * 0.13 * _SS)),
                    joint="curve",
                )
                d.line(
                    [*path_i, path_i[0]],
                    fill=(*white, int(255 * 0.88)),
                    width=max(1, round(outer * 0.055 * _SS)),
                    joint="curve",
                )
            else:
                # fillStarGradient（L7623）：径向多停点
                cx = cy = size / 2
                path = sparkle_path(outer * _SS, inner_r * _SS)
                mask = _poly_mask((size, size), path)
                yy, xx = np.mgrid[0:size, 0:size]
                dist = np.hypot(xx - cx, yy - cy) / (outer * _SS)
                if style == "a":
                    seg_pos = (0.0, 0.34, 0.72, 1.0)
                    seg_alpha = (0.03, 0.14, 0.82, 0.0)
                    seg_white = (False, False, False, False)
                else:
                    seg_pos = (0.0, 0.35, 0.82, 1.0)
                    seg_alpha = (0.95, 0.95, 0.72, 0.0)
                    seg_white = (True, False, False, False)
                col_c = np.array(col, dtype=np.float32)
                col_w = np.array(white, dtype=np.float32)
                a_map = np.zeros_like(dist)
                c_map = np.zeros((*dist.shape, 3), dtype=np.float32)
                for si in range(len(seg_pos) - 1):
                    m = (dist >= seg_pos[si]) & (dist <= seg_pos[si + 1])
                    if not m.any():
                        continue
                    f = (dist[m] - seg_pos[si]) / (seg_pos[si + 1] - seg_pos[si])
                    a0v, a1v = seg_alpha[si], seg_alpha[si + 1]
                    a_map[m] = a0v + (a1v - a0v) * f
                    c0v = col_w if seg_white[si] else col_c
                    c1v = col_w if seg_white[si + 1] else col_c
                    for ch in range(3):
                        c_map[..., ch][m] = c0v[ch] + (c1v[ch] - c0v[ch]) * f
                arr = np.dstack(
                    [
                        c_map.clip(0, 255).astype(np.uint8),
                        (a_map * alpha_mult * 255).clip(0, 255).astype(np.uint8),
                    ]
                )
                img.paste(Image.fromarray(arr, "RGBA"), (0, 0), mask)
            sprites.append(img)
        self._star_sprites = sprites
        return sprites

    def render(self, c: _Canvas, touches: list[_Note], now: float) -> None:
        """renderTouchHitEffects L7513（dedup 同位取最新）。"""
        r480 = _RADIUS / 480
        if r480 <= 0:
            return
        latest: dict[str, float] = {}
        for n in touches:
            if n.kind == "touch-hold":
                continue
            if n.timing > now or n.timing < now - _TOUCHHIT_LIFETIME * 1000:
                continue
            key = _touch_position_name(n)
            if key not in latest or n.timing > latest[key]:
                latest[key] = n.timing
        tr = _TouchRenderer(self.r)
        for n in touches:
            if n.kind == "touch-hold":
                continue
            if n.timing > now:
                break
            key = _touch_position_name(n)
            if latest.get(key) != n.timing:
                continue
            elapsed = (now - n.timing) / 1000
            if elapsed < 0 or elapsed >= _TOUCHHIT_LIFETIME:
                continue
            pos = tr.position(n)
            seed = round(n.timing * 1000) & 0xFFFFFFFF
            if seed >= 2**31:
                seed -= 2**32
            for i, ch in enumerate(_touch_position_name(n)):
                seed = seed ^ (ord(ch) * (i + 1) * 73856093 & 0xFFFFFFFF)
                if seed >= 2**31:
                    seed -= 2**32
                elif seed < -(2**31):
                    seed += 2**32
            self._center_ring(c, pos[0], pos[1], elapsed, r480)
            seed2 = seed ^ 2246822519
            burst1, burst2 = _TOUCHHIT_BURSTS
            self._star_burst(
                c,
                pos[0],
                pos[1],
                elapsed,
                r480,
                seed,
                burst1[0],
                burst1[1],
                burst1[2],
                0.0,
                0.0,
            )
            self._star_burst(
                c,
                pos[0],
                pos[1],
                elapsed,
                r480,
                seed2,
                burst2[0],
                burst2[1],
                burst2[2],
                burst2[3],
                burst2[4],
            )

    def _center_ring(
        self, c: _Canvas, x: float, y: float, elapsed: float, r480: float
    ) -> None:
        o = elapsed / _TOUCHHIT_LIFETIME
        s = _TOUCHHIT_RING_BASE * max(0.0, _hermite(_TOUCHHIT_RING_SPLINE, o))
        s = min(s, _TOUCHHIT_RING_MAX)
        radius = s * r480 / 2
        if radius < 0.5:
            return
        alpha = _lerp_stops(_TOUCHHIT_RING_ALPHA, o)
        if alpha <= 0.01:
            return
        cx, cy = c.s(x), c.s(y)
        region = int(radius * _SS) + 2
        x0, y0 = int(max(0, cx - region)), int(max(0, cy - region))
        x1, y1 = int(min(c.img.width, cx + region)), int(min(c.img.height, cy + region))
        if x1 <= x0 or y1 <= y0:
            return
        w, h = x1 - x0, y1 - y0
        yy, xx = np.mgrid[0:h, 0:w]
        dist = np.hypot(xx - (cx - x0), yy - (cy - y0)) / _SS
        t = dist / radius
        n_stops = len(_TOUCHHIT_GLOW)
        glow = np.zeros_like(t)
        for i in range(n_stops):
            stop_pos = i / (n_stops - 1) * 0.96
            if i + 1 < n_stops:
                next_pos = (i + 1) / (n_stops - 1) * 0.96
                m = (t >= stop_pos) & (t <= next_pos)
                if m.any():
                    f = (t[m] - stop_pos) / max(next_pos - stop_pos, 1e-9)
                    glow[m] = (
                        _TOUCHHIT_GLOW[i]
                        + (_TOUCHHIT_GLOW[i + 1] - _TOUCHHIT_GLOW[i]) * f
                    )
        m = (t > 0.96) & (t <= 1.0)
        glow[m] = _TOUCHHIT_GLOW[-1] * (1 - (t[m] - 0.96) / 0.04)
        a = glow * alpha
        arr = np.dstack(
            [
                np.full((h, w), self.color[0], np.uint8),
                np.full((h, w), self.color[1], np.uint8),
                np.full((h, w), self.color[2], np.uint8),
                (a * 255).clip(0, 255).astype(np.uint8),
            ]
        )
        _additive_composite(c.img, Image.fromarray(arr, "RGBA"), (x0, y0, x1, y1))

    def _star_burst(
        self,
        c: _Canvas,
        x: float,
        y: float,
        elapsed: float,
        r480: float,
        seed: int,
        count: int,
        start_r: float,
        peak_r: float,
        drift_t: float,
        drift_d: float,
    ) -> None:
        f = elapsed / _TOUCHHIT_LIFETIME
        p = max(0.0, _hermite(_TOUCHHIT_STAR_SPLINE, f))
        if p <= 0.02:
            return
        m_alpha = min(1.0, p * 1.05)
        drift = (
            _drift_dist(elapsed, drift_t, drift_d)
            if drift_t > 0 and drift_d > 0
            else 0.0
        )
        sprites = self.star_sprites()
        lay = Image.new("RGBA", c.img.size, (0, 0, 0, 0))
        for r_i in range(count):
            a1 = _hash01(seed + r_i * 4 + 1)
            s2 = _hash01(seed + r_i * 4 + 2)
            u2 = _hash01(seed + r_i * 4 + 3)
            d2 = _hash01(seed + r_i * 4 + 4)
            ang = a1 * math.tau
            ca, sa = math.cos(ang), math.sin(ang)
            px = x + (ca * start_r + ca * drift) * r480
            py = y + (sa * start_r + sa * drift) * r480
            b = peak_r * p * r480 * (0.9 + 0.2 * s2) * 0.55
            if b < 0.5:
                continue
            frame = min(3, int(d2 * 4))
            s_px = b * _TOUCHHIT_STAR_SCALE
            sprite = sprites[frame]
            ratio = c.s(s_px) * 2 / sprite.width
            scaled = sprite.resize(
                (max(2, int(sprite.width * ratio)),) * 2, Image.BILINEAR
            )
            scaled = scaled.rotate(math.degrees(u2 * math.tau), resample=Image.BILINEAR)
            if m_alpha < 1:
                scaled.putalpha(
                    scaled.getchannel("A").point(lambda v: int(v * m_alpha))
                )
            lay.alpha_composite(
                scaled,
                (int(c.s(px) - scaled.width / 2), int(c.s(py) - scaled.height / 2)),
            )
        _additive_composite(c.img, lay, (0, 0, c.img.width, c.img.height))


class _ChartRenderer:
    """整帧组合（renderFrame L7997 层序 + prepareRenderNotes L8232）。"""

    def __init__(self, chart: SiteChart):
        self.chart = chart
        self.notes = _map_notes(chart)
        hit_notes = _collect_hit_notes(chart)
        base_renderer = _Renderer(self.notes, chart, hit_notes)
        self.slide_renderer = _SlideRenderer(base_renderer)
        self.renderer = base_renderer
        self.touches = _TouchRenderer(base_renderer)
        self.fireworks = _Fireworks(base_renderer)
        self.touch_hits = _TouchHitEffects(base_renderer)
        # BPM 变化点（HUD 闪色：change 时刻表 + 方向）
        self._bpm_changes: list[tuple[float, int]] = []
        previous_bpm: float | None = None
        for timing, bpm, _cumulative in chart.timeline.bpm_points:
            if previous_bpm is not None and bpm != previous_bpm:
                self._bpm_changes.append(
                    (
                        chart.timeline.ms_from_beat(timing),
                        1 if bpm > previous_bpm else -1,
                    )
                )
            previous_bpm = bpm
        # 静态底图（黑底 + 判定圈 + 按钮点）与 clip 圆 alpha mask：预生成每帧 copy
        base = _Canvas()
        self._judgment_line(base)
        self._base_img = base.img
        size = base.img.width
        yy, xx = np.mgrid[0:size, 0:size]
        circle = np.hypot(xx - size / 2, yy - size / 2) <= size / 2 - 1
        self._clip_mask = Image.fromarray((circle * 255).astype(np.uint8), "L")
        self._hit_sprite_cache: dict[tuple, Image.Image] = {}

    # ---- 帧渲染
    def render_frame(self, now_ms: float, fps: int) -> Image.Image:
        from PIL import ImageChops

        c = _Canvas()
        c.img = self._base_img.copy()
        c.d = ImageDraw.Draw(c.img, "RGBA")
        # 花火（clip 圆内、HUD 之下，L8101）——直接画在最终画布（此刻只有底图，
        # 与 JS 在 clear/判定线之后立即绘制的背景一致；lighter 混合语义成立）
        fire_notes = [n for n in self.renderer.touches if n.fire]
        if fire_notes:
            self.fireworks.render(c, fire_notes, now_ms)
        self._hud(c, now_ms, fps)
        # clip 圆内播放场（ctx.clip 等价：先画到独立层再按圆裁切合成）
        field = Image.new("RGBA", c.img.size, (0, 0, 0, 0))
        fc = _Canvas()
        fc.img = field
        fc.d = ImageDraw.Draw(field, "RGBA")
        now = now_ms
        # 1) 轨道 + 2) 移动星（slides 倒序：晚出的先画=在下，L8120/8126）
        for slide in reversed(self.renderer.slides):
            for part in self.slide_renderer.ordered_parts(slide):
                self._draw_tracks(fc, slide, part, now)
        for slide in reversed(self.renderer.slides):
            self.slide_renderer.draw_slide_stars(fc, slide, now)
        # 3) 接近指示（hold/slide 弧 + 全对 GOLD 连接线，L8129）+ tap 弧批（L8131）
        self._draw_approach_indicators(fc, now)
        self.renderer.draw_tap_approach_arcs(fc, self.renderer.taps, now)
        # 4) 分层头（L8271-8283：按时刻倒序绘制=晚出的在下）
        self._draw_layered_heads(fc, now)
        # 5) touch：边框盒（L8133）→ 部件（ke/Ae 排序，L8135）
        self._draw_touches(fc, now)
        # clip 圆合成（静态 mask）：field 的 alpha 圈外清零后 alpha_composite
        # 叠加——底图的判定圈/按钮点保留（paste 会整块替换，把圈内底图抹掉）
        field.putalpha(ImageChops.darker(field.getchannel("A"), self._clip_mask))
        c.img.alpha_composite(field)
        # 6) 特效阶段（L8136-8139）：screen/lighter 混合必须对最终画面进行
        # （JS 直接画在主画布上；画在透明 field 层会把底图盖黑）——按 clip 掩码
        # 裁剪后逐一绘制：hold 涟漪 → touch hold 按压/松手 → 命中 → 打击
        fx = _Canvas()
        fx.img = c.img
        fx.d = ImageDraw.Draw(c.img, "RGBA")
        for n in self.renderer.holds:
            if n.timing <= now < n.timing + n.duration:
                self.renderer.draw_press_ripple(
                    fx,
                    *self.renderer.button_position(n.pos),
                    n.timing,
                    n.timing + n.duration,
                    now,
                )
        self._draw_touch_hold_effects(fx, now)
        self.touch_hits.render(fx, self.renderer.touches, now)
        self._draw_tap_hit_effects(fx, now)
        return c.img

    def _draw_layered_heads(self, fc: _Canvas, now: float) -> None:
        """renderLayeredHeads（L8500）：按时刻倒序绘制；同刻 tap→hold→slide。

        JS 先按 timingMs 降序稳定排序再 reverse，再从尾部倒序绘制——效果是
        晚出的头先画（在下）、早出的后画（在上）；同刻顺序 tap,hold,slide。
        """
        order = {"tap": 0, "hold": 1, "slide": 2}
        items: list[tuple[float, int, _Note]] = []
        for n in self.renderer.taps:
            items.append((n.timing, order["tap"], n))
        for n in self.renderer.holds:
            items.append((n.timing, order["hold"], n))
        for n in self.renderer.slides:
            if not n.is_headless:
                items.append((n.timing, order["slide"], n))
        items.sort(key=lambda item: (-item[0], item[1]))
        for _timing, kind_order, n in items:
            if kind_order == 0:
                pos = _note_position(n, now, fc.cx, fc.cy, fc.radius)
                if pos.visible:
                    if n.is_star:
                        self.slide_renderer.draw_star_tap(fc, n, pos, now)
                    else:
                        self.renderer.draw_tap(fc, n, pos)
            elif kind_order == 1:
                head = _note_position(n, now, fc.cx, fc.cy, fc.radius)
                if head.visible:
                    self.renderer.draw_hold(fc, n, now)
            else:
                self.slide_renderer.draw_slide_head(fc, n, now)

    def _draw_approach_indicators(self, fc: _Canvas, now: float) -> None:
        """renderApproachIndicators（L8352）：hold/slide 接近组弧 + 全对连线。"""
        groups: dict[float, list[_Note]] = {}
        for n in self.renderer.holds + [
            s for s in self.renderer.slides if not s.is_headless
        ]:
            groups.setdefault(round(n.timing, 3), []).append(n)
        for key in sorted(groups):
            group = groups[key]
            visible = []
            for n in group:
                if n.timing <= now:
                    continue
                pos = _note_position(n, now, fc.cx, fc.cy, fc.radius)
                if not pos.visible:
                    continue
                if n.kind == "hold":
                    color = (
                        C_BREAK_DOT
                        if n.is_break
                        else C_SIMUL_DOT
                        if n.is_each
                        else C_TAP_DOT
                    )
                else:  # slide（hasTapHead → TAP_PINK，否则 CYAN，L8366）
                    color = (
                        C_BREAK_DOT
                        if n.is_break
                        else C_SIMUL_DOT
                        if n.is_each
                        else C_TAP_DOT
                        if n.has_tap_head
                        else C_SLIDE_CYAN
                    )
                self.renderer.draw_approach_arc(
                    fc, n.pos, math.hypot(pos.x - fc.cx, pos.y - fc.cy), color
                )
                visible.append((n, pos))
            if len(visible) >= 2:
                dist0 = math.hypot(visible[0][1].x - fc.cx, visible[0][1].y - fc.cy)
                for i in range(len(visible)):
                    for j in range(i + 1, len(visible)):
                        self.renderer.draw_connector(
                            fc, visible[i][0].pos, visible[j][0].pos, dist0
                        )

    def _draw_touches(self, fc: _Canvas, now: float) -> None:
        touches = self.renderer.touches
        # 边框：同位 touch ≥2（renderTouchBorders L8391，倒序绘制）
        count_by_pos: dict[tuple, int] = {}
        for n in touches:
            if n.kind == "touch-hold":
                continue
            dt = n.timing - now
            if -50 <= dt <= _APPROACH_MS:
                key = (n.sensor, n.touch_num)
                count_by_pos[key] = count_by_pos.get(key, 0) + 1
        for n in reversed(touches):
            key = (n.sensor, n.touch_num)
            self.touches.draw_border(fc, n, count_by_pos.get(key, 0), now)
        # 部件（ke/Ae 排序）
        entries = []
        for n in touches:
            e = self.touches.collect(fc, n, now)
            if e is not None:
                entries.append(e)
        for draw, part in _TouchRenderer.ordered_parts(entries):
            draw(part)

    def _draw_touch_hold_effects(self, fc: _Canvas, now: float) -> None:
        """renderTouchHoldPressEffects（L8539）+ Release（L8549）。"""
        for n in self.renderer.touches:
            if n.kind != "touch-hold":
                continue
            end = n.timing + n.duration
            # 按压涟漪
            if n.timing <= now < end:
                x, y = self.touches.position(n)
                self.renderer.draw_press_ripple(fc, x, y, n.timing, end, now)
            # 松手 hexagon 打击特效（角指向圆心，C 区 -π/6）
            if end <= now < end + _HIT_MS:
                x, y = self.touches.position(n)
                if n.sensor == "C":
                    ang = -math.pi / 6
                else:
                    ang = math.atan2(fc.cy - y, fc.cx - x)
                self._hit_effect_at(fc, x, y, ang, (now - end) / _HIT_MS, "hexagon")

    def _draw_tracks(
        self, fc: _Canvas, note: _Note, part: SlidePart, now: float
    ) -> None:
        """renderSlidePath 的 tracks 分支（L5670）：按并行动径各自的 delay/duration。"""
        if not part.segments:
            return
        start = note.timing + part.delay_ms
        if now > start + part.duration_ms:
            return
        # 普通段与 wifi 段分别取轨道透明度（JS L5682/5689）
        alpha_plain = self.slide_renderer.track_alpha(note, now, False)
        alpha_wifi = self.slide_renderer.track_alpha(note, now, True)
        if alpha_plain <= 0 and alpha_wifi <= 0:
            return
        ranges = self.slide_renderer.path_ranges(part.segments)
        if ranges is None:  # 零长度路径：getSlidePathMetrics 返回 null 不画
            return
        progress = 0.0
        if now >= start and part.duration_ms > 0:
            progress = min(1.0, (now - start) / part.duration_ms)
        # 轨道配色：同刻 slide≥2 或 `*` 分裂滑轨 → 黄（L5677 d = isSplitSlide）
        is_simul_slide = note.simul_slide_count >= 2 or len(note.parts) > 1
        # 段从后往前画（JS L5688）：第一段压在后段之上
        for i in range(len(part.segments) - 1, -1, -1):
            seg = part.segments[i]
            alpha = alpha_wifi if seg.type == "w" else alpha_plain
            if alpha <= 0:
                continue
            r0, r1 = ranges[i]
            seg_progress = 0.0
            if progress > r0:
                seg_progress = 1.0 if progress >= r1 else (progress - r0) / (r1 - r0)
            extend = i < len(part.segments) - 1
            self.slide_renderer.draw_segment_arrows(
                fc, seg, seg_progress, alpha, part.is_break, is_simul_slide, extend
            )

    def _draw_tap_hit_effects(self, fc: _Canvas, now: float) -> None:
        """renderTapHitEffect（L8559）：同位去重（只画窗内最新）。"""
        window = [
            (pos, timing, is_star)
            for pos, timing, is_star in self.renderer.hit_notes
            if now - _HIT_MS <= timing <= now
        ]
        latest: dict[int, float] = {}
        for pos, timing, _is_star in window:
            current = latest.get(pos)
            if current is None or timing > current:
                latest[pos] = timing
        for pos, timing, is_star in window:
            if latest.get(pos) != timing:
                continue
            ang = _button_angle(pos)
            px = fc.cx + math.cos(ang) * fc.radius
            py = fc.cy + math.sin(ang) * fc.radius
            self._hit_effect_at(
                fc,
                px,
                py,
                ang,
                (now - timing) / _HIT_MS,
                "star" if is_star else "hexagon",
            )

    def _hit_effect_at(
        self, fc: _Canvas, x: float, y: float, rot: float, progress: float, shape: str
    ) -> None:
        """renderHitEffectAt（L7289）：5 sprite（1 主 4 环绕），整体随按钮角旋转。"""
        s = 1 - 0.75 * (progress - 1) * (progress - 1)
        c_alpha = 1 - 4 * (progress - 0.5) * (progress - 0.5)
        if c_alpha <= 0 or s <= 0:
            return
        fade_l = 1 - (progress - 1) * (progress - 1)
        u = max(0.0, min(1.0, 1 - 8 / 9 * progress * progress))
        d_size = fc.radius * _R_NOTE * 1.36 * 1.5
        f = d_size * s
        p = f * 0.7
        m2 = f * 0.8
        h = d_size * u * 0.7
        sprite = self.renderer.sprites.hit(shape)
        # JS 先 translate/rotate 画布再画 5 个 sprite：等价于在局部层画好后整体旋转
        # 一次（比逐 sprite rotate 快数倍，也避免重复重采样）
        half = d_size * 1.1 + _RADIUS * (4 / 300 * 0.7) * 2.4 * 3 + 4
        size = math.ceil(half * 2 * _SS)
        lay = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        center = size / 2

        def blit(lx: float, ly: float, sz: float) -> None:
            quantized = round(sz * 2) / 2
            key = (shape, quantized)
            img = self._hit_sprite_cache.get(key)
            if img is None:
                ratio = quantized / d_size
                img = (
                    sprite
                    if abs(ratio - 1) < 1e-6
                    else sprite.resize(
                        (max(2, int(sprite.width * ratio)),) * 2, Image.BILINEAR
                    )
                )
                if len(self._hit_sprite_cache) > 64:
                    self._hit_sprite_cache.clear()
                self._hit_sprite_cache[key] = img
            if c_alpha < 1:
                img = img.copy()
                img.putalpha(img.getchannel("A").point(lambda v: int(v * c_alpha)))
            lay.alpha_composite(
                img,
                (
                    int(center + lx * _SS - img.width / 2),
                    int(center + ly * _SS - img.height / 2),
                ),
            )

        x6 = math.pi / 6
        s6 = -math.pi / 6
        c_ang = x6 + math.pi * fade_l
        w_ang = x6 + math.pi * (1 + fade_l)
        t_ang = s6 - math.pi * fade_l
        e_ang = s6 + math.pi * (1 - fade_l)
        blit(0, 0, f)
        blit(math.cos(c_ang) * h, math.sin(c_ang) * h * 0.7, p)
        blit(math.cos(w_ang) * h, math.sin(w_ang) * h, p)
        blit(math.cos(t_ang) * h, math.sin(t_ang) * h, m2)
        blit(math.cos(e_ang) * h, math.sin(e_ang) * h, m2)
        if abs(rot) > 1e-9:
            lay = lay.rotate(
                -math.degrees(rot), resample=Image.BILINEAR, center=(center, center)
            )
        x0 = int(fc.s(x) - center)
        y0 = int(fc.s(y) - center)
        box = (x0, y0, x0 + size, y0 + size)
        fc.img.alpha_composite(_clip_layer(lay, box, fc.img.width), (x0, y0))

    def _judgment_line(self, c: _Canvas) -> None:
        """renderJudgmentLine（L8086）simple 设计：白圈 + 8 按钮点。"""
        c.stroke_circle(c.cx, c.cy, c.radius, C_WHITE, _JLINE * c.radius)
        for pos in range(1, 9):
            bx, by = self.renderer.button_position(pos)
            c.fill_circle(bx, by, _DOT * c.radius, C_WHITE)

    def _hud(self, c: _Canvas, now: float, fps: int) -> None:
        """renderBpmDisplay（L8583）+ renderNoteCounts（L8637）。"""

        s = _SS
        r300 = c.radius / 300
        n22, n16 = round(22 * r300) * s, round(16 * r300) * s
        n20, n4 = round(20 * r300) * s, round(4 * r300) * s
        n18 = round(18 * r300) * s
        shadow_off = 2 * r300 * s
        shadow_col = (0, 0, 0, 191)

        def text(x, y, txt, px, color, anchor="la"):
            c.d.text(
                (x + shadow_off, y + shadow_off),
                txt,
                font=self._font(px),
                fill=shadow_col,
                anchor=anchor,
            )
            c.d.text((x, y), txt, font=self._font(px), fill=color, anchor=anchor)

        beat = self.chart.timeline.beat_from_ms(now)
        bpm = self.chart.timeline.bpm_at_beat(beat)
        bpm_col = C_WHITE
        for change_ms, direction in self._bpm_changes:
            dt = now - change_ms
            if 0 <= dt < 500:
                e = max(0.0, (dt - 50) / 450)
                if direction > 0:
                    bpm_col = (255, round(255 * e), round(255 * e))
                else:
                    bpm_col = (round(255 * e), 255, round(255 * e))
        text(n20, n20, f"BPM: {math.floor(bpm)}", n22, bpm_col)
        div = self.chart.timeline.divisor_at_beat(beat)
        measure = int(beat // 4)
        beat_in = int(beat % 4) + 1
        frac = beat % 1
        text(
            n20,
            n20 + n22 + n4,
            f"{measure}:{beat_in}.{int(frac * 100):02d} [1/{div}]",
            n16,
            C_HUD_GRAY,
        )
        total = len(self.renderer.completions)
        combo = self.renderer.count_done(self.renderer.completions, now)
        text(_LOGICAL * s - n20, n20, f"连击: {combo} / {total}", n22, C_WHITE, "ra")
        b_total = len(self.renderer.break_completions)
        b_done = self.renderer.count_done(self.renderer.break_completions, now)
        text(
            n20,
            _LOGICAL * s - n20,
            f"BREAK: {b_done} / {b_total}",
            n22,
            C_BREAK_DOT,
            "lb",
        )
        # 右下两行用「无保护」（非 EX break）计数（L8649-8651）
        nx_total = len(self.renderer.noex_break_completions)
        nx_done = self.renderer.count_done(self.renderer.noex_break_completions, now)
        right_x = _LOGICAL * s - n20
        bottom = _LOGICAL * s - n20
        text(right_x, bottom, f"BREAK: {nx_done} / {nx_total}", n22, C_ORANGE, "rb")
        text(right_x, bottom - n22 - n4, "无保护", n18, C_ORANGE, "rb")

    def _font(self, px: int):
        cached = self.renderer._fonts.get(px)
        if cached is None:
            try:
                cached = _load_font(px, "ResourceHanRoundedCN-Bold.ttf")
            except Exception:
                from PIL import ImageFont

                cached = ImageFont.load_default()
            self.renderer._fonts[px] = cached
        return cached


def _collect_hit_notes(chart: SiteChart) -> list[tuple[int, float, bool]]:
    """hitEffectNotes（L8268）：(按钮位, 时刻 ms, 是否星形打击特效)。

    JS 口径：带数字位的 note 中排除 touch/touch-hold/hold-start/headless slide；
    星形 = break tap / `$` 星 tap / break hold 的 hold-end / break slide 头。
    """
    items: list[tuple[int, float, bool]] = []
    for note in chart.notes:
        if note.type in (
            "tap",
            "break",
            "simultaneous",
            "hold-end",
            "hold-end-simultaneous",
            "slide",
        ):
            if note.type == "slide" and note.is_headless:
                continue
            if not isinstance(note.position, int):
                continue
            is_star = (
                note.type == "break"
                or (note.type in ("tap", "simultaneous") and note.is_star)
                or note.is_break_hold
                or (note.type == "slide" and note.is_start_break)
            )
            items.append((note.position, note.timing_ms, is_star))
    return items


def _map_notes(chart: SiteChart) -> list[_Note]:
    """SiteChart → 渲染 note 序列（字段/顺序对齐 JS preparedRenderNotes 输入）。"""
    notes: list[_Note] = []
    hold_starts: dict[tuple[int, float], ChartNote] = {}
    # Le()（L7031）：touch 部件排序用的 sourceNoteIndex（hold-end 类不计）
    source_index: dict[int, int] = {}
    counter = 0
    for note in chart.notes:
        if note.type in ("hold-end", "hold-end-simultaneous", "touch-hold-end"):
            continue
        if note.type in ("touch", "touch-hold-start"):
            source_index[id(note)] = counter
        counter += 1

    for note in chart.notes:
        kind = note.type
        if kind in ("hold-start", "hold-start-simultaneous"):
            hold_starts[(int(note.position), round(note.timing, 3))] = note
        elif kind in ("hold-end", "hold-end-simultaneous"):
            start_key = (
                int(note.position),
                round(note.hold_start_timing or 0.0, 3),
            )
            start = hold_starts.pop(start_key, None)
            if start is None:
                continue
            notes.append(
                _Note(
                    "hold",
                    int(note.position),
                    start.timing_ms,
                    is_break=start.is_break_hold,
                    is_ex=start.is_ex,
                    duration=note.timing_ms - start.timing_ms,
                )
            )
        elif kind == "slide":
            notes.append(
                _Note(
                    "slide",
                    int(note.position),
                    note.timing_ms,
                    is_break=note.is_start_break,
                    is_ex=note.is_ex,
                    parts=[
                        SlidePart(
                            segments=part.segments,
                            delay_ms=part.delay_ms,
                            duration_ms=part.duration_ms,
                            is_break=part.is_break,
                        )
                        for part in note.parts
                    ],
                    has_tap_head=note.has_tap_head,
                    headless_mode=note.headless_mode,
                    is_headless=note.is_headless,
                    has_delay_marker=note.has_delay_marker,
                )
            )
        elif kind in ("tap", "break", "simultaneous"):
            notes.append(
                _Note(
                    "tap",
                    int(note.position),
                    note.timing_ms,
                    is_break=kind == "break",
                    is_ex=note.is_ex,
                    is_star=note.is_star,
                    is_spinning_star=note.is_spinning_star,
                    has_delay_marker=note.has_delay_marker,
                )
            )
        elif kind in ("touch", "touch-hold-start"):
            position = str(note.position)
            sensor = position[:1]
            number = None
            if sensor != "C" and len(position) > 1:
                try:
                    number = int(position[1:])
                except ValueError:
                    number = None
            duration = 0.0
            if kind == "touch-hold-start":
                duration = note.duration_ms
            notes.append(
                _Note(
                    "touch-hold" if kind == "touch-hold-start" else "touch",
                    0,
                    note.timing_ms,
                    duration=duration,
                    fire=note.has_firework,
                    sensor=sensor,
                    touch_num=number,
                    src_index=source_index.get(id(note), 0),
                )
            )
    return notes


_WORKER_RENDERER: "_ChartRenderer | None" = None
_WORKER_FPS: int = 30
_WORKER_SIZE: int = int(_LOGICAL)


def _worker_init(chart: SiteChart, fps: int, supersample: float) -> None:
    """子进程初始化：各自持有一个渲染器（fork 继承谱面对象，无需重解析）。"""
    global _WORKER_RENDERER, _WORKER_FPS, _SS
    _SS = supersample
    _WORKER_RENDERER = _ChartRenderer(chart)
    _WORKER_FPS = fps


def _worker_render_chunk(args: tuple[int, int]) -> list[bytes]:
    """渲染 [start, start+count) 帧并缩到输出尺寸。

    回传 **zlib 压缩帧**（1080KB→约 80KB）：原始 RGB 逐帧过管道/反序列化会在
    父进程形成约 10ms/帧 的固定瓶颈，压缩后只剩约 1ms 解压（压缩本身在子进程
    并行完成）。保序由 pool.map 保证。
    """
    start, count = args
    assert _WORKER_RENDERER is not None
    frames: list[bytes] = []
    for index in range(start, start + count):
        frame = _WORKER_RENDERER.render_frame(index / _WORKER_FPS * 1000.0, _WORKER_FPS)
        frames.append(
            zlib.compress(
                frame.convert("RGB")
                .resize((_WORKER_SIZE, _WORKER_SIZE), Image.BILINEAR)
                .tobytes(),
                1,
            )
        )
    return frames


def _write_frames_parallel(
    proc, chart: SiteChart, n_frames: int, fps: int, workers: int
) -> bool:
    """多进程分帧渲染并保序写入 ffmpeg 管道。

    fork 不可用时（Windows/受限环境）返回 False，由调用方退回串行渲染。
    渲染与 x264 编码同时进行，故 workers 取 min(CPU-1, 上限) 留一个核给 ffmpeg。
    """
    import multiprocessing

    try:
        context = multiprocessing.get_context("fork")
    except ValueError:
        return False
    workers = max(1, min(workers, (multiprocessing.cpu_count() or 1) - 1))
    if workers <= 1:
        return False
    chunk = max(1, min(30, n_frames // (workers * 4) or 1))
    tasks = [
        (start, min(chunk, n_frames - start)) for start in range(0, n_frames, chunk)
    ]
    from concurrent.futures import ProcessPoolExecutor

    wrote = 0
    try:
        with ProcessPoolExecutor(
            max_workers=workers,
            mp_context=context,
            initializer=_worker_init,
            initargs=(chart, fps, _SS),
        ) as pool:
            for frames in pool.map(_worker_render_chunk, tasks):
                for frame in frames:
                    proc.stdin.write(zlib.decompress(frame))
                    wrote += 1
        return True
    except (OSError, RuntimeError) as exc:
        # 池创建/子进程启动失败且尚未写入任何帧 → 允许调用方退回串行；
        # 已写入部分帧时不能重来（会重复帧），直接上抛由 finally 收尾报错。
        if wrote:
            raise RuntimeError(f"并行渲染中断（已写入 {wrote} 帧）") from exc
        return False


_SS_LOCK = threading.Lock()


def render_chart_video(
    chart: SiteChart,
    *,
    audio: bytes | None,
    out_path: Path,
    fps: int = 30,
    limit_seconds: float | None = None,
    workers: int = 0,
    supersample: float = 2.0,
) -> Path:
    """渲染入口：按质量档设置超采样倍率（1.0–2.0），串行化并发渲染。"""
    global _SS
    clamped = max(1.0, min(2.0, float(supersample)))
    with _SS_LOCK:
        previous = _SS
        _SS = clamped
        try:
            return _render_chart_video_impl(
                chart,
                audio=audio,
                out_path=out_path,
                fps=fps,
                limit_seconds=limit_seconds,
                workers=workers,
            )
        finally:
            _SS = previous


def _render_chart_video_impl(
    chart: SiteChart,
    *,
    audio: bytes | None,
    out_path: Path,
    fps: int = 30,
    limit_seconds: float | None = None,
    workers: int = 0,
) -> Path:
    """渲染谱面预览 mp4（CPU 密集，调用方放线程池）。

    时间轴与站内播放器一致：视频从 chart beat 0 起（前 4 拍空场 lead-in），
    音频从 beat 4 起播（chartpage.js syncFrame 的 240000/bpm 偏移）。
    ``limit_seconds`` 截断渲染时长；音频 None 时出无声视频。
    """
    import os
    import shutil
    import subprocess

    if shutil.which("ffmpeg") is None:
        raise RuntimeError("未找到 ffmpeg，谱面预览渲染不可用（请安装 ffmpeg）")
    renderer = _ChartRenderer(chart)
    if workers <= 0:  # 0=自动：CPU-1（给 ffmpeg 留一核），上限 8
        workers = max(1, min(8, (os.cpu_count() or 1) - 1))
    total = max(1.0, chart.timeline.ms_from_beat(chart.total_beats) / 1000.0)
    if limit_seconds is not None:
        total = min(total, limit_seconds)
    n_frames = int(total * fps)
    lead_ms = 240000.0 / chart.bpm
    audio_delay = max(0.0, lead_ms - (chart.first_ms or 0.0))
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
        f"{int(_LOGICAL)}x{int(_LOGICAL)}",
        "-r",
        str(fps),
        "-i",
        "pipe:",
    ]
    audio_file: Path | None = None
    if audio:
        audio_file = out_path.with_suffix(".audio.tmp")
        audio_file.write_bytes(audio)
        cmd += ["-i", str(audio_file), "-c:a", "aac", "-b:a", "160k"]
        if audio_delay > 0:
            cmd += ["-af", f"adelay={int(audio_delay)}:all=1"]
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
    ]
    if audio:
        cmd += ["-shortest"]
    cmd += [str(out_path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    assert proc.stdin is not None
    try:
        if workers > 1 and _write_frames_parallel(proc, chart, n_frames, fps, workers):
            pass
        else:
            for i in range(n_frames):
                frame = renderer.render_frame(i / fps * 1000.0, fps)
                proc.stdin.write(
                    frame.convert("RGB")
                    .resize((int(_LOGICAL), int(_LOGICAL)), Image.BILINEAR)
                    .tobytes()
                )
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
