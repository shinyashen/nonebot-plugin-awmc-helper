"""主帮助与项目地址指令（对齐原版 mai_base）。

挂在 bind 子插件下（基础设置类指令）。
"""

from nonebot import on_command
from nonebot.plugin import PluginMetadata
from nonebot_plugin_alconna.uniseg import UniMessage

from ...core.utils import handle_errors
from ...core.render.tools import text_to_image, image_to_bytes

__plugin_meta__ = PluginMetadata(
    name="awmc.base",
    description="舞萌DX 帮助与项目地址",
    usage="帮助maimaiDX｜项目地址maimaiDX",
    type="application",
    homepage="https://github.com/shinyashen/nonebot-plugin-awmc-helper",
)

HELP_TEXT = """舞萌DX 插件指令总览（详细见 docs/commands.md）
──────────── 绑定/设置 ────────────
绑定水鱼 <用户名> / 绑定水鱼token <Token>
绑定落雪 / 绑定落雪 <Token|好友码>
数据源 <0|1> / 主题 <0|1> / 我的绑定 / 解绑
──────────── 查歌 ────────────
查歌 <标题> [页] / 定数查歌 <定数> [页]
bpm查歌 / 曲师查歌 / 谱师查歌
<名称>是什么歌 / id <数字>
──────────── 别名 ────────────
<名称>有什么别名 / 添加本地别名 <id> <别名>
添加别名 <id> <别名> / 同意别名 <TAG>
当前投票 [页] / (全局)开启|关闭别名推送
──────────── 查分 ────────────
b50 [水鱼用户名] / ap50 / minfo <曲> / ginfo <[色]曲>
──────────── 工具 ────────────
分数线 <色><id> <线> / 我要上N分 / 查看排名 / 我的排名
──────────── 表格 ────────────
<等级>定数表 / <等级><评价>完成表 / <等级><评价>进度
<版本><牌种>完成表|进度 / 牌子条件 / <等级|定数>分数列表
──────────── 娱乐 ────────────
来个/随个/给个 <谱面> / mai什么 / 今日mai
猜歌 / 猜曲绘 / 重置猜歌 / 开启|关闭mai猜歌
──────────── 排卡 ────────────
开启|关闭排卡（群管，默认关） / 订阅机厅
添加/删除/修改机厅 / 查找机厅
XX店+2人 / 机厅几人 / 帮助maimaiDX排卡"""

REPO_URL = "项目地址：https://github.com/shinyashen/nonebot-plugin-awmc-helper"

help_cmd = on_command("帮助maimaiDX", aliases={"帮助maimaidx"}, block=True)
repo_cmd = on_command("项目地址maimaiDX", aliases={"项目地址maimaidx"}, block=True)


@help_cmd.handle()
@handle_errors()
async def _():
    await UniMessage.image(
        raw=image_to_bytes(text_to_image(HELP_TEXT, size=22))
    ).finish(at_sender=True)


@repo_cmd.handle()
@handle_errors()
async def _():
    await UniMessage.text(REPO_URL + "\n求 star，求宣传~").finish(at_sender=True)
