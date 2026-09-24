"""awmc.songdb：歌曲库 SUPERUSER 指令（规范表手动运维入口）。

- `重载补充数据`：立即重读全部外部补充源并按模式合并（§7.5-C）。
- `刷新歌曲库`：手动执行全量管线（四源重建 → 底图 → 运行时刷新）。
"""

from nonebot import on_command
from nonebot.plugin import PluginMetadata
from nonebot.permission import SUPERUSER
from nonebot_plugin_alconna.uniseg import UniMessage

from ...core import songs, songdb
from ...core.utils import handle_errors

__plugin_meta__ = PluginMetadata(
    name="awmc.songdb",
    description="歌曲库规范表手动运维：重载补充数据 / 刷新歌曲库",
    usage="重载补充数据\n刷新歌曲库",
    type="application",
    homepage="https://github.com/shinyashen/nonebot-plugin-awmc-helper",
)

reload_extra = on_command("重载补充数据", permission=SUPERUSER, block=True)


@reload_extra.handle()
@handle_errors("重载补充数据失败")
async def _():
    # 进度提示必须用 send：finish 会抛 FinishedException 终止 handler，后续逻辑不再执行
    await UniMessage.text(" 正在重载外部补充数据……").send(at_sender=True)
    # force：显式重载必须真重放——重建以基础源覆写过外部字段时，文件哈希虽未变
    # 也得把校正写回去，按"哈希未变"跳过是错的
    summary = await songdb.apply_external_sources(force=True)
    if summary.get("changed"):
        from ...core.songs import prerender_templates

        await prerender_templates()  # 与自动管线共用：变化即重建底图（§7.5-C）
        await UniMessage.text(
            f"补充数据已重载并重建底图（源 {summary['sources']} 个）。"
        ).finish(at_sender=True)
    await UniMessage.text(
        f"补充数据无变化（源 {summary['sources']} 个，"
        f"本次应用 {summary.get('applied', 0)} 处）。"
    ).finish(at_sender=True)


refresh_songs = on_command("刷新歌曲库", permission=SUPERUSER, block=True)


@refresh_songs.handle()
@handle_errors("刷新歌曲库失败")
async def _():
    # 进度提示必须用 send：finish 会抛 FinishedException 终止 handler，后续逻辑不再执行
    await UniMessage.text(" 正在刷新歌曲库（四源重建，预计几分钟）……").send(
        at_sender=True
    )
    result = await songs.full_refresh()
    if not result:
        await UniMessage.text(
            " 刷新失败（详见服务端日志），曲库运行时未受影响。"
        ).finish(at_sender=True)
    warn_n = len(result.get("warnings") or [])
    warn_msg = f"（告警 {warn_n} 条，详见日志）" if warn_n else ""
    await UniMessage.text(
        f" 歌曲库刷新完成：{result['songs']} 曲 / {result['groups']} 谱面组 / "
        f"{result['charts']} 谱面，删除 {result.get('removed', 0)} 曲，"
        f"国服当前版本 {result.get('cn_current_version', '?')}{warn_msg}"
    ).finish(at_sender=True)
