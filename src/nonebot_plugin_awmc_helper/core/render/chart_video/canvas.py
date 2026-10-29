"""单帧画布原语、渐变/掩码/合成工具、颜色小工具与 sprite 缓存。

站内对照：ctx 绘制语义 + sprite cache（getTapSprite 等逐函数映射见
parity-notes.md）。
"""

import math

import numpy as np
from PIL import Image, ImageDraw

from . import constants
from .constants import (
    _CENTER,
    _RADIUS,
    _STROKE,
    C_BLACK,
    _LOGICAL,
    C_TAP_DOT,
    C_TAP_GRAD,
    C_BREAK_DOT,
    C_SIMUL_DOT,
    C_BREAK_GRAD,
    C_SIMUL_GRAD,
)


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


class _Canvas:
    """单帧画布：logical 600 × SS 超采样，ImageDraw(RGBA) 直接混色。"""

    def __init__(
        self,
        transparent: bool = False,
        base_image: Image.Image | None = None,
        rgb: bool = False,
    ):
        self.size = (
            base_image.width
            if base_image is not None
            else int(_LOGICAL * constants._SS)
        )
        if base_image is not None:
            self.img = base_image.copy()
        elif rgb:
            self.img = Image.new("RGB", (self.size, self.size), C_BLACK)
        else:
            fill = (0, 0, 0, 0) if transparent else (*C_BLACK, 255)
            self.img = Image.new("RGBA", (self.size, self.size), fill)
        self.d = ImageDraw.Draw(self.img, "RGBA")
        self.radius = _RADIUS
        self.cx = _CENTER
        self.cy = _CENTER
        self.sw = _STROKE * _RADIUS  # note 描边宽（logical px）

    def over(
        self, layer: Image.Image, pos=None, mask: Image.Image | None = None
    ) -> None:
        """alpha 叠加到本画布：RGBA 走 alpha_composite；RGB 底板走 paste(mask)。

        RGB 底图不需要整体 alpha_composite（两步 RGBA 合成 + 末尾 convert 约
        4ms/帧），paste 用图层 alpha 作掩码与它逐像素等价（实测 diff=0）且快
        约 10 倍。
        """
        if self.img.mode == "RGB":
            self.img.paste(layer, pos or (0, 0), mask if mask is not None else layer)
        elif pos is None:
            self.img.alpha_composite(layer)
        else:
            self.img.alpha_composite(layer, pos)

    def s(self, v: float) -> float:
        return v * constants._SS

    def pts(self, points: list[tuple[float, float]]) -> list[tuple[float, float]]:
        return [(x * constants._SS, y * constants._SS) for x, y in points]

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
    region = base.crop(box)
    dst = np.asarray(
        region if region.mode == "RGB" else region.convert("RGB"), dtype=np.int32
    )
    src = np.asarray(layer.convert("RGBA"), dtype=np.int32)
    a = src[..., 3:4] / 255.0
    out = dst + (a * src[..., :3] * (255 - dst) // 255).astype(np.int32)
    base.paste(Image.fromarray(out.clip(0, 255).astype(np.uint8), "RGB"), (x0, y0))


def _additive_composite(base: Image.Image, layer: Image.Image, box: tuple) -> None:
    """globalCompositeOperation='lighter'（不透明底）：out = dst + a·src。"""
    x0, y0 = box[0], box[1]
    layer = _clip_layer(layer, box, base.width)
    region = base.crop(box)
    dst = np.asarray(
        region if region.mode == "RGB" else region.convert("RGB"), dtype=np.int32
    )
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


def ImageChops_darker(a: Image.Image, b: Image.Image) -> Image.Image:
    from PIL import ImageChops

    return ImageChops.darker(a, b)
