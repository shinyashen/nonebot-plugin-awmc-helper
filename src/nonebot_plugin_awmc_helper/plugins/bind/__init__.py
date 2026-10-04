"""awmc.bind：绑定与设置子插件。

指令（对齐原版语义，绑定方案按规划 §5.3 与 maimai-py 对齐）：
- `绑定水鱼`：水鱼 OAuth 设备码授权（QQ 平台，确认码回填完成绑定）
- `绑定水鱼用户名 <用户名>`：用户名公开查询档
- `绑定水鱼token <Import-Token>`：全量成绩档
- `水鱼授权码 <确认码>` / `dfcode <确认码>`：回填水鱼 OAuth 确认码
- `绑定落雪`：OAuth 授权（需部署配置）；`绑定落雪 <个人Token|好友码>` 直绑
- `落雪授权码 <code>` / `lxcode <code>`：回填授权码
- `绑定日服 <SEGA ID> <密码>`：日服 NET 直连（b50；密码落库，建议私聊操作）
- `解绑`、`数据源 <0/1/2>`、`主题 <0/1>`、`我的绑定`
"""

from nonebot.plugin import PluginMetadata

__plugin_meta__ = PluginMetadata(
    name="awmc.bind",
    description="舞萌DX 查分器绑定与个人设置",
    usage=(
        "绑定水鱼（OAuth 授权）｜绑定水鱼用户名 <用户名>｜"
        "绑定水鱼token <Import-Token>｜水鱼授权码 <确认码>｜"
        "绑定落雪｜绑定落雪 <Token|好友码>｜落雪授权码 <code>｜"
        "绑定日服 <SEGA ID> <密码>｜解绑｜数据源 <0|1|2>｜主题 <0|1>｜我的绑定"
    ),
    type="application",
    homepage="https://github.com/shinyashen/nonebot-plugin-awmc-helper",
)

# matcher 导出（nonebug 按包属性取用；对齐其他拆分插件的装配形态）
from .matchers import (  # noqa: F401
    unbind,
    df_bind,
    df_code,
    df_user,
    lx_bind,
    lx_code,
    my_bind,
    df_token,
    net_bind,
    set_theme,
    set_provider,
    bind_code_fill,
)
