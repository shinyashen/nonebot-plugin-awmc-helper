"""谱面查询参数解析：``<难度色>+<歌名/别名/id>`` 形态（多指令共用）。

统一口径（2026-10-27 定案）：
- ``紫+799`` / ``紫+琪露诺``：``+`` 前为难度色（绿/黄/红/紫/白），后为查询键；
- ``宴+<id>``：宴谱模式（无难度色，diff_id 即完整规格）；
- 也兼容旧式无 ``+`` 的连写（``紫799``）与纯查询（无难度色）——
  分数线/linfo 仍走同一函数，颜色归属歧义（如「白雪」）由调用方按根 id
  交集决策，本函数只负责拆分。
"""

from __future__ import annotations

from .types import LevelIndex
from ..constants import COLOR_TO_LEVEL_INDEX

UTAGE_PREFIX = "宴"


def split_color_query(text: str) -> tuple[LevelIndex | None, bool, str]:
    """拆分 ``<难度色>+<查询>`` → (难度色或 None, 是否宴查询, 查询键)。

    ``+`` 形态严格要求前缀是难度色/宴；前缀无法识别时整串视为查询（宽容
    回退，交由上层报「未找到」）。无 ``+`` 时兼容旧式：首字符是颜色字或
    「宴」前缀则剥离（剥空则保留整串）。
    """
    text = text.strip()
    if "+" in text:
        head, tail = text.split("+", 1)
        head = head.strip()
        tail = tail.strip()
        if head in COLOR_TO_LEVEL_INDEX:
            return COLOR_TO_LEVEL_INDEX[head], False, tail or text
        if head == UTAGE_PREFIX:
            return None, True, tail or text
        return None, False, text
    if text and text[0] in COLOR_TO_LEVEL_INDEX:
        level = COLOR_TO_LEVEL_INDEX[text[0]]
        stripped = text[1:].strip()
        return level, False, stripped if stripped else text
    if text.startswith(UTAGE_PREFIX):
        stripped = text[len(UTAGE_PREFIX) :].strip()
        return None, True, stripped if stripped else text
    return None, False, text
