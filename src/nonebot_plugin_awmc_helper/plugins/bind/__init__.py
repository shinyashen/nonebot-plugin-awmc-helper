"""awmc.bind：绑定与设置子插件。

指令（对齐原版语义，绑定方案按规划 §5.3 与 maimai-py 对齐）：
- `绑定水鱼`：水鱼 OAuth 设备码授权（QQ 平台，确认码回填完成绑定）
- `绑定水鱼 <用户名>`：公开查询档（QQ 号自动识别）
- `绑定水鱼token <Import-Token>`：全量成绩档
- `水鱼授权码 <确认码>` / `dfcode <确认码>`：回填水鱼 OAuth 确认码
- `绑定落雪`：OAuth 授权（需部署配置）；`绑定落雪 <个人Token|好友码>` 直绑
- `落雪授权码 <code>` / `lxcode <code>`：回填授权码
- `绑定日服 <SEGA ID> <密码>`：日服 NET 直连（b50；密码落库，建议私聊操作）
- `解绑`、`数据源 <0/1/2>`、`主题 <0/1>`、`我的绑定`
"""


from nonebot.plugin import PluginMetadata

from .matchers import (
    unbind as unbind,
)
from .matchers import (
    df_bind as df_bind,
)
from .matchers import (
    df_code as df_code,
)
from .matchers import (
    lx_bind as lx_bind,
)
from .matchers import (
    lx_code as lx_code,
)
from .matchers import (
    my_bind as my_bind,
)
from .matchers import (
    df_token as df_token,
)
from .matchers import (
    net_bind as net_bind,
)
from .matchers import (
    set_theme as set_theme,
)
from .matchers import (
    set_provider as set_provider,
)
from .matchers import (  # matcher 导出：nonebug 按包属性取用
    bind_code_fill as bind_code_fill,
)

__plugin_meta__ = PluginMetadata(
    name="awmc.bind",
    description="舞萌DX 查分器绑定与个人设置",
    usage=(
        "绑定水鱼（OAuth 授权）｜绑定水鱼 <用户名>｜绑定水鱼token <Import-Token>｜"
        "水鱼授权码 <确认码>｜绑定落雪｜绑定落雪 <Token|好友码>｜落雪授权码 <code>｜"
        "绑定日服 <SEGA ID> <密码>｜解绑｜数据源 <0|1|2>｜主题 <0|1>｜我的绑定"
    ),
    type="application",
    homepage="https://github.com/shinyashen/nonebot-plugin-awmc-helper",
)
