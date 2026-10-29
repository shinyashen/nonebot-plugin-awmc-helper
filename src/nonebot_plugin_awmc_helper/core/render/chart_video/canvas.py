"""单帧画布原语、渐变/掩码/合成工具、颜色小工具与 sprite 缓存。

站内对照：ctx 绘制语义 + sprite cache（getTapSprite 等逐函数映射见
parity-notes.md）。
"""

import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import constants
from .motion import _button_angle
from .constants import (
    _P_MIX,
    _CENTER,
    _R_NOTE,
    _RADIUS,
    _STROKE,
    C_BLACK,
    C_WHITE,
    _LOGICAL,
    _R_INNER,
    _EX_ALPHA,
    _TOUCH_IN,
    C_TAP_DOT,
    C_HIT_GOLD,
    C_TAP_GRAD,
    C_BREAK_DOT,
    C_SIMUL_DOT,
    C_BREAK_GRAD,
    C_SIMUL_GRAD,
    C_TOUCH_BLUE,
    C_TOUCH_CYAN,
    C_TOUCH_SIMUL_G,
    C_TOUCH_SIMUL_Y,
    C_TOUCHHOLD_PETALS,
)


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
        dist_out = math.hypot(nxt[0] - cur[0], nxt[1] - cur[1]) or 1.0
        rr = min(radius, din / 2, dist_out / 2)
        a_pt = (
            cur[0] + (prev[0] - cur[0]) / din * rr,
            cur[1] + (prev[1] - cur[1]) / din * rr,
        )
        b_pt = (
            cur[0] + (nxt[0] - cur[0]) / dist_out * rr,
            cur[1] + (nxt[1] - cur[1]) / dist_out * rr,
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
        size = math.ceil(half * 2 * constants._SS)
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img, "RGBA")
        cx = cy = size / 2
        c0, c1 = _grad_pair(is_break, is_each)

        def ring_px(r: float, color, width: float) -> None:
            rr = r * constants._SS
            d.ellipse(
                (cx - rr, cy - rr, cx + rr, cy + rr),
                outline=color,
                width=max(1, round(width * constants._SS)),
            )

        def sector(
            r0: float, r1: float, a0: float, a1: float
        ) -> list[tuple[float, float]]:
            out = [
                (
                    cx + math.cos(a0 + (a1 - a0) * k / 12) * r1 * constants._SS,
                    cy + math.sin(a0 + (a1 - a0) * k / 12) * r1 * constants._SS,
                )
                for k in range(13)
            ]
            inn = [
                (
                    cx + math.cos(a1 - (a1 - a0) * k / 12) * r0 * constants._SS,
                    cy + math.sin(a1 - (a1 - a0) * k / 12) * r0 * constants._SS,
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
                cx + math.cos(a0) * mid * constants._SS,
                cy + math.sin(a0) * mid * constants._SS,
            )
            g1 = (
                cx + math.cos(a1) * mid * constants._SS,
                cy + math.sin(a1) * mid * constants._SS,
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
            (
                cx - dot * constants._SS,
                cy - dot * constants._SS,
                cx + dot * constants._SS,
                cy + dot * constants._SS,
            ),
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
        l_blur = c_blur * 2.4  # 系数乘积
        # JS getHitEffectShapeSprite L7329-7332：
        # l = at·R·coef·X，pad = s + u + l/X·ct + 1
        half = d_size + _STROKE * _RADIUS + l_blur * 2.5 + 1
        size = math.ceil(half * 2 * constants._SS)
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        cx = cy = size / 2
        rr = d_size * constants._SS
        lw = max(2, round(_STROKE * _RADIUS * 2 * constants._SS))
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

        # 辉光层：α.55 + blur(l)（L7338 系数），主层：α1 + blur(c)
        glow = stroke_layer().filter(ImageFilter.GaussianBlur(l_blur * constants._SS))
        ga = glow.getchannel("A").point(lambda v: int(v * 0.55))
        glow.putalpha(ga)
        img.alpha_composite(glow)
        main = stroke_layer().filter(ImageFilter.GaussianBlur(c_blur * constants._SS))
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
        size = math.ceil(half * 2 * constants._SS)
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img, "RGBA")
        cx = cy = size / 2
        is_hold = kind == "h"
        p = (-math.pi / 4, math.pi / 4, 3 * math.pi / 4, -3 * math.pi / 4)[k] + (
            0 if is_hold else -math.pi / 4
        )
        m = _RADIUS * _TOUCH_IN * 1.3
        h_ang = p + math.pi
        tip = (
            cx + math.cos(h_ang) * m * constants._SS,
            cy + math.sin(h_ang) * m * constants._SS,
        )
        left = (
            cx + math.cos(p + math.pi / 2) * m * constants._SS,
            cy + math.sin(p + math.pi / 2) * m * constants._SS,
        )
        right = (
            cx + math.cos(p - math.pi / 2) * m * constants._SS,
            cy + math.sin(p - math.pi / 2) * m * constants._SS,
        )
        corner = _RADIUS * (8 / 300) * constants._SS
        outer = _rounded_tri_points(tip, left, right, corner)
        # 内三角（非 hold）：顶点向质心收缩 40%，圆角 0.4×
        gx = (tip[0] + left[0] + right[0]) / 3
        gy = (tip[1] + left[1] + right[1]) / 3
        inner_pts = [
            (gx + (q[0] - gx) * 0.4, gy + (q[1] - gy) * 0.4) for q in (tip, left, right)
        ]
        inner = _rounded_tri_points(
            inner_pts[0], inner_pts[1], inner_pts[2], corner * 0.4
        )

        if layer_kind == "sb":
            # 投影（shadow 层 L6798）：canvas shadowBlur=8/300·R 是设备像素
            # （不受 CTM 影响、且 blur≈2σ），offset=2/300·R；换到 2× 超采样画布
            blur_r = _RADIUS * (8 / 300) * constants._SS / 2
            off = _RADIUS * (2 / 300) * constants._SS
            sil = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ImageDraw.Draw(sil).polygon(outer, fill=(0, 0, 0, 255))
            shadow = sil.filter(ImageFilter.GaussianBlur(blur_r))
            shadow.putalpha(shadow.getchannel("A").point(lambda v: int(v * 0.5)))
            img.alpha_composite(shadow, (round(off), round(off)))
            d.line(
                [*outer, outer[0]],
                fill=(*C_BLACK, 255),
                width=round(sw * 3 * constants._SS),
                joint="curve",
            )
            if not is_hold:
                d.line(
                    [*inner, inner[0]],
                    fill=(*C_BLACK, 255),
                    width=round(sw * 3 * constants._SS),
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
                width=max(1, round(sw * constants._SS)),
                joint="curve",
            )
            if not is_hold:
                d.line(
                    [*inner, inner[0]],
                    fill=(*C_WHITE, 255),
                    width=max(1, round(sw * constants._SS)),
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


def ImageChops_darker(a: Image.Image, b: Image.Image) -> Image.Image:
    from PIL import ImageChops

    return ImageChops.darker(a, b)
