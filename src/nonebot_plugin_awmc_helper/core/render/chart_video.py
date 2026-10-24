"""谱面预览视频渲染（落雪站内 canvas 渲染器的 PIL 忠实移植）。

绘制逻辑逐条对照 maimai.lxns.net 渲染器 bundle（beautify 存档
``local/reference/lxns-renderer/chart-canvas.pretty.js``，各方法标注 L* 行号），
常量/配色/层序/运动公式与站内一致；滑键路径数据来自同源 ue/le 表
（``_lxns_slide_data``，40 形状 1200 点，自动生成）。

关键语义（均引自源码，勿凭直觉改）：
- 运动（calculateNotePosition L312）：接近窗前半程 0.25R 驻留淡入，后半程
  0.25R→R；tap 判定后立即消失（无外飞）；hold 头判定后驻留 R；
- 接近时长 = 2250ms / (hiSpeed×0.6)（setHiSpeed 隐藏系数），hiSpeed=6 → 625ms；
- tap = 四象限线性渐变环（随按钮角旋转）+ 双白环 + 黑描边 + 中心点
  （drawTapNoteVector L495）；
- hold = 头尾双六边形：头 note 下落（判定后驻留 R），尾为 hold-end 虚拟
  note 同款下落，六侧面线性渐变、进行中白色呼吸（Se L6417）+ 头部光晕
  （drawActiveHeadGlow L6583）；
- slide = 箭头链（ue 单位坐标 + ce 形状映射 L553 + le 关键帧隐藏）+ 起点
  星形头 + 沿路径移动的双层十角星（drawStar L6193，切向旋转）；
- touch = 四瓣渐变三角从 R/6.25 处四次方缓动收拢到传感器位（renderTouch
  L6854），命中后六边形扩散（touch hit）；
- 打击特效 = 金色六/星形描边 sprite（辉光 blur）+ 1 主 4 环绕，450ms
  （renderHitEffectAt L7277）；
- 帧序（renderFrame L7997）：clear → 判定圈 → HUD → clip 圆 → 轨道 → 星 →
  接近弧 → 分层头 → touch → 长条涟漪/打击特效。

帧在 2× 超采样画布绘制（等价站内 G=2 backing）后缩至 600²，rawvideo 管道
喂 ffmpeg 合成 h264+aac mp4（音频 t=0 = 谱面 t=0，构造性同步）。
"""

import math
from pathlib import Path
from dataclasses import field, dataclass

from PIL import Image, ImageDraw, ImageFilter

from .fonts import font as _load_font
from ..simai import SimaiTap, SimaiHold, SimaiSlide, SimaiTouch, SimaiDifficulty
from ._lxns_slide_data import SLIDE_POINTS, SLIDE_KEYFRAMES

# ------------------------------------------------- 常量（JS L16-60 / I 表 L66）
_LOGICAL = 600.0
_SS = 2  # 超采样（站内 G=2 backing）
_RADIUS = _LOGICAL * 0.45
_CENTER = _LOGICAL / 2
_BASE_APPROACH_MS = 2250.0
_HI_SPEED = 6.0 * 0.6  # setHiSpeed 隐藏系数
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
_DELAY_FADE = 50.0
_TRAIL_RAMP = 200.0
_HIT_MS = 450.0
_EX_ALPHA = 1.19
_P_MIX = 0.18
_TOUCH_OUT = 1 / 6.25
_TOUCH_IN = 1 / 12.5
_STAR_HEAD = 1.15 * 1.25

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
C_ORANGE = (255, 165, 0)
C_TOUCH_HOLD = ((233, 85, 19), (250, 237, 0), (13, 172, 103), (44, 166, 224))

# 运动常量（涟漪 it 类 Ge/Ke/Ye/Xe/Je/qe 与 $e 色）
_RIPPLE_GE = 16000 / 60 / 1000
_RIPPLE_KE = (0.0, 4 / 60, 8 / 60)
_RIPPLE_YE = 0.1355
_RIPPLE_XE = 0.735
_RIPPLE_JE = 1.0
_RIPPLE_QE = 11 / 60
_RIPPLE_MIN_ALPHA = 8 / 255  # nt


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
    """H()（L91）：ddr 关闭时 break/each/normal 三组渐变对。"""
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
    """Se()（L6417）：进行中 hold 白边亮度呼吸。"""
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
    """渲染 note（由 SimaiDifficulty 映射；字段语义对齐 JS note 对象）。"""

    kind: str  # tap / hold / slide / touch / touch-hold
    pos: int  # 按钮 1-8（touch 为 0）
    timing: float  # ms
    is_break: bool = False
    is_each: bool = False
    is_ex: bool = False
    duration: float = 0.0  # hold/touch-hold ms
    segments: list = field(default_factory=list)
    # slide 分段：(shape, start, end, mid, delay_ms, dur_ms, is_break)
    fire: bool = False
    sensor: str = ""
    touch_num: int | None = None
    is_headless: bool = False
    visual_only: bool = False


@dataclass
class _Pos:
    x: float
    y: float
    alpha: float
    visible: bool


def _note_position(
    note: _Note, now_ms: float, cx: float, cy: float, radius: float
) -> _Pos:
    """calculateNotePosition（L312）。tap 判定后立即消失；hold 头驻留 R。"""
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

    def __init__(self):
        self.size = int(_LOGICAL * _SS)
        self.img = Image.new("RGBA", (self.size, self.size), (*C_BLACK, 255))
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
        self.d.polygon(
            self.pts(points),
            fill=fill,
            outline=outline,
            width=max(1, round(self.s(width))) if outline else 0,
        )

    def line(self, points: list[tuple[float, float]], color, width: float) -> None:
        self.d.line(
            self.pts(points),
            fill=color,
            width=max(1, round(self.s(width))),
            joint="curve",
        )


class _Sprites:
    """缓存 sprite（tap 环 / touch 花瓣 / 打击特效），等价 JS 的 sprite cache。"""

    def __init__(self):
        self.taps: dict[tuple, Image.Image] = {}
        self.petals: dict[tuple, Image.Image] = {}
        self.hits: dict[tuple, Image.Image] = {}

    # ---- tap：getTapSprite（L461）＝ drawTapNoteVector 画在离屏画布（L495）
    def tap(self, pos: int, is_break: bool, is_each: bool, is_ex: bool) -> Image.Image:
        key = (pos, is_break, is_each, is_ex)
        cached = self.taps.get(key)
        if cached:
            return cached
        f = _RADIUS * _R_NOTE * 1.36  # d
        p = f * _R_INNER  # p
        sw = _STROKE * _RADIUS
        pad = sw * 3 + f * 0.25 + 4
        size = math.ceil((f + pad) * 2 * _SS)
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

        # EX 覆盖环（renderExRing L395）：环带 f→f*1.19 填充
        if is_ex:
            overlay = C_EX_BREAK if is_break else C_EX_NORMAL
            d.polygon(
                sector(
                    f,
                    f * _EX_ALPHA,
                    0,
                    math.tau,
                ),
                fill=overlay,
            )
        # 四象限渐变环（renderDirectionalTapRing L423）：渐变轴随按钮角
        rot = _button_angle(pos)
        mid = (p + f) / 2
        for k in range(4):
            a0 = rot + k * math.pi / 2
            a1 = a0 + math.pi / 2
            g0 = (
                cx + math.cos(a0 + math.pi / 4) * mid * _SS,
                cy + math.sin(a0 + math.pi / 4) * mid * _SS,
            )
            g1 = (
                cx + math.cos(a1 + math.pi / 4) * mid * _SS,
                cy + math.sin(a1 + math.pi / 4) * mid * _SS,
            )
            path = sector(p, f, a0, a1)
            bbox = (
                int(min(x for x, _ in path) - 2),
                int(min(y for _, y in path) - 2),
                int(max(x for x, _ in path) + 2),
                int(max(y for _, y in path) + 2),
            )
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
        # 黑描边（非 EX）+ 白双环 + 中心点（drawTapNoteVector L504-513）
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

    # ---- 打击特效 sprite（getHitEffectShapeSprite L7308）
    def hit(self, shape: str) -> Image.Image:
        cached = self.hits.get(shape)
        if cached:
            return cached
        d_size = _RADIUS * _R_NOTE * 1.36 * 1.5  # d
        blur = _RADIUS * (4 / 300 * 0.7) * 2 * 2.4 / 2  # l/X
        pad = _STROKE * _RADIUS * 2 + blur * 2 + 4
        size = math.ceil((d_size + pad) * 2 * _SS)
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img, "RGBA")
        cx = cy = size / 2
        rr = d_size * _SS
        if shape == "star":
            pts = []
            for k in range(10):
                ang = k * math.pi / 5 + math.pi * 1.5
                rad = rr if k % 2 == 0 else rr * 0.5
                pts.append((cx + math.cos(ang) * rad, cy + math.sin(ang) * rad))
            lw = max(2, round(_STROKE * _RADIUS * 2 * _SS))
            glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ImageDraw.Draw(glow).line(
                [*pts, pts[0]], fill=(*C_HIT_GOLD, 255), width=lw, joint="curve"
            )
            glow = glow.filter(ImageFilter.GaussianBlur(blur * _SS))
            ga = glow.getchannel("A").point(lambda v: int(v * 0.55))
            glow.putalpha(ga)
            img.alpha_composite(glow)
            d.line([*pts, pts[0]], fill=(*C_HIT_GOLD, 255), width=lw, joint="curve")
        else:
            pts = [
                (
                    cx + rr * math.cos(k * math.pi / 3),
                    cy + rr * math.sin(k * math.pi / 3),
                )
                for k in range(6)
            ]
            lw = max(2, round(_STROKE * _RADIUS * 2 * _SS))
            glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ImageDraw.Draw(glow).polygon(pts, outline=(*C_HIT_GOLD, 255), width=lw)
            glow = glow.filter(ImageFilter.GaussianBlur(blur * _SS))
            ga = glow.getchannel("A").point(lambda v: int(v * 0.55))
            glow.putalpha(ga)
            img.alpha_composite(glow)
            d.polygon(pts, outline=(*C_HIT_GOLD, 255), width=lw)
        self.hits[shape] = img
        return img


def _linear_gradient(
    size: tuple[int, int],
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    c0: tuple,
    c1: tuple,
) -> Image.Image:
    import numpy as np

    w, h = size
    ax, ay = x1 - x0, y1 - y0
    len2 = ax * ax + ay * ay or 1.0
    yy, xx = np.mgrid[0:h, 0:w]
    t = np.clip(((xx - x0) * ax + (yy - y0) * ay) / len2, 0, 1)[..., None]
    a = np.array((*c0[:3], 255), dtype=float)
    b = np.array((*c1[:3], 255), dtype=float)
    return Image.fromarray((a * (1 - t) + b * t).astype(np.uint8), "RGBA")


class _Renderer:
    """渲染器（U 基类 + 各渲染器合流；方法注释 L* = JS 源码行号）。"""

    def __init__(self, notes: list[_Note]):
        self.notes = notes
        self.sw = _STROKE * _RADIUS  # note 描边宽（logical px）
        self.slides = [n for n in notes if n.kind == "slide"]
        self.heads = [n for n in notes if n.kind in ("tap", "hold", "slide")]
        self.touches = [n for n in notes if n.kind in ("touch", "touch-hold")]
        self.hit_notes = [
            n for n in notes if n.kind in ("tap", "slide") and not n.is_headless
        ]
        # 完成时刻表（noteCompletionTimes L8280：tap=timing，hold/slide=尾端）
        self.completions: list[float] = []
        self.break_completions: list[float] = []
        for n in notes:
            end = n.timing
            if n.kind == "hold":
                end = n.timing + n.duration
            elif n.kind == "slide":
                delay = n.segments[0][4] if n.segments else 0.0
                end = n.timing + delay + sum(s[5] for s in n.segments)
            elif n.kind == "touch-hold":
                end = n.timing + n.duration
            self.completions.append(end)
            if n.is_break:
                self.break_completions.append(end)
        self.completions.sort()
        self.break_completions.sort()
        # 同刻分组（each 判定与连接弧用）
        self._simul_groups: dict[float, list[_Note]] = {}
        for n in self.heads:
            if n.kind == "hold":
                continue
            self._simul_groups.setdefault(round(n.timing, 1), []).append(n)
        self.sprites = _Sprites()
        self._chain_cache: dict[tuple, list] = {}
        self._arrow_cache: dict[tuple, list] = {}
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
        sprite = self.sprites.tap(note.pos, note.is_break, note.is_each, note.is_ex)
        if pos.alpha < 1:
            sprite = sprite.copy()
            sprite.putalpha(int(255 * pos.alpha))
        x, y = c.s(pos.x) - sprite.width / 2, c.s(pos.y) - sprite.height / 2
        c.img.alpha_composite(sprite, (int(x), int(y)))

    def draw_approach_arcs(self, c: _Canvas, group: list[_Note], now: float) -> None:
        """renderApproachArcsBatch（L343）：接近中 tap 的彗星尾弧（±π/8 主弧 +
        4 段渐隐尾）。"""
        seg = math.pi / 8 / 4
        for note in group:
            if note.timing <= now:
                continue
            pos = _note_position(note, now, c.cx, c.cy, c.radius)
            if not pos.visible:
                continue
            color = _grad_pair(note.is_break, note.is_each)[0]
            angle = _button_angle(note.pos)
            dist = math.hypot(pos.x - c.cx, pos.y - c.cy)
            rr = c.s(dist)
            cx, cy = c.s(c.cx), c.s(c.cy)

            def arc(a0: float, a1: float) -> list[tuple[float, float]]:
                return [
                    (
                        cx + rr * math.cos(a0 + (a1 - a0) * k / 12),
                        cy + rr * math.sin(a0 + (a1 - a0) * k / 12),
                    )
                    for k in range(13)
                ]

            for k in range(4, -1, -1):
                alpha = int(255 * (0.4 if k == 0 else 0.4 * (1 - k / 5)))
                col = (*color, alpha)
                if k == 0:
                    c.line(
                        arc(angle - math.pi / 8, angle + math.pi / 8), col, self.sw / 2
                    )
                else:
                    c.line(
                        arc(
                            angle - math.pi / 8 - k * seg,
                            angle - math.pi / 8 - (k - 1) * seg,
                        ),
                        col,
                        self.sw / 2,
                    )
                    a0 = angle + math.pi / 8 + (k - 1) * seg
                    c.line(arc(a0, angle + math.pi / 8 + k * seg), col, self.sw / 2)

    def draw_connectors(self, c: _Canvas, group: list[_Note], now: float) -> None:
        """renderSimultaneousConnector（L381）：同刻 note 沿所在半径的连接弧。"""
        if len(group) < 2:
            return
        ordered = sorted(group, key=lambda n: n.pos)
        ref = _note_position(ordered[0], now, c.cx, c.cy, c.radius)
        if not ref.visible:
            return
        dist = math.hypot(ref.x - c.cx, ref.y - c.cy)
        color = _grad_pair(ordered[0].is_break, ordered[0].is_each)[0]
        pts = []
        for k in range(17):
            a = (
                _button_angle(ordered[0].pos)
                + (_button_angle(ordered[-1].pos) - _button_angle(ordered[0].pos))
                * k
                / 16
            )
            pts.append((c.cx + math.cos(a) * dist, c.cy + math.sin(a) * dist))
        layer = Image.new("RGBA", c.img.size, (0, 0, 0, 0))
        ImageDraw.Draw(layer, "RGBA").line(
            c.pts(pts),
            fill=(*color, 178),
            width=max(1, round(c.s(self.sw) / 2)),
            joint="curve",
        )
        c.img.alpha_composite(layer)

    # ------------------------------------------------------------ hold（Ce 类 L6429）
    def draw_hold(self, c: _Canvas, note: _Note, now: float) -> None:
        head = _note_position(note, now, c.cx, c.cy, c.radius)
        tail_note = _Note("tap", note.pos, note.timing + note.duration)
        tail = _note_position(tail_note, now, c.cx, c.cy, c.radius)
        angle = _button_angle(note.pos)
        m = c.radius * _R_NOTE * _B_HOLD
        h = m * head.alpha if head.visible else 0.0
        # 尾宽 j：头未进入后半接近窗时渐长（renderHold L6449 j 逻辑）
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
            overlay = (
                C_EX_BREAK
                if note.is_break
                else C_EX_SIMUL
                if note.is_each
                else C_EX_NORMAL
            )
            ex = [
                (hx + (px - hx) * _EX_ALPHA, hy + (py - hy) * _EX_ALPHA)
                for px, py in (OH, K, A2)
            ] + [
                (tx + (px - tx) * _EX_ALPHA, ty + (py - ty) * _EX_ALPHA)
                for px, py in (N, F, L)
            ]
            c.polygon(ex, fill=overlay)
        bw = self.sw * 3
        c.polygon(outer, outline=C_BLACK, width=bw)
        c.polygon(inner, outline=C_BLACK, width=bw)
        sides = [
            (OH, K, z, B),
            (K, F, B, w2),
            (F, N, w2, w1),
            (N, L, w1, w3),
            (L, A2, w3, A3),
            (A2, OH, A3, z),
        ]
        for os_, oe_, is_, ie_ in sides:
            self._quad_gradient(c, os_, oe_, is_, ie_, _mix(c0, C_WHITE, _P_MIX), c1)
        flick = _hold_flicker(now - note.timing) if active else 1.0
        white = _brightness(C_WHITE, flick)
        c.polygon(outer, outline=white, width=self.sw)
        c.polygon(inner, outline=white, width=self.sw)
        if active:
            self._head_glow(c, hx, hy, c0)
        else:
            c.fill_circle(hx, hy, h * 0.15, c0)
        # 尾端点（尾进入后半接近窗后出现，L6577）
        if (
            tail.visible
            and note.duration
            and note.timing + note.duration - now <= _HALF_APPROACH
        ):
            c.fill_circle(tx, ty, m_tail * 0.15, c0)
        # 进行中按压涟漪（it 类 L7186，screen 混合）
        if active:
            self._hold_ripples(c, note, now)

    def _poly_gradient(self, c: _Canvas, poly: list, c0, c1,
                       g0: tuple, g1: tuple) -> None:
        path = c.pts(poly)
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
        grad = _linear_gradient(
            mask.size,
            g0[0] * _SS - bbox[0],
            g0[1] * _SS - bbox[1],
            g1[0] * _SS - bbox[0],
            g1[1] * _SS - bbox[1],
            c0,
            c1,
        )
        c.img.paste(grad, (bbox[0], bbox[1]), mask)

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
        gx0, gy0 = c.s((os_[0] + is_[0]) / 2), c.s((os_[1] + is_[1]) / 2)
        gx1, gy1 = c.s((oe_[0] + ie_[0]) / 2), c.s((oe_[1] + ie_[1]) / 2)
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
        """drawActiveHeadGlow（L6583）：E 表径向渐变。"""
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
        import numpy as np

        r = c.s(_T_GLOW * c.radius)
        if r < 1:
            return
        size = int(r * 2) + 4
        yy, xx = np.mgrid[0:size, 0:size]
        dist = np.hypot(xx - size / 2, yy - size / 2) / (size / 2)
        arr = np.zeros((size, size, 4), dtype=np.uint8)
        for i in range(len(table) - 1):
            t0, r0, g0, b0 = table[i]
            t1, r1, g1, b1 = table[i + 1]
            msk = (dist >= t0) & (dist <= t1)
            frac = (dist[msk] - t0) / (t1 - t0 + 1e-9)
            arr[msk, 0] = np.round(color[0] * (r0 + (r1 - r0) * frac)).clip(0, 255)
            arr[msk, 1] = np.round(color[1] * (g0 + (g1 - g0) * frac)).clip(0, 255)
            arr[msk, 2] = np.round(color[2] * (b0 + (b1 - b0) * frac)).clip(0, 255)
        arr[..., 3] = np.round(255 * np.clip(1 - dist, 0, 1)).astype(np.uint8)
        glow = Image.fromarray(arr, "RGBA")
        c.img.alpha_composite(glow, (int(x * _SS - size / 2), int(y * _SS - size / 2)))

    def _hold_ripples(self, c: _Canvas, note: _Note, now: float) -> None:
        """renderPressRippleAt（L7211）：按住期间金圈扩散（screen 混合近似加法）。"""
        bx, by = self.button_position(note.pos)
        elapsed = (now - note.timing) / 1000
        rings = []
        k0 = max(0, math.floor((elapsed - _RIPPLE_QE) / _RIPPLE_GE))
        k1 = math.floor(elapsed / _RIPPLE_GE)
        for k in range(k0, k1 + 1):
            for offset in _RIPPLE_KE:
                n = elapsed - (k * _RIPPLE_GE + offset)
                if 0 <= n < _RIPPLE_QE:
                    radius = (_RIPPLE_YE + _RIPPLE_XE * n) * c.radius
                    alpha = min(1.0, (_RIPPLE_QE - n) / 0.1667) * _RIPPLE_JE
                    if radius >= 0.75 and alpha > _RIPPLE_MIN_ALPHA:
                        rings.append((radius, alpha))
        if not rings:
            return
        rings.sort(key=lambda t: -t[0])
        region = int(max(rr for rr, _ in rings) * _SS) + 8
        cx, cy = c.s(bx), c.s(by)
        x0, y0 = int(max(0, cx - region)), int(max(0, cy - region))
        x1, y1 = int(min(c.img.width, cx + region)), int(min(c.img.height, cy + region))
        if x1 <= x0 or y1 <= y0:
            return
        base = c.img.crop((x0, y0, x1, y1))
        add = Image.new("RGBA", base.size, (0, 0, 0, 0))
        ad = ImageDraw.Draw(add, "RGBA")
        for radius, alpha in rings:
            rr = radius * _SS
            ad.ellipse(
                (cx - x0 - rr, cy - y0 - rr, cx - x0 + rr, cy - y0 + rr),
                outline=(*C_RIPPLE, int(255 * alpha)),
                width=max(1, round(c.s(self.sw))),
            )
        blended = ImageChops_screen(base.convert("RGB"), add.convert("RGB"))
        c.img.paste(blended.convert("RGBA"), (x0, y0))


def ImageChops_screen(a: Image.Image, b: Image.Image) -> Image.Image:
    import numpy as np

    arr = (
        255
        - (255 - np.asarray(a, dtype=np.int16))
        * (255 - np.asarray(b, dtype=np.int16))
        // 255
    )
    return Image.fromarray(arr.clip(0, 255).astype(np.uint8), "RGB")


class _SlideRenderer:
    """slide 渲染（xe 类 L5557 + renderSlidePath L5670 + drawStar L6193）。"""

    def __init__(self, renderer: _Renderer):
        self.r = renderer
        self._chain_cache: dict[tuple, list] = {}
        self._arrow_cache: dict[tuple, list] = {}
        self._lut_cache: dict[tuple, list] = {}

    # ---- getBarChain（L5580）：段 → 旋转后锚点列 [{x,y,angle}]
    def bar_chain(self, seg: tuple) -> list | None:
        shape_op, start, end, mid = seg[0], seg[1], seg[2], seg[3]
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
        rot = (start - (1 if mirror else 0)) * math.pi / 4
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
        """ce()（L553）。"""
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

    def _apply_game_arrow_angles(self, chain: list[dict]) -> None:
        n = len(chain)
        raw = [0.0] * n
        for i in range(n - 1):
            raw[i] = math.atan2(
                chain[i + 1]["y"] - chain[i]["y"], chain[i + 1]["x"] - chain[i]["x"]
            )
        raw[n - 1] = raw[n - 2]
        chain[0]["angle"] = raw[0]
        for i in range(1, n - 1):
            chain[i]["angle"] += self._angle_delta(chain[i]["angle"], raw[i]) * 0.5
        chain[n - 1]["angle"] = raw[n - 1]

    # ---- getHiddenCount（L5906）：le 关键帧 → 已被星星吃掉的箭头数
    def hidden_count(self, seg: tuple, progress: float) -> int:
        mapped = self._shape_map(seg[0], seg[1], seg[2], seg[3])
        kf = SLIDE_KEYFRAMES.get(mapped[0], [0]) if mapped else [0]
        if len(kf) < 2:
            return 0
        idx = kf[min(len(kf) - 1, int(progress * (len(kf) - 1)))]
        if seg[0] == "w" and progress > 0:
            return idx + 1
        return idx

    # ---- buildArrowPaths（L6051）：单支箭头 主/影/左半
    def arrow_paths(self, bar: dict) -> tuple[list, list, list] | None:
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
        bis = self._bisector(dx, dy, px, my, hx, gy, r)
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

    # ---- getSegmentLut（L6320）：段路径弧长 LUT（含首尾按钮位）
    def segment_lut(self, seg: tuple, steps: int = 64) -> list[dict]:
        key = (seg[0], seg[1], seg[2], seg[3])
        cached = self._lut_cache.get(key)
        if cached:
            return cached
        chain = self.bar_chain(seg) or []
        start = self.r.button_position(seg[1])
        end = self.r.button_position(seg[2])
        nodes = [start] + [(p["x"], p["y"]) for p in chain] + [end]
        lut = [{"x": nodes[0][0], "y": nodes[0][1], "s": 0.0}]
        acc = 0.0
        for i in range(1, len(nodes)):
            acc += math.dist(nodes[i - 1], nodes[i])
            lut.append({"x": nodes[i][0], "y": nodes[i][1], "s": acc})
        # 步内插值加密（steps 段均匀采样）
        dense = []
        total = acc
        for k in range(steps + 1):
            target = total * k / steps
            i = 0
            while i < len(lut) - 2 and lut[i + 1]["s"] < target:
                i += 1
            span = lut[i + 1]["s"] - lut[i]["s"]
            f = (target - lut[i]["s"]) / span if span > 0 else 0.0
            dense.append(
                {
                    "x": lut[i]["x"] + (lut[i + 1]["x"] - lut[i]["x"]) * f,
                    "y": lut[i]["y"] + (lut[i + 1]["y"] - lut[i]["y"]) * f,
                    "s": target,
                }
            )
        self._lut_cache[key] = dense
        return dense

    def point_along(self, t: float, segments: list[tuple]) -> tuple[float, float]:
        """getPointAlongPath（L6343）：跨段弧长插值。"""
        luts = [self.segment_lut(s) for s in segments]
        lengths = [lut[-1]["s"] for lut in luts]
        total = sum(lengths) or 1.0
        target = t * total
        acc = 0.0
        for seg, lut, length in zip(segments, luts, lengths):
            if acc + length >= target:
                local = target - acc
                i = 0
                while i < len(lut) - 2 and lut[i + 1]["s"] < local:
                    i += 1
                span = lut[i + 1]["s"] - lut[i]["s"]
                f = (local - lut[i]["s"]) / span if span > 0 else 0.0
                return (
                    lut[i]["x"] + (lut[i + 1]["x"] - lut[i]["x"]) * f,
                    lut[i]["y"] + (lut[i + 1]["y"] - lut[i]["y"]) * f,
                )
            acc += length
        return self.r.button_position(segments[-1][2])

    def path_tangent(self, t: float, segments: list[tuple]) -> float:
        p0 = self.point_along(max(0.0, t - 0.01), segments)
        p1 = self.point_along(min(1.0, t + 0.01), segments)
        return math.atan2(p1[1] - p0[1], p1[0] - p0[0])

    # ---- 轨道透明度 ge()（L5530）
    def track_alpha(self, note: _Note, now: float) -> float:
        is_split = len(note.segments) > 1
        approach = _APPROACH_MS
        delay = note.segments[0][4] if note.segments else 0.0
        r = round((delay + 1) * 10)
        star_delay = note.timing - approach + approach * r / 21
        if is_split and now <= star_delay:
            return 0.0
        if now + _DELAY_FADE >= note.timing:
            return (120 / 255) if is_split else 1.0
        i = now - star_delay
        if i <= 0:
            return 0.0
        span = note.timing - star_delay
        if is_split or span >= _TRAIL_RAMP:
            return min(0.5, 0.5 * i / _TRAIL_RAMP)
        return max(0.0, min(0.5, 0.5 * (1 - (note.timing - now) / i)))

    # ---- drawSegmentArrows（L5935）
    def draw_segment_arrows(
        self,
        c: _Canvas,
        seg: tuple,
        progress: float,
        alpha: float,
        is_break: bool,
        is_simul: bool,
        normal_color_break: bool,
    ) -> None:
        chain = self.bar_chain(seg)
        if not chain:
            return
        hidden = self.hidden_count(seg, progress)
        stroke = (
            C_BREAK_DOT
            if is_break and not normal_color_break
            else C_SLIDE_SIMUL
            if is_simul
            else C_SLIDE_CYAN
        )
        fill_main = C_SLIDE_ARROW if is_break and not normal_color_break else stroke
        fill_left = C_SLIDE_SIMUL if is_break and not normal_color_break else None
        o = len(chain) - 1
        for i in range(o, hidden - 1, -1):
            bar = chain[i]
            if is_break or i != o:
                angle = (
                    bar["angle"]
                    if seg[0] != "-"
                    else math.atan2(
                        chain[min(i + 1, o)]["y"] - chain[max(0, i - 1)]["y"],
                        chain[min(i + 1, o)]["x"] - chain[max(0, i - 1)]["x"],
                    )
                )
            else:
                a0 = chain[max(0, i - 1)]
                a1 = chain[min(o, i + 1)]
                angle = math.atan2(a1["y"] - a0["y"], a1["x"] - a0["x"])
            paths = self.arrow_paths({"x": bar["x"], "y": bar["y"], "angle": angle})
            if not paths:
                continue
            main, shadow, left = paths
            c.polygon(shadow, fill=(*C_BLACK, int(102 * alpha)))
            c.polygon(
                main,
                fill=(*fill_main, int(255 * alpha)),
                outline=(*C_BLACK, int(255 * alpha)),
                width=self.r.sw,
            )
            if fill_left:
                c.polygon(left, fill=(*fill_left, int(255 * alpha)))

    # ---- drawStar（L6193）
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

        if is_ex:
            # renderExStarRing（L6258）：双层十角环 1.19 倍覆盖
            overlay = C_EX_SLIDE
            c.polygon(star_pts(s * _EX_ALPHA, cc * _EX_ALPHA, rotation), fill=overlay)
        c.polygon(star_pts(s, cc, rotation), outline=C_BLACK, width=sw3)
        c.polygon(star_pts(inner_s, u, rotation), outline=C_BLACK, width=sw3)
        c.polygon(star_pts(s, cc, rotation), fill=(*color, int(255 * alpha)))
        c.polygon(star_pts(s, cc, rotation), outline=C_WHITE, width=self.r.sw)
        c.polygon(star_pts(inner_s, u, rotation), outline=C_WHITE, width=self.r.sw)
        c.fill_circle(x, y, size * 0.15, color)

    # ---- renderSlideStar（L6114）：起点生长 → 沿路径移动
    def draw_slide_star(self, c: _Canvas, note: _Note, now: float) -> None:
        if not note.segments:
            return
        delay = note.segments[0][4]
        start = note.timing + delay
        total = sum(s[5] for s in note.segments)
        if now < note.timing:
            return
        color = (
            C_BREAK_DOT
            if note.is_break
            else C_SLIDE_SIMUL
            if note.is_each
            else C_SLIDE_CYAN
        )
        if now >= start:
            t = min(1.0, (now - start) / total if total > 0 else 1.0)
            px, py = self.point_along(t, note.segments)
            rot = self.path_tangent(t, note.segments) + math.pi / 2
            size = _RADIUS * _STAR
            alpha = 1.0
        else:
            if note.is_headless:
                return
            px, py = self.r.button_position(note.segments[0][1])
            r = min(1.0, (now - note.timing) / delay)
            size = _RADIUS * _STAR * (0.5 + 0.5 * r)
            alpha = r
            rot = self.path_tangent(0.0, note.segments) + math.pi / 2
        self.draw_star(c, px, py, size, color, rot, note.is_ex, alpha)

    # ---- slide 起点星形头（renderSingleSlideStart → renderStarHead L8546）
    def draw_slide_head(self, c: _Canvas, note: _Note, now: float) -> None:
        pos = _note_position(note, now, c.cx, c.cy, c.radius)
        if not pos.visible:
            return
        size = c.radius / 12.5 * _STAR_HEAD
        color = (
            C_BREAK_DOT
            if note.is_break
            else C_SLIDE_SIMUL
            if note.is_each
            else C_SLIDE_CYAN
        )
        self.draw_star(c, pos.x, pos.y, size, color, 0.0, note.is_ex, pos.alpha)


class _TouchRenderer:
    """touch 渲染（Ie 类 L6751）。"""

    def __init__(self, renderer: _Renderer):
        self.r = renderer

    def position(self, note: _Note) -> tuple[float, float]:
        """z()（L134）：传感器半径 M 表 + 角度（D/E 用按钮角，A/B 偏 22.5°）。"""
        sensor = note.sensor or "C"
        if sensor == "C":
            return _CENTER, _CENTER
        num = note.touch_num or 1
        ratio = {"A": 400 / 480, "B": 220 / 480, "D": 410 / 480, "E": 310 / 480}[sensor]
        base = (
            -math.pi / 2 + (num - 1) * math.pi / 4
            if sensor in ("D", "E")
            else -math.pi / 2 + math.pi / 8 + (num - 1) * math.pi / 4
        )
        d = _RADIUS * ratio
        return _CENTER + math.cos(base) * d, _CENTER + math.sin(base) * d

    def draw(self, c: _Canvas, note: _Note, now: float) -> None:
        is_hold = note.kind == "touch-hold"
        dt = note.timing - now
        approach = _APPROACH_MS * 0.9  # getTouchApproachTimeMs D=0.9
        life = (note.duration + 50) if is_hold else 50.0
        if dt > approach or dt < -life:
            return
        fade = 1.0
        if dt > approach * 0.95:
            fade = 1 - (dt - approach * 0.95) / (approach * 0.05)
        flick = 1.0
        if 0 < dt < approach and approach - dt < 150:
            flick = (approach - dt) / 150
        p_out = c.radius * _TOUCH_OUT * 1.1
        p_in = c.radius * _TOUCH_IN * 1.3
        h = p_out
        if 0 < dt <= approach:
            e = 1 - dt / approach
            h = p_out - (p_out - p_in) * e * e * e * e
        elif dt <= 0:
            h = p_in
        pos = self.position(note)
        petal = c.radius * _TOUCH_IN * 1.3  # 花瓣尺寸 m（固定）
        simul = note.is_each
        quad = (-math.pi / 4, math.pi / 4, 3 * math.pi / 4, -3 * math.pi / 4)
        base_off = 0.0 if is_hold else -math.pi / 4
        alpha = fade * flick
        if alpha <= 0:
            return
        for k in range(4):
            a = quad[k] + base_off
            pcx = pos[0] + math.cos(a) * h
            pcy = pos[1] + math.sin(a) * h
            tip = (
                pcx + math.cos(a + math.pi) * petal,
                pcy + math.sin(a + math.pi) * petal,
            )
            left = (
                pcx + math.cos(a + math.pi / 2) * petal,
                pcy + math.sin(a + math.pi / 2) * petal,
            )
            right = (
                pcx + math.cos(a - math.pi / 2) * petal,
                pcy + math.sin(a - math.pi / 2) * petal,
            )
            if is_hold:
                colors = (
                    _mix(C_TOUCH_HOLD[k], C_WHITE, 0.24),
                    C_TOUCH_HOLD[k],
                    _mix(C_TOUCH_HOLD[k], C_BLACK, 0.22),
                    _mix(C_TOUCH_HOLD[k], C_WHITE, 0.5),
                )
                self._tri_gradient(c, tip, left, right, colors, pos, alpha)
            elif simul:
                self._tri_gradient(
                    c, tip, left, right, (C_TOUCH_SIMUL_Y, C_TOUCH_SIMUL_G), pos, alpha
                )
            else:
                self._tri_gradient(
                    c, tip, left, right, (C_TOUCH_CYAN, C_TOUCH_BLUE), pos, alpha
                )
            self._tri_outline(
                c, tip, left, right, (*C_WHITE, int(255 * alpha)), self.r.sw
            )
        # 中心点（center L6951）：黑描边×3 + 填色 + 白描边
        cr = c.radius * (1 / 37.5) * 0.8
        c.stroke_circle(pos[0], pos[1], cr, C_BLACK, self.r.sw * 3)
        c.fill_circle(pos[0], pos[1], cr, C_TOUCH_CENTER_S if simul else C_TOUCH_CENTER)
        c.stroke_circle(pos[0], pos[1], cr, C_WHITE, self.r.sw)
        # 命中后六边形扩散（renderTouchHitEffects Ft 类，近似）
        if now >= note.timing:
            burst = (now - note.timing) / 450
            if 0 <= burst <= 1:
                hex_r = c.radius * (0.06 + 0.12 * burst)
                alpha = int(255 * (1 - burst))
                pts = [
                    (
                        pos[0] + hex_r * math.cos(k * math.pi / 3 + math.pi / 6),
                        pos[1] + hex_r * math.sin(k * math.pi / 3 + math.pi / 6),
                    )
                    for k in range(6)
                ]
                c.polygon(pts, outline=(*C_SLIDE_SIMUL, alpha), width=self.r.sw)

    def _tri_gradient(
        self, c: _Canvas, tip, left, right, colors, origin, alpha
    ) -> None:
        path = c.pts([tip, left, right])
        bbox = (
            max(0, int(min(x for x, _ in path) - 2)),
            max(0, int(min(y for _, y in path) - 2)),
            min(c.img.width, int(max(x for x, _ in path) + 2)),
            min(c.img.height, int(max(y for _, y in path) + 2)),
        )
        if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            return
        mask = Image.new("L", (bbox[2] - bbox[0], bbox[3] - bbox[1]), 0)
        ImageDraw.Draw(mask).polygon(
            [(x - bbox[0], y - bbox[1]) for x, y in path], fill=255
        )
        grad = _linear_gradient(
            mask.size,
            origin[0] * _SS - bbox[0],
            origin[1] * _SS - bbox[1],
            tip[0] * _SS - bbox[0],
            tip[1] * _SS - bbox[1],
            colors[0],
            colors[-1],
        )
        c.img.paste(grad, (bbox[0], bbox[1]), mask)

    def _tri_outline(self, c: _Canvas, tip, left, right, color, width) -> None:
        c.d.line(
            c.pts([tip, left, right, tip]),
            fill=color,
            width=max(1, round(c.s(width))),
            joint="curve",
        )


class _ChartRenderer:
    """整帧组合（renderFrame L7997 层序 + prepareRenderNotes L8232）。"""

    def __init__(self, chart: SimaiDifficulty):
        self.chart = chart
        self.notes = _map_notes(chart)
        self.slide_renderer = _SlideRenderer(_Renderer(self.notes))
        self.renderer = self.slide_renderer.r
        self.touches = _TouchRenderer(self.renderer)
        # 静态底图（黑底 + 判定圈 + 按钮点）与 clip 圆 alpha mask：预生成，每帧 copy
        base = _Canvas()
        self._judgment_line(base)
        # FPS 行静态（渲染时 fps 参数恒定）
        r300 = _RADIUS / 300
        n16 = round(16 * r300) * _SS
        n20, n4 = round(20 * r300) * _SS, round(4 * r300) * _SS
        n22 = round(22 * r300) * _SS
        base.d.text(
            (n20, n20 + n22 + n4 + n16 + n4 * 2), "FPS: 30",
            font=self._font(n16), fill=C_FPS_GREEN,
            stroke_width=1, stroke_fill=(0, 0, 0, 191),
        )
        self._base_img = base.img
        self._hud_cache: dict[str, str] = {}
        import numpy as np

        size = base.img.width
        yy, xx = np.mgrid[0:size, 0:size]
        circle = np.hypot(xx - size / 2, yy - size / 2) <= size / 2 - 1
        self._clip_mask = Image.fromarray((circle * 255).astype(np.uint8), "L")

    # ---- 帧渲染
    def render_frame(self, now_ms: float, fps: int) -> Image.Image:
        c = _Canvas()
        c.img = self._base_img.copy()
        c.d = ImageDraw.Draw(c.img, "RGBA")
        self._hud(c, now_ms, fps)
        # clip 圆内播放场（ctx.clip 等价：先画到独立层再按圆裁切合成）
        field = Image.new("RGBA", c.img.size, (0, 0, 0, 0))
        fc = _Canvas()
        fc.img = field
        fc.d = ImageDraw.Draw(field, "RGBA")
        # 1) 轨道（slides，稳定层）
        for slide in self.renderer.slides:
            self._draw_tracks(fc, slide, now_ms)
        # 2) 移动星
        for slide in self.renderer.slides:
            self.slide_renderer.draw_slide_star(fc, slide, now_ms)
        # 3) 接近弧 + 分层头（tap/hold/slide start，同刻分组）
        groups: dict[float, list[_Note]] = {}
        for n in self.renderer.heads:
            if 0 < n.timing - now_ms <= _APPROACH_MS or (
                n.kind == "hold" and n.timing <= now_ms < n.timing + n.duration
            ):
                groups.setdefault(round(n.timing, 1), []).append(n)
        for key in sorted(groups, reverse=True):
            group = groups[key]
            self.slide_renderer.r.draw_approach_arcs(fc, group, now_ms) if all(
                n.kind == "tap" for n in group
            ) else None
            non_hold = [n for n in group if n.kind != "hold"]
            self.renderer.draw_connectors(fc, non_hold, now_ms)
            for n in group:
                if n.kind == "tap":
                    pos = _note_position(n, now_ms, c.cx, c.cy, c.radius)
                    if pos.visible:
                        self.renderer.draw_tap(fc, n, pos)
                elif n.kind == "hold":
                    self.renderer.draw_hold(fc, n, now_ms)
                else:
                    self.slide_renderer.draw_slide_head(fc, n, now_ms)
        # 4) touch
        for touch in self.renderer.touches:
            self.touches.draw(fc, touch, now_ms)
        # 5) 打击特效（taps + slide starts，450ms）
        for n in self.renderer.hit_notes:
            dt = now_ms - n.timing
            if 0 <= dt <= _HIT_MS:
                self._tap_hit_effect(fc, n, dt / _HIT_MS)
        # clip 圆合成（静态 mask）
        c.img.paste(field, (0, 0), self._clip_mask)
        return c.img

    def _draw_tracks(self, fc: _Canvas, note: _Note, now: float) -> None:
        if not note.segments:
            return
        delay = note.segments[0][4]
        total = sum(s[5] for s in note.segments)
        end = note.timing + delay + total
        if now > end:
            return
        alpha = self.slide_renderer.track_alpha(note, now)
        if alpha <= 0:
            return
        progress = 0.0
        if now >= note.timing + delay and total > 0:
            progress = min(1.0, (now - note.timing - delay) / total)
        acc = 0.0
        for i, seg in enumerate(note.segments):
            seg_span = seg[5] / total if total > 0 else 0.0
            seg_progress = 0.0
            if progress > acc:
                seg_progress = (
                    min(1.0, (progress - acc) / seg_span) if seg_span > 0 else 1.0
                )
            acc += seg_span
            seg_alpha = alpha
            self.slide_renderer.draw_segment_arrows(
                fc, seg, seg_progress, seg_alpha, seg[6], note.is_each, False
            )

    def _tap_hit_effect(self, fc: _Canvas, note: _Note, progress: float) -> None:
        """renderHitEffectAt（L7277）：金色 sprite + 1 主 4 环绕。"""
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
        shape = "star" if note.is_break else "hexagon"
        sprite = self.renderer.sprites.hit(shape)
        angle = _button_angle(note.pos)
        px, py = (
            fc.radius * math.cos(angle) + fc.cx,
            fc.radius * math.sin(angle) + fc.cy,
        )
        rot = angle  # ctx.rotate(buttonAngle)

        def blit(cx, cy, sz):
            ratio = sz / d_size
            img = (
                sprite
                if abs(ratio - 1) < 1e-6
                else sprite.resize(
                    (max(2, int(sprite.width * ratio)),) * 2, Image.BILINEAR
                )
            )
            # sprite 以按钮角旋转（ctx.translate+rotate 等价）
            if abs(rot) > 1e-9:
                img = img.rotate(-math.degrees(rot), resample=Image.BILINEAR)
            fc.img.alpha_composite(
                img, (int(cx * _SS - img.width / 2), int(cy * _SS - img.height / 2))
            )

        x = math.pi / 6
        s_ang = -math.pi / 6
        c_ang = x + math.pi * fade_l
        w_ang = x + math.pi * (1 + fade_l)
        t_ang = s_ang - math.pi * fade_l
        e_ang = s_ang + math.pi * (1 - fade_l)
        lay = Image.new("RGBA", fc.img.size, (0, 0, 0, 0))
        ld = ImageDraw.Draw(lay)
        blit(px, py, f)
        blit(px + math.cos(c_ang) * h, py + math.sin(c_ang) * h * 0.7, p)
        blit(px + math.cos(w_ang) * h, py + math.sin(w_ang) * h, p)
        blit(px + math.cos(t_ang) * h, py + math.sin(t_ang) * h, m2)
        blit(px + math.cos(e_ang) * h, py + math.sin(e_ang) * h, m2)
        lay.putalpha(
            ld.point if False else lay.getchannel("A").point(lambda v: int(v * c_alpha))
        )
        fc.img.alpha_composite(lay)

    def _judgment_line(self, c: _Canvas) -> None:
        """renderJudgmentLine（L8086）simple 设计。"""
        c.stroke_circle(c.cx, c.cy, c.radius, C_WHITE, _JLINE * c.radius)
        for pos in range(1, 9):
            bx, by = self.renderer.button_position(pos)
            c.fill_circle(bx, by, _DOT * c.radius, C_WHITE)

    def _hud(self, c: _Canvas, now: float, fps: int) -> None:
        """renderBpmDisplay（L8583）+ renderNoteCounts（L8637）。"""
        d = c.d
        s = _SS
        r300 = c.radius / 300
        n22, n16 = round(22 * r300) * s, round(16 * r300) * s
        n20, n4 = round(20 * r300) * s, round(4 * r300) * s
        n18 = round(18 * r300) * s
        shadow = (0, 0, 0, 191)
        sw = 1

        def text(x, y, txt, px, color, anchor="la"):
            d.text(
                (x, y),
                txt,
                font=self._font(px),
                fill=color,
                anchor=anchor,
                stroke_width=sw,
                stroke_fill=shadow,
            )

        bpm = self._bpm_at(now)
        text(n20, n20, f"BPM: {math.floor(bpm)}", n22, C_WHITE)
        div = self._divisor_at(now)
        beat = self._beat_at(now)
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
        text(n20, n20 + n22 + n4 + n16 + n4 * 2, f"FPS: {fps}", n16, C_FPS_GREEN)
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
        text(
            _LOGICAL * s - n20,
            _LOGICAL * s - n20,
            f"BREAK: {b_done} / {b_total}",
            n22,
            C_ORANGE,
            "ra",
        )
        text(
            _LOGICAL * s - n20,
            _LOGICAL * s - n20 - n22 - n4,
            "无保护",
            n18,
            C_ORANGE,
            "ra",
        )

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

    def _bpm_at(self, ms: float) -> float:
        bpm = self.chart.bpm_events[0][1] if self.chart.bpm_events else 120.0
        for t, b in self.chart.bpm_events:
            if t * 1000.0 <= ms:
                bpm = b
            else:
                break
        return bpm or 120.0

    def _divisor_at(self, ms: float) -> int:
        div = self.chart.divisor_events[0][1] if self.chart.divisor_events else 4
        for t, v in self.chart.divisor_events:
            if t * 1000.0 <= ms:
                div = v
            else:
                break
        return div

    def _beat_at(self, ms: float) -> float:
        events = [
            (0.0, self.chart.bpm_events[0][1] if self.chart.bpm_events else 120.0)
        ]
        events += [(t * 1000.0, b) for t, b in self.chart.bpm_events[1:]]
        beat = 0.0
        for i, (t0, b0) in enumerate(events):
            t1 = events[i + 1][0] if i + 1 < len(events) else ms
            span = min(ms, t1) - t0
            if span > 0:
                beat += span * b0 / 60000.0
            if ms <= t1:
                break
        return beat


def _map_notes(chart: SimaiDifficulty) -> list[_Note]:
    """SimaiDifficulty → 渲染 note 列表（连接滑归并为多段 slide）。"""
    notes: list[_Note] = []
    slides: list[_Note] = []

    def bpm_at(t_sec: float) -> float:
        bpm = chart.bpm_events[0][1] if chart.bpm_events else 120.0
        for t, b in chart.bpm_events:
            if t <= t_sec:
                bpm = b
            else:
                break
        return bpm or 120.0

    for n in sorted(chart.notes, key=lambda x: x.time):
        ms = n.time * 1000.0
        if isinstance(n, SimaiTap):
            notes.append(
                _Note(
                    "tap",
                    n.position + 1,
                    ms,
                    is_break=n.is_break,
                    is_each=n.is_each,
                    is_ex=n.is_ex,
                    visual_only=getattr(n, "visual_only", False),
                )
            )
        elif isinstance(n, SimaiHold):
            notes.append(
                _Note(
                    "hold",
                    n.position + 1,
                    ms,
                    duration=n.duration * 1000.0,
                    is_break=n.is_break,
                    is_each=n.is_each,
                    is_ex=n.is_ex,
                )
            )
        elif isinstance(n, SimaiSlide):
            delay = 60000.0 / bpm_at(n.time)
            seg = (
                n.shape,
                n.start_pos + 1,
                n.end_pos + 1,
                (n.reflect_pos + 1) if n.reflect_pos is not None else None,
                delay,
                n.duration * 1000.0,
                n.is_break,
            )
            prev = slides[-1] if slides else None
            if (
                prev is not None
                and prev.kind == "slide"
                and abs(prev.timing + sum(s[5] for s in prev.segments) - ms) < 1.0
                and prev.segments[-1][2] == seg[1]
            ):
                prev.segments.append(seg)
            else:
                slide = _Note(
                    "slide",
                    n.start_pos + 1,
                    ms,
                    is_break=n.is_break,
                    is_each=n.is_each,
                    is_ex=n.is_ex,
                    segments=[seg],
                    is_headless=n.tapless,
                )
                slides.append(slide)
                notes.append(slide)
        elif isinstance(n, SimaiTouch):
            kind = "touch-hold" if n.duration > 0 else "touch"
            notes.append(
                _Note(
                    kind,
                    0,
                    ms,
                    duration=n.duration * 1000.0,
                    is_each=n.is_each,
                    fire=n.fire,
                    sensor=n.sensor,
                    touch_num=(n.position + 1) if n.position is not None else None,
                )
            )
    return notes


def render_chart_video(
    chart: SimaiDifficulty,
    *,
    title: str,
    level_label: str,
    suffix: int,
    audio: bytes | None,
    out_path: Path,
    fps: int = 30,
    size: int = 600,
    limit_seconds: float | None = None,
) -> Path:
    """渲染谱面预览 mp4（CPU 密集，调用方放线程池）。

    ``limit_seconds`` 截断渲染时长；音频 None 时出无声视频。
    """
    import shutil
    import subprocess

    if shutil.which("ffmpeg") is None:
        raise RuntimeError("未找到 ffmpeg，谱面预览渲染不可用（请安装 ffmpeg）")
    del title, level_label, suffix, size  # 画面元素由渲染器按谱面自持
    renderer = _ChartRenderer(chart)
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
        for i in range(n_frames):
            frame = renderer.render_frame(i / fps * 1000.0, fps)
            proc.stdin.write(
                frame.convert("RGB")
                .resize((int(_LOGICAL), int(_LOGICAL)), Image.LANCZOS)
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
