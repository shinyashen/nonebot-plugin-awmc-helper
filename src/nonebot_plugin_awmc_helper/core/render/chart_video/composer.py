"""整帧组合：renderFrame 层序（L7997）+ prepareRenderNotes（L8232）。"""

from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw

from .hud import _HudMixin
from .notes import _Note, _map_notes, _collect_hit_notes
from .canvas import _Canvas
from .motion import _button_angle, _note_position
from .press_fx import _HitEffectRenderer
from .constants import (
    _DOT,
    _JLINE,
    _HIT_MS,
    C_WHITE,
    C_TAP_DOT,
    C_BREAK_DOT,
    C_SIMUL_DOT,
    C_SLIDE_CYAN,
)
from .fireworks import _Fireworks
from .touch_hit import _TouchHitEffects
from ..chart_notes import SiteChart, SlidePart
from .note_renderer import _Renderer
from .render_config import DEFAULT_CONFIG, RenderConfig
from .slide_renderer import _SlideRenderer
from .touch_renderer import _TouchRenderer


class _ChartRenderer(_HudMixin):
    """整帧组合（renderFrame L7997 层序 + prepareRenderNotes L8232）。"""

    def __init__(self, chart: SiteChart, config: RenderConfig | None = None):
        self.chart = chart
        self.config = config or DEFAULT_CONFIG
        self.notes = _map_notes(chart)
        hit_notes = _collect_hit_notes(chart)
        base_renderer = _Renderer(self.notes, chart, hit_notes, self.config)
        self.slide_renderer = _SlideRenderer(base_renderer)
        self.renderer = base_renderer
        self.touches = _TouchRenderer(base_renderer)
        self.fireworks = _Fireworks(base_renderer)
        self.touch_hits = _TouchHitEffects(base_renderer)
        self.hit_effects = _HitEffectRenderer(base_renderer)
        # BPM 变化点（HUD 闪色：change 时刻表 + 方向）
        self._bpm_changes: list[tuple[float, int]] = []
        previous_bpm: float | None = None
        for timing, bpm, _cumulative in chart.timeline.bpm_points:
            if previous_bpm is not None and bpm != previous_bpm:
                self._bpm_changes.append(
                    (
                        chart.timeline.ms_from_beat(timing),
                        1 if bpm > previous_bpm else -1,
                    )
                )
            previous_bpm = bpm
        # 静态底图（黑底 + 判定圈 + 按钮点）与 clip 圆 alpha mask：预生成每帧 copy
        base = _Canvas(self.config, rgb=True)
        self._judgment_line(base)
        self._base_img = base.img
        size = base.img.width
        yy, xx = np.mgrid[0:size, 0:size]
        circle = np.hypot(xx - size / 2, yy - size / 2) <= size / 2 - 1
        self._clip_mask = Image.fromarray((circle * 255).astype(np.uint8), "L")
        # HUD 文本缓存：(文本, 字号, 颜色, 锚点) → (阴影+文字的合成图, 粘贴偏移)
        self._text_cache: dict[
            tuple, tuple[Image.Image, Image.Image, float, float]
        ] = {}

    # ---- 帧渲染
    def render_frame(self, now_ms: float, fps: int) -> Image.Image:
        from PIL import ImageChops

        c = _Canvas(self.config, base_image=self._base_img)
        # 花火（clip 圆内、HUD 之下，L8101）——直接画在最终画布（此刻只有底图，
        # 与 JS 在 clear/判定线之后立即绘制的背景一致；lighter 混合语义成立）
        fire_notes = [n for n in self.renderer.touches if n.fire]
        if fire_notes:
            self.fireworks.render(c, fire_notes, now_ms)
        self._hud(c, now_ms, fps)
        # clip 圆内播放场（ctx.clip 等价：先画到独立层再按圆裁切合成）
        field = Image.new("RGBA", c.img.size, (0, 0, 0, 0))
        fc = _Canvas(self.config)
        fc.img = field
        fc.d = ImageDraw.Draw(field, "RGBA")
        now = now_ms
        # 1) 轨道 + 2) 移动星（slides 倒序：晚出的先画=在下，L8120/8126）
        for slide in reversed(self.renderer.slides):
            for part in self.slide_renderer.ordered_parts(slide):
                self._draw_tracks(fc, slide, part, now)
        for slide in reversed(self.renderer.slides):
            self.slide_renderer.draw_slide_stars(fc, slide, now)
        # 3) 接近指示（hold/slide 弧 + 全对 GOLD 连接线，L8129）+ tap 弧批（L8131）
        self._draw_approach_indicators(fc, now)
        self.renderer.draw_tap_approach_arcs(fc, self.renderer.taps, now)
        # 4) 分层头（L8271-8283：按时刻倒序绘制=晚出的在下）
        self._draw_layered_heads(fc, now)
        # 5) touch：边框盒（L8133）→ 部件（ke/Ae 排序，L8135）
        self._draw_touches(fc, now)
        # clip 圆合成（静态 mask）：field 的 alpha 圈外清零后 alpha_composite
        # 叠加——底图的判定圈/按钮点保留（paste 会整块替换，把圈内底图抹掉）
        field.putalpha(ImageChops.darker(field.getchannel("A"), self._clip_mask))
        c.over(field)
        # 6) 特效阶段（L8136-8139）：screen/lighter 混合必须对最终画面进行
        # （JS 直接画在主画布上；画在透明 field 层会把底图盖黑）——按 clip 掩码
        # 裁剪后逐一绘制：hold 涟漪 → touch hold 按压/松手 → 命中 → 打击
        fx = _Canvas(self.config)
        fx.img = c.img
        fx.d = ImageDraw.Draw(c.img, "RGBA")
        for n in self.renderer.holds:
            if n.timing <= now < n.timing + n.duration:
                self.renderer.draw_press_ripple(
                    fx,
                    *self.renderer.button_position(n.pos),
                    n.timing,
                    n.timing + n.duration,
                    now,
                )
        self._draw_touch_hold_effects(fx, now)
        self.touch_hits.render(fx, self.renderer.touches, now)
        self._draw_tap_hit_effects(fx, now)
        return c.img

    def _draw_layered_heads(self, fc: _Canvas, now: float) -> None:
        """renderLayeredHeads（L8500）：按时刻倒序绘制；同刻 tap→hold→slide。

        JS 先按 timingMs 降序稳定排序再 reverse，再从尾部倒序绘制——效果是
        晚出的头先画（在下）、早出的后画（在上）；同刻顺序 tap,hold,slide。
        """
        order = {"tap": 0, "hold": 1, "slide": 2}
        items: list[tuple[float, int, _Note]] = []
        for n in self.renderer.taps:
            items.append((n.timing, order["tap"], n))
        for n in self.renderer.holds:
            items.append((n.timing, order["hold"], n))
        for n in self.renderer.slides:
            if not n.is_headless:
                items.append((n.timing, order["slide"], n))
        items.sort(key=lambda item: (-item[0], item[1]))
        for _timing, kind_order, n in items:
            if kind_order == 0:
                pos = _note_position(n, now, fc.cx, fc.cy, fc.radius, self.config)
                if pos.visible:
                    if n.is_star:
                        self.slide_renderer.draw_star_tap(fc, n, pos, now)
                    else:
                        self.renderer.draw_tap(fc, n, pos)
            elif kind_order == 1:
                head = _note_position(n, now, fc.cx, fc.cy, fc.radius, self.config)
                if head.visible:
                    self.renderer.draw_hold(fc, n, now)
            else:
                self.slide_renderer.draw_slide_head(fc, n, now)

    def _draw_approach_indicators(self, fc: _Canvas, now: float) -> None:
        """renderApproachIndicators（L8352）：hold/slide 接近组弧 + 全对连线。"""
        groups: dict[float, list[_Note]] = {}
        for n in self.renderer.holds + [
            s for s in self.renderer.slides if not s.is_headless
        ]:
            groups.setdefault(round(n.timing, 3), []).append(n)
        for key in sorted(groups):
            group = groups[key]
            visible = []
            for n in group:
                if n.timing <= now:
                    continue
                pos = _note_position(n, now, fc.cx, fc.cy, fc.radius, self.config)
                if not pos.visible:
                    continue
                if n.kind == "hold":
                    color = (
                        C_BREAK_DOT
                        if n.is_break
                        else C_SIMUL_DOT
                        if n.is_each
                        else C_TAP_DOT
                    )
                else:  # slide（hasTapHead → TAP_PINK，否则 CYAN，L8366）
                    color = (
                        C_BREAK_DOT
                        if n.is_break
                        else C_SIMUL_DOT
                        if n.is_each
                        else C_TAP_DOT
                        if n.has_tap_head
                        else C_SLIDE_CYAN
                    )
                self.renderer.draw_approach_arc(
                    fc, n.pos, math.hypot(pos.x - fc.cx, pos.y - fc.cy), color
                )
                visible.append((n, pos))
            if len(visible) >= 2:
                dist0 = math.hypot(visible[0][1].x - fc.cx, visible[0][1].y - fc.cy)
                for i in range(len(visible)):
                    for j in range(i + 1, len(visible)):
                        self.renderer.draw_connector(
                            fc, visible[i][0].pos, visible[j][0].pos, dist0
                        )

    def _draw_touches(self, fc: _Canvas, now: float) -> None:
        touches = self.renderer.touches
        # 边框：同位 touch ≥2（renderTouchBorders L8391，倒序绘制）
        count_by_pos: dict[tuple, int] = {}
        for n in touches:
            if n.kind == "touch-hold":
                continue
            dt = n.timing - now
            if -50 <= dt <= self.config.approach_ms:
                key = (n.sensor, n.touch_num)
                count_by_pos[key] = count_by_pos.get(key, 0) + 1
        for n in reversed(touches):
            key = (n.sensor, n.touch_num)
            self.touches.draw_border(fc, n, count_by_pos.get(key, 0), now)
        # 部件（ke/Ae 排序）
        entries = []
        for n in touches:
            e = self.touches.collect(fc, n, now)
            if e is not None:
                entries.append(e)
        for draw, part in _TouchRenderer.ordered_parts(entries):
            draw(part)

    def _draw_touch_hold_effects(self, fc: _Canvas, now: float) -> None:
        """renderTouchHoldPressEffects（L8539）+ Release（L8549）。"""
        for n in self.renderer.touches:
            if n.kind != "touch-hold":
                continue
            end = n.timing + n.duration
            # 按压涟漪
            if n.timing <= now < end:
                x, y = self.touches.position(n)
                self.renderer.draw_press_ripple(fc, x, y, n.timing, end, now)
            # 松手 hexagon 打击特效（角指向圆心，C 区 -π/6）
            if end <= now < end + _HIT_MS:
                x, y = self.touches.position(n)
                if n.sensor == "C":
                    ang = -math.pi / 6
                else:
                    ang = math.atan2(fc.cy - y, fc.cx - x)
                self.hit_effects.render_at(
                    fc, x, y, ang, (now - end) / _HIT_MS, "hexagon"
                )

    def _draw_tracks(
        self, fc: _Canvas, note: _Note, part: SlidePart, now: float
    ) -> None:
        """renderSlidePath 的 tracks 分支（L5670）：按并行动径各自的 delay/duration。"""
        if not part.segments:
            return
        start = note.timing + part.delay_ms
        if now > start + part.duration_ms:
            return
        # 普通段与 wifi 段分别取轨道透明度（JS L5682/5689）
        alpha_plain = self.slide_renderer.track_alpha(note, now, False)
        alpha_wifi = self.slide_renderer.track_alpha(note, now, True)
        if alpha_plain <= 0 and alpha_wifi <= 0:
            return
        ranges = self.slide_renderer.path_ranges(part.segments)
        if ranges is None:  # 零长度路径：getSlidePathMetrics 返回 null 不画
            return
        progress = 0.0
        if now >= start and part.duration_ms > 0:
            progress = min(1.0, (now - start) / part.duration_ms)
        # 轨道配色：同刻 slide≥2 或 `*` 分裂滑轨 → 黄（L5677 d = isSplitSlide）
        is_simul_slide = note.simul_slide_count >= 2 or len(note.parts) > 1
        # 段从后往前画（JS L5688）：第一段压在后段之上
        for i in range(len(part.segments) - 1, -1, -1):
            seg = part.segments[i]
            alpha = alpha_wifi if seg.type == "w" else alpha_plain
            if alpha <= 0:
                continue
            r0, r1 = ranges[i]
            seg_progress = 0.0
            if progress > r0:
                seg_progress = 1.0 if progress >= r1 else (progress - r0) / (r1 - r0)
            extend = i < len(part.segments) - 1
            self.slide_renderer.draw_segment_arrows(
                fc, seg, seg_progress, alpha, part.is_break, is_simul_slide, extend
            )

    def _draw_tap_hit_effects(self, fc: _Canvas, now: float) -> None:
        """renderTapHitEffect（L8559）：同位去重（只画窗内最新）。"""
        window = [
            (pos, timing, is_star)
            for pos, timing, is_star in self.renderer.hit_notes
            if now - _HIT_MS <= timing <= now
        ]
        latest: dict[int, float] = {}
        for pos, timing, _is_star in window:
            current = latest.get(pos)
            if current is None or timing > current:
                latest[pos] = timing
        for pos, timing, is_star in window:
            if latest.get(pos) != timing:
                continue
            ang = _button_angle(pos)
            px = fc.cx + math.cos(ang) * fc.radius
            py = fc.cy + math.sin(ang) * fc.radius
            self.hit_effects.render_at(
                fc,
                px,
                py,
                ang,
                (now - timing) / _HIT_MS,
                "star" if is_star else "hexagon",
            )

    def _judgment_line(self, c: _Canvas) -> None:
        """renderJudgmentLine（L8086）simple 设计：白圈 + 8 按钮点。"""
        c.stroke_circle(c.cx, c.cy, c.radius, C_WHITE, _JLINE * c.radius)
        for pos in range(1, 9):
            bx, by = self.renderer.button_position(pos)
            c.fill_circle(bx, by, _DOT * c.radius, C_WHITE)
