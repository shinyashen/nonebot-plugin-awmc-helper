"""别名申请 SSE 推送域：别名申请事件分发到开启推送的群。

合并转发优先（core/forward 统一构造），失败降级普通消息；启动注册在
插件装配层 ``__init__`` 完成，本模块无副作用。
"""

import asyncio

from nonebot import logger, get_bots

from ...core import store
from ...config import plugin_config
from ...core.ext import yuzu as yuzu_ext
from ...core.songs import song_service
from ...core.forward import is_ob11, ob11_text, ob11_available, try_send_forward

PUSH_FEATURE = "alias_push"
"""群级开关键：推送分发（本模块）与 开关指令（matchers）共用。"""


async def push_apply(push: yuzu_ext.AliasPush) -> None:
    if not push.status:
        return
    lines = [
        "检测到新的别名申请，可使用同意别名指令进行投票，"
        f"点击下方链接查看详情：「{yuzu_ext.VOTE_URL}」\n"
        "如果不需要接收推送消息，请使用「关闭别名推送」指令关闭推送"
    ]
    # 标题回取并发化（读运行时缓存，无远端请求）
    hits = await asyncio.gather(
        *(song_service.by_id(item.song_id) for item in push.status)
    )
    for item, song in zip(push.status, hits):
        title = song.title if song else str(item.song_id)
        lines.append(
            f"{item.tag}：\nID：{item.song_id}\n标题：{title}\n别名：{item.apply_alias}"
        )
    text = "\n======\n".join(lines)

    default = plugin_config.awmc_alias_push
    if not ob11_available():
        return
    # 按 bot 并发推送（各连接限速互不影响）：原先 bot 间串行 + 每群 sleep(5)，
    # 200 群即 1000s/轮会压住 SSE 回调；单 bot 内仍逐群串行 + sleep 防风控
    tasks = []
    for bot in list(get_bots().values()):
        if not is_ob11(bot):
            continue
        try:
            group_list = await bot.get_group_list()
        except Exception as e:
            logger.debug(f"群列表拉取失败（bot={bot}），跳过该连接：{e}")
            continue
        tasks.append(_push_to_groups(bot, group_list, text, default))
    if tasks:
        await asyncio.gather(*tasks)


async def _push_to_groups(bot, group_list, text: str, default: bool) -> None:
    for g in group_list:
        gid = str(g["group_id"])
        if not await store.get_switch(gid, PUSH_FEATURE, default):
            continue
        # 合并转发优先（core/forward 统一构造），失败降级普通消息
        if not await try_send_forward(bot, [text], group_id=gid):
            try:
                await bot.send_group_msg(group_id=int(gid), message=ob11_text(text))
            except Exception:
                logger.exception(f"别名推送到群 {gid} 失败")
        await asyncio.sleep(5)
