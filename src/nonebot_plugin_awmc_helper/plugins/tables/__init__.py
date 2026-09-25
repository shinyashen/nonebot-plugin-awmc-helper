"""awmc.tables：定数表 / 完成表 / 牌子 / 进度 / 分数列表。

指令（对齐原版 maimaiDX）：
- `<等级>定数表`（如 13+定数表）
- `<等级><评价>完成表`（如 13fc完成表 / 14sssp完成表）
- `<等级><评价>进度 [页]`（如 13fc进度）
- `<版本><牌种>完成表 / 进度`（如 真将完成表 / 樱极进度）
- `牌子条件`
- `<等级|定数>分数列表 [页]`
- `更新定数表 / 更新完成表`（SUPERUSER，预渲染底图；查询时叠加成绩）

内部结构：``matchers`` 定义指令入口；``sheet`` 为表格编排域（评价计划
判定 / 等级条目查询 / 牌子完成表与进度总览）。
"""

from nonebot import get_driver
from nonebot.plugin import PluginMetadata

from ...config import plugin_config

__plugin_meta__ = PluginMetadata(
    name="awmc.tables",
    description="舞萌DX 完成度表格：定数表/完成表/牌子/进度/分数列表",
    usage="13+定数表｜13fc完成表｜13fc进度｜真将完成表｜牌子条件｜13+分数列表",
    type="application",
    homepage="https://github.com/shinyashen/nonebot-plugin-awmc-helper",
)


@get_driver().on_startup
async def _warn_missing_templates() -> None:
    """NB 方案：底图未生成时启动告警（提示 SUPERUSER 执行更新指令）。"""
    if not plugin_config.awmc_startup_tasks:
        return
    from nonebot import logger

    from ...core.render.table_template import plate_table_dir, rating_table_dir

    if not rating_table_dir().exists() or not any(rating_table_dir().iterdir()):
        logger.warning("定数表底图未生成，请 SUPERUSER 执行「更新定数表」")
    if not plate_table_dir().exists() or not any(plate_table_dir().iterdir()):
        logger.warning("完成表底图未生成，请 SUPERUSER 执行「更新完成表」")


from .matchers import (  # noqa: F401
    plate_cmd,
    plate_help,
    ds_table_cmd,
    progress_cmd,
    update_plate,
    update_rating,
    score_list_cmd,
    score_table_cmd,
)
