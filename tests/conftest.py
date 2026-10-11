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
