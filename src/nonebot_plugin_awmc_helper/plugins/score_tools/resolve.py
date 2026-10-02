"""分数线指令解析：参数拆分与难度色/宴前缀候选生成。

「难度色与别名连写」（紫琪露诺 / 紫799）在本插件自行解析。⚠️ 别名本身可能
以颜色字开头（如「白雪」「绿9」「白银」），首字符是颜色字**不等于**难度色——
归属判定在 handler：剥色/整串两候选都解析后按根 id 交集反推（交集=色、
仅整串=色字属于别名），本模块只负责拆线与产出候选列表。

「宴」前缀同理（宴蛸 → 查该曲的宴谱）：宴谱的 6 位 diff_id 本身即完整
规格（曲 + 谱面），无需难度色补位——宴模式下候选列表只列 diff_id。
"""

import re

from ...constants import COLOR_TO_LEVEL_INDEX
from ...core.types import LevelIndex

_LINE_RE = re.compile(r"^-?[0-9]+(\.[0-9]+)?$")
UTAGE_PREFIX = "宴"


def split_args(
    args: str,
) -> tuple[LevelIndex | None, bool, list[str], float | None]:
    """参数拆分 → (暂定难度色或 None, 宴前缀, 查询候选列表, 线或 None)。

    ``args`` 形如「紫799 100.5」「紫 琪露诺 100」「宴蛸 100」「琪露诺 100」：

    - 末个 token 为数字时视为分数线（100.5% 一类小数均可），否则线为 None
      （调用方提示格式错误）；
    - 剩余部分首字符为难度色（绿黄红紫白）时产出候选 ``[剥色查询键, 整串]``
      （剥色为空只剩整串），``暂定难度色`` 的生效与否由调用方按候选命中与
      根 id 交集判定；无色前缀则候选仅整串、难度色恒 None；
    - 首字符为「宴」时置 ``宴前缀``（宴谱查询模式，无难度色），候选产出
      规则与颜色相同（颜色优先——首字符不可能同属两者）。
    """
    tokens = args.split()
    line: float | None = None
    if tokens and _LINE_RE.match(tokens[-1]):
        line = float(tokens[-1])
        tokens = tokens[:-1]
    query = " ".join(tokens).strip()
    if query and query[0] in COLOR_TO_LEVEL_INDEX:
        level_index = COLOR_TO_LEVEL_INDEX[query[0]]
        stripped = query[1:].strip()
        candidates = [stripped, query] if stripped else [query]
        return level_index, False, candidates, line
    if query.startswith(UTAGE_PREFIX):
        stripped = query[len(UTAGE_PREFIX) :].strip()
        candidates = [stripped, query] if stripped else [query]
        return None, True, candidates, line
    return None, False, [query] if query else [], line
