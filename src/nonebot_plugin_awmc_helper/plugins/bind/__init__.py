"""awmc.bind：绑定与设置子插件。

指令（对齐原版语义，绑定方案按规划 §5.3 与 maimai-py 对齐）：
- `绑定水鱼 <用户名>`：公开查询档（QQ 号自动识别）
- `绑定水鱼token <Import-Token>` / `水鱼授权码 <token>`：全量成绩档
- `绑定落雪`：OAuth 授权（需部署配置）；`绑定落雪 <个人Token|好友码>` 直绑
- `落雪授权码 <code>` / `lxcode <code>`：回填授权码
- `解绑`、`数据源 <0/1>`、`主题 <0/1>`、`我的绑定`
"""

from nonebot import on_command, on_message
from nonebot.rule import Rule
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata
from nonebot.adapters import Bot, Event, Message
from nonebot_plugin_uninfo import Session, UniSession
from nonebot_plugin_alconna.uniseg import UniMessage

from ...core.ext import lxns as lxns_ext
from ...core.utils import handle_errors
from ...core.binding import (
    QQ_PLATFORMS,
    SERVICE_LXNS,
    SERVICE_DIVINGFISH,
    session_keys,
    binding_service,
    pending_bindings,
)

__plugin_meta__ = PluginMetadata(
    name="awmc.bind",
    description="舞萌DX 查分器绑定与个人设置",
    usage=(
        "绑定水鱼 <用户名>｜绑定水鱼token <Import-Token>｜绑定落雪｜"
        "绑定落雪 <Token|好友码>｜落雪授权码 <code>｜解绑｜数据源 <0|1>｜"
        "主题 <0|1>｜我的绑定"
    ),
    type="application",
    homepage="https://github.com/shinyashen/nonebot-plugin-awmc-helper",
)


df_bind = on_command("绑定水鱼", aliases={"绑定df", "dfbind"}, block=True)
df_token = on_command("绑定水鱼token", aliases={"水鱼授权码", "dftoken"}, block=True)
lx_bind = on_command("绑定落雪", aliases={"绑定lx", "lxbind"}, block=True)
lx_code = on_command("落雪授权码", aliases={"lxcode"}, block=True)
unbind = on_command("解绑", block=True)
set_provider = on_command("数据源", block=True)
set_theme = on_command("主题", block=True)
my_bind = on_command("我的绑定", block=True)


async def _is_pending_lxns_code(bot: Bot, event: Event) -> bool:
    from nonebot_plugin_uninfo import get_session

    session = await get_session(bot, event)
    if session is None:
        return False
    if not pending_bindings.is_active(*session_keys(session), "lxns"):
        return False
    return lxns_ext.extract_authorization_code(event.get_plaintext()) is not None


async def _is_expired_lxns_code(bot: Bot, event: Event) -> bool:
    """回填会话刚超时仍发码：给出重发指引而非静默。"""
    from nonebot_plugin_uninfo import get_session

    session = await get_session(bot, event)
    if session is None:
        return False
    if pending_bindings.is_active(*session_keys(session), "lxns"):
        return False
    if not pending_bindings.expired_recently(*session_keys(session), "lxns"):
        return False
    return lxns_ext.extract_authorization_code(event.get_plaintext()) is not None


bind_code = on_message(rule=Rule(_is_pending_lxns_code), priority=0, block=True)
bind_code_expired = on_message(rule=Rule(_is_expired_lxns_code), priority=0, block=True)


@bind_code.handle()
@handle_errors("绑定失败，请稍后再试")
async def _(bot: Bot, event: Event):
    from nonebot_plugin_uninfo import get_session

    session = await get_session(bot, event)
    assert session is not None
    code = lxns_ext.extract_authorization_code(event.get_plaintext())
    assert code is not None
    await _complete_lxns(*session_keys(session), code)


@bind_code_expired.handle()
@handle_errors("绑定失败，请稍后再试")
async def _(bot: Bot, event: Event):
    from nonebot_plugin_uninfo import get_session

    session = await get_session(bot, event)
    assert session is not None
    await UniMessage.text(
        " 落雪授权已超时，请重新发送「绑定落雪」获取新的授权链接"
    ).finish(at_sender=True)


@df_bind.handle()
@handle_errors("绑定失败，请稍后再试")
async def _(session: Session = UniSession(), message: Message = CommandArg()):
    platform, user_id = session_keys(session)
    arg = str(message).strip()
    if not arg and platform not in QQ_PLATFORMS:
        # 无参且无凭据可用：先回用法，不 ensure（否则查一次用法就落一行库）
        await UniMessage.text(" 用法：绑定水鱼 <水鱼用户名>").finish(at_sender=True)
    binding = await binding_service.ensure(platform, user_id)
    if not arg:
        if platform in QQ_PLATFORMS:
            await binding_service.set_service(binding, SERVICE_DIVINGFISH)
            await UniMessage.text(
                "已使用 QQ 号作为水鱼公开查询凭据。\n"
                "如需查询全量成绩（牌子/表格），请使用「绑定水鱼token <Import-Token>」"
                "（获取方式：水鱼个人页 → 设置 → Import-Token）"
            ).finish(at_sender=True)
        await UniMessage.text(" 用法：绑定水鱼 <水鱼用户名>").finish(at_sender=True)
    await binding_service.bind_divingfish_username(binding, arg)
    await UniMessage.text(
        f" 已绑定水鱼账号「{arg}」（公开查询）。\n"
        "如需查询全量成绩（牌子/表格），请使用「绑定水鱼token <Import-Token>」"
    ).finish(at_sender=True)


@df_token.handle()
@handle_errors("绑定失败，请稍后再试")
async def _(session: Session = UniSession(), message: Message = CommandArg()):
    platform, user_id = session_keys(session)
    token = str(message).strip()
    if not token:
        await UniMessage.text(
            "用法：绑定水鱼token <Import-Token>\n"
            "获取方式：水鱼查分器个人页 → 设置 → 生成 Import-Token"
        ).finish(at_sender=True)
    binding = await binding_service.ensure(platform, user_id)
    await binding_service.bind_divingfish_token(binding, token)
    await UniMessage.text(
        " 已保存水鱼 Import-Token（仅存于本机数据库，用于查询全量成绩）"
    ).finish(at_sender=True)


@lx_bind.handle()
@handle_errors("绑定失败，请稍后再试")
async def _(session: Session = UniSession(), message: Message = CommandArg()):
    platform, user_id = session_keys(session)
    arg = str(message).strip()
    if not arg:
        if lxns_ext.oauth_configured():
            pending_bindings.start(platform, user_id, "lxns")
            await UniMessage.text(
                "请点击以下链接完成落雪授权（授权码 90 秒内有效）：\n"
                f"{lxns_ext.build_authorize_url()}\n\n"
                "完成后请直接把授权码回复给我（无需任何前缀）"
            ).finish(at_sender=True)
        await UniMessage.text(
            "BOT 管理员尚未配置落雪 OAuth\n"
            "（AWMC_LXNS_CLIENT_ID/SECRET/REDIRECT_URI）。\n"
            "仍可直接绑定：绑定落雪 <个人Token> 或 绑定落雪 <好友码>"
        ).finish(at_sender=True)
    # 带参数直绑：好友码（纯数字）或个人 Token
    binding = await binding_service.ensure(platform, user_id)
    if arg.isdigit() and 9 <= len(arg) <= 12:
        await binding_service.bind_lxns(binding, token=None, friend_code=int(arg))
        await UniMessage.text(
            f" 已绑定落雪好友码 {arg}（需要部署配置开发者 Token 才能查询）"
        ).finish(at_sender=True)
    await binding_service.bind_lxns(binding, token=arg, friend_code=None)
    await UniMessage.text(" 已绑定落雪个人 Token（仅存于本机数据库）").finish(
        at_sender=True
    )


@lx_code.handle()
@handle_errors("绑定失败，请稍后再试")
async def _(session: Session = UniSession(), message: Message = CommandArg()):
    platform, user_id = session_keys(session)
    code = lxns_ext.extract_authorization_code(str(message))
    if code is None:
        await UniMessage.text(" 授权码格式有误，请重新提交").finish(at_sender=True)
    await _complete_lxns(platform, user_id, code)


async def _complete_lxns(platform: str, user_id: str, code: str) -> None:
    try:
        token = await lxns_ext.fetch_token(code)
    except Exception as e:
        await UniMessage.text(f" 落雪授权失败：{e}").finish(at_sender=True)
    binding = await binding_service.ensure(platform, user_id)
    await binding_service.bind_lxns(
        binding, token=token.access_token, friend_code=token.friend_code
    )
    pending_bindings.discard(platform, user_id)
    fc = f"，好友码 {token.friend_code}" if token.friend_code else ""
    await UniMessage.text(f" 落雪绑定成功{fc}").finish(at_sender=True)


@unbind.handle()
@handle_errors("操作失败，请稍后再试")
async def _(session: Session = UniSession()):
    platform, user_id = session_keys(session)
    if await binding_service.unbind(platform, user_id):
        await UniMessage.text(" 已解除绑定").finish(at_sender=True)
    await UniMessage.text(" 尚未绑定").finish(at_sender=True)


@set_provider.handle()
@handle_errors("设置失败，请稍后再试")
async def _(session: Session = UniSession(), message: Message = CommandArg()):
    arg = str(message).strip()
    service = {"0": SERVICE_DIVINGFISH, "1": SERVICE_LXNS}.get(arg)
    if service is None:
        await UniMessage.text(" 用法：数据源 <0|1>（0 = 水鱼，1 = 落雪）").finish(
            at_sender=True
        )
    platform, user_id = session_keys(session)
    binding = await binding_service.ensure(platform, user_id)
    try:
        await binding_service.set_service(binding, service)
    except Exception as e:
        await UniMessage.text(f" {e}").finish(at_sender=True)
    name = "水鱼" if service == SERVICE_DIVINGFISH else "落雪"
    await UniMessage.text(f" 数据源已切换为{name}").finish(at_sender=True)


@set_theme.handle()
@handle_errors("设置失败，请稍后再试")
async def _(session: Session = UniSession(), message: Message = CommandArg()):
    arg = str(message).strip()
    if arg not in ("0", "1"):
        await UniMessage.text(" 用法：主题 <0|1>（0 = prism_plus，1 = circle）").finish(
            at_sender=True
        )
    platform, user_id = session_keys(session)
    binding = await binding_service.ensure(platform, user_id)
    await binding_service.set_theme(binding, "prism_plus" if arg == "0" else "circle")
    await UniMessage.text(" 主题已切换").finish(at_sender=True)


@my_bind.handle()
@handle_errors("查询失败，请稍后再试")
async def _(session: Session = UniSession()):
    platform, user_id = session_keys(session)
    binding = await binding_service.get(platform, user_id)
    if binding is None:
        await UniMessage.text(" 尚未绑定").finish(at_sender=True)
    lines = [f" 数据源：{'水鱼' if binding.service == SERVICE_DIVINGFISH else '落雪'}"]
    if binding.divingfish_username:
        lines.append(f"水鱼用户名：{binding.divingfish_username}")
    if binding.divingfish_import_token:
        lines.append(f"水鱼 Import-Token：{binding.divingfish_import_token[:4]}****")
    if binding.lxns_friend_code:
        lines.append(f"落雪好友码：{binding.lxns_friend_code}")
    if binding.lxns_token:
        lines.append(f"落雪 Token：{binding.lxns_token[:4]}****")
    lines.append(f"主题：{'prism_plus' if binding.theme == 'prism_plus' else 'circle'}")
    await UniMessage.text("\n".join(lines)).finish(at_sender=True)
