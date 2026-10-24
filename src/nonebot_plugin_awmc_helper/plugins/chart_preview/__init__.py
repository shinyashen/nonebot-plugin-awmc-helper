"""awmc.chart_preview：谱面预览子插件。

指令：
- `谱面预览 <曲名/别名/id> [难度]`——渲染 maimai 下落式谱面预览视频
  （simai 热链落雪素材站 + 整曲音频，ffmpeg 合成 mp4）。

内部结构：``matchers`` 定义指令入口；``preview`` 负责难度词解析、
曲目定位与「抓取 → 解析 → 渲染 → 缓存」编排。
"""

from nonebot.plugin import PluginMetadata

__plugin_meta__ = PluginMetadata(
    name="awmc.chart_preview",
    description="舞萌DX 谱面预览：渲染下落式谱面预览视频",
    usage=(
        "谱面预览 <曲名/别名/id> [难度]（难度可省略，默认 Master；"
        "支持 紫/红/黄/绿/白 与英文难度名）"
    ),
    type="application",
    homepage="https://github.com/shinyashen/nonebot-plugin-awmc-helper",
)

from .matchers import chart_preview  # noqa: F401
