"""awmc.score_query：查分子插件（b50 / ap50 / minfo / ginfo）。

- 支持 @某人 代查（b50/minfo/ginfo）；
- 未绑定时按部署默认源以 QQ 公开查询（仅水鱼、QQ 平台）；
- ap50 为本地过滤实现（maimai-py 无 AP50 端点）：全量成绩中取 FC=AP/APP 的 RA 前 50。

内部结构：``matchers`` 定义指令入口；``render`` 为结果增强渲染（ginfo 统计卡拼接）。
"""

from nonebot.plugin import PluginMetadata

__plugin_meta__ = PluginMetadata(
    name="awmc.score_query",
    description="舞萌DX 查分：b50/ap50/minfo/ginfo",
    usage="b50 [水鱼用户名]｜ap50｜minfo <曲目ID|曲名|别名>｜ginfo <[难度色]曲目>",
    type="application",
    homepage="https://github.com/shinyashen/nonebot-plugin-awmc-helper",
)

from .matchers import b50, ap50, ginfo, minfo  # noqa: F401
