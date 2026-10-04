"""core/cache：TtlCache 过期 / 容量淘汰 / 清空基本行为（time.monotonic 打桩）。

TtlCache 为同步纯内存设施，用例不进事件循环——monkeypatch 打桩时钟即可
确定性覆盖边界（TTL 命中口径与原 divingfish 手写缓存一致：-age < ttl）。
"""

import pytest


@pytest.fixture
def advance_clock(monkeypatch):
    """可控单调时钟：返回推钟函数（缺省从 1000.0 起步）。"""
    now = 1000.0

    def monotonic() -> float:
        return now

    def advance(delta: float) -> None:
        nonlocal now
        now += delta

    monkeypatch.setattr("time.monotonic", monotonic)
    return advance


def test_get_miss_then_hit(advance_clock):
    """未写入 miss；TTL 内写入后命中。"""
    from nonebot_plugin_awmc_helper.core.cache import TtlCache

    cache = TtlCache(ttl=600.0)
    assert cache.get("k") is None
    cache.set("k", [1, 2, 3])
    assert cache.get("k") == [1, 2, 3]


def test_ttl_expiry_lazy_and_purge(advance_clock):
    """惰性过期：age < ttl 命中；≥ ttl 返回 None 且该键顺带清除（不占容量）。"""
    from nonebot_plugin_awmc_helper.core.cache import TtlCache

    cache = TtlCache(ttl=600.0)
    cache.set("k", "v")
    advance_clock(599.9)
    assert cache.get("k") == "v"  # 边界内
    advance_clock(0.2)  # 累计 600.1 ≥ TTL
    assert cache.get("k") is None
    assert cache._data == {}  # 顺带清键


def test_maxsize_evicts_oldest(advance_clock):
    """容量淘汰：超 maxsize 淘汰最旧写入键。"""
    from nonebot_plugin_awmc_helper.core.cache import TtlCache

    cache = TtlCache(ttl=600.0, maxsize=2)
    cache.set("a", 1)
    advance_clock(10)
    cache.set("b", 2)
    advance_clock(10)
    cache.set("c", 3)
    assert cache.get("a") is None
    assert cache.get("b") == 2
    assert cache.get("c") == 3


def test_maxsize_rewrite_refreshes_timestamp(advance_clock):
    """同键重写刷新时间戳：重写过的键不再被当作最旧淘汰。"""
    from nonebot_plugin_awmc_helper.core.cache import TtlCache

    cache = TtlCache(ttl=600.0, maxsize=2)
    cache.set("a", 1)
    advance_clock(10)
    cache.set("b", 2)
    advance_clock(10)
    cache.set("a", 10)  # a 时间戳变新 → 最旧是 b
    cache.set("c", 3)
    assert cache.get("b") is None
    assert cache.get("a") == 10
    assert cache.get("c") == 3


def test_clear(advance_clock):
    """clear 整体清空：任何键立即失效。"""
    from nonebot_plugin_awmc_helper.core.cache import TtlCache

    cache = TtlCache(ttl=600.0)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.clear()
    assert cache.get("a") is None
    assert cache.get("b") is None
