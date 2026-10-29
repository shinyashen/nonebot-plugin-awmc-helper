"""判定/按压即时反馈：按压涟漪与 hold 头部光晕（渲染器 mixin）。

站内对照：it 类（L7143-7241 涟漪，screen 混合）/ drawActiveHeadGlow
（L6584 光晕）。花火在 fireworks、touch 命中在 touch_hit、按钮打击特效
放置逻辑在 composer（待并入本模块）。
"""

from __future__ import annotations

import math

import numpy as np
from PIL import Image

from .canvas import (
    _Canvas,
    _screen_composite,
)
from .constants import (
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
    _RIPPLE_QE_OUT,
    _RIPPLE_MIN_ALPHA,
    _RIPPLE_PROFILE_X,
    _RIPPLE_PROFILE_Y,
)


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
        c.over(glow, (int(x * c.ss - size / 2), int(y * c.ss - size / 2)))

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
            region = int(r_out * c.ss) + 4
            x0, y0 = int(max(0, cx - region)), int(max(0, cy - region))
            x1, y1 = (
                int(min(c.img.width, cx + region)),
                int(min(c.img.height, cy + region)),
            )
            if x1 <= x0 or y1 <= y0:
                continue
            w, h = x1 - x0, y1 - y0
            yy, xx = np.mgrid[0:h, 0:w]
            dist = np.hypot(xx - (cx - x0), yy - (cy - y0)) / c.ss  # logical
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
