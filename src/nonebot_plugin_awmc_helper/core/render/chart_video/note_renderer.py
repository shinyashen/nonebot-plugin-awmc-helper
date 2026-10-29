"""基础渲染器 _Renderer：tap/hold 绘制、接近指示、同刻分组与完成时刻表。

站内对照：U 基类与 ne/Ce 类（L439 tap、L6429 hold、L333 接近弧、L378 连接线）；
涟漪/光晕特效方法在 effects._RendererEffectsMixin。
"""

from __future__ import annotations

import math
import bisect

from PIL import Image, ImageDraw, ImageFont

from . import constants
from .notes import _Pos, _Note
from .canvas import (
    ImageChops_darker,
    _mix,
    _Canvas,
    _grad_pair,
    _brightness,
    _linear_gradient,
)
from .motion import _button_angle, _hold_flicker, _note_position
from .effects import _RendererEffectsMixin
from .sprites import _Sprites
from .constants import (
    _P_MIX,
    _B_HOLD,
    _CENTER,
    _R_NOTE,
    _RADIUS,
    _STROKE,
    C_BLACK,
    C_WHITE,
    _C_TRAVEL,
    _EX_ALPHA,
    C_TAP_DOT,
    _INNER_HEX,
    C_EX_BREAK,
    C_EX_SIMUL,
    C_BREAK_DOT,
    C_EX_NORMAL,
    C_SIMUL_DOT,
    C_SLIDE_CYAN,
)
from ..chart_notes import (
    SiteChart,
    note_completion_times,
    break_completion_times,
    break_noex_completion_times,
)


class _Renderer(_RendererEffectsMixin):
    """渲染器（U 基类 + 各渲染器合流；方法注释 L* = JS 源码行号）。

    涟漪/光晕特效方法在 effects._RendererEffectsMixin（零行为差异混入）。
    """

    def __init__(
        self,
        notes: list[_Note],
        site: SiteChart,
        hit_notes: list[tuple[int, float, bool]],
    ):
        self.notes = notes
        self.site = site
        self.sw = _STROKE * _RADIUS  # note 描边宽（logical px）
        self.slides = [n for n in notes if n.kind == "slide"]
        self.holds = [n for n in notes if n.kind == "hold"]
        self.taps = [n for n in notes if n.kind == "tap"]
        self.touches = [n for n in notes if n.kind in ("touch", "touch-hold")]
        # hitEffectNotes（L8268）：带数字位的非 touch/非 hold-start 事件
        # （tap/break/simultaneous、hold-end、非 headless slide），按时刻升序
        self.hit_notes = sorted(hit_notes, key=lambda item: item[1])
        # 同刻分组（calculateSimultaneousCounts L8191，按 timingMs.toFixed(3)）
        self._simul_groups: dict[float, list[_Note]] = {}
        for n in notes:
            if n.has_delay_marker:  # JS L8193：反引号 pseudo-each 不参与同刻分组
                continue
            self._simul_groups.setdefault(round(n.timing, 3), []).append(n)
        # touch-hold-end 是独立 note（L8193 不跳过），参与同刻计数
        for note in self.touches:
            if note.kind != "touch-hold" or note.duration <= 0:
                continue
            end = round(note.timing + note.duration, 3)
            self._simul_groups.setdefault(end, []).append(_Note("touch-end", 0, 0.0))
        for group in self._simul_groups.values():
            plain = [
                n
                for n in group
                if (n.kind == "tap" and not n.is_break)
                or n.kind in ("hold", "slide", "touch", "touch-hold", "touch-end")
            ]
            breaks = [
                n for n in group if n.is_break and n.kind in ("tap", "hold", "slide")
            ]
            is_simul = len(plain) >= 2 or (len(breaks) >= 1 and len(plain) >= 1)
            slide_count = sum(1 for n in group if n.kind == "slide")
            for n in group:
                n.is_each = is_simul
                n.simul_slide_count = slide_count
        # 完成时刻表（noteCompletionTimes/breakCompletionTimes L8251）
        self.completions = note_completion_times(site.notes)
        self.break_completions = break_completion_times(site.notes)
        self.noex_break_completions = break_noex_completion_times(site.notes)
        self.sprites = _Sprites()
        self._fonts: dict[int, ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}

    # ------------------------------------------------------------ 通用
    def button_position(self, pos: int) -> tuple[float, float]:
        a = _button_angle(pos)
        return _CENTER + math.cos(a) * _RADIUS, _CENTER + math.sin(a) * _RADIUS

    def count_done(self, times: list[float], ms: float) -> int:

        return bisect.bisect_right(times, ms)

    # ------------------------------------------------------------ tap（ne 类）
    def draw_tap(self, c: _Canvas, note: _Note, pos: _Pos) -> None:
        """renderTapNote L439：sprite 显示尺寸 ×scale（接近期缩小，非 alpha）。"""
        x, y = c.s(pos.x), c.s(pos.y)
        if note.is_ex:
            # EX 覆盖环画在 scale=1 的 sprite 内（半径 f..1.19f），sprite 整体再按
            # note scale 缩放（renderTapNote L439-449）→ 显示环 = [f·s, 1.19f·s]
            f = _RADIUS * _R_NOTE * 1.36
            s = pos.alpha
            r0, r1 = f * s, _EX_ALPHA * f * s
            if r1 > r0:
                color = (
                    C_EX_BREAK
                    if note.is_break
                    else C_EX_SIMUL
                    if note.is_each
                    else C_EX_NORMAL
                )
                lay = Image.new("RGBA", c.img.size, (0, 0, 0, 0))
                ImageDraw.Draw(lay).ellipse(
                    (
                        x - r1 * constants._SS,
                        y - r1 * constants._SS,
                        x + r1 * constants._SS,
                        y + r1 * constants._SS,
                    ),
                    fill=color,
                )
                hole = Image.new("L", c.img.size, 0)
                ImageDraw.Draw(hole).ellipse(
                    (
                        x - r0 * constants._SS,
                        y - r0 * constants._SS,
                        x + r0 * constants._SS,
                        y + r0 * constants._SS,
                    ),
                    fill=255,
                )
                lay.putalpha(ImageChops_darker(lay.getchannel("A"), hole))
                c.over(lay)
        sprite = self.sprites.tap(note.pos, note.is_break, note.is_each, note.is_ex)
        if abs(pos.alpha - 1) > 1e-3:
            new_size = max(2, round(sprite.width * pos.alpha))
            if new_size % 2 != sprite.width % 2:
                new_size += 1
            sprite = sprite.resize((new_size, new_size), Image.Resampling.BILINEAR)
        c.over(sprite, (int(x - sprite.width / 2), int(y - sprite.height / 2)))

    def draw_approach_arc(
        self, c: _Canvas, pos_btn: int, dist: float, color: tuple
    ) -> None:
        """renderApproachArc（L333，批量版 L345 同绘制）：±π/8 主弧 + 4 段
        渐隐尾，线宽 sw（全宽）。"""
        angle = _button_angle(pos_btn)
        seg = math.pi / 8 / 4
        for k in range(4, -1, -1):
            alpha = 0.4 if k == 0 else 0.4 * (1 - k / 5)
            col = (*color, int(255 * alpha))
            if k == 0:
                c.line(
                    c.arc_points(
                        c.cx, c.cy, dist, angle - math.pi / 8, angle + math.pi / 8
                    ),
                    col,
                    self.sw,
                )
            else:
                c.line(
                    c.arc_points(
                        c.cx,
                        c.cy,
                        dist,
                        angle - math.pi / 8 - k * seg,
                        angle - math.pi / 8 - (k - 1) * seg,
                    ),
                    col,
                    self.sw,
                )
                a0 = angle + math.pi / 8 + (k - 1) * seg
                c.line(
                    c.arc_points(c.cx, c.cy, dist, a0, angle + math.pi / 8 + k * seg),
                    col,
                    self.sw,
                )

    def draw_tap_approach_arcs(self, c: _Canvas, taps: list[_Note], now: float) -> None:
        """renderTapApproachArcs L8506：tap 批量彗星尾弧（颜色 L8532）。"""
        for n in taps:
            if n.timing <= now:
                continue
            pos = _note_position(n, now, c.cx, c.cy, c.radius)
            if not pos.visible:
                continue
            color = (
                C_BREAK_DOT
                if n.is_break
                else C_SIMUL_DOT
                if n.is_each
                else C_SLIDE_CYAN
                if n.is_star
                else C_TAP_DOT
            )
            self.draw_approach_arc(
                c, n.pos, math.hypot(pos.x - c.cx, pos.y - c.cy), color
            )

    def draw_connector(self, c: _Canvas, pos_a: int, pos_b: int, dist: float) -> None:
        """renderSimultaneousConnector L378：min→max 角弧（|Δ|=4 整圆），GOLD。"""
        a0 = _button_angle(pos_a)
        a1 = _button_angle(pos_b)
        delta = pos_b - pos_a
        while delta > 4:
            delta -= 8
        while delta < -4:
            delta += 8
        col = (*C_SIMUL_DOT, 178)  # α0.7
        lay = Image.new("RGBA", c.img.size, (0, 0, 0, 0))
        ld = ImageDraw.Draw(lay, "RGBA")
        if abs(delta) == 4:
            pts = c.arc_points(c.cx, c.cy, dist, 0, math.tau)
        else:
            lo, hi = (a0, a1) if delta > 0 else (a1, a0)
            if hi < lo:
                hi += math.tau
            pts = c.arc_points(c.cx, c.cy, dist, lo, hi)
        ld.line(c.pts(pts), fill=col, width=max(1, round(c.s(self.sw))), joint="curve")
        c.over(lay)

    # ------------------------------------------------------------ hold（Ce 类 L6429）
    def draw_hold(self, c: _Canvas, note: _Note, now: float) -> None:
        head = _note_position(note, now, c.cx, c.cy, c.radius)
        tail_note = _Note("tap", note.pos, note.timing + note.duration)
        tail = _note_position(tail_note, now, c.cx, c.cy, c.radius)
        angle = _button_angle(note.pos)
        m = c.radius * _R_NOTE * _B_HOLD
        h = m * head.alpha if head.visible else 0.0
        # 尾宽 j：头未进入后半接近窗时渐长（renderHold L6464 j 逻辑，基于头 timing）
        start_dt = note.timing - now
        j = 1.0
        if start_dt > constants._HALF_APPROACH:
            j = max(
                0.0,
                1 - (start_dt - constants._HALF_APPROACH) / constants._HALF_APPROACH,
            )
        m_tail = m * j
        if head.visible:
            hx, hy = head.x, head.y
        else:
            hx, hy = (
                c.cx + math.cos(angle) * c.radius * _C_TRAVEL,
                c.cy + math.sin(angle) * c.radius * _C_TRAVEL,
            )
        if tail.visible:
            tx, ty = tail.x, tail.y
        else:
            tx, ty = (
                c.cx + math.cos(angle) * c.radius * _C_TRAVEL,
                c.cy + math.sin(angle) * c.radius * _C_TRAVEL,
            )
        c0, c1 = _grad_pair(note.is_break, note.is_each)
        active = head.visible and note.timing <= now < note.timing + note.duration

        def hv(cx0, cy0, half, base_offset):
            base = angle + base_offset
            return (cx0 + math.cos(base) * half, cy0 + math.sin(base) * half)

        OH = hv(hx, hy, h, 0)
        K = hv(hx, hy, h, math.pi / 3)
        A2 = hv(hx, hy, h, -math.pi / 3)
        N = hv(tx, ty, m_tail, math.pi)
        F = hv(tx, ty, m_tail, math.pi - math.pi / 3)
        L = hv(tx, ty, m_tail, math.pi + math.pi / 3)
        z = hv(hx, hy, h * _INNER_HEX, 0)
        B = hv(hx, hy, h * _INNER_HEX, math.pi / 3)
        A3 = hv(hx, hy, h * _INNER_HEX, -math.pi / 3)
        w1 = hv(tx, ty, m_tail * _INNER_HEX, math.pi)
        w2 = hv(tx, ty, m_tail * _INNER_HEX, math.pi - math.pi / 3)
        w3 = hv(tx, ty, m_tail * _INNER_HEX, math.pi + math.pi / 3)
        outer = [OH, K, F, N, L, A2]
        inner = [z, B, w2, w1, w3, A3]
        if note.is_ex:
            # EX 覆盖环顶点序 O,k,F,N,L,A（L6519）
            overlay = (
                C_EX_BREAK
                if note.is_break
                else C_EX_SIMUL
                if note.is_each
                else C_EX_NORMAL
            )
            t19 = _EX_ALPHA
            ex = [
                hv(hx, hy, h * t19, 0),
                hv(hx, hy, h * t19, math.pi / 3),
                hv(tx, ty, m_tail * t19, math.pi - math.pi / 3),
                hv(tx, ty, m_tail * t19, math.pi),
                hv(tx, ty, m_tail * t19, math.pi + math.pi / 3),
                hv(hx, hy, h * t19, -math.pi / 3),
            ]
            c.polygon(ex, fill=overlay)
        bw = self.sw * 3
        if not note.is_ex:
            c.polygon(outer, outline=C_BLACK, width=bw)
        c.polygon(inner, outline=C_BLACK, width=bw)
        # 六面渐变：轴 os→oe（外边端点），颜色受呼吸 p() 调制（L6527/6567）
        flick = _hold_flicker(now - note.timing) if active else 1.0
        g_start = _brightness(_mix(c0, C_WHITE, _P_MIX), flick)
        g_end = _brightness(c1, flick)
        sides = [
            (OH, K, z, B),
            (K, F, B, w2),
            (F, N, w2, w1),
            (N, L, w1, w3),
            (L, A2, w3, A3),
            (A2, OH, A3, z),
        ]
        for os_, oe_, is_, ie_ in sides:
            self._quad_gradient(c, os_, oe_, is_, ie_, g_start, g_end)
        white = _brightness(C_WHITE, flick)
        c.polygon(outer, outline=white, width=self.sw)
        c.polygon(inner, outline=white, width=self.sw)
        if active:
            self._head_glow(c, hx, hy, c0)
        else:
            c.fill_circle(hx, hy, h * 0.15, c0)
        # 尾端点（尾进入后半接近窗后出现，填 p(r[0])，L6575-6580）
        if (
            tail.visible
            and note.duration
            and note.timing + note.duration - now <= constants._HALF_APPROACH
        ):
            c.fill_circle(tx, ty, m_tail * 0.15, _brightness(c0, flick))
        # 进行中按压涟漪在特效阶段统一绘制（renderNotes L8138，见 render_frame）

    def _quad_gradient(self, c: _Canvas, os_, oe_, is_, ie_, c0, c1) -> None:
        path = c.pts([os_, oe_, ie_, is_])
        bbox = (
            int(min(x for x, _ in path) - 2),
            int(min(y for _, y in path) - 2),
            int(max(x for x, _ in path) + 2),
            int(max(y for _, y in path) + 2),
        )
        bbox = (
            max(0, bbox[0]),
            max(0, bbox[1]),
            min(c.img.width, bbox[2]),
            min(c.img.height, bbox[3]),
        )
        if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            return
        mask = Image.new("L", (bbox[2] - bbox[0], bbox[3] - bbox[1]), 0)
        ImageDraw.Draw(mask).polygon(
            [(x - bbox[0], y - bbox[1]) for x, y in path], fill=255
        )
        gx0, gy0 = c.s(os_[0]), c.s(os_[1])  # 轴 = 外边两端点（L6567）
        gx1, gy1 = c.s(oe_[0]), c.s(oe_[1])
        grad = _linear_gradient(
            mask.size,
            gx0 - bbox[0],
            gy0 - bbox[1],
            gx1 - bbox[0],
            gy1 - bbox[1],
            c0,
            c1,
        )
        c.img.paste(grad, (bbox[0], bbox[1]), mask)
