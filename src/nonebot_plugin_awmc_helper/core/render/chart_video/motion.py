"""接近运动公式（站内 calculateNotePosition L307 移植）与 hold 呼吸。"""

import math

from . import constants
from .notes import _Pos, _Note
from .constants import _C_TRAVEL, _FLICK_FLOOR, _FLICK_PERIOD


def _button_angle(pos: int) -> float:
    """getButtonAngle（L262）：-π/2 + π/8 + (pos-1)·π/4。"""
    return -math.pi / 2 + math.pi / 8 + (pos - 1) * math.pi / 4


def _hold_flicker(elapsed_ms: float) -> float:
    """Se()（L6420）：进行中 hold 白边亮度呼吸。"""
    if elapsed_ms < 0:
        return 1.0
    table = (
        0.714,
        0.51,
        0.286,
        0.095,
        0.0,
        0.01,
        0.095,
        0.293,
        0.552,
        0.762,
        0.917,
        0.976,
        1.0,
        0.995,
        0.969,
        0.917,
    )
    n = len(table)
    pos = elapsed_ms % _FLICK_PERIOD / _FLICK_PERIOD * n
    i = int(pos)
    frac = pos - i
    a = table[i % n]
    b = table[(i + 1) % n]
    return _FLICK_FLOOR + (a + (b - a) * frac) * (1 - _FLICK_FLOOR)


def _note_position(
    note: _Note, now_ms: float, cx: float, cy: float, radius: float
) -> _Pos:
    """calculateNotePosition（L307）。tap 判定后立即消失；hold 头驻留 R。

    alpha 字段对应 JS 的 scale：前半程由 0→1，用于 note 尺寸缩放。
    """
    ang = _button_angle(note.pos)
    dt = note.timing - now_ms
    is_hold = note.kind == "hold"
    end_guard = -note.duration if is_hold else 0.0
    if dt > constants._APPROACH_MS or dt < end_guard:
        return _Pos(0.0, 0.0, 0.0, False)
    if dt > constants._HALF_APPROACH:
        d = radius * _C_TRAVEL
        alpha = 1 - (dt - constants._HALF_APPROACH) / constants._HALF_APPROACH
    elif dt >= 0:
        e = 1 - dt / constants._HALF_APPROACH
        d = radius * (_C_TRAVEL + 0.75 * e)
        alpha = 1.0
    elif is_hold:
        d = radius
        alpha = 1.0
    else:
        return _Pos(0.0, 0.0, 0.0, False)
    return _Pos(cx + math.cos(ang) * d, cy + math.sin(ang) * d, alpha, True)
