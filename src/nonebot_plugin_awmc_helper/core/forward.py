"""OneBot v11 合并转发（协议端实测构造，``message``/``messages`` 双参数兼容）。

参考形态：本插件别名推送（LLBot 实测可用）与 maimaidx/mystool 的构造——
节点为 ``node_custom(bot.self_id, "Bot", 内容)``；alconna uniseg 的 Reference
导出链路在本项目部署的协议端会发出点开为空的转发卡片，故此处直接构造。
NapCat 等实现读 ``messages``、LLOneBot 部分版本只读 ``message``，两参数同时
下发以最大兼容（协议端忽略不认识的参数）。非 OneBot v11 适配器无该能力，
返回 False 由调用方降级为普通消息。
"""

from collections.abc import Sequence

from nonebot import logger
from nonebot.adapters import Bot
from nonebot_plugin_alconna.uniseg import UniMessage, FallbackStrategy

from ..config import plugin_config

try:  # OneBot v11 可用时提供合并转发能力
    from nonebot.adapters.onebot.v11 import Bot as OB11Bot
    from nonebot.adapters.onebot.v11 import Message as OB11Message
    from nonebot.adapters.onebot.v11 import MessageSegment as OB11Segment

    _OB11 = True
except ImportError:  # pragma: no cover
    OB11Bot = None
    OB11Message = None
    OB11Segment = None
    _OB11 = False


async def try_send_forward(
    bot: Bot,
    entries: Sequence["str | UniMessage"],
    *,
    group_id: int | str | None = None,
    user_id: int | str | None = None,
) -> bool:
    """以合并转发发送多条消息（每条一节点，可为文本或含图片的 UniMessage）。

    - ``group_id``：群聊场景；``user_id``：私聊场景（二选一，群优先）；
    - 仅 OneBot v11 支持，其余适配器或协议端发送失败返回 False；
    - 节点身份为 bot 自身（LLOneBot 不支持自定义身份，见 mystool 实测）；
    - ``AWMC_FORWARD=false`` 时直接返回 False（协议端转发实现损坏的部署降级用）。
    """
    if not plugin_config.awmc_forward:
        return False
    if not (
        _OB11
        and OB11Bot is not None
        and OB11Message is not None
        and OB11Segment is not None
    ):
        return False
    if not isinstance(bot, OB11Bot):
        return False
    try:
        nodes = []
        for entry in entries:
            # uniseg 导出在 OB11 适配器下实为 OB11 Message（fallback=forbid，
            # 失败直接抛出走降级）；构造器按段复制，类型系统无需表达适配器关联
            exported = await UniMessage(entry).export(
                bot, fallback=FallbackStrategy.forbid
            )
            nodes.append(
                OB11Segment.node_custom(int(bot.self_id), "Bot", OB11Message(exported))
            )
        forward = OB11Message(nodes)
        if group_id is not None:
            await bot.call_api(
                "send_group_forward_msg",
                group_id=int(group_id),
                message=forward,
                messages=forward,
            )
        elif user_id is not None:
            await bot.call_api(
                "send_private_forward_msg",
                user_id=int(user_id),
                message=forward,
                messages=forward,
            )
        else:
            return False
        return True
    except Exception:
        logger.debug("合并转发发送失败（由调用方降级为普通消息）", exc_info=True)
        return False
