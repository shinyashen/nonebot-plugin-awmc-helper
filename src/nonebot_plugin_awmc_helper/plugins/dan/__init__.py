"""awmc.dan：段位认定查询（普通/真段位卡 + 随机段位规则）。

指令与业务在 matchers.py（含帮助注册）；本文件仅装配。
"""

from nonebot.plugin import PluginMetadata

__plugin_meta__ = PluginMetadata(
    name="awmc.dan",
    description="段位认定查询：段位表卡片（达成率/底分预测）与随机段位规则",
    usage="段位\n段位 <段位名>\n段位 随机\n刷新段位",
    type="application",
    homepage="https://github.com/shinyashen/nonebot-plugin-awmc-helper",
)

from .matchers import dan_cmd, dan_refresh

__all__ = ["dan_cmd", "dan_refresh"]
