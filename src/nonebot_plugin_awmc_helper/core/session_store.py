"""泛型 TTL 会话表：awmc 生态会话型交互的内存底座（绑定回填 / 排卡搜索追问等）。

按生态约定，会话型交互不用 nonebot 的 ``got``——维护一张「键 → 待续动作」
的进程内存表，配 priority=0 的 on_message 消费 matcher（由各插件自行定义）。

- 单槽互踢：同键 start 覆盖旧会话（正确性依赖「同键同时至多一个活跃会话」
  的不变量，放宽前须重审拦截规则）；
- 过期惰性清除：get 命中过期键即删；start 时顺带全表清扫；
- 时间源统一 ``time.monotonic()``（不受系统时钟调整影响），值类型的
  ``expire_at`` 由调用方按 ``monotonic() + ttl`` 填写。
"""

import time
from typing import Generic, TypeVar
from dataclasses import dataclass


@dataclass(kw_only=True)
class TtlSession:
    """TTL 会话值基类：业务会话 dataclass 继承并补自身字段。

    ``expire_at`` keyword-only：子类自有字段保持位置序，不受基类默认值
    （无默认字段不能跟在默认字段后）约束。
    """

    expire_at: float = 0.0


S = TypeVar("S", bound=TtlSession)
K = TypeVar("K")


class TtlSessionStore(Generic[K, S]):
    """进程内存 TTL 会话表（单槽互踢 + 过期惰性清除）。"""

    def __init__(self) -> None:
        self._sessions: dict[K, S] = {}

    def start(self, key: K, session: S) -> None:
        """开启/覆盖会话（附带惰性清扫过期项）。"""
        now = time.monotonic()
        for stale in [k for k, v in self._sessions.items() if v.expire_at <= now]:
            del self._sessions[stale]
        self._sessions[key] = session

    def get(self, key: K) -> S | None:
        """取会话（过期即清除并返回 None）。"""
        session = self._sessions.get(key)
        if session is None:
            return None
        if session.expire_at <= time.monotonic():
            del self._sessions[key]
            return None
        return session

    def any_active(self) -> bool:
        """是否可能存在活跃会话（O(1) 空表短路；**不清扫、不物化**）。

        供 priority=0 拦截规则先行短路：空表直接 False，免走每条消息的
        uninfo Session 构造链（L-29）。非空表含过期残留时返回 True（过期
        惰性清除语义）——本方法只回答「有无候选会话」，精确的活跃判定
        仍由调用方走 :meth:`get`/:meth:`take`。
        """
        return bool(self._sessions)

    def active(self, key: K) -> S | None:
        """探测会话是否存在且未过期，**不过期不清除**（无副作用探测）。

        当前零调用，预留：与 :meth:`get`/:meth:`take` 的「命中过期即删」不同，
        探测不得改变会话状态——拦截规则若需要「看一眼但不影响过期语义」
        （如 O(1) any_active 短路）应使用本方法而非 get/take。
        """
        session = self._sessions.get(key)
        if session is None:
            return None
        if session.expire_at <= time.monotonic():
            return None
        return session

    def pop(self, key: K) -> S | None:
        """取会话并结束（无论是否过期）。"""
        return self._sessions.pop(key, None)

    def take(self, key: K) -> "tuple[S | None, bool]":
        """取会话并删除，返回 (session, expired)；无会话 (None, False)。

        供「过期需转入副表/给特殊提示」的调用方：普通过期清除用 :meth:`get`。
        """
        session = self._sessions.pop(key, None)
        if session is None:
            return None, False
        return session, session.expire_at <= time.monotonic()

    def discard(self, key: K) -> None:
        """静默结束会话（无会话时无操作）。"""
        self._sessions.pop(key, None)

    def clear(self) -> None:
        """清空全部会话（测试隔离用）。"""
        self._sessions.clear()
