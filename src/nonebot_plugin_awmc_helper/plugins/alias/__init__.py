"""awmc.alias：别名子插件。

指令（对齐原版 maimaiDX）：
- `<名称>有什么别名` / `id <数字>有什么别名`
- `添加本地别名 <id> <别名>`（写本地库，热更新）
- `添加别名 <id> <别名>`（向柚子提交公开申请）
- `同意别名 <TAG>`
- `当前投票 [页]`
- `开启/关闭别名推送`（群管）、`全局开启/关闭别名推送`（SUPERUSER）
- `更新别名库`（SUPERUSER）

内部结构：``matchers`` 定义指令入口；``push`` 为别名申请 SSE 推送域
（SSE 启动注册在下方装配层完成）。
"""

from nonebot import logger, get_driver
from nonebot.plugin import PluginMetadata

from ...config import plugin_config
from ...core.ext import yuzu as yuzu_ext

__plugin_meta__ = PluginMetadata(
    name="awmc.alias",
    description="舞萌DX 别名：查询/本地别名/申请/投票/推送",
    usage="<名称>有什么别名｜添加本地别名 <id> <别名>｜添加别名 <id> <别名>｜"
    "同意别名 <TAG>｜当前投票 [页]｜(全局)开启/关闭别名推送｜更新别名库",
    type="application",
    homepage="https://github.com/shinyashen/nonebot-plugin-awmc-helper",
)

from .push import push_apply
from .matchers import (  # noqa: F401
    alias_song,
    alias_agree,
    alias_apply,
    alias_status,
    alias_switch,
    update_alias,
    alias_local_apply,
    alias_global_switch,
)


@get_driver().on_startup
async def _startup_alias_push() -> None:
    if plugin_config.awmc_alias_push and plugin_config.awmc_startup_tasks:
        yuzu_ext.start_alias_push(push_apply)
        logger.info("别名推送 SSE 已启动")


@get_driver().on_shutdown
async def _shutdown_alias_push() -> None:
    await yuzu_ext.stop_alias_push()
