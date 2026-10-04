"""进程内 TTL + 容量上限缓存。

仓内既有「时间戳元组 + 读时判过期」手写缓存（core/ext 各源）的上位抽象：
不落库、不跨进程，只服务用户指令热路径的短缓存——持久化缓存走 maimai_py
``client._cache``（禁止手动触碰，见 AGENTS.md 硬性规则 10）。
"""

import time
from typing import Any


class TtlCache:
    """进程内 TTL + 容量上限缓存（asyncio 单线程，无锁；惰性过期）。

    - :meth:`get`：命中且未过期返回值，否则 None（过期键顺带清除）；
    - :meth:`set`：写入并盖 ``time.monotonic`` 时间戳，超出 ``maxsize`` 淘汰
      最旧（重写同键视为刷新时间戳，惰性——过期未被读到的键同样按时间戳
      参与淘汰）；
    - :meth:`clear`：整体清空（测试助手 / 外部失效入口）。

    值为 None 时与「未命中」不可区分——本缓存面向列表/对象载荷，勿存 None。
    """

    def __init__(self, ttl: float, maxsize: int = 128) -> None:
        self.ttl = ttl
        self.maxsize = maxsize
        self._data: "dict[Any, tuple[float, Any]]" = {}

    def get(self, key: Any) -> Any | None:
        """命中且未过期返回值，否则 None（过期键顺带清掉，不占容量）。"""
        item = self._data.get(key)
        if item is None:
            return None
        stamp, value = item
        if time.monotonic() - stamp >= self.ttl:
            del self._data[key]
            return None
        return value

    def set(self, key: Any, value: Any) -> None:
        """写入并盖时间戳；容量超限时按时间戳淘汰最旧。"""
        self._data[key] = (time.monotonic(), value)
        while len(self._data) > self.maxsize:
            del self._data[min(self._data, key=lambda k: self._data[k][0])]

    def clear(self) -> None:
        """整体清空。"""
        self._data.clear()
