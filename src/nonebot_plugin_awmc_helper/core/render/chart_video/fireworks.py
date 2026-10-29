"""花火特效（Wt 类 L7665）：fire touch（`!`）完成后的单发烟花。"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image, ImageDraw

from .notes import _Note
from .canvas import _Canvas, _clip_layer, _additive_composite
from .constants import (
    _RADIUS,
    _FIREWORK_R,
    _FIREWORK_MS,
    _FIREWORK_LATE,
    _FIREWORK_PHASE,
    _FIREWORK_SMALL,
    _FIREWORK_COLORS,
    _FIREWORK_WEDGE_SCALE,
)
from .touch_renderer import _TouchRenderer

if TYPE_CHECKING:
    from .note_renderer import _Renderer  # 渲染器主类注解


class _Fireworks:
    """花火（Wt 类 L7665）：fire touch（`!`）完成后的烟花。"""

    def __init__(self, renderer: _Renderer):
        self.r = renderer
        self._wedge: Image.Image | None = None
        self._touch = _TouchRenderer(renderer)

    def wedge_sprite(self) -> Image.Image:
        if self._wedge is not None:
            return self._wedge
        ss = self.r.config.supersample
        half = _RADIUS * _FIREWORK_WEDGE_SCALE
        size = math.ceil(half * 2 * ss)
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
                cx + math.cos(t - half_ang) * wedge_len * ss,
                cy + math.sin(t - half_ang) * wedge_len * ss,
            )
            p2 = (
                cx + math.cos(t + half_ang) * wedge_len * ss,
                cy + math.sin(t + half_ang) * wedge_len * ss,
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
            dist = np.hypot(xx + bbox[0] - cx, yy + bbox[1] - cy) / (wedge_len * ss)
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
        ss = self.r.config.supersample
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
        region = int(max_r * ss) + 2
        cx, cy = x * ss, y * ss
        x0, y0 = int(max(0, cx - region)), int(max(0, cy - region))
        x1, y1 = int(min(layer.width, cx + region)), int(min(layer.height, cy + region))
        if x1 <= x0 or y1 <= y0:
            return
        w, h = x1 - x0, y1 - y0
        yy, xx = np.mgrid[0:h, 0:w]
        dist = np.hypot(xx - (cx - x0), yy - (cy - y0)) / ss
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
            dist = np.hypot(xx - c.s(pos[0]), yy - c.s(pos[1])) / c.ss
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
        cx, cy = x * c.ss, y * c.ss
        for e in range(n):
            t = e / n * math.tau + rot
            pts = [
                (cx, cy),
                (
                    cx + math.cos(t - half_ang) * a * c.ss,
                    cy + math.sin(t - half_ang) * a * c.ss,
                ),
                (
                    cx + math.cos(t + half_ang) * a * c.ss,
                    cy + math.sin(t + half_ang) * a * c.ss,
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
            dist = np.hypot(xx - (cx - x0), yy - (cy - y0)) / (a * c.ss)
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
