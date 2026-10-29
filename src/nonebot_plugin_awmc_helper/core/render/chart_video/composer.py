"""整帧组合：renderFrame 层序（L7997）+ prepareRenderNotes（L8232）。"""

from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw

from . import constants
from .hud import _HudMixin
from .notes import _Note, _map_notes, _collect_hit_notes
from .canvas import _Canvas, _clip_layer
from .motion import _button_angle, _note_position
from .constants import (
    _DOT,
    _JLINE,
    _HIT_MS,
    _R_NOTE,
    _RADIUS,
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
from .slide_renderer import _SlideRenderer
from .touch_renderer import _TouchRenderer


class _ChartRenderer(_HudMixin):
    """整帧组合（renderFrame L7997 层序 + prepareRenderNotes L8232）。"""

    def __init__(self, chart: SiteChart):
        self.chart = chart
        self.notes = _map_notes(chart)
        hit_notes = _collect_hit_notes(chart)
        base_renderer = _Renderer(self.notes, chart, hit_notes)
        self.slide_renderer = _SlideRenderer(base_renderer)
        self.renderer = base_renderer
        self.touches = _TouchRenderer(base_renderer)
        self.fireworks = _Fireworks(base_renderer)
        self.touch_hits = _TouchHitEffects(base_renderer)
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
        base = _Canvas(rgb=True)
        self._judgment_line(base)
        self._base_img = base.img
        size = base.img.width
        yy, xx = np.mgrid[0:size, 0:size]
        circle = np.hypot(xx - size / 2, yy - size / 2) <= size / 2 - 1
        self._clip_mask = Image.fromarray((circle * 255).astype(np.uint8), "L")
        self._hit_sprite_cache: dict[tuple, Image.Image] = {}
        # HUD 文本缓存：(文本, 字号, 颜色, 锚点) → (阴影+文字的合成图, 粘贴偏移)
        self._text_cache: dict[
            tuple, tuple[Image.Image, Image.Image, float, float]
        ] = {}

    # ---- 帧渲染
    def render_frame(self, now_ms: float, fps: int) -> Image.Image:
        from PIL import ImageChops

        c = _Canvas(base_image=self._base_img)
        # 花火（clip 圆内、HUD 之下，L8101）——直接画在最终画布（此刻只有底图，
        # 与 JS 在 clear/判定线之后立即绘制的背景一致；lighter 混合语义成立）
        fire_notes = [n for n in self.renderer.touches if n.fire]
        if fire_notes:
            self.fireworks.render(c, fire_notes, now_ms)
        self._hud(c, now_ms, fps)
        # clip 圆内播放场（ctx.clip 等价：先画到独立层再按圆裁切合成）
        field = Image.new("RGBA", c.img.size, (0, 0, 0, 0))
        fc = _Canvas()
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
        fx = _Canvas()
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
                pos = _note_position(n, now, fc.cx, fc.cy, fc.radius)
                if pos.visible:
                    if n.is_star:
                        self.slide_renderer.draw_star_tap(fc, n, pos, now)
                    else:
                        self.renderer.draw_tap(fc, n, pos)
            elif kind_order == 1:
                head = _note_position(n, now, fc.cx, fc.cy, fc.radius)
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
                pos = _note_position(n, now, fc.cx, fc.cy, fc.radius)
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
            if -50 <= dt <= constants._APPROACH_MS:
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
                self._hit_effect_at(fc, x, y, ang, (now - end) / _HIT_MS, "hexagon")

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
            self._hit_effect_at(
                fc,
                px,
                py,
                ang,
                (now - timing) / _HIT_MS,
                "star" if is_star else "hexagon",
            )

    def _hit_effect_at(
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
        sprite = self.renderer.sprites.hit(shape)
        # JS 先 translate/rotate 画布再画 5 个 sprite：等价于在局部层画好后整体旋转
        # 一次（比逐 sprite rotate 快数倍，也避免重复重采样）
        half = d_size * 1.1 + _RADIUS * (4 / 300 * 0.7) * 2.4 * 3 + 4
        size = math.ceil(half * 2 * constants._SS)
        lay = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        center = size / 2

        def blit(lx: float, ly: float, sz: float) -> None:
            quantized = round(sz * 2) / 2
            key = (shape, quantized)
            img = self._hit_sprite_cache.get(key)
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
                if len(self._hit_sprite_cache) > 64:
                    self._hit_sprite_cache.clear()
                self._hit_sprite_cache[key] = img
            if c_alpha < 1:
                img = img.copy()
                img.putalpha(img.getchannel("A").point(lambda v: int(v * c_alpha)))
            lay.alpha_composite(
                img,
                (
                    int(center + lx * constants._SS - img.width / 2),
                    int(center + ly * constants._SS - img.height / 2),
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

    def _judgment_line(self, c: _Canvas) -> None:
        """renderJudgmentLine（L8086）simple 设计：白圈 + 8 按钮点。"""
        c.stroke_circle(c.cx, c.cy, c.radius, C_WHITE, _JLINE * c.radius)
        for pos in range(1, 9):
            bx, by = self.renderer.button_position(pos)
            c.fill_circle(bx, by, _DOT * c.radius, C_WHITE)
