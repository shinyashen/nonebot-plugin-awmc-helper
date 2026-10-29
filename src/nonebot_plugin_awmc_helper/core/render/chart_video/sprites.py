"""sprite 缓存：tap 环 / touch 花瓣 / 打击特效（getTapSprite 等移植）。"""

from __future__ import annotations

import math

from PIL import Image, ImageDraw, ImageFilter

from .canvas import (
    _mix,
    _dot_color,
    _grad_pair,
    _poly_mask,
    _linear_gradient,
    _multi_stop_gradient,
)
from .motion import _button_angle
from .constants import (
    _P_MIX,
    _R_NOTE,
    _RADIUS,
    _STROKE,
    C_BLACK,
    C_WHITE,
    _R_INNER,
    _EX_ALPHA,
    _TOUCH_IN,
    C_HIT_GOLD,
    C_TOUCH_BLUE,
    C_TOUCH_CYAN,
    C_TOUCH_SIMUL_G,
    C_TOUCH_SIMUL_Y,
    C_TOUCHHOLD_PETALS,
)
from .render_config import RenderConfig


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


class _Sprites:
    """缓存 sprite（tap 环 / touch 花瓣 / 打击特效 / gauge），等价 JS sprite cache。"""

    def __init__(self, config: RenderConfig):
        self._ss = config.supersample
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
        size = math.ceil(half * 2 * self._ss)
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img, "RGBA")
        cx = cy = size / 2
        c0, c1 = _grad_pair(is_break, is_each)

        def ring_px(r: float, color, width: float) -> None:
            rr = r * self._ss
            d.ellipse(
                (cx - rr, cy - rr, cx + rr, cy + rr),
                outline=color,
                width=max(1, round(width * self._ss)),
            )

        def sector(
            r0: float, r1: float, a0: float, a1: float
        ) -> list[tuple[float, float]]:
            out = [
                (
                    cx + math.cos(a0 + (a1 - a0) * k / 12) * r1 * self._ss,
                    cy + math.sin(a0 + (a1 - a0) * k / 12) * r1 * self._ss,
                )
                for k in range(13)
            ]
            inn = [
                (
                    cx + math.cos(a1 - (a1 - a0) * k / 12) * r0 * self._ss,
                    cy + math.sin(a1 - (a1 - a0) * k / 12) * r0 * self._ss,
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
                cx + math.cos(a0) * mid * self._ss,
                cy + math.sin(a0) * mid * self._ss,
            )
            g1 = (
                cx + math.cos(a1) * mid * self._ss,
                cy + math.sin(a1) * mid * self._ss,
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
                cx - dot * self._ss,
                cy - dot * self._ss,
                cx + dot * self._ss,
                cy + dot * self._ss,
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
        size = math.ceil(half * 2 * self._ss)
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        cx = cy = size / 2
        rr = d_size * self._ss
        lw = max(2, round(_STROKE * _RADIUS * 2 * self._ss))
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
        glow = stroke_layer().filter(ImageFilter.GaussianBlur(l_blur * self._ss))
        ga = glow.getchannel("A").point(lambda v: int(v * 0.55))
        glow.putalpha(ga)
        img.alpha_composite(glow)
        main = stroke_layer().filter(ImageFilter.GaussianBlur(c_blur * self._ss))
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
        size = math.ceil(half * 2 * self._ss)
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
            cx + math.cos(h_ang) * m * self._ss,
            cy + math.sin(h_ang) * m * self._ss,
        )
        left = (
            cx + math.cos(p + math.pi / 2) * m * self._ss,
            cy + math.sin(p + math.pi / 2) * m * self._ss,
        )
        right = (
            cx + math.cos(p - math.pi / 2) * m * self._ss,
            cy + math.sin(p - math.pi / 2) * m * self._ss,
        )
        corner = _RADIUS * (8 / 300) * self._ss
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
            blur_r = _RADIUS * (8 / 300) * self._ss / 2
            off = _RADIUS * (2 / 300) * self._ss
            sil = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ImageDraw.Draw(sil).polygon(outer, fill=(0, 0, 0, 255))
            shadow = sil.filter(ImageFilter.GaussianBlur(blur_r))
            shadow.putalpha(shadow.getchannel("A").point(lambda v: int(v * 0.5)))
            img.alpha_composite(shadow, (round(off), round(off)))
            d.line(
                [*outer, outer[0]],
                fill=(*C_BLACK, 255),
                width=round(sw * 3 * self._ss),
                joint="curve",
            )
            if not is_hold:
                d.line(
                    [*inner, inner[0]],
                    fill=(*C_BLACK, 255),
                    width=round(sw * 3 * self._ss),
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
                width=max(1, round(sw * self._ss)),
                joint="curve",
            )
            if not is_hold:
                d.line(
                    [*inner, inner[0]],
                    fill=(*C_WHITE, 255),
                    width=max(1, round(sw * self._ss)),
                    joint="curve",
                )
        self.petals[key] = img
        return img
