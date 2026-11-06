import os
from pathlib import Path

import pytest
import nonebot
from pytest_asyncio import is_async_test
from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

if Path(".env.dev").exists():
    os.environ["ENVIRONMENT"] = "dev"
else:
    os.environ["ENVIRONMENT"] = "test"


def pytest_collection_modifyitems(items: list[pytest.Item]):
    pytest_asyncio_tests = (item for item in items if is_async_test(item))
    session_scope_marker = pytest.mark.asyncio(loop_scope="session")
    for async_test in pytest_asyncio_tests:
        async_test.add_marker(session_scope_marker, append=False)


@pytest.fixture(scope="session", autouse=True)
async def after_nonebot_init(after_nonebot_init: None):
    # 加载适配器
    driver = nonebot.get_driver()
    driver.register_adapter(OnebotV11Adapter)

    # 加载插件
    nonebot.load_from_toml("pyproject.toml")


@pytest.fixture(autouse=True)
def plate_source_offline(monkeypatch):
    """柚子牌单缓存默认离线（Q60）：冷启动拉取即失败 → 负缓存 → 过筛点
    全部拿 None = 不过筛，既有用例口径不变；牌单相关用例自行注入进程内
    缓存 / kv 快照或重打 _fetch。"""
    from nonebot_plugin_awmc_helper.core import plate_source
    from nonebot_plugin_awmc_helper.core.ext import ExtError

    async def _no_fetch():
        raise ExtError("测试环境牌单禁网")

    plate_source._cache_clear()
    monkeypatch.setattr(plate_source, "_fetch", _no_fetch)
    yield
    plate_source._cache_clear()


def _clear_songdb_derived_caches() -> None:
    from nonebot_plugin_awmc_helper.core import songdb
    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.net_score import net_score_service

    songdb.CURRENT_FINGERPRINT = None
    song_service._jp_cache_clear()
    net_score_service._title_index_clear()


@pytest.fixture(autouse=True)
def songdb_derived_caches_isolated():
    """规范表派生的进程级缓存按用例隔离（前后各清一次）。

    日服视图、日服标题索引等按 ``songdb.CURRENT_FINGERPRINT`` 失效，指纹只在
    规范表重建时刷新；各用例换库（``store.set_db_file``）不动指纹，前一个
    用例的库建出的视图会被指纹相同的下一个用例原样复用——xdist 打散顺序
    后偶发（2026-11-06：songdb_views 的 8 个用例留下含「物語はここから」的
    日服视图，test_alias_song_pending_fallback_card 因此出日服限定卡而非
    pending 兜底卡）。
    """
    _clear_songdb_derived_caches()
    yield
    _clear_songdb_derived_caches()
