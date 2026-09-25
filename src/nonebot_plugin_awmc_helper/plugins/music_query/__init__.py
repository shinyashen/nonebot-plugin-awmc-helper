"""awmc.music_query：查歌子插件。

指令（对齐原版 maimaiDX）：
- `查歌 <标题> [页]`
- `定数查歌 <定数|最小 最大> [页]`
- `bpm查歌 <bpm|最小 最大> [页]`
- `曲师查歌 <曲师> [页]` / `谱师查歌 <谱师> [页]`
- `<名称>是什么歌 [页]`（别名优先，其次关键词）
- `id <数字>`

内部结构：``matchers`` 定义指令入口；``resolve`` 负责 id/别名解析链；
``render`` 负责结果渲染与国服 → 日服 → pending 兜底编排。
"""

from nonebot.plugin import PluginMetadata

__plugin_meta__ = PluginMetadata(
    name="awmc.music_query",
    description="舞萌DX 查歌：标题/定数/BPM/曲师/谱师/别名/ID",
    usage=(
        "查歌 <标题> [页]｜定数查歌 <定数> [页]｜bpm查歌｜曲师查歌｜谱师查歌｜"
        "<名称>是什么歌｜id <数字>"
    ),
    type="application",
    homepage="https://github.com/shinyashen/nonebot-plugin-awmc-helper",
)

from .matchers import search, query_chart, search_alias_song  # noqa: F401
