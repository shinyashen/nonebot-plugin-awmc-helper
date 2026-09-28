"""落雪令牌每日保活。

refresh_token 30 天不刷新即失效（落雪 OAuth 文档口径；每次刷新轮换并重置
有效期），闲置超期的绑定会走进「续期死局」——该用户落雪链路（b50/minfo/
导分）持续失败且只能重新绑定。本任务每日低峰对全部持有 refresh_token 的
绑定续期一次，正常部署下 rt 永不过期。

必须复用 :meth:`BindingService.refresh_lxns`（per-user 锁 + 锁内重读 + 锁内
落库），与按需续期同一条并发安全链路，不得绕开另行 grant。dead（需重新
绑定）不做数据库标记：对已确认失效的 rt 每天重试一次无副作用（8 行规模
可忽略），用户重新绑定后自然恢复；汇总经 SUPERUSER 私聊报告（仅在有需
重绑用户时推送，网络类跳过不打扰）。
"""

import asyncio

from nonebot.log import logger
from nonebot_plugin_apscheduler import scheduler

from . import store, utils
from ..config import plugin_config
from .binding import binding_service


async def lxns_keepalive() -> tuple[str, int]:
    """对全部持有 refresh_token 的绑定逐个续期一次。

    返回 (人读摘要, 需重新绑定数)。逐个执行并轻微节流：避免瞬时并发
    grant 打上游。
    """
    rows = await store.get_lxns_refreshable_bindings()
    if not rows:
        return "落雪令牌保活：无绑定需要处理", 0
    counts = {"refreshed": 0, "dead": 0, "skip": 0}
    dead_users: list[str] = []
    for b in rows:
        status = await binding_service.refresh_lxns(b)
        counts[status] += 1
        if status == "dead":
            dead_users.append(
                b.user_id if b.user_id.isdigit() else f"{b.platform}:{b.user_id}"
            )
        await asyncio.sleep(1)
    summary = (
        f"落雪令牌保活完成：成功 {counts['refreshed']} 个，"
        f"需重新绑定 {counts['dead']} 个，暂时跳过 {counts['skip']} 个"
    )
    if dead_users:
        summary += "（" + "、".join(dead_users) + "）"
    logger.info(summary)
    return summary, counts["dead"]


async def _daily_keepalive() -> None:
    try:
        summary, dead = await lxns_keepalive()
    except Exception:
        logger.exception("落雪令牌保活任务失败")
        return
    if dead:
        await utils.notify_superusers(summary)


if plugin_config.awmc_lxns_keepalive:
    scheduler.add_job(_daily_keepalive, "cron", hour=4, minute=30)
    """每日 4:30 保活（awmc_lxns_keepalive=false 可关闭）。"""
