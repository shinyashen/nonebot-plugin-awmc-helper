"""渲染配置：超采样与谱面流速（Majdata 标定）的不可变单值对象。

取代旧的模块级可变全局（_SS/_APPROACH_MS/… + _apply_hi_speed 写入 +
_SS_LOCK 串行化）：一次渲染构造一份 :class:`RenderConfig` 并贯穿全部
渲染器，并发渲染（不同流速/超采样）互不可见、无需加锁。字段在
:meth:`RenderConfig.create` 里按旧全局的初始化口径一次性算好，逐位一致。
"""

from __future__ import annotations

from dataclasses import dataclass

_BASE_APPROACH_MS = 2250.0  # lxns 渲染器「approach = base/hiSpeed」的基准值


# ── 谱面流速标定来源（2026-10-27 调研定案）────────────────────────────────
# 原先照搬 maimai.lxns.net：hiSpeed = 设定值 × 0.6，approach = 2250/hiSpeed。
# 但该口径比 Majdata（simai 生态事实标准播放器）快约 17.7%，7.0 相当于
# Majdata 的 8.4；群友反馈按官机手感 Majdata 的 7.0 已偏快，故改用 Majdata
# 标定（源码核对自 MajdataView `HttpHandler.cs` / MajdataPlay
# `GamePlayManager`，team-carol/carol `chartRenderer.ts` 亦独立移植同一公式）：
#   内部速度 S(s) = 107.25 / (71.4184491 · (s + 0.9975)^-0.985558604)
#   出现→判定时长 = 7.349 / S(s) 秒
#   其中 7.349 = 4.8 − (−2.549)：4.8 为判定线距离，NoteAppearRate=0.265 时
#   destScale=0 的起始距离为 −2.549。
# lxns 的 hiSpeed 语义是 approach=2250/hiSpeed，因此换算：
#   hiSpeed = 2250 / (7349 / S(s)) = 0.30616 · S(s)
# TOUCH 在 Majdata 是独立速度公式（默认 TouchSpeed 7.5；此处沿用用户流速）：
#   touch 出现→判定时长 = 3209.385682 · s^-0.9549621752 ms
def _majdata_note_approach_ms(setting: float) -> float:
    """音符出现→判定总时长（ms），Majdata 标定。"""
    s = 107.25 / (71.4184491 * (setting + 0.9975) ** -0.985558604)
    return 7349.0 / s


def _majdata_touch_approach_ms(setting: float) -> float:
    """TOUCH 出现→判定时长（ms），Majdata `TouchDrop.wholeDuration` 公式。"""
    return 3209.385682 * setting**-0.9549621752


_DEFAULT_HI_SPEED = 7.0
_HI_SPEED_MIN = 3.0
_HI_SPEED_MAX = 9.75
_HI_SPEED_STEP = 0.25
DEFAULT_HI_SPEED = _DEFAULT_HI_SPEED


def normalize_hi_speed(value: float | None) -> float:
    """谱面流速参数校验：3–9.75、步长 0.25；非法回退默认 7.0。"""
    try:
        speed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return _DEFAULT_HI_SPEED
    if not (_HI_SPEED_MIN <= speed <= _HI_SPEED_MAX):
        return _DEFAULT_HI_SPEED
    steps = speed / _HI_SPEED_STEP
    if abs(steps - round(steps)) > 1e-6:
        return _DEFAULT_HI_SPEED
    return speed


@dataclass(frozen=True)
class RenderConfig:
    """一次渲染的全程配置（frozen：线程可共享，并发渲染各持一份）。

    旧全局逐位等价（2026-10-30 定案）：
    - ``supersample`` = 旧 ``_SS``（[1.0, 2.0] 钳制，2.0 = 站内 G=2 backing）；
    - ``speed_setting`` = 旧 ``_SPEED_SETTING``（经 normalize 校验回退）；
    - ``approach_ms``/``half_approach_ms`` = 旧 ``_APPROACH_MS``/``_HALF_APPROACH``；
    - ``touch_approach_ms`` = 旧 ``_TOUCH_APPROACH_MS``；
    - ``hi_speed`` = 旧 ``_HI_SPEED``（2250/approach，站内 hiSpeed 语义）。
    """

    supersample: float
    speed_setting: float
    hi_speed: float
    approach_ms: float
    half_approach_ms: float
    touch_approach_ms: float

    @classmethod
    def create(
        cls, supersample: float = 2.0, hi_speed: float = _DEFAULT_HI_SPEED
    ) -> "RenderConfig":
        """钳制超采样、校验流速并派生 approach 组（公式见上方标定注释）。"""
        clamped = max(1.0, min(2.0, float(supersample)))
        speed = normalize_hi_speed(hi_speed)
        approach_ms = _majdata_note_approach_ms(speed)
        return cls(
            supersample=clamped,
            speed_setting=speed,
            hi_speed=_BASE_APPROACH_MS / approach_ms,
            approach_ms=approach_ms,
            half_approach_ms=approach_ms / 2,
            touch_approach_ms=_majdata_touch_approach_ms(speed),
        )


DEFAULT_CONFIG = RenderConfig.create()
