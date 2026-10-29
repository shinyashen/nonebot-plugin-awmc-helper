"""slide 路径纯几何：形状映射/锚点链/弧长参数化/箭头多边形。

站内对照：ce/getBarChain/getHiddenCount/getVisibleBarsForSegment/
buildArrowPaths/getPointOnSegment/buildArcLut/getSlidePathMetrics/
getPointAlongPath/getPathTangentAngle（无 PIL 依赖，不触碰画布）。
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from .constants import (
    _CENTER,
    _RADIUS,
    _ARROW_W,
    _ARROW_LEN,
    _ARROW_TIP,
    _ARROW_BACK,
    _SHADOW_OFF,
)
from ..chart_notes import SlideSegment
from .._lxns_slide_data import SLIDE_POINTS, SLIDE_KEYFRAMES

if TYPE_CHECKING:
    from .note_renderer import _Renderer  # 渲染器主类注解


def _mirror_pair(pos: int) -> int:
    """ae()：圆滑镜像位（re 表）。"""
    return (-1, 1, 8, 7, 6, 5, 4, 3, 2)[pos]


class _SlideGeometryMixin:
    """_SlideRenderer 的路径几何方法：按滑键渲染器混入。

    三个缓存（锚点链/LUT/段长）由 _SlideRenderer.__init__ 初始化；
    ``self.r`` 为渲染器主类（button_position 等经其访问）。
    """

    r: "_Renderer"  # 运行时由 _SlideRenderer 注入

    # ---- getBarChain（L5580）：段 → 旋转后锚点列 [{x,y,angle}]
    def bar_chain(self, seg: SlideSegment) -> list | None:
        shape_op, start, end, mid = seg.type, seg.start_pos, seg.end_pos, seg.mid_pos
        key = (shape_op, start, end, mid)
        cached = self._chain_cache.get(key)
        if cached is not None:
            return cached
        mapped = self._shape_map(shape_op, start, end, mid)
        if not mapped:
            self._chain_cache[key] = None
            return None
        shape, mirror = mapped
        pts = SLIDE_POINTS.get(shape)
        if not pts:
            self._chain_cache[key] = None
            return None
        # rot = (startPos − +!mirror)·π/4（L5582）：非镜像减 1、镜像减 0
        rot = (start - (0 if mirror else 1)) * math.pi / 4
        cos_i, sin_i = math.cos(rot), math.sin(rot)
        is_circle = shape.startswith("circle")
        is_line = shape.startswith("line")
        x0 = -pts[0][0] if mirror else pts[0][0]
        y0 = pts[0][1]
        dx = (-pts[-1][0] if mirror else pts[-1][0]) - x0
        dy = pts[-1][1] - y0
        v = dx * dx + dy * dy or 1.0
        chain = []
        for px, py, pr in pts:
            nx = -px if mirror else px
            ny = py
            if is_circle:
                ang = round(math.atan2(ny, nx) * 32 / math.pi) * (math.pi / 32)
                nx, ny = math.cos(ang), math.sin(ang)
            elif is_line:
                t = ((nx - x0) * dx + (ny - y0) * dy) / v
                nx, ny = x0 + t * dx, y0 + t * dy
            angle = (math.pi - pr if mirror else pr) + rot
            chain.append(
                {
                    "x": _CENTER + (nx * cos_i - ny * sin_i) * _RADIUS,
                    "y": _CENTER + (nx * sin_i + ny * cos_i) * _RADIUS,
                    "angle": angle,
                }
            )
        if (shape_op in ("p", "q", "pp", "qq") or is_circle) and len(chain) >= 2:
            self._apply_game_arrow_angles(chain)
        self._chain_cache[key] = chain
        return chain

    @staticmethod
    def _shape_map(shape: str, start: int, end: int, mid: int | None):
        """ce()（L553）：形状串 → (单位坐标表键, 是否镜像)。"""
        dist = ((end - start) % 8 + 8) % 8 + 1
        if shape == "-":
            if dist < 3 or dist > 7:
                return None
            e = 10 - dist if dist > 5 else dist
            return (f"line{e}", e != dist)
        if shape == ">":
            flat = start in (1, 2, 7, 8)
            return (
                (f"circle{dist}", False)
                if flat
                else (f"circle{_mirror_pair(dist)}", True)
            )
        if shape == "<":
            flat = start in (1, 2, 7, 8)
            return (
                (f"circle{_mirror_pair(dist)}", True)
                if flat
                else (f"circle{dist}", False)
            )
        if shape == "^":
            if dist in (1, 5):
                return None
            return (
                (f"circle{dist}", False)
                if dist < 5
                else (f"circle{_mirror_pair(dist)}", True)
            )
        if shape == "v":
            return None if dist == 5 else (f"v{dist}", False)
        if shape == "pp":
            return (f"ppqq{dist}", False)
        if shape == "qq":
            return (f"ppqq{_mirror_pair(dist)}", True)
        if shape == "p":
            return (f"pq{dist}", False)
        if shape == "q":
            return (f"pq{_mirror_pair(dist)}", True)
        if shape == "s":
            return ("s", False) if dist == 5 else None
        if shape == "z":
            return ("s", True) if dist == 5 else None
        if shape == "V":
            if mid is None:
                return None
            e = (start + 5) % 8 + 1
            n = (start + 1) % 8 + 1
            if mid == e and 2 <= dist <= 5:
                return (f"L{dist}", False)
            a = (8 - (dist - 1)) % 8 + 1
            if mid == n and 2 <= a <= 5:
                return (f"L{a}", True)
            return None
        if shape == "w":
            return ("wifi", False)
        return None

    @staticmethod
    def _angle_delta(a: float, b: float) -> float:
        d = (b - a) % math.tau
        if d > math.pi:
            d -= math.tau
        if d < -math.pi:
            d += math.tau
        return d

    def _lerp_angle(self, a: float, b: float, t: float) -> float:
        return a + self._angle_delta(a, b) * t

    def _apply_game_arrow_angles(self, chain: list[dict]) -> None:
        """applyGameArrowAngles L5625：p/q/pp/qq/circle 的弦切角 lerp。"""
        n = len(chain)
        raw = [0.0] * n
        for i in range(n - 1):
            raw[i] = math.atan2(
                chain[i + 1]["y"] - chain[i]["y"], chain[i + 1]["x"] - chain[i]["x"]
            )
        raw[n - 1] = raw[n - 2]
        chain[0]["angle"] = raw[0]
        for i in range(1, n - 1):
            # JS L5630：以上一个**已更新**角度为基准插值，不是原表角度
            chain[i]["angle"] = self._lerp_angle(chain[i - 1]["angle"], raw[i], 0.5)
        chain[n - 1]["angle"] = raw[n - 1]

    # ---- getHiddenCount（L5882）：le 关键帧 → 已被星星吃掉的箭头数
    def hidden_count(self, seg: SlideSegment, progress: float) -> int:
        mapped = self._shape_map(seg.type, seg.start_pos, seg.end_pos, seg.mid_pos)
        kf = SLIDE_KEYFRAMES.get(mapped[0], [0]) if mapped else [0]
        if len(kf) < 2:
            return 0
        index = kf[min(len(kf) - 1, int(progress * (len(kf) - 1)))]
        if seg.type == "w" and progress > 0:
            return index + 1
        return index

    # ---- getVisibleBarsForSegment（L5915）：可见箭头列（外推优先）
    def visible_bars(
        self, seg: SlideSegment, hidden: int, extend: bool
    ) -> list[dict] | None:
        if seg.type == "w":
            return None
        chain = self.bar_chain(seg)
        if not chain:
            return None
        use_chain_angle = seg.type != "-"
        bars: list[dict] = []
        o = len(chain) - 1
        if extend and len(chain) >= 2 and hidden < len(chain):
            last, prev = chain[o], chain[o - 1]
            nx, ny = 2 * last["x"] - prev["x"], 2 * last["y"] - prev["y"]
            if math.hypot(nx - _CENTER, ny - _CENTER) <= _RADIUS:
                ang = (
                    last["angle"]
                    if use_chain_angle
                    else math.atan2(last["y"] - prev["y"], last["x"] - prev["x"])
                )
                bars.append({"x": nx, "y": ny, "angle": ang})
        for i in range(o, hidden - 1, -1):
            if use_chain_angle:
                ang = chain[i]["angle"]
            else:
                lo, hi = chain[max(0, i - 1)], chain[min(o, i + 1)]
                ang = math.atan2(hi["y"] - lo["y"], hi["x"] - lo["x"])
            bars.append({"x": chain[i]["x"], "y": chain[i]["y"], "angle": ang})
        return bars

    # ---- buildArrowPaths（L6032）：单支箭头 主/影/左半
    @staticmethod
    def arrow_paths(bar: dict) -> tuple[list, list, list] | None:
        t = _ARROW_BACK * _RADIUS
        n = _ARROW_LEN * _RADIUS
        r = _ARROW_W * _RADIUS
        i = _ARROW_TIP * _RADIUS
        a = _SHADOW_OFF * _RADIUS
        e, c = math.cos(bar["angle"]), math.sin(bar["angle"])
        lx, uy = bar["x"] + e * i, bar["y"] + c * i
        dx = lx - e * n / 2 + c * t / 2
        dy = uy - c * n / 2 - e * t / 2
        px, my = lx + e * n / 2, uy + c * n / 2
        hx = lx - e * n / 2 - c * t / 2
        gy = uy - c * n / 2 + e * t / 2
        bis = _SlideGeometryMixin._bisector(dx, dy, px, my, hx, gy, r)
        if not bis:
            return None
        bx, by = bis
        b2x, b2y = bx + hx - px, by + gy - my
        s2x, s2y = bx + dx - px, by + dy - my
        main = [(px, my), (hx, gy), (b2x, b2y), (bx, by), (s2x, s2y), (dx, dy)]
        shadow = [
            (px - e * a, my - c * a),
            (hx - e * a, gy - c * a),
            (b2x - e * a, b2y - c * a),
            (bx - e * a, by - c * a),
            (s2x - e * a, s2y - c * a),
            (dx - e * a, dy - c * a),
        ]
        left = [(px, my), (bx, by), (b2x, b2y), (hx, gy)]
        return main, shadow, left

    @staticmethod
    def _bisector(x1, y1, x2, y2, x3, y3, offset):
        s = math.hypot(x1 - x2, y1 - y2)
        c = math.hypot(x3 - x2, y3 - y2)
        if s == 0 or c == 0:
            return None
        dot = max(
            -1.0, min(1.0, ((x1 - x2) * (x3 - x2) + (y1 - y2) * (y3 - y2)) / (s * c))
        )
        u = math.sqrt((1 - dot) / 2)
        if u <= 0.001:
            return None
        mx, my = (x1 + x3) / 2, (y1 + y3) / 2
        p = math.hypot(mx - x2, my - y2)
        if p == 0:
            return None
        g = offset / u
        return (x2 + (mx - x2) / p * g, y2 + (my - y2) / p * g)

    # ---- getPointOnSegment（L6338）：节点索引空间参数化
    def point_on_segment(self, seg: SlideSegment, t: float) -> tuple[float, float]:
        chain = self.bar_chain(seg)
        if chain and len(chain) >= 2:
            start = self.r.button_position(seg.start_pos)
            end = self.r.button_position(seg.end_pos)
            a = len(chain) + 2
            o = max(0.0, min(a - 1.0, t * (a - 1)))
            i = min(a - 2, int(o))
            f = o - i

            def node(k: int) -> tuple[float, float]:
                if k == 0:
                    return start
                if k == a - 1:
                    return end
                return (chain[k - 1]["x"], chain[k - 1]["y"])

            u, d = node(i), node(i + 1)
            return (u[0] + (d[0] - u[0]) * f, u[1] + (d[1] - u[1]) * f)
        start = self.r.button_position(seg.start_pos)
        end = self.r.button_position(seg.end_pos)
        return (
            start[0] + (end[0] - start[0]) * t,
            start[1] + (end[1] - start[1]) * t,
        )

    # ---- buildArcLut（L5952）：64 均匀参数采样 + 弧长累计
    def segment_lut(self, seg: SlideSegment, steps: int = 64) -> list[dict]:
        key = (seg.type, seg.start_pos, seg.end_pos, seg.mid_pos)
        cached = self._lut_cache.get(key)
        if cached:
            return cached
        lut = []
        acc = 0.0
        prev = None
        for k in range(steps + 1):
            p = self.point_on_segment(seg, k / steps)
            if prev is not None:
                acc += math.dist(prev, p)
            lut.append({"x": p[0], "y": p[1], "s": acc})
            prev = p
        self._lut_cache[key] = lut
        return lut

    @staticmethod
    def _sample_lut(lut: list[dict], target: float) -> tuple[float, float]:
        if target <= 0:
            return lut[0]["x"], lut[0]["y"]
        last = lut[-1]
        if target >= last["s"]:
            return last["x"], last["y"]
        for i in range(len(lut) - 1):
            if lut[i]["s"] <= target <= lut[i + 1]["s"]:
                span = lut[i + 1]["s"] - lut[i]["s"]
                f = (target - lut[i]["s"]) / span if span > 0 else 0.0
                return (
                    lut[i]["x"] + (lut[i + 1]["x"] - lut[i]["x"]) * f,
                    lut[i]["y"] + (lut[i + 1]["y"] - lut[i]["y"]) * f,
                )
        return last["x"], last["y"]

    def segment_length(self, seg: SlideSegment) -> float:
        key = (seg.type, seg.start_pos, seg.end_pos, seg.mid_pos)
        cached = self._metrics_cache.get(key)
        if cached is None:
            cached = self.segment_lut(seg)[-1]["s"]
            self._metrics_cache[key] = cached
        return cached

    # ---- getSlidePathMetrics（L5706）：多段路径按弧长的进度区间
    def path_ranges(
        self, segments: list[SlideSegment]
    ) -> list[tuple[float, float]] | None:
        lengths = [self.segment_length(s) for s in segments]
        total = sum(lengths)
        if total <= 0:
            return None
        ranges = []
        acc = 0.0
        for length in lengths:
            ranges.append((acc / total, (acc + length) / total))
            acc += length
        return ranges

    def point_along(
        self, t: float, segments: list[SlideSegment]
    ) -> tuple[float, float]:
        """getPointAlongPath（L6362）：跨段弧长插值。"""
        if not segments:
            return (_CENTER, _CENTER)
        luts = [self.segment_lut(s) for s in segments]
        lengths = [lut[-1]["s"] for lut in luts]
        total = sum(lengths)
        if total == 0:
            return self.r.button_position(segments[0].start_pos)
        target = t * total
        acc = 0.0
        for lut, length in zip(luts, lengths):
            if acc + length >= target:
                return self._sample_lut(lut, target - acc)
            acc += length
        return self.r.button_position(segments[-1].end_pos)

    # ---- getPathTangentAngle（L6386）：路径切向角（弧长 lerp）
    def path_tangent_angle(self, t: float, segments: list[SlideSegment]) -> float:
        total = 0.0
        lengths: list[float] = []
        angles: list[float] = []
        for seg in segments:
            chain = self.bar_chain(seg)
            if not chain:
                continue
            start = self.r.button_position(seg.start_pos)
            end = self.r.button_position(seg.end_pos)
            last = len(chain) - 1
            extended = [
                {"x": start[0], "y": start[1], "angle": chain[0]["angle"]},
                *chain,
                {"x": end[0], "y": end[1], "angle": chain[last]["angle"]},
            ]
            prev = None
            for point in extended:
                if prev is not None:
                    total += math.hypot(point["x"] - prev["x"], point["y"] - prev["y"])
                lengths.append(total)
                angles.append(point["angle"])
                prev = point
        if not angles:
            return 0.0
        if len(angles) == 1:
            return angles[0]
        target = max(0.0, min(1.0, t)) * total
        for i in range(len(lengths) - 1):
            if lengths[i] <= target <= lengths[i + 1]:
                span = lengths[i + 1] - lengths[i]
                return self._lerp_angle(
                    angles[i],
                    angles[i + 1],
                    (target - lengths[i]) / span if span > 0 else 0.0,
                )
        return angles[-1]
