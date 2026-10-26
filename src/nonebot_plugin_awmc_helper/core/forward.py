"""OneBot v11 合并转发（协议端实测构造，``message``/``messages`` 双参数兼容）。

参考形态：本插件别名推送（LLBot 实测可用）与 maimaidx/mystool 的构造——
节点为 ``node_custom(bot.self_id, "Bot", 内容)``；alconna uniseg 的 Reference
导出链路在本项目部署的协议端会发出点开为空的转发卡片，故此处直接构造。
NapCat 等实现读 ``messages``、LLOneBot 部分版本只读 ``message``，两参数同时
下发以最大兼容（协议端忽略不认识的参数）。非 OneBot v11 适配器无该能力，
返回 False 由调用方降级为普通消息。
"""

import asyncio
from pathlib import Path
from collections.abc import Sequence

from nonebot import logger
from nonebot.adapters import Bot
from nonebot_plugin_alconna.uniseg import UniMessage, FallbackStrategy

from .utils import user_id_of, group_id_of
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
# 故给单次转发发送设上限；超时后先取最近消息核对是否其实已落地（2026-10-27
# 实测：卡片已发出但 ack 超过 10s，直接降级会让用户收到重复消息）。
FORWARD_SEND_TIMEOUT = 20.0
FORWARD_VERIFY_TIMEOUT = 5.0


async def _find_forward_landed(
    bot: Bot,
    *,
    group_id: int | str | None,
    user_id: int | str | None,
    since: float,
) -> bool | None:
    """发送超时后查最近消息，判断转发卡片是否其实已落地。

    返回 True=已落地；False=确认没有；None=无法校验（接口不支持/调用失败）。
    LLBot 的 send_group_forward_msg 可能卡片已发出但 ack 很慢（>超时阈值），
    仅凭超时降级会重复发送，故用消息历史兜底；只认 ``since`` 之后出现的
    forward/json/node 段，避免把历史里的旧转发误判成本次结果。
    """

    if group_id is not None:
        api = "get_group_msg_history"
        params: dict[str, object] = {"group_id": int(group_id), "count": 10}
    elif user_id is not None:
        api = "get_friend_msg_history"
        params = {"user_id": int(user_id), "count": 10}
    else:
        return None
    try:
        data = await asyncio.wait_for(
            bot.call_api(api, **params), timeout=FORWARD_VERIFY_TIMEOUT
        )
    except Exception:
        return None
    messages = (data or {}).get("messages") if isinstance(data, dict) else None
    if not isinstance(messages, list):
        return None
    for message in messages:
        if not isinstance(message, dict):
            continue
        sent_at = message.get("time")
        if isinstance(sent_at, (int, float)) and sent_at + 2 < since:
            continue  # 早于本次发送时间的旧消息，不算
        content = message.get("message")
        segments = content if isinstance(content, list) else []
        for segment in segments:
            if isinstance(segment, dict) and segment.get("type") in (
                "forward",
                "json",
                "node",
            ):
                return True
    return False


async def try_send_forward(
    bot: Bot,
    entries: Sequence["str | UniMessage"],
    *,
    group_id: int | str | None = None,
    user_id: int | str | None = None,
) -> bool:
    """以合并转发发送多条消息（每条一节点，可为文本或含图片的 UniMessage）。

    - ``group_id``：群聊场景；``user_id``：私聊场景（二选一，群优先）；
    - 仅 OneBot v11 支持，其余适配器、协议端报错返回 False；发送超时先查
      最近消息历史核对卡片是否已落地（LLBot 卡片已发出但 ack 慢于阈值时
      不降级，避免重复发送），确认未落地才返回 False 交由调用方降级；
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
            content = [{"type": seg.type, "data": dict(seg.data)} for seg in exported]
            for seg in content:
                # 本地图片/视频都转 file:/// URI（同 image 的协议端口径；
                # 谱面预览合并转发的视频节点走这里）
                if seg["type"] in ("image", "video"):
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
        # 群/私聊仅 api 名与定位参数不同，单调用点组装（message/messages
        # 双参数同时下发以最大兼容，协议端忽略不认识的参数）
        if group_id is not None:
            api, params = "send_group_forward_msg", {"group_id": int(group_id)}
        elif user_id is not None:
            api, params = "send_private_forward_msg", {"user_id": int(user_id)}
        else:
            return False
        import time

        started = time.time()
        try:
            await asyncio.wait_for(
                bot.call_api(api, **params, message=nodes, messages=nodes),
                timeout=FORWARD_SEND_TIMEOUT,
            )
            logger.debug(f"合并转发已发送（{api}，耗时 {time.time() - started:.1f}s）")
            return True
        except asyncio.TimeoutError:
            # 超时不等于失败：LLBot 实测存在卡片已发出但 ack 很慢的情况，
            # 先查消息历史核对；真没落地才让调用方降级，避免重复发送。
            landed = await _find_forward_landed(
                bot, group_id=group_id, user_id=user_id, since=started
            )
            if landed is False:
                logger.warning(
                    f"合并转发超时且确认未落地（{api}，"
                    f"{time.time() - started:.1f}s），降级为普通消息"
                )
                return False
            if landed is None:
                logger.warning(
                    f"合并转发超时且无法核对（{api}，"
                    f"{time.time() - started:.1f}s），按已发送处理以避免重复"
                )
                return True
            logger.warning(
                f"合并转发 ack 超时但卡片已落地（{api}，"
                f"{time.time() - started:.1f}s），按成功处理"
            )
            return True
        except Exception as exc:
            logger.opt(exception=True).warning(
                f"合并转发发送失败（{api}：{exc!r}），降级为普通消息"
            )
            return False
    except Exception as exc:  # 构造节点/导出失败（非发送阶段）
        logger.opt(exception=True).warning(
            f"合并转发构造失败（{exc!r}），降级为普通消息"
        )
        return False


async def try_send_forward_session(
    bot: Bot,
    entries: Sequence["str | UniMessage"],
    session,
) -> bool:
    """按会话场景发送合并转发：群聊取群 id、私聊取用户 id（uninfo 谓词单源）。

    各子插件此前在调用点内联的「group_id 判定 + user_id 回退」三处重复
    收编本助手，内部转发 :func:`try_send_forward`，返回值语义不变
    （非 OB11 适配器/发送失败返回 False 由调用方降级）。
    """
    group_id = group_id_of(session)
    return await try_send_forward(
        bot,
        entries,
        group_id=group_id,
        user_id=None if group_id is not None else user_id_of(session),
    )
