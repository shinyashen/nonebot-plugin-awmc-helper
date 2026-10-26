"""awmc.chart_preview：谱面预览子插件。

指令：
- `谱面预览 <曲名/别名/id> [难度]`——渲染 maimai 下落式谱面预览视频
  （simai 热链落雪素材站 + 整曲音频，ffmpeg 合成 mp4）。

内部结构：``matchers`` 定义指令入口；``preview`` 负责难度词解析、
曲目定位与「抓取 → 解析 → 渲染 → 缓存」编排。
"""

from nonebot import logger, get_driver
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


@get_driver().on_startup
async def _check_ffmpeg() -> None:
    """启动时探测 ffmpeg 可用性/编码器/滤镜，缺失时写日志提示部署方。"""
    from ...config import plugin_config
    from ...core.render.chart_video import (
        FFmpegUnavailableError,
        check_ffmpeg,
    )

    if not plugin_config.awmc_chart_preview_enabled:
        return
    try:
        status = check_ffmpeg(plugin_config.awmc_chart_preview_ffmpeg)
    except FFmpegUnavailableError as exc:
        logger.warning(f"谱面预览不可用：{exc}")
        return
    if status.missing:
        logger.warning(
            "谱面预览用的 ffmpeg 缺少 {}（{}）：渲染/混音会失败，请换完整构建"
            "或配置 awmc_chart_preview_ffmpeg".format(
                "、".join(status.missing), status.version
            )
        )
    else:
        logger.debug(f"谱面预览 ffmpeg 就绪：{status.version}（{status.path}）")
