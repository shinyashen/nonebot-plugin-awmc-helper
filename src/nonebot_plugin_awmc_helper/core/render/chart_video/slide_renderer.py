"""slide 渲染：轨道箭头/wifi chevron/星形头（xe 类 L5557 等移植）。"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw

from . import constants
from .notes import _Pos, _Note
from .canvas import _Canvas, _poly_mask
from .motion import _note_position
from .constants import (
    _QT,
    _STAR,
    _RADIUS,
    C_BLACK,
    C_WHITE,
    _EX_ALPHA,
    _STAR_MIN,
    _WIFI_EASE,
    C_EX_BREAK,
    C_EX_SIMUL,
    C_EX_SLIDE,
    _DELAY_FADE,
    _TRAIL_RAMP,
    C_BREAK_DOT,
    _SLIDE_DELAY,
    C_SLIDE_CYAN,
    C_SLIDE_ARROW,
    C_SLIDE_SIMUL,
    _WIFI_HALF_ANGLE,
    _WIFI_WIDTH_BASE,
)
from ..chart_notes import SlidePart, SlideSegment
from .slide_geometry import _SlideGeometryMixin
from .._lxns_slide_data import SLIDE_KEYFRAMES

if TYPE_CHECKING:
    from .note_renderer import _Renderer  # 渲染器主类注解


def _mirror_pair(pos: int) -> int:
    """ae()：圆滑镜像位（re 表）。"""
    return (-1, 1, 8, 7, 6, 5, 4, 3, 2)[pos]


class _SlideRenderer(_SlideGeometryMixin):
    """slide 渲染（xe 类 L5557 + renderSlidePath L5670 + drawStar L6193）。"""

    def __init__(self, renderer: _Renderer):
        self.r = renderer
        self._chain_cache: dict[tuple, list | None] = {}
        self._lut_cache: dict[tuple, list] = {}
        self._metrics_cache: dict[tuple, float] = {}

    # ---- 轨道透明度 ge()（L5527；config.slideDelay=0）
    def _star_delay(self, note: _Note) -> float:
        """me()（L5514）：轨道淡入起点。"""
        r = round((_SLIDE_DELAY + 1) * 10)
        return note.timing - constants._APPROACH_MS + constants._APPROACH_MS * r / 21

    def track_alpha(self, note: _Note, now: float, fading: bool) -> float:
        """ge()（L5527）：fading=wifi 段（视作 split 档）。"""
        r = self._star_delay(note)
        if fading and now <= r:
            return 0.0
        if now + _DELAY_FADE >= note.timing:
            return (120 / 255) if fading else 1.0
        i = now - r
        if i <= 0:
            return 0.0
        if fading and i > _TRAIL_RAMP:
            return 0.5 if (note.timing - now + _TRAIL_RAMP <= now) else 120 / 255
        a = note.timing - r
        if fading or a >= _TRAIL_RAMP:
            return min(0.5, 0.5 * i / _TRAIL_RAMP)
        return max(0.0, min(0.5, 0.5 * (1 - (note.timing - now) / i)))

    # ---- getRenderPathIndexes + renderSlide（L5556/5656）：去重 + wifi 先行
    def ordered_parts(self, note: _Note) -> list[SlidePart]:
        seen: set[tuple] = set()
        unique: list[SlidePart] = []
        for part in note.parts:
            signature = (
                tuple(
                    (seg.type, seg.start_pos, seg.end_pos, seg.mid_pos)
                    for seg in part.segments
                ),
                round(part.delay_ms, 3),
                round(part.duration_ms, 3),
                part.is_break,
            )
            if signature in seen:
                continue
            seen.add(signature)
            unique.append(part)
        wifi = [p for p in unique if p.segments and p.segments[0].type == "w"]
        rest = [p for p in unique if not (p.segments and p.segments[0].type == "w")]
        return wifi + rest

    # ---- drawSegmentArrows（L5889）：影→主填充→break 左半→黑描边
    def draw_segment_arrows(
        self,
        c: _Canvas,
        seg: SlideSegment,
        progress: float,
        alpha: float,
        is_break: bool,
        is_simul: bool,
        extend: bool,
    ) -> None:
        if seg.type == "w":
            self.draw_wifi_bars(c, seg, progress, alpha, is_break, is_simul)
            return
        hidden = self.hidden_count(seg, progress)
        bars = self.visible_bars(seg, hidden, extend)
        if not bars:
            return
        is_break_track = is_break  # normalColorBreakSlide=false
        stroke = (
            C_BREAK_DOT
            if is_break_track
            else C_SLIDE_SIMUL
            if is_simul
            else C_SLIDE_CYAN
        )
        fill_main = C_SLIDE_ARROW if is_break_track else stroke
        fill_left = C_SLIDE_SIMUL if is_break_track else None
        for bar in bars:
            paths = self.arrow_paths(bar)
            if not paths:
                continue
            main, shadow, left = paths
            c.polygon(shadow, fill=(*C_BLACK, int(102 * alpha)))
            c.polygon(main, fill=(*fill_main, int(255 * alpha)))
            if fill_left:
                c.polygon(left, fill=(*fill_left, int(255 * alpha)))
            c.polygon(main, outline=(*C_BLACK, int(255 * alpha)), width=self.r.sw)

    # ---- renderWifiBars + drawWifiChevronsBatch（L5744/6070）
    def draw_wifi_bars(
        self,
        c: _Canvas,
        seg: SlideSegment,
        progress: float,
        alpha: float,
        is_break: bool,
        is_simul: bool,
    ) -> None:
        kf = SLIDE_KEYFRAMES.get("wifi", [0, 1, 4, 6, 11])
        last_kf = kf[-1]
        hidden = self.hidden_count(seg, progress)
        a0 = self.r.button_position(seg.start_pos)
        b0 = self.r.button_position(seg.end_pos)
        length = math.hypot(b0[0] - a0[0], b0[1] - a0[1])
        if length == 0:
            return
        ux, uy = (b0[0] - a0[0]) / length, (b0[1] - a0[1]) / length
        cos_d, sin_d = math.cos(_WIFI_HALF_ANGLE), math.sin(_WIFI_HALF_ANGLE)
        m0 = 0.075 * length
        h0 = 0.975 * length
        g0 = _RADIUS * 0.075
        color = C_BREAK_DOT if is_break else C_SLIDE_SIMUL if is_simul else C_SLIDE_CYAN
        # 影（后向偏移 5/300·R，α0.4×）与主链各合成一次：JS 把全部 chevron 累进
        # 同一个 Path2D 后一次性填充（L6106-6113），逐多边形填充会让重叠处变暗
        shadow_off = _RADIUS * (5 / 300)
        shadow_polys: list[list[tuple[float, float]]] = []
        main_polys: list[list[tuple[float, float]]] = []
        for e in range(last_kf - 1, hidden - 1, -1):
            t = e / (last_kf - 1)
            along_t = m0 + (h0 - m0) * _WIFI_EASE[e]
            arm_len = cos_d * along_t + g0 * (1 - t)
            width = _RADIUS * _WIFI_WIDTH_BASE * (0.7 + 0.3 * t)
            tip = (a0[0] + ux * along_t, a0[1] + uy * along_t)
            # 手臂定义在路径局部系（+x 前向），按路径方向旋转到世界系（L6106）
            al1x, al1y = -arm_len * cos_d, arm_len * sin_d
            al2x, al2y = -arm_len * cos_d, -arm_len * sin_d
            arm1 = (ux * al1x - uy * al1y, uy * al1x + ux * al1y)
            arm2 = (ux * al2x - uy * al2y, uy * al2x + ux * al2y)
            shadow_pts = self._wifi_chevron_points(
                tip, arm1, arm2, (ux, uy), width, shadow_off
            )
            if shadow_pts:
                shadow_polys.append(shadow_pts)
            main_pts = self._wifi_chevron_points(tip, arm1, arm2, (ux, uy), width, 0.0)
            if main_pts:
                main_polys.append(main_pts)
        for polys, col, shade in (
            (shadow_polys, C_BLACK, 0.4),
            (main_polys, color, 1.0),
        ):
            if not polys:
                continue
            xs = [p[0] for poly in polys for p in poly]
            ys = [p[1] for poly in polys for p in poly]
            x0 = max(0, int(min(xs) * constants._SS) - 2)
            y0 = max(0, int(min(ys) * constants._SS) - 2)
            x1 = min(c.img.width, int(max(xs) * constants._SS) + 3)
            y1 = min(c.img.height, int(max(ys) * constants._SS) + 3)
            if x1 <= x0 or y1 <= y0:
                continue
            mask = Image.new("L", (x1 - x0, y1 - y0), 0)
            md = ImageDraw.Draw(mask)
            for poly in polys:
                md.polygon(
                    [
                        (px * constants._SS - x0, py * constants._SS - y0)
                        for px, py in poly
                    ],
                    fill=255,
                )
            mask = mask.point(lambda v: int(v * alpha * shade))
            lay = Image.new("RGBA", (x1 - x0, y1 - y0), (*col, 255))
            lay.putalpha(mask)
            c.over(lay, (x0, y0))

    def _wifi_chevron_points(
        self,
        tip: tuple[float, float],
        arm1: tuple[float, float],
        arm2: tuple[float, float],
        direction: tuple[float, float],
        width: float,
        offset: float,
    ) -> list[tuple[float, float]] | None:
        """drawWifiChevronsBatch 内联 s()（L6077）：箭尖后掠多边形。"""
        a1x, a1y = arm1
        a2x, a2y = arm2
        p = math.hypot(a1x, a1y)
        m = math.hypot(a2x, a2y)
        if p == 0 or m == 0:
            return None
        cos_ang = max(-1.0, min(1.0, (a1x * a2x + a1y * a2y) / (p * m)))
        g = math.sqrt((1 - cos_ang) / 2)
        if g <= 0.001:
            return None
        back = -((a1x * direction[0] + a1y * direction[1]) / p)
        if back <= 0:
            return None
        v = p / back
        shrink = max(0.0, v - width / g) / v
        apex = (tip[0] - direction[0] * v, tip[1] - direction[1] * v)

        def toward(pt: tuple[float, float]) -> tuple[float, float]:
            return (
                apex[0] + (pt[0] - apex[0]) * shrink,
                apex[1] + (pt[1] - apex[1]) * shrink,
            )

        w1 = (tip[0] + arm1[0], tip[1] + arm1[1])
        w2 = (tip[0] + arm2[0], tip[1] + arm2[1])
        sw1, stp, sw2 = toward(w1), toward(tip), toward(w2)
        pts = [w1, tip, w2, sw2, stp, sw1]
        if offset:
            pts = [
                (x - direction[0] * offset, y - direction[1] * offset) for x, y in pts
            ]
        return pts

    # ---- drawStar（L6184）：黑边→环形填充→白边→中心点
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
        if alpha < 0.999:
            # JS ctx.globalAlpha 作用于整颗星（黑/白边、填充、中心点），
            # 不是只淡出填充——生长期的星整体半透明
            layer = _Canvas(transparent=True)
            self.draw_star(layer, x, y, size, color, rotation, is_ex, 1.0)
            layer.img.putalpha(
                layer.img.getchannel("A").point(lambda v: int(v * alpha))
            )
            c.over(layer.img)
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

        # 黑边：外星仅非 EX（L6193 `if (!a)`）；内星恒画
        if not is_ex:
            c.polygon(star_pts(s, cc, rotation), outline=C_BLACK, width=sw3)
        c.polygon(star_pts(inner_s, u, rotation), outline=C_BLACK, width=sw3)
        # 填充 = 外正向 + 内反向（nonzero 环形，L6220-6228）
        path_o = c.pts(star_pts(s, cc, rotation))
        path_i = c.pts(star_pts(inner_s, u, rotation))
        bbox = (
            int(min(p[0] for p in path_o)),
            int(min(p[1] for p in path_o)),
            int(max(p[0] for p in path_o)) + 1,
            int(max(p[1] for p in path_o)) + 1,
        )
        bbox = (
            max(0, bbox[0]),
            max(0, bbox[1]),
            min(c.img.width, bbox[2]),
            min(c.img.height, bbox[3]),
        )
        if bbox[2] > bbox[0] and bbox[3] > bbox[1]:
            mask = _poly_mask(
                (bbox[2] - bbox[0], bbox[3] - bbox[1]),
                [(px - bbox[0], py - bbox[1]) for px, py in path_o],
                [(px - bbox[0], py - bbox[1]) for px, py in path_i],
            )
            fill_img = Image.new("RGBA", mask.size, (*color[:3], int(255 * alpha)))
            c.img.paste(fill_img, (bbox[0], bbox[1]), mask)
        # 白边 ×2 + 中心点
        c.polygon(star_pts(s, cc, rotation), outline=C_WHITE, width=self.r.sw)
        c.polygon(star_pts(inner_s, u, rotation), outline=C_WHITE, width=self.r.sw)
        c.fill_circle(x, y, size * 0.15, color)

    def draw_ex_star_ring(
        self,
        c: _Canvas,
        x: float,
        y: float,
        size: float,
        is_break: bool,
        is_each: bool,
        split: bool = False,
        rotation: float = 0.0,
    ) -> None:
        """renderExStarRing（L6249）：1.19× 十角环覆盖（pinkSlideStart 关）。

        ``rotation`` 与星体同角度：站内 JS 的环不参与 ctx.rotate，slideRotation
        开启时星体自旋而环不动，视觉上会看到「两颗错位的星」；这里让环跟随
        星体旋转，保持环-星同心（与站内视频的稳定观感一致）。
        """
        overlay = C_EX_BREAK if is_break else C_EX_SIMUL if is_each else C_EX_SLIDE
        scale = _EX_ALPHA
        outer, outer_mid = size * scale, size * 0.5 * scale
        inner, inner_mid = size, size * 0.5

        def pts(radius_outer: float, radius_inner: float, rot: float = 0.0):
            return [
                (
                    x
                    + math.cos(k * math.pi / 5 - math.pi / 2 + rot + rotation)
                    * (radius_outer if k % 2 == 0 else radius_inner),
                    y
                    + math.sin(k * math.pi / 5 - math.pi / 2 + rot + rotation)
                    * (radius_outer if k % 2 == 0 else radius_inner),
                )
                for k in range(10)
            ]

        rotations = [0.0, math.pi] if split else [0.0]
        for rot in rotations:
            path_o = c.pts(pts(outer, outer_mid, rot))
            path_i = c.pts(pts(inner, inner_mid, rot))
            bbox = (
                int(min(p[0] for p in path_o)),
                int(min(p[1] for p in path_o)),
                int(max(p[0] for p in path_o)) + 1,
                int(max(p[1] for p in path_o)) + 1,
            )
            bbox = (
                max(0, bbox[0]),
                max(0, bbox[1]),
                min(c.img.width, bbox[2]),
                min(c.img.height, bbox[3]),
            )
            if bbox[2] > bbox[0] and bbox[3] > bbox[1]:
                mask = _poly_mask(
                    (bbox[2] - bbox[0], bbox[3] - bbox[1]),
                    [(px - bbox[0], py - bbox[1]) for px, py in path_o],
                    [(px - bbox[0], py - bbox[1]) for px, py in path_i],
                )
                fill_img = Image.new("RGBA", mask.size, overlay)
                c.img.paste(fill_img, (bbox[0], bbox[1]), mask)

    # ---- calculateStarRotation（L6321）：接近期星头自旋角
    def calculate_star_rotation(self, note: _Note, now: float) -> float:
        if not note.parts:
            return 0.0
        part = note.parts[0]
        duration = part.duration_ms
        if not part.segments or not duration:
            return 0.0
        length = sum(self.segment_length(s) for s in part.segments)
        rate = length * (480.0 / _RADIUS) / math.pi / duration * 15 * 0.06
        per_ms = -min(rate, 1.08) * (math.pi / 180)
        start = note.timing - constants._APPROACH_MS
        return 0.0 if now < start else per_ms * (now - start)

    # ---- renderSlideStar（L6115）：起点生长 → 沿路径移动（每条并行动径一颗）
    def draw_slide_stars(
        self,
        c: _Canvas,
        note: _Note,
        now: float,
        *,
        crossing: float | None = None,
        only_in_progress: bool = False,
    ) -> None:
        if not note.parts or now < note.timing:
            return
        is_simul_slide = note.simul_slide_count >= 2 or len(note.parts) > 1
        for part in self.ordered_parts(note):
            start = note.timing + part.delay_ms
            if crossing is not None and not (
                start < crossing < start + part.duration_ms
            ):
                # 片段前置空小节：只画延续进内容区的长条 note
                continue
            if only_in_progress and not (start <= now <= start + part.duration_ms):
                continue
            if now > start + part.duration_ms:
                continue
            segments = part.segments
            if not segments:
                continue
            color = (
                C_BREAK_DOT
                if part.is_break
                else C_SLIDE_SIMUL
                if is_simul_slide
                else C_SLIDE_CYAN
            )
            if segments[0].type == "w":
                self._draw_wifi_stars(c, note, part, now, color)
                continue
            progress = 0.0
            if now >= start:
                progress = (
                    min(1.0, (now - start) / part.duration_ms)
                    if part.duration_ms > 0
                    else 1.0
                )
                px, py = self.point_along(progress, segments)
                size = _RADIUS * _STAR
                alpha = 1.0
            else:
                if note.headless_mode == "pop":
                    continue
                px, py = self.r.button_position(segments[0].start_pos)
                r = (
                    min(1.0, (now - note.timing) / part.delay_ms)
                    if part.delay_ms > 0
                    else 1.0
                )
                size = _RADIUS * _STAR * (_STAR_MIN + (1 - _STAR_MIN) * r)
                alpha = r
            rotation = (
                self.path_tangent_angle(progress if now >= start else 0.0, segments)
                + math.pi / 2
            )
            self.draw_star(c, px, py, size, color, rotation, note.is_ex, alpha)

    def _draw_wifi_stars(
        self,
        c: _Canvas,
        note: _Note,
        part: SlidePart,
        now: float,
        color: tuple,
    ) -> None:
        """renderWifiStars（L6140）：主路 + 两侧 ±1 位三线三星。"""
        segments = part.segments
        if not segments:
            return
        start = note.timing + part.delay_ms
        progress = 0.0
        if now >= start:
            progress = (
                min(1.0, (now - start) / part.duration_ms)
                if part.duration_ms > 0
                else 1.0
            )
        a0, b0 = segments[0].start_pos, segments[0].end_pos
        targets = [b0, (b0 - 1 - 1 + 8) % 8 + 1, (b0 - 1 + 1) % 8 + 1]
        for end in targets:
            p_start = self.r.button_position(a0)
            p_end = self.r.button_position(end)
            rotation = math.atan2(p_end[1] - p_start[1], p_end[0] - p_start[0]) + (
                math.pi / 2
            )
            if now < start:
                if note.headless_mode == "pop":
                    continue
                px, py = p_start
                r = (
                    min(1.0, (now - note.timing) / part.delay_ms)
                    if part.delay_ms > 0
                    else 1.0
                )
                size = _RADIUS * _STAR * (_STAR_MIN + (1 - _STAR_MIN) * r)
                alpha = r
            else:
                px = p_start[0] + (p_end[0] - p_start[0]) * progress
                py = p_start[1] + (p_end[1] - p_start[1]) * progress
                size = _RADIUS * _STAR
                alpha = 1.0
            self.draw_star(c, px, py, size, color, rotation, note.is_ex, alpha)

    # ---- 星星 tap 头（renderStarTapNote L8535）：`$` 修饰的 tap/break
    def draw_star_tap(self, c: _Canvas, note: _Note, pos: _Pos, now: float) -> None:
        size = c.radius / 12.5 * pos.alpha * 1.15 * 1.25
        color = (
            C_BREAK_DOT
            if note.is_break
            else C_SLIDE_SIMUL
            if note.is_each
            else C_SLIDE_CYAN
        )
        rotation = -now * _QT if note.is_spinning_star else 0.0
        if note.is_ex:
            self.draw_ex_star_ring(
                c, pos.x, pos.y, size, note.is_break, note.is_each, False, rotation
            )
        self.draw_star(c, pos.x, pos.y, size, color, rotation, note.is_ex)

    # ---- slide 起点星形头（renderSingleSlideStart L8411）
    def draw_slide_head(self, c: _Canvas, note: _Note, now: float) -> None:
        pos = _note_position(note, now, c.cx, c.cy, c.radius)
        if not pos.visible:
            return
        is_simul = note.is_each
        if note.has_tap_head:
            self.r.draw_tap(
                c,
                _Note(
                    "tap",
                    note.pos,
                    note.timing,
                    is_break=note.is_break,
                    is_ex=note.is_ex,
                ),
                pos,
            )
            return
        size = c.radius / 12.5 * pos.alpha * 1.15 * 1.25
        color = (
            C_BREAK_DOT
            if note.is_break
            else C_SLIDE_SIMUL
            if is_simul
            else C_SLIDE_CYAN
        )
        rotation = self.calculate_star_rotation(note, now)
        if note.is_ex:
            self.draw_ex_star_ring(
                c,
                pos.x,
                pos.y,
                size,
                note.is_break,
                is_simul,
                bool(note.parts and len(note.parts) > 1),
                rotation,
            )
        if len(note.parts) > 1:
            self._draw_split_slide_star(
                c, pos.x, pos.y, size, color, rotation, note.is_ex
            )
        else:
            self.draw_star(c, pos.x, pos.y, size, color, rotation, note.is_ex)

    def _draw_split_slide_star(
        self,
        c: _Canvas,
        x: float,
        y: float,
        size: float,
        color: tuple,
        rotation: float,
        is_ex: bool,
    ) -> None:
        """renderSplitSlideStar（L8427）：`*` 分裂滑轨的双星头（正交两枚十角环）。"""

        def star_pts(outer: float, inner: float, rot: float):
            return [
                (
                    x
                    + math.cos(k * math.pi / 5 + rot)
                    * (outer if k % 2 == 0 else inner),
                    y
                    + math.sin(k * math.pi / 5 + rot)
                    * (outer if k % 2 == 0 else inner),
                )
                for k in range(10)
            ]

        if not is_ex:
            for rot in (-math.pi / 2, math.pi / 2):
                c.polygon(
                    star_pts(size, size * 0.5, rot + rotation),
                    outline=C_BLACK,
                    width=self.r.sw * 3,
                )
        # 填充：两个朝向的环（外正向 + 内 0.55 反向）
        for rot in (-math.pi / 2, math.pi / 2):
            outer_ring = [
                (
                    x
                    + math.cos(k * math.pi / 5 + rot + rotation)
                    * (size if k % 2 == 0 else size * 0.5),
                    y
                    + math.sin(k * math.pi / 5 + rot + rotation)
                    * (size if k % 2 == 0 else size * 0.5),
                )
                for k in range(10)
            ]
            inner_ring = [
                (
                    x
                    + math.cos(k * math.pi / 5 + rot + rotation)
                    * (size * 0.55 if k % 2 == 0 else size * 0.275),
                    y
                    + math.sin(k * math.pi / 5 + rot + rotation)
                    * (size * 0.55 if k % 2 == 0 else size * 0.275),
                )
                for k in range(10)
            ][::-1]
            path = [*c.pts(outer_ring), *c.pts(inner_ring)]
            mask = Image.new("L", (c.img.width, c.img.height), 0)
            ImageDraw.Draw(mask).polygon(path, fill=255)
            fill_img = Image.new("RGBA", c.img.size, (*color[:3], int(255 * 1.0)))
            c.img.paste(fill_img, (0, 0), mask)
        for rot in (-math.pi / 2, math.pi / 2):
            c.polygon(
                star_pts(size, size * 0.5, rot + rotation),
                outline=C_WHITE,
                width=self.r.sw,
            )
        c.fill_circle(x, y, size * 0.15, color)
