"""判定/按压即时反馈：按压涟漪与 hold 头部光晕（渲染器 mixin）。

站内对照：it 类（L7143-7241 涟漪，screen 混合）/ drawActiveHeadGlow
（L6584 光晕）。花火在 fireworks、touch 命中在 touch_hit、按钮打击特效
放置逻辑在 composer（待并入本模块）。
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image

from .canvas import (
    _Canvas,
    _clip_layer,
    _screen_composite,
)
from .constants import (
    _R_NOTE,
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
    _RIPPLE_QE_OUT,
    _RIPPLE_MIN_ALPHA,
    _RIPPLE_PROFILE_X,
    _RIPPLE_PROFILE_Y,
)

if TYPE_CHECKING:
    from .note_renderer import _Renderer  # 渲染器主类注解

# 光晕形状缓存：size → (亮度场 float64, fade float64, alpha uint8)。
# 形状只依赖超采样（每配置至多一两种 size），内容不可变、进程级共享
# （同 canvas._CLIP_MASK_CACHE 习语）。
_GLOW_FIELD_CACHE: dict[int, tuple] = {}


class _RendererEffectsMixin:
    """_Renderer 的特效方法（涟漪/光晕）：按渲染器混入。"""

    def _head_glow(self, c: _Canvas, x: float, y: float, color: tuple) -> None:
        """drawActiveHeadGlow（L6584）：E 表径向渐变。

        形状（亮度场/alpha）只依赖超采样（size），颜色逐帧变——几何缓存
        一次构建，每次仅按颜色着色（float64 保留，数值与逐帧重算逐位一致）。
        """
        r = c.s(_T_GLOW * c.radius)
        if r < 1:
            return
        size = int(r * 2) + 2
        cached = _GLOW_FIELD_CACHE.get(size)
        if cached is None:
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
            yy, xx = np.mgrid[0:size, 0:size]
            dist = np.hypot(xx - size / 2, yy - size / 2) / r
            bright = np.zeros((size, size, 3))
            for i in range(len(table) - 1):
                t0, r0, g0, b0 = table[i]
                t1, r1, g1, b1 = table[i + 1]
                msk = (dist >= t0) & (dist <= t1)
                frac = (dist[msk] - t0) / (t1 - t0 + 1e-9)
                bright[msk, 0] = r0 + (r1 - r0) * frac
                bright[msk, 1] = g0 + (g1 - g0) * frac
                bright[msk, 2] = b0 + (b1 - b0) * frac
            # 表覆盖 0..0.9；0.9..1.0 rgb 与 α 同步向 0 插值（canvas rgba stop 语义）
            fade = np.clip((1.0 - dist) / 0.1, 0.0, 1.0)
            fade = np.where(dist <= 0.9, 1.0, fade)
            alpha = np.round(255 * fade).astype(np.uint8)
            cached = (bright, fade, alpha)
            _GLOW_FIELD_CACHE[size] = cached
        bright, fade, alpha = cached
        arr = np.zeros((size, size, 4), dtype=np.uint8)
        for ch in range(3):
            arr[..., ch] = np.round(color[ch] * bright[..., ch]).clip(0, 255)
        arr[..., :3] = (arr[..., :3].astype(np.float32) * fade[..., None]).astype(
            np.uint8
        )
        arr[..., 3] = alpha
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


class _HitEffectRenderer:
    """按钮打击特效（renderHitEffectAt L7289）：5 sprite（1 主 4 环绕）。

    tap/break/hold-end/touch-hold 松手的星形/六边形打击特效放置，
    供 composer 编排调用（同位去重后的最新命中）。
    """

    def __init__(self, renderer: _Renderer):
        self.r = renderer
        self._sprite_cache: dict[tuple, Image.Image] = {}

    def render_at(
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
        sprite = self.r.sprites.hit(shape)
        # JS 先 translate/rotate 画布再画 5 个 sprite：等价于在局部层画好后整体旋转
        # 一次（比逐 sprite rotate 快数倍，也避免重复重采样）
        half = d_size * 1.1 + _RADIUS * (4 / 300 * 0.7) * 2.4 * 3 + 4
        size = math.ceil(half * 2 * fc.ss)
        lay = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        center = size / 2

        def blit(lx: float, ly: float, sz: float) -> None:
            quantized = round(sz * 2) / 2
            key = (shape, quantized)
            img = self._sprite_cache.get(key)
            if img is None:
                ratio = quantized / d_size
                img = (
                    sprite
                    if abs(ratio - 1) < 1e-6
                    else sprite.resize(
                        (
                            max(2, int(sprite.width * ratio)),
                            max(2, int(sprite.height * ratio)),
                        ),
                        Image.Resampling.BILINEAR,
                    )
                )
                if len(self._sprite_cache) > 64:
                    self._sprite_cache.clear()
                self._sprite_cache[key] = img
            if c_alpha < 1:
                img = img.copy()
                img.putalpha(img.getchannel("A").point(lambda v: int(v * c_alpha)))
            lay.alpha_composite(
                img,
                (
                    int(center + lx * fc.ss - img.width / 2),
                    int(center + ly * fc.ss - img.height / 2),
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
        x0 = int(fc.s(x) - center)
        y0 = int(fc.s(y) - center)
        if abs(rot) > 1e-9:
            # 旋转只作用于内容区域：整层 210² BILINEAR 旋转约 1ms/次，裁剪到
            # 「内容外接圆 + 3px 余量」的正方形后再转，采样中心不变、结果一致。
            bbox = lay.getbbox()
            if bbox is None:
                return
            rx = max(abs(bbox[0] - center), abs(bbox[2] - center))
            ry = max(abs(bbox[1] - center), abs(bbox[3] - center))
            radius = math.hypot(rx, ry) + 3
            side = min(size, int(radius * 2) + 1)
            if side < size:
                ox = int(center - side / 2)
                oy = int(center - side / 2)
                sub = lay.crop((ox, oy, ox + side, oy + side))
                sub = sub.rotate(
                    -math.degrees(rot),
                    resample=Image.Resampling.BILINEAR,
                    center=(center - ox, center - oy),
                )
                lay = sub
                x0 += ox
                y0 += oy
                size = side
            else:
                lay = lay.rotate(
                    -math.degrees(rot),
                    resample=Image.Resampling.BILINEAR,
                    center=(center, center),
                )
        box = (x0, y0, x0 + size, y0 + size)
        fc.over(_clip_layer(lay, box, fc.img.width), (x0, y0))
