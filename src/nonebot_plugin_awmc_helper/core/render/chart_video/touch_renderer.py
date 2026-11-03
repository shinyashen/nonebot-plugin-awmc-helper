"""touch 渲染（Ie 类 L6751）与部件排序（ke/Ae L6672）。"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image, ImageDraw, ImageChops

from .notes import _Note
from .canvas import (
    _mix,
    _Canvas,
    _poly_mask,
    _multi_stop_gradient,
)
from .constants import (
    _CENTER,
    _R_NOTE,
    _RADIUS,
    C_BLACK,
    C_WHITE,
    _TOUCH_IN,
    _GAUGE_GEO,
    _TOUCH_OUT,
    C_SIMUL_DOT,
    _TOUCH_ZONES,
    C_TOUCH_CYAN,
    C_TOUCH_CENTER,
    _TOUCH_FLICK_MS,
    C_TOUCH_CENTER_S,
    C_TOUCHHOLD_GAUGE,
    _TOUCH_SORT_SENTINELS,
)

if TYPE_CHECKING:
    from .note_renderer import _Renderer


def _sort_key(layer: int, order: int) -> int:
    """we()（L6604）：部件排序键。"""
    return ((layer + 32768) << 16 | (order + 32768) & 0xFFFF) & 0xFFFFFFFF


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
        approach = self.r.config.touch_approach_ms  # Majdata TouchDrop.wholeDuration
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

    # ---- gauge 进度填充（getTouchHoldProgressSprite L6818 + L6918 楔形 clip）
    def gauge_sprite(self) -> Image.Image:
        if self._gauge_sprite is not None:
            return self._gauge_sprite
        ss = self.r.config.supersample
        r = _RADIUS / 480
        # 画布按轮廓最大伸达外延（站内 canvas_half=99.75 仅作下限保护）
        half = max(
            _GAUGE_GEO["canvas_half"] * r,
            _GAUGE_GEO["outer_axis"] * r + 2.0,
        )

        def ring_path(axis: float, diag: float) -> list[tuple[float, float]]:
            # 轮廓 = 45° 旋转方 + 与直边相切的大圆弧角（机台实拍确认的特征：
            # 斜边平直、四角宽大圆弧），见 local/reference/lxns-renderer/
            # parity-notes.md touch 节。K 表语义沿站内口径：平边距 a = diag·r、
            # 角弧顶点伸达 c = axis·r；圆角半径由二者反解
            # rc = (a√2 − c)/(√2−1)。圆弧与直边相切（G1 连续）且曲率恒定，
            # 消除站内「二次贝塞尔切角」在接合处的曲率跳变（生硬感的来源）。
            a = diag * r
            c = axis * r
            rc = (a * math.sqrt(2) - c) / (math.sqrt(2) - 1)
            rc = max(0.0, min(rc, a))  # 常数异常时退化为方角/圆角全占
            arc_n = 48  # 每 90° 弧采样数（弦差 ~0.004 设计px，足够圆滑）
            pts: list[tuple[float, float]] = []
            corners = (
                (1, 1, 0.0),
                (-1, 1, math.pi / 2),
                (-1, -1, math.pi),
                (1, -1, 3 * math.pi / 2),
            )
            for sx, sy, base in corners:
                cxc, cyc = sx * (a - rc), sy * (a - rc)
                for k in range(arc_n + 1):
                    t = base + k / arc_n * math.pi / 2
                    pts.append((cxc + rc * math.cos(t), cyc + rc * math.sin(t)))
                # 直边不补中间点：多边形以单一线段直连相邻弧端点。PIL 的
                # polygon 填充对共线中间顶点会逐点独立求扫描线穿越，舍入
                # 抖动直接变成直边上的 ±1px 锯齿（实测对比见
                # local/scratch/th_edge_probe.py）；单一线段则阶梯干净。
            # 方坐标系 → 屏幕系（旋转 -45°，方角落在轴向上）
            inv = 1 / math.sqrt(2)
            return [((x + y) * inv, (y - x) * inv) for x, y in pts]

        size = math.ceil(half * 2 * ss)
        # sprite 内部再翻倍构建后缩回设备尺寸：掩码是 PIL 硬填充多边形（无
        # 抗采样），45° 直边在部分子像素相位下会出现 ±1px 阶梯回退（实测见
        # local/scratch/th_edge_probe.py），加倍光栅化 + 预乘 LANCZOS 缩放
        # 让轮廓亚像素平滑。gauge sprite 每次渲染仅构建一次，开销可忽略。
        ss_b = ss * 2
        size_b = math.ceil(half * 2 * ss_b)
        img = Image.new("RGBA", (size_b, size_b), (0, 0, 0, 0))
        off = size_b / 2

        def to_px(pts):
            return [(px * ss_b + off, py * ss_b + off) for px, py in pts]

        outer = to_px(ring_path(_GAUGE_GEO["outer_axis"], _GAUGE_GEO["outer_diagonal"]))
        # 实心象限填充（机台同构：彩色填满整个圆角菱形，花瓣叠于其上）。
        # 站内的环带（外-内双轮廓）在此不成立：花瓣剪影（弦边 ~31、角向顶点
        # ~43）盖不住环带内缘的平边（36），会露出 4 条弧形黑缝。
        mask = _poly_mask((size_b, size_b), outer, None)
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
                (size_b, size_b),
                g0[0] * ss_b + off,
                g0[1] * ss_b + off,
                g1[0] * ss_b + off,
                g1[1] * ss_b + off,
                stops,
            )
            # 象限矩形裁剪（fillRect i>0?0:-n 等）
            x0q = 0 if ca > 0 else -n_max
            y0q = 0 if sa > 0 else -n_max
            quad_mask = Image.new("L", (size_b, size_b), 0)
            ImageDraw.Draw(quad_mask).rectangle(
                [
                    x0q * ss_b + off,
                    y0q * ss_b + off,
                    (x0q + n_max) * ss_b + off,
                    (y0q + n_max) * ss_b + off,
                ],
                fill=255,
            )

            quad = Image.composite(
                grad, Image.new("RGBA", (size_b, size_b), (0, 0, 0, 0)), quad_mask
            )
            img.alpha_composite(quad)
        # 轮廓裁剪（外轮廓多边形掩码）
        ring = Image.new("RGBA", (size_b, size_b), (0, 0, 0, 0))
        ring.paste(img, (0, 0), mask)
        # 预乘 LANCZOS 缩回设备尺寸（直接缩 RGBA 会在透明边缘渗黑）
        ch = ring.split()
        rgb_p = Image.merge(
            "RGB",
            (
                ImageChops.multiply(ch[0], ch[3]),
                ImageChops.multiply(ch[1], ch[3]),
                ImageChops.multiply(ch[2], ch[3]),
            ),
        )
        a_s = ch[3].resize((size, size), Image.LANCZOS)
        rgb_s = rgb_p.resize((size, size), Image.LANCZOS)
        a_arr = np.asarray(a_s).astype(np.float32) / 255.0
        rgb_arr = np.asarray(rgb_s).astype(np.float32)
        # 反预乘（alpha≥1 处无变化；极小 alpha 处钳制防溢出）
        rgb_u = np.clip(rgb_arr / np.maximum(a_arr, 1e-3)[..., None], 0, 255).astype(
            np.uint8
        )
        out = Image.merge(
            "RGBA",
            (
                Image.fromarray(rgb_u[..., 0]),
                Image.fromarray(rgb_u[..., 1]),
                Image.fromarray(rgb_u[..., 2]),
                a_s,
            ),
        )
        self._gauge_sprite = out
        return out

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
        layer.paste(sprite, (0, 0), ImageChops.darker(sprite.getchannel("A"), pie))
        if alpha < 1:
            layer.putalpha(layer.getchannel("A").point(lambda v: int(v * alpha)))
        c.over(layer, (int(c.s(x) - size / 2), int(c.s(y) - size / 2)))

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
                cx, cy = c.s(pos[0]), c.s(pos[1])
                rr = center_r * c.ss
                lw3 = max(1, round(c.s(self.r.sw * 3)))
                lw1 = max(1, round(c.s(self.r.sw)))
                a255 = int(255 * fade)
                # 局部窗口层（外扩一个最宽描边）：bbox 外 paste 为 no-op，逐像素等价
                half = math.ceil(rr) + lw3 + 2
                x0 = max(0, int(cx - half))
                y0 = max(0, int(cy - half))
                x1 = min(c.img.width, int(cx + half) + 1)
                y1 = min(c.img.height, int(cy + half) + 1)
                if x1 <= x0 or y1 <= y0:
                    return
                lay = Image.new("RGBA", (x1 - x0, y1 - y0), (0, 0, 0, 0))
                ld = ImageDraw.Draw(lay, "RGBA")
                lx, ly = cx - x0, cy - y0
                ld.ellipse(
                    (lx - rr, ly - rr, lx + rr, ly + rr),
                    outline=(*C_BLACK, a255),
                    width=lw3,
                )
                ld.ellipse(
                    (lx - rr, ly - rr, lx + rr, ly + rr),
                    fill=(
                        *(C_TOUCH_CENTER_S if note.is_each else C_TOUCH_CENTER),
                        a255,
                    ),
                )
                ld.ellipse(
                    (lx - rr, ly - rr, lx + rr, ly + rr),
                    outline=(*C_WHITE, a255),
                    width=lw1,
                )
                c.over(lay, (x0, y0))
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
                    c.over(
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
        if dt > self.r.config.approach_ms or dt < -50:
            return
        x, y = self.position(note)
        r_base = _RADIUS * (1 / 4.46) * 1.1 * 1.2
        corner_r = _RADIUS * _R_NOTE
        color = C_SIMUL_DOT if note.is_each else C_TOUCH_CYAN
        lw = max(1, round(3 * _RADIUS / 300 * c.ss))
        # 先收集两圈方框的全部路径点，取并集外接窗口建局部层
        squares: list[list[tuple[float, float]]] = []
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
            squares.append(pts)
        dev = [c.pts(pts) for pts in squares]
        x0 = max(0, int(min(p[0] for pts in dev for p in pts)) - lw - 2)
        y0 = max(0, int(min(p[1] for pts in dev for p in pts)) - lw - 2)
        x1 = min(c.img.width, int(max(p[0] for pts in dev for p in pts)) + lw + 2)
        y1 = min(c.img.height, int(max(p[1] for pts in dev for p in pts)) + lw + 2)
        if x1 <= x0 or y1 <= y0:
            return
        lay = Image.new("RGBA", (x1 - x0, y1 - y0), (0, 0, 0, 0))
        ld = ImageDraw.Draw(lay, "RGBA")
        for pts in dev:
            ld.line(
                [(px - x0, py - y0) for px, py in pts],
                fill=(*color, 255),
                width=lw,
                joint="curve",
            )
        c.over(lay, (x0, y0))
