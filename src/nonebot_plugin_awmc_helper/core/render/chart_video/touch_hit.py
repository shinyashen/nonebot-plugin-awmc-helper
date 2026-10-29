"""touch 命中特效（Ft 类 L7509）：中心环 + 双层星爆（lighter）。

插值工具（Mt/jt/Nt/Pt）仅本模块消费，随之迁入。
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image, ImageDraw

from . import constants
from .notes import _Note, _touch_position_name
from .canvas import _Canvas, _poly_mask, _additive_composite
from .constants import (
    _RADIUS,
    _TOUCHHIT_GLOW,
    _TOUCHHIT_BURSTS,
    _TOUCHHIT_LIFETIME,
    _TOUCHHIT_RING_MAX,
    _TOUCHHIT_RING_BASE,
    _TOUCHHIT_RING_ALPHA,
    _TOUCHHIT_STAR_SCALE,
    _TOUCHHIT_RING_SPLINE,
    _TOUCHHIT_SPRITE_SIZE,
    _TOUCHHIT_STAR_SPLINE,
    _TOUCHHIT_DRIFT_SPLINE,
)
from .touch_renderer import _TouchRenderer

if TYPE_CHECKING:
    from .note_renderer import _Renderer  # 渲染器主类注解


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
