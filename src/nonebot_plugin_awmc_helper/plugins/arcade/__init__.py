"""awmc.arcade：机厅排卡（原版独立 Service 的完整复刻）。

指令（对齐原版 maimaiDX排卡）：
- `开启排卡 / 关闭排卡`（群管；排卡默认关，对齐原版 Service enable_on_default=False）
- `帮助maimaiDX排卡`（不设门禁，便于发现开启入口）
- `添加机厅 <店名> <地址> <机台数> [别称...]`（SUPERUSER，自定义 ID≥10000）
- `删除机厅 <店名>`（SUPERUSER）
- `添加机厅别名 <店名|ID> <别名>` / `删除机厅别名 <别名>`
- `修改机厅 <店名|ID> 数量 <数量>`
- `订阅机厅 / 取消订阅机厅 <店名|ID>`、`查看订阅`
- `查找机厅 <关键词>`（店名/地址/别称模糊，≥5 条转图）
- `<店名|别称>设置/=/增加/加/+/减少/减/- <人数>[人|卡]`（人人可排，对齐原版；
  仅本群订阅机厅，±上限可配；无店名消息静默忽略）
- `机厅几人 / jtj`、`<店名|别称>有多少人/几人/几卡...`
- 每日 4 点：同步华立官方机厅数据并清零排卡人数

群级开关经 core/store 的 group_switch（特征名 `arcade`），部署级默认
AWMC_ARCADE_ENABLED（默认 False）。内部结构：``matchers`` 定义指令入口；
``sync`` 为每日同步任务（定时注册在下方装配层完成）。
"""

from nonebot.plugin import PluginMetadata

try:
    from nonebot_plugin_apscheduler import scheduler
except ImportError:  # pragma: no cover
    scheduler = None  # type: ignore[assignment]

__plugin_meta__ = PluginMetadata(
    name="awmc.arcade",
    description="舞萌DX 机厅排卡",
    usage=(
        "开启/关闭排卡（群管）｜添加机厅 <店名> <地址> <机台数> [别称...]｜"
        "订阅机厅 <店名>｜查找机厅 <关键词>｜XX店+2人｜机厅几人｜帮助maimaiDX排卡"
    ),
    type="application",
    homepage="https://github.com/shinyashen/nonebot-plugin-awmc-helper",
)

from .sync import sync_and_reset
from .matchers import (  # noqa: F401
    arcade_add,
    arcade_del,
    arcade_set,
    arcade_sub,
    arcade_help,
    arcade_search,
    arcade_switch,
    arcade_show_sub,
    arcade_alias_set,
    arcade_add_person,
    arcade_person_num,
    arcade_person_num_2,
)

if scheduler is not None:
    # misfire_grace_time=3600 + coalesce：进程繁忙或重启跨过 4 点时当日同步
    # 补跑一次而非静默跳过
    scheduler.add_job(
        sync_and_reset,
        "cron",
        hour=4,
        minute=0,
        misfire_grace_time=3600,
        coalesce=True,
    )
