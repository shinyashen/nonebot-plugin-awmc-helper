"""nonebot-plugin-awmc-example：awmc-helper 第三方扩展示例。

演示一个独立发布的 NoneBot 插件如何复用 nonebot_plugin_awmc_helper 的
core 公开接口（曲库/渲染/异常兜底），可作为第三方插件的起点模板。
与「主插件仓内嵌套子插件」的差异点均以【差异】注释标出：

1. 【差异】先 require() 主插件，再 import（官方《跨插件访问》规范）；
2. 【差异】以绝对模块路径导入 core 公开接口（嵌套子插件用相对导入）；
3. 【差异】PluginMetadata 用第三方自有命名（awmc. 前缀规则仅限主插件仓内）；
4. 【差异】不声明 supported_adapters：经 awmc_plugins/ 加载时主插件元数据
   尚未就绪，inherit_supported_adapters 会 ValueError；确需声明可显式写
   supported_adapters={"~onebot.v11"}；
5. 【差异】帮助注册：指令就近入主插件类别、第三方也能上「舞萌帮助」总览
   （M10 帮助系统，见 docs/subplugin-dev-guide.md「帮助注册」）。

一份代码支持三种加载方式：
- 放入 bot 工作目录的 awmc_plugins/，主插件自动加载（主推，零配置）；
- bot 顶层 [tool.nonebot] plugin_dirs 加载；
- pip 安装后写入加载列表（发布 PyPI 后）。

功能仅为演示接线方式，错误分支未做完备处理——正式插件请对齐主插件
docs/subplugin-dev-guide.md 的硬性规则。
"""

__version__ = "0.1.0"

# 【差异 1】require 必须先于一切对主插件的 import
from nonebot import require

require("nonebot_plugin_awmc_helper")

from nonebot import on_command
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata
from nonebot.adapters import Message
from nonebot_plugin_uninfo import Session, SceneType, UniSession
from nonebot_plugin_alconna.uniseg import UniMessage

from nonebot_plugin_awmc_helper.core.help import CommandSpec, help_registry
from nonebot_plugin_awmc_helper.core.songs import song_service
from nonebot_plugin_awmc_helper.core.utils import handle_errors

# 【差异 2】绝对路径导入 core 公开接口；接口清单见主插件 docs/api.md
from nonebot_plugin_awmc_helper.core.render.tools import text_image_bytes

# 【差异 3】第三方自有命名；supported_adapters 策略见模块 docstring【差异 4】
__plugin_meta__ = PluginMetadata(
    name="awmc-example",
    description="awmc-helper 第三方扩展示例：ext点歌 <关键词>",
    usage="ext点歌 <关键词|编号>：按编号/别名/标题搜索曲目，结果渲染成图回复",
    type="application",
    homepage="https://github.com/shinyashen/nonebot-plugin-awmc-helper",
)

song_cmd = on_command("ext点歌", block=True)


def format_result(kw: str, songs: list, limit: int) -> str:
    """搜索结果转多行文本（渲染前最后一环，测试与本实现同源断言）。"""
    lines = [f"为「{kw}」找到 {len(songs)} 首：", ""]
    for s in songs[:limit]:
        lines.append(f"[{s.id}] {s.title} - {s.artist}")
        diffs = s.difficulties.standard + s.difficulties.dx
        levels = " / ".join(f"{d.level_index.name} {d.level}" for d in diffs)
        lines.append(f"    谱面：{levels or '（无常规谱面）'}")
    return "\n".join(lines)


@song_cmd.handle()
@handle_errors()
async def _(
    session: Session = UniSession(),
    keyword: Message = CommandArg(),
):
    kw = keyword.extract_plain_text().strip()
    if not kw:
        await UniMessage.text(" 用法：ext点歌 <关键词|编号>").finish(at_sender=True)

    # core 曲库服务全部为异步接口；编号精确 → 别名 → 标题模糊 三级回退
    if kw.isdigit():
        songs = [s] if (s := await song_service.by_id(int(kw))) else []
    else:
        songs = await song_service.by_alias(kw) or await song_service.by_title_fuzzy(kw)
    if not songs:
        await UniMessage.text(f" 没找到包含「{kw}」的曲目").finish(at_sender=True)

    # 群聊结果列表收敛到 5 首，私聊等场景放宽到 10 首
    limit = 5 if session.scene.type == SceneType.GROUP else 10
    await UniMessage.image(
        raw=text_image_bytes(format_result(kw, songs, limit))
    ).finish(at_sender=True)


# ---------------------------------------------------------------- 帮助声明
# 【差异 5】matcher 对象直接作声明键（防文案漂移）；未知 category 会自动建类，
# 这里就近入内置「查歌」类。声明块惯例放 matcher 定义之后（文件末尾）。
help_registry.declare(
    plugin="awmc-example",
    title="扩展点歌",
    category="query",
    description="第三方扩展示例",
    commands=[
        CommandSpec(
            matcher=song_cmd,
            name="ext点歌",
            brief="按编号/别名/标题搜索曲目（结果渲染成图）",
            detail="格式：ext点歌 <关键词|编号>；群聊列 5 首，私聊 10 首。",
        ),
    ],
)
