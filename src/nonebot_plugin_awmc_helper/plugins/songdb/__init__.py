"""awmc.songdb：歌曲库 SUPERUSER 指令（规范表手动运维入口）。

- `重载补充数据`：立即重读全部外部补充源并按模式合并（§7.5-C）。
"""

from nonebot import on_command
from nonebot.plugin import PluginMetadata
from nonebot.permission import SUPERUSER
from nonebot_plugin_alconna.uniseg import UniMessage

from ...core import songdb
from ...core.utils import handle_errors

__plugin_meta__ = PluginMetadata(
    name="awmc.songdb",
    description="歌曲库规范表手动运维：重载补充数据 / 刷新歌曲库",
    usage="重载补充数据",
    type="application",
    homepage="https://github.com/shinyashen/nonebot-plugin-awmc-helper",
)

reload_extra = on_command("重载补充数据", permission=SUPERUSER, block=True)


@reload_extra.handle()
@handle_errors("重载补充数据失败")
async def _():
    await UniMessage.text(" 正在重载外部补充数据……").finish(at_sender=True)
    summary = await songdb.apply_external_sources()
    if summary.get("changed"):
        from ...core.songs import _prerender_templates

        await _prerender_templates()  # 与自动管线共用：变化即重建底图（§7.5-C）
        await UniMessage.text(
            f"补充数据已重载并重建底图（源 {summary['sources']} 个）。"
        ).finish(at_sender=True)
    await UniMessage.text(
        f"补充数据无变化（源 {summary['sources']} 个，"
        f"本次应用 {summary.get('applied', 0)} 处）。"
    ).finish(at_sender=True)
