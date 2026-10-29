"""打击特效：按压涟漪/头部光晕（渲染器 mixin）、花火、touch 命中星爆。

站内对照：it 类（L7186 涟漪）/ drawActiveHeadGlow（L6584）/ Wt 类
（L7665 花火）/ Ft 类（L7509 touch 命中），插值工具 Mt/jt/Nt/Pt。
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image, ImageDraw

from . import constants
from .notes import _Note, _touch_position_name
from .canvas import (
    _Canvas,
    _poly_mask,
    _clip_layer,
    _screen_composite,
    _additive_composite,
)
from .constants import (
    _RADIUS,
    _T_GLOW,
    C_RIPPLE,
    _RIPPLE_ET,
    _RIPPLE_GE,
    _RIPPLE_JE,
    _RIPPLE_KE,
    _RIPPLE_QE,
    _RIPPLE_XE,
    _RIPPLE_YE,
    _RIPPLE_ZE,
    _FIREWORK_R,
    _FIREWORK_MS,
    _FIREWORK_LATE,
    _RIPPLE_QE_OUT,
    _TOUCHHIT_GLOW,
    _FIREWORK_PHASE,
    _FIREWORK_SMALL,
    _FIREWORK_COLORS,
    _TOUCHHIT_BURSTS,
    _RIPPLE_MIN_ALPHA,
    _RIPPLE_PROFILE_X,
    _RIPPLE_PROFILE_Y,
    _TOUCHHIT_LIFETIME,
    _TOUCHHIT_RING_MAX,
    _TOUCHHIT_RING_BASE,
    _TOUCHHIT_RING_ALPHA,
    _TOUCHHIT_STAR_SCALE,
    _FIREWORK_WEDGE_SCALE,
    _TOUCHHIT_RING_SPLINE,
    _TOUCHHIT_SPRITE_SIZE,
    _TOUCHHIT_STAR_SPLINE,
    _TOUCHHIT_DRIFT_SPLINE,
)
from .touch_renderer import _TouchRenderer

if TYPE_CHECKING:
    from .note_renderer import _Renderer  # 渲染器主类注解（终点模块）


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


class _RendererEffectsMixin:
    """_Renderer 的特效方法（涟漪/光晕）：按渲染器混入。"""

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
        c.over(
            glow, (int(x * constants._SS - size / 2), int(y * constants._SS - size / 2))
        )

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
            region = int(r_out * constants._SS) + 4
            x0, y0 = int(max(0, cx - region)), int(max(0, cy - region))
            x1, y1 = (
                int(min(c.img.width, cx + region)),
                int(min(c.img.height, cy + region)),
            )
            if x1 <= x0 or y1 <= y0:
                continue
            w, h = x1 - x0, y1 - y0
            yy, xx = np.mgrid[0:h, 0:w]
            dist = np.hypot(xx - (cx - x0), yy - (cy - y0)) / constants._SS  # logical
            t = (dist - r_in) / (r_out - r_in)
            msk = (t >= 0) & (t <= 1)
            if not msk.any():
                continue
            v = np.zeros_like(t)
            # 原实现逐像素调用 _lerp_stops（每帧 4-5 万次），改用 np.interp
            # 向量化线性插值，数值口径一致（落点仅取 16 个停点包络）。
            v[msk] = np.interp(t[msk], _RIPPLE_PROFILE_X, _RIPPLE_PROFILE_Y)
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
        size = math.ceil(half * 2 * constants._SS)
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
                cx + math.cos(t - half_ang) * wedge_len * constants._SS,
                cy + math.sin(t - half_ang) * wedge_len * constants._SS,
            )
            p2 = (
                cx + math.cos(t + half_ang) * wedge_len * constants._SS,
                cy + math.sin(t + half_ang) * wedge_len * constants._SS,
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
            dist = np.hypot(xx + bbox[0] - cx, yy + bbox[1] - cy) / (
                wedge_len * constants._SS
            )
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
        region = int(max_r * constants._SS) + 2
        cx, cy = x * constants._SS, y * constants._SS
        x0, y0 = int(max(0, cx - region)), int(max(0, cy - region))
        x1, y1 = int(min(layer.width, cx + region)), int(min(layer.height, cy + region))
        if x1 <= x0 or y1 <= y0:
            return
        w, h = x1 - x0, y1 - y0
        yy, xx = np.mgrid[0:h, 0:w]
        dist = np.hypot(xx - (cx - x0), yy - (cy - y0)) / constants._SS
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
            resample=Image.Resampling.BILINEAR,
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
            dist = np.hypot(xx - c.s(pos[0]), yy - c.s(pos[1])) / constants._SS
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
        c.over(_clip_layer(scratch))

    def _draw_wedges_vector(
        self, c: _Canvas, x: float, y: float, s: float, rot: float
    ) -> None:
        a = _RADIUS / 4.5 * s
        if a <= 0:
            return
        lay = Image.new("RGBA", c.img.size, (0, 0, 0, 0))
        n = len(_FIREWORK_COLORS)
        half_ang = math.pi / n * 0.45
        cx, cy = x * constants._SS, y * constants._SS
        for e in range(n):
            t = e / n * math.tau + rot
            pts = [
                (cx, cy),
                (
                    cx + math.cos(t - half_ang) * a * constants._SS,
                    cy + math.sin(t - half_ang) * a * constants._SS,
                ),
                (
                    cx + math.cos(t + half_ang) * a * constants._SS,
                    cy + math.sin(t + half_ang) * a * constants._SS,
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
            dist = np.hypot(xx - (cx - x0), yy - (cy - y0)) / (a * constants._SS)
            fade = np.clip((1.0 - dist) / 0.1, 0.0, 1.0)
            arr = np.zeros((y1 - y0, x1 - x0, 4), dtype=np.uint8)
            arr[..., 0], arr[..., 1], arr[..., 2] = _FIREWORK_COLORS[e]
            arr[..., 3] = (fade * 0.589 * 255).astype(np.uint8)
            lay.paste(Image.fromarray(arr, "RGBA"), (x0, y0), mask)
        c.over(lay)

    def _blit_wedge(self, c: _Canvas, x: float, y: float, e: float, rot: float) -> None:
        wedge = self.wedge_sprite()
        rotated = wedge.rotate(
            -math.degrees(rot),
            resample=Image.Resampling.BILINEAR,
            center=(wedge.width / 2, wedge.height / 2),
        )
        ratio = c.s(e) * 2 / wedge.width
        scaled = rotated.resize(
            (
                max(2, int(rotated.width * ratio)),
                max(2, int(rotated.height * ratio)),
            ),
            Image.Resampling.BILINEAR,
        )
        c.over(
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
        size = math.ceil(
            _TOUCHHIT_SPRITE_SIZE * _TOUCHHIT_STAR_SCALE * 2 * constants._SS
        )
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
                path_o = sparkle_path(
                    outer * 1.08 * constants._SS, inner_r * 1.08 * constants._SS
                )
                path_i = sparkle_path(outer * constants._SS, inner_r * constants._SS)
                d.line(
                    [*path_o, path_o[0]],
                    fill=(*col, int(255 * 0.22)),
                    width=max(1, round(outer * 0.13 * constants._SS)),
                    joint="curve",
                )
                d.line(
                    [*path_i, path_i[0]],
                    fill=(*white, int(255 * 0.88)),
                    width=max(1, round(outer * 0.055 * constants._SS)),
                    joint="curve",
                )
            else:
                # fillStarGradient（L7623）：径向多停点
                cx = cy = size / 2
                path = sparkle_path(outer * constants._SS, inner_r * constants._SS)
                mask = _poly_mask((size, size), path)
                yy, xx = np.mgrid[0:size, 0:size]
                dist = np.hypot(xx - cx, yy - cy) / (outer * constants._SS)
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
        region = int(radius * constants._SS) + 2
        x0, y0 = int(max(0, cx - region)), int(max(0, cy - region))
        x1, y1 = int(min(c.img.width, cx + region)), int(min(c.img.height, cy + region))
        if x1 <= x0 or y1 <= y0:
            return
        w, h = x1 - x0, y1 - y0
        yy, xx = np.mgrid[0:h, 0:w]
        dist = np.hypot(xx - (cx - x0), yy - (cy - y0)) / constants._SS
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
        # 星爆只覆盖命中点周围的小区域：原先建整帧 RGBA 层 + 整帧 additive
        # 合成（900² 下约 24ms/次，84s 段落一次 7-8 个星爆），改为按解析
        # 上界裁 bbox 层，结果一致但开销与面积成正比。
        max_r = (start_r + abs(drift)) * r480 + peak_r * r480 * 0.847 + 2
        region = int(max_r * constants._SS) + 2
        cx, cy = c.s(x), c.s(y)
        x0 = max(0, int(cx - region))
        y0 = max(0, int(cy - region))
        x1 = min(c.img.width, int(cx + region))
        y1 = min(c.img.height, int(cy + region))
        if x1 <= x0 or y1 <= y0:
            return
        lay = Image.new("RGBA", (x1 - x0, y1 - y0), (0, 0, 0, 0))
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
                (
                    max(2, int(sprite.width * ratio)),
                    max(2, int(sprite.height * ratio)),
                ),
                Image.Resampling.BILINEAR,
            )
            scaled = scaled.rotate(
                math.degrees(u2 * math.tau), resample=Image.Resampling.BILINEAR
            )
            if m_alpha < 1:
                scaled.putalpha(
                    scaled.getchannel("A").point(lambda v: int(v * m_alpha))
                )
            lay.alpha_composite(
                scaled,
                (
                    int(c.s(px) - scaled.width / 2) - x0,
                    int(c.s(py) - scaled.height / 2) - y0,
                ),
            )
        _additive_composite(c.img, lay, (x0, y0, x1, y1))
