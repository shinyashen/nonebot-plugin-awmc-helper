"""nonebot-plugin-awmc-helper：NoneBot2 舞萌DX 辅助插件。

主插件入口（官方《嵌套插件》结构）：
- 核心服务在 ``core/``（唯一允许 import maimai_py 的地方）；
- 全部用户指令在 ``plugins/`` 下的子插件中，按官方机制 ``nonebot.load_plugins`` 加载，
  可用 ``awmc_disabled_plugins`` 配置按目录名停用。
"""

from pathlib import Path

import nonebot
from nonebot import logger, require
from nonebot.plugin import PluginMetadata, inherit_supported_adapters

require("nonebot_plugin_alconna")
require("nonebot_plugin_uninfo")
require("nonebot_plugin_localstore")
require("nonebot_plugin_apscheduler")

from .config import plugin_config

__plugin_meta__ = PluginMetadata(
    name="awmc-helper",
    description="NoneBot2 舞萌DX 辅助插件（数据层基于 maimai-py）",
    usage="发送 帮助maimaiDX 查看指令总览；配置见 README。",
    type="application",
    homepage="https://github.com/shinyashen/nonebot-plugin-awmc-helper",
    config=plugin_config,  # type: ignore[arg-type]
    supported_adapters=inherit_supported_adapters(
        "nonebot_plugin_alconna", "nonebot_plugin_uninfo"
    ),
    extra={"author": "shinyashen <shinyashen@qq.com>"},
)

from . import core  # noqa: F401  # 导入即构造唯一 MaimaiClient（进程单例）

logger.debug("awmc-helper 核心服务初始化完成")

_plugins_dir = Path(__file__).parent.joinpath("plugins").resolve()
_disabled = plugin_config.awmc_disabled_plugins

# 官方《嵌套插件》加载方式；配置了停用列表时改为逐个调用官方 load_plugin 跳过对应目录
if _disabled:
    sub_plugins = [
        nonebot.load_plugin(f"{__package__}.plugins.{name}")
        for name in sorted(
            p.name
            for p in _plugins_dir.iterdir()
            if p.is_dir() and (p / "__init__.py").exists() and p.name not in _disabled
        )
    ]
    if skipped := sorted(_disabled):
        logger.info(f"awmc-helper 已停用子插件：{', '.join(skipped)}")
elif _plugins_dir.is_dir():
    sub_plugins = nonebot.load_plugins(str(_plugins_dir))
else:  # 首个子插件落地前 plugins/ 目录尚不存在
    sub_plugins = []
