"""OneBot v11 合并转发（协议端实测构造，``message``/``messages`` 双参数兼容）。

参考形态：本插件别名推送（LLBot 实测可用）与 maimaidx/mystool 的构造——
节点为 ``node_custom(bot.self_id, "Bot", 内容)``；alconna uniseg 的 Reference
导出链路在本项目部署的协议端会发出点开为空的转发卡片，故此处直接构造。
NapCat 等实现读 ``messages``、LLOneBot 部分版本只读 ``message``，两参数同时
下发以最大兼容（协议端忽略不认识的参数）。非 OneBot v11 适配器无该能力，
返回 False 由调用方降级为普通消息。
"""

import json
import asyncio
from pathlib import Path
from collections.abc import Sequence

from nonebot import logger
from nonebot.adapters import Bot
from nonebot_plugin_alconna.uniseg import UniMessage, FallbackStrategy

from ..config import NICKNAME

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


def is_ob11(bot) -> bool:
    """bot 实例是否为 OneBot v11 适配器（合并转发/群列表能力判定统一入口）。"""
    return _OB11 and OB11Bot is not None and isinstance(bot, OB11Bot)


def ob11_available() -> bool:
    """OneBot v11 适配器是否可用。"""
    return _OB11 and OB11Bot is not None and OB11Message is not None


def ob11_text(text: str):
    """构造 OneBot v11 文本消息（适配器不可用返回 None）。"""
    if not (_OB11 and OB11Message is not None):
        return None
    return OB11Message(text)


# 转发节点发送者昵称：取 .env 变量列表的 NICKNAME（部署时配置的 bot 名），
# 未配置回退 "Bot"（LLOneBot 源码 senderName: name ?? nickname ?? selfInfo.nick）
NODE_NICKNAME = NICKNAME or "Bot"

# 协议端发送黑洞兜底：LLBot 实测出现过收到 send_group_forward_msg 后不回包、
# 不报错、消息也未落地的挂起（NTQQ 瞬时卡死，同载荷重试即恢复正常）。
# OneBot v11 适配器对动作响应不设超时，无限等待会让调用方永远走不到降级，
# 故给单次转发发送设上限，超时按失败处理。
FORWARD_SEND_TIMEOUT = 10.0


async def try_send_forward(
    bot: Bot,
    entries: Sequence["str | UniMessage"],
    *,
    group_id: int | str | None = None,
    user_id: int | str | None = None,
) -> bool:
    """以合并转发发送多条消息（每条一节点，可为文本或含图片的 UniMessage）。

    - ``group_id``：群聊场景；``user_id``：私聊场景（二选一，群优先）；
    - 仅 OneBot v11 支持，其余适配器、协议端发送失败或超过 ``FORWARD_SEND_TIMEOUT``
      无响应均返回 False，由调用方降级为普通消息；
    - 节点发送者昵称取 ``.env`` 的 ``NICKNAME``（未配置回退 "Bot"），身份为
      bot 自身。
    """
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
            # 节点构造对齐 Hoshino 实测可用形态（2026-09-26 升级）：data 用
            # name/uin 键、content 为裸 dict 数组，图片段 file 统一 file:///
            # URI——node_custom 生成的 user_id/nickname 键与图片段其他 file
            # 形式在 LLOneBot 转发卡片下会渲染为「消息类型暂不支持查看」
            content = [
                {"type": seg.type, "data": dict(seg.data)} for seg in exported
            ]
            for seg in content:
                if seg["type"] == "image":
                    file = str(seg["data"].get("file", ""))
                    if file.startswith("/"):
                        seg["data"]["file"] = Path(file).as_uri()
            nodes.append(
                {
                    "type": "node",
                    "data": {
                        "name": NODE_NICKNAME,
                        "uin": str(bot.self_id),
                        "content": content,
                    },
                }
            )
        forward = nodes
        if group_id is not None:
            await asyncio.wait_for(
                bot.call_api(
                    "send_group_forward_msg",
                    group_id=int(group_id),
                    message=forward,
                    messages=forward,
                ),
                timeout=FORWARD_SEND_TIMEOUT,
            )
        elif user_id is not None:
            await asyncio.wait_for(
                bot.call_api(
                    "send_private_forward_msg",
                    user_id=int(user_id),
                    message=forward,
                    messages=forward,
                ),
                timeout=FORWARD_SEND_TIMEOUT,
            )
        else:
            return False
        return True
    except Exception:
        logger.warning("合并转发发送失败或超时（降级为普通消息）", exc_info=True)
        return False
