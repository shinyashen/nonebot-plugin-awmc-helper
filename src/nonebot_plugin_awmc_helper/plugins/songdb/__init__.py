"""awmc.songdb：歌曲库 SUPERUSER 指令（规范表手动运维入口）。

- `重载补充数据`：立即重读全部外部补充源并按模式合并（§7.5-C）；
  MuNET 别名全量走查在此后台启动（force 跳过间隔检查，互斥锁防并发）。
- `刷新歌曲库`：手动执行全量管线（四源重建 → MuNET 批次补充 → 底图 →
  运行时刷新）。
"""

import asyncio

from nonebot import logger, on_command
from nonebot.plugin import PluginMetadata
from nonebot.permission import SUPERUSER
from nonebot_plugin_alconna.uniseg import UniMessage

from ...core import songs, songdb
from ...core.help import CommandSpec, help_registry
from ...core.utils import handle_errors

__plugin_meta__ = PluginMetadata(
    name="awmc.songdb",
    description="歌曲库规范表手动运维：重载补充数据 / 刷新歌曲库",
    usage="重载补充数据\n刷新歌曲库",
    type="application",
    homepage="https://github.com/shinyashen/nonebot-plugin-awmc-helper",
)

_munet_bg_tasks: set[asyncio.Task] = set()
"""后台走查任务强引用（防 create_task 结果被 GC）。"""


async def _munet_walk_bg() -> None:
    from ...core.ext import munet

    try:
        result = await munet.refresh_aliases_full(force=True)
        logger.info(f"MuNET 别名走查（手动触发）：{result}")
    except Exception:
        logger.exception("MuNET 别名走查（手动触发）失败")


reload_extra = on_command("重载补充数据", permission=SUPERUSER, block=True)


@reload_extra.handle()
@handle_errors("重载补充数据失败")
async def _():
    # 进度提示必须用 send：finish 会抛 FinishedException 终止 handler，后续逻辑不再执行
    await UniMessage.text(" 正在重载外部补充数据……").send(at_sender=True)
    # force：显式重载必须真重放——重建以基础源覆写过外部字段时，文件哈希虽未变
    # 也得把校正写回去，按"哈希未变"跳过是错的
    summary = await songdb.apply_external_sources(force=True)
    # gamerch wiki 运行时补充与文件源同序（fill：只补缺口谱面）
    from ...core.ext import gamerch

    g_applied, g_changed = await gamerch.apply_fill()
    # MuNET 别名全量走查：后台启动（约 30 分钟；互斥锁防与定时任务并发，
    # force 跳过 7 天间隔检查；awmc_munet_alias_days=0 视为功能未启用）
    from ...config import plugin_config

    if plugin_config.awmc_munet_alias_days > 0:
        task = asyncio.create_task(_munet_walk_bg())
        _munet_bg_tasks.add(task)
        task.add_done_callback(_munet_bg_tasks.discard)
        munet_msg = "；MuNET 别名走查已后台启动（约 30 分钟，结果见日志）"
    else:
        munet_msg = "；MuNET 别名走查未启用（awmc_munet_alias_days=0）"
    if summary.get("changed") or g_changed:
        from ...core.songs import prerender_templates

        await prerender_templates()  # 与自动管线共用：变化即重建底图（§7.5-C）
        wiki_msg = f"，wiki 补充 {g_applied} 处" if g_applied else ""
        await UniMessage.text(
            f"补充数据已重载并重建底图（源 {summary['sources']} 个"
            f"{wiki_msg}）{munet_msg}。"
        ).finish(at_sender=True)
    wiki_msg = f"，wiki 补充 {g_applied} 处" if g_applied else "，wiki 无缺口"
    await UniMessage.text(
        f"补充数据无变化（源 {summary['sources']} 个，"
        f"本次应用 {summary.get('applied', 0)} 处{wiki_msg}）{munet_msg}。"
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
    mu = result.get("munet_batch") or {}
    mu_msg = (
        f"，MuNET 批次补充 {mu['entries']} 首" if mu.get("status") == "batch" else ""
    )
    await UniMessage.text(
        f" 歌曲库刷新完成：{result['songs']} 曲 / {result['groups']} 谱面组 / "
        f"{result['charts']} 谱面，删除 {result.get('removed', 0)} 曲，"
        f"国服当前版本 {result.get('cn_current_version', '?')}{warn_msg}{mu_msg}"
    ).finish(at_sender=True)


# ---------------------------------------------------------------- 帮助声明

help_registry.declare(
    plugin="awmc.songdb",
    title="曲库运维",
    category="manage",
    commands=[
        CommandSpec(
            matcher=reload_extra,
            name="重载补充数据",
            scope="SUPERUSER",
            hidden=True,
            brief="重读全部外部补充源并合并（MuNET 走查后台启动）",
        ),
        CommandSpec(
            matcher=refresh_songs,
            name="刷新歌曲库",
            scope="SUPERUSER",
            hidden=True,
            brief="手动执行曲库全量重建管线",
        ),
    ],
)
