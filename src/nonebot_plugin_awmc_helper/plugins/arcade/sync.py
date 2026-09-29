"""每日 4 点定时任务：同步华立官方机厅数据并清零排卡人数。

注册（apscheduler cron）在插件装配层 ``__init__`` 完成，本模块无副作用。
"""

from nonebot import logger

from ...core import store
from ...core.ext import wahlap as wahlap_ext
from ...core.utils import notify_superusers


async def sync_and_reset() -> int:
    """同步华立机厅并清零排卡人数，返回官方机厅数。"""
    try:
        official = await wahlap_ext.fetch_locations()
    except wahlap_ext.ExtError as e:
        # 连日失败=机厅数据过期+排卡人数不清零，部署者需有感知
        logger.warning(f"华立机厅同步失败：{e}")
        await notify_superusers(f"maimaiDX排卡每日同步失败：{e}")
        return 0
    # 单事务批量 upsert（原先每机厅独立 session 串行两次事务）
    await store.upsert_arcades(
        [
            store.Arcade(
                id=w.id,
                name=w.name,
                address=w.address,
                province=w.province,
                mall=w.mall,
                machines=w.machine_count,
                is_custom=False,
            )
            for w in official
        ]
    )
    count = await store.reset_all_persons()
    logger.info(
        f"maimaiDX排卡数据更新完毕"
        f"（{len(official)} 台官方机厅，{count} 个机厅人数清零）"
    )
    return len(official)
