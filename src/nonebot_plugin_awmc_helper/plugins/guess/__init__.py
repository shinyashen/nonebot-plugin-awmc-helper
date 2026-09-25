"""awmc.guess：猜歌 / 猜曲绘。

机制（对齐原版）：
- 题库优先取热门曲（谱面游玩样本总数 > 10000；无曲线数据时退化为全曲库）；
- 猜歌：每 ``awmc_guess_interval`` 秒发一条特征提示（8 种抽 6），提示用尽后发
  FFT 裁剪曲绘，``awmc_guess_duration`` 秒后揭晓；猜曲绘直接发裁剪曲绘；
- 答案 = 曲目 ID / 标题 / 别名（不区分大小写），用 priority=0 但不 block 的
  on_message 兜住答案（不吞同群其他指令），不独占会话；
- 群开关 ``guess``：部署默认 ``awmc_guess_enabled``，群级覆盖入库；关闭时终止
  进行中的游戏；`重置猜歌` 强制结束当前对局。

内部结构：``game`` 为对局状态机与编排（可脱离事件层单测），``matchers``
定义指令入口与答案拦截。
"""

from nonebot.plugin import PluginMetadata

__plugin_meta__ = PluginMetadata(
    name="awmc.guess",
    description="舞萌DX 群内猜歌游戏",
    usage="猜歌｜猜曲绘｜重置猜歌｜开启/关闭mai猜歌",
    type="application",
    homepage="https://github.com/shinyashen/nonebot-plugin-awmc-helper",
)

from .matchers import (  # noqa: F401
    guess,
    guess_pic,
    guess_reset,
    guess_answer,
    guess_switch,
)
