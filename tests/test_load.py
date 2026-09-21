"""主插件加载冒烟测试：验证插件可被 NoneBot 正常加载、元数据齐全。"""

import pytest
import nonebot


def test_plugin_loaded():
    plugins = {
        p.metadata.name for p in nonebot.get_loaded_plugins() if p.metadata is not None
    }
    assert "awmc-helper" in plugins


def test_plugin_metadata():
    from nonebot_plugin_awmc_helper import __plugin_meta__

    assert __plugin_meta__.name == "awmc-helper"
    assert __plugin_meta__.homepage is not None
    assert __plugin_meta__.homepage.startswith("https://github.com/shinyashen/")
    assert __plugin_meta__.config is not None


def test_config_defaults():
    """必填项 awmc_static_path 由测试环境提供，其余取默认值。"""
    from nonebot_plugin_awmc_helper.config import plugin_config

    assert plugin_config.awmc_static_path is not None
    assert plugin_config.awmc_default_provider == "divingfish"
    assert plugin_config.awmc_disabled_plugins == set()
    assert plugin_config.awmc_cache_ttl_hours == 24


@pytest.mark.asyncio
async def test_maimai_client_singleton():
    """core.client 必须持有唯一 MaimaiClient 实例（进程单例，二次实例化会告警）。"""
    from maimai_py import MaimaiClient

    from nonebot_plugin_awmc_helper.core import client as awmc_client

    with pytest.warns(UserWarning, match="singleton"):
        instance = MaimaiClient()
    assert awmc_client.client is instance
