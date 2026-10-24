"""MaiConverter simai 片段解析器 fork（vendor，MIT License，见同目录 LICENSE）。

上游：https://github.com/donmai-me/MaiConverter ``maiconverter/simai/``
（``simai_fragment.lark`` + ``simai_parser.py``，2026-10-24 同步）。

相对上游的修改（fork 差异，升级上游时须重放）：

1. touch 区域保留 A/D（上游 transformer 把非 CBE 区域静默丢弃，是 bug）；
2. touch 语法放宽为 ``[A-E][1-8]?`` + C 的 ``[0-2]`` 特例（落雪库实测含
   A/D 区与省略位的裸 touch，如 ``B6``/``Cf``/``Chf[1:1]``）；
3. 连接滑 token 的 ``*`` 与时长均可选（落雪库存在 ``3x>1v4[4:3]`` 类无星链，
   上游语法解析不了）；调用方（core.simai 预处理器）保证无星链先补插 ``*``；
4. ``Lark`` 实例进程级缓存（上游每次 parse 重建，热点路径开销不可接受）；
5. 去掉上游 print 副作用，错误改为抛异常由调用方处理。

语义与上游一致：``x`` = EX 强判定修饰、``b`` = 破坏、``$``/``?``/``!`` =
tapless/无判定系、``@`` = ？（保留标记透传）、时长 ``[X:Y]`` = Y/X 小节。
"""

from pathlib import Path

from lark import Lark
from lark.exceptions import LarkError

from .simai_parser import FragmentTransformer

_LARK: Lark | None = None


def _get_lark() -> Lark:
    global _LARK
    if _LARK is None:
        _LARK = Lark.open("simai_fragment.lark", rel_to=Path(__file__), parser="lalr")
    return _LARK


def parse_fragment(fragment: str) -> list[dict]:
    """解析单个逗号步（fragment）为事件 dict 列表。

    事件形态见 :mod:`.simai_parser`（tap/hold/slide/touch_tap/touch_hold/
    bpm/divisor）。解析失败抛 LarkError/ValueError，调用方记 warning 跳步。
    """
    if not fragment.strip():
        return []
    tree = _get_lark().parse(fragment)
    result = FragmentTransformer().transform(tree)
    # Transformer 对 chain 顶层返回 list；单 value 分支可能直接是 dict/list
    if isinstance(result, dict):
        return [result]
    if isinstance(result, list):
        flat: list[dict] = []
        for item in result:
            if isinstance(item, dict):
                flat.append(item)
            elif isinstance(item, list):
                flat.extend(x for x in item if isinstance(x, dict))
        return flat
    raise LarkError(f"unexpected transform result: {result!r}")
