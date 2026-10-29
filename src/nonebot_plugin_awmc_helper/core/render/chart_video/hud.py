"""HUD 绘制：BPM/小节/连击/BREAK 计数（renderBpmDisplay L8583 等）。"""

from __future__ import annotations

import math

from PIL import Image, ImageDraw, ImageFont

from . import constants
from ..fonts import font as _load_font
from .canvas import _Canvas
from .constants import (
    C_WHITE,
    _LOGICAL,
    C_ORANGE,
    C_HUD_GRAY,
    C_BREAK_DOT,
)


class _HudMixin:
    """_ChartRenderer 的 HUD 方法：按整帧组合器混入。"""

    def _hud(self, c: _Canvas, now: float, fps: int) -> None:
        """renderBpmDisplay（L8583）+ renderNoteCounts（L8637）。"""

        s = constants._SS
        r300 = c.radius / 300
        n22, n16 = round(22 * r300) * s, round(16 * r300) * s
        n20, n4 = round(20 * r300) * s, round(4 * r300) * s
        n18 = round(18 * r300) * s
        shadow_off = 2 * r300 * s
        shadow_col = (0, 0, 0, 191)

        def text(x, y, txt, px, color, anchor="la"):
            self._hud_text(c, x, y, txt, px, color, anchor, shadow_off, shadow_col)

        beat = self.chart.timeline.beat_from_ms(now)
        bpm = self.chart.timeline.bpm_at_beat(beat)
        bpm_col = C_WHITE
        for change_ms, direction in self._bpm_changes:
            dt = now - change_ms
            if 0 <= dt < 500:
                e = max(0.0, (dt - 50) / 450)
                if direction > 0:
                    bpm_col = (255, round(255 * e), round(255 * e))
                else:
                    bpm_col = (round(255 * e), 255, round(255 * e))
        text(n20, n20, f"BPM: {math.floor(bpm)}", n22, bpm_col)
        div = self.chart.timeline.divisor_at_beat(beat)
        measure = int(beat // 4)
        beat_in = int(beat % 4) + 1
        frac = beat % 1
        text(
            n20,
            n20 + n22 + n4,
            f"{measure}:{beat_in}.{int(frac * 100):02d} [1/{div}]",
            n16,
            C_HUD_GRAY,
        )
        total = len(self.renderer.completions)
        combo = self.renderer.count_done(self.renderer.completions, now)
        text(_LOGICAL * s - n20, n20, f"连击: {combo} / {total}", n22, C_WHITE, "ra")
        b_total = len(self.renderer.break_completions)
        b_done = self.renderer.count_done(self.renderer.break_completions, now)
        text(
            n20,
            _LOGICAL * s - n20,
            f"BREAK: {b_done} / {b_total}",
            n22,
            C_BREAK_DOT,
            "lb",
        )
        # 右下「无保护 BREAK」：非 EX break（EX 是保护音符）计数，与站内 JS
        # （L8649-8651）一致：无条件绘制该行，不做 0/0 隐藏。
        nx_total = len(self.renderer.noex_break_completions)
        nx_done = self.renderer.count_done(self.renderer.noex_break_completions, now)
        right_x = _LOGICAL * s - n20
        bottom = _LOGICAL * s - n20
        text(right_x, bottom, f"BREAK: {nx_done} / {nx_total}", n22, C_ORANGE, "rb")
        text(right_x, bottom - n22 - n4, "无保护", n18, C_ORANGE, "rb")

    def _hud_text(
        self,
        c: _Canvas,
        x: float,
        y: float,
        txt: str,
        px: int,
        color,
        anchor: str,
        shadow_off: float,
        shadow_col,
    ) -> None:
        """缓存 HUD 文本（Font.render 约占单帧 20%）。

        阴影与文字先合成到一张透明小图缓存，再整体 paste：文本内容/字号/颜色/
        锚点相同的帧重复率很高（BPM、连击、BREAK、无保护），而 canvas 的
        alpha 叠加满足结合律，缓存合成结果与逐次绘制等价。
        """
        key = (txt, px, color, anchor)
        entry = self._text_cache.get(key)
        if entry is None:
            font = self._font(px)
            bbox_la = c.d.textbbox((0, 0), txt, font=font, anchor="la")
            bbox_a = c.d.textbbox((0, 0), txt, font=font, anchor=anchor)
            w = int(bbox_la[2] - bbox_la[0])
            h = int(bbox_la[3] - bbox_la[1])
            pad = int(shadow_off) + 4
            size: tuple[int, int] = (max(1, w + pad * 2), max(1, h + pad * 2))
            # 文字/阴影各存一张：两次 alpha_composite 与「直接画两遍」逐像素等价
            text_img = Image.new("RGBA", size, (0, 0, 0, 0))
            ImageDraw.Draw(text_img, "RGBA").text(
                (pad - bbox_la[0], pad - bbox_la[1]),
                txt,
                font=font,
                fill=color,
                anchor="la",
            )
            shadow_img = Image.new("RGBA", size, (0, 0, 0, 0))
            ImageDraw.Draw(shadow_img, "RGBA").text(
                (pad - bbox_la[0], pad - bbox_la[1]),
                txt,
                font=font,
                fill=shadow_col,
                anchor="la",
            )
            entry = (
                text_img,
                shadow_img,
                bbox_a[0] - pad,
                bbox_a[1] - pad,
            )
            if len(self._text_cache) > 512:
                self._text_cache.clear()
            self._text_cache[key] = entry
        text_img, shadow_img, dx, dy = entry
        c.over(shadow_img, (int(x + dx + shadow_off), int(y + dy + shadow_off)))
        c.over(text_img, (int(x + dx), int(y + dy)))

    def _font(self, px: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        cached = self.renderer._fonts.get(px)
        if cached is None:
            try:
                cached = _load_font(px, "ResourceHanRoundedCN-Bold.ttf")
            except Exception:
                from PIL import ImageFont

                cached = ImageFont.load_default()
            self.renderer._fonts[px] = cached
        return cached
