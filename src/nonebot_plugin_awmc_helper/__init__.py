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
from .plugin_loader import load_extra_plugins

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

# 官方《嵌套插件》结构：plugins/ 下每个子目录为独立子插件（awmc. 前缀命名、可停用）。
# 加载用官方 load_plugin(完整模块名)（同文档"停用扩展"API）逐个加载：
# 不用 load_plugins(目录)：其内部以 CWD 推导模块名，装到 site-packages 后会
# ValueError 崩溃（上游缺陷，实测 2.4.3/master 均如此，见 local/QUESTIONS.md Q1）。
if _plugins_dir.is_dir():
    sub_plugins = [
        p
        for name in sorted(
            x.name
            for x in _plugins_dir.iterdir()
            if x.is_dir() and (x / "__init__.py").exists() and x.name not in _disabled
        )
        if (p := nonebot.load_plugin(f"{__package__}.plugins.{name}")) is not None
    ]
else:  # 首个子插件落地前 plugins/ 目录尚不存在
    sub_plugins = []
if _disabled:
    logger.info(f"awmc-helper 已停用子插件：{', '.join(sorted(_disabled))}")

# 固定目录 awmc_plugins/（CWD 相对路径）：第三方子插件的「git clone 即安装」
# 落点，存在才扫描、不存在零影响；须在 core 导入之后（第三方插件 import core）。
_awmc_plugins_dir = Path("awmc_plugins")
if _awmc_plugins_dir.is_dir():
    _extra = load_extra_plugins(_awmc_plugins_dir, _disabled)
    if _extra:
        logger.info(f"awmc-helper 已加载第三方子插件：{', '.join(_extra)}")
