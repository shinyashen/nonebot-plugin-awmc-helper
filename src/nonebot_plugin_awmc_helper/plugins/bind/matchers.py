"""awmc.bind 指令入口：绑定/设置指令的 matcher 与 handler。

NET 绑定验证域的业务逻辑在 :mod:`.net`（无注册副作用）；本模块只做
参数解析、门禁与文案组装。
"""

from collections.abc import Callable

from nonebot import on_command, on_message
from nonebot.rule import Rule
from nonebot.params import CommandArg
from nonebot.adapters import Bot, Event, Message
from nonebot_plugin_uninfo import Session, UniSession
from nonebot_plugin_alconna.uniseg import UniMessage

from . import net
from ...core.ext import lxns as lxns_ext
from ...core.ext import divingfish as df_ext
from ...constants import SERVICE_ZH
from ...core.help import CommandSpec, help_registry
from ...core.utils import handle_errors, is_private_session
from ...core.binding import (
    SERVICE_NET,
    QQ_PLATFORMS,
    SERVICE_LXNS,
    SERVICE_DIVINGFISH,
    session_keys,
    binding_service,
    pending_bindings,
)
from ...core.sources import command_hints

# 水鱼 OAuth 设备码绑定文案（对齐 Hoshino oauth_message.py 措辞）
DIVINGFISH_NO_SESSION_MSG = "请先发送「绑定水鱼」获取授权链接，完成授权后再发送确认码。"
DIVINGFISH_INVALID_CODE_MSG = (
    "未识别到有效的水鱼确认码。\n"
    "请发送授权完成页面显示的完整确认码，形如 BCDF-GHJK-LMNP。"
)
DIVINGFISH_MISMATCH_MSG = (
    "水鱼绑定失败：这串确认码对应的授权不属于您的账号。\n"
    "确认码只能由发起绑定的本人使用，请勿使用他人转发给您的确认码。\n"
    "如需绑定自己的账号，请发送「绑定水鱼」重新走一遍授权。"
)
DIVINGFISH_BIND_SUCCESS_MSG = "水鱼查分器授权完成，现在可以直接使用查询指令了。"
DIVINGFISH_TOKEN_LOOKALIKE_MSG = (
    "这串内容像是水鱼 Import-Token 而不是用户名，为避免误绑未做保存：\n"
    "保存 Token 请发送「绑定水鱼token <Import-Token>」；\n"
    "绑定公开查询请发送「绑定水鱼用户名 <水鱼用户名>」（水鱼个人页显示的用户名）。"
)

df_bind = on_command("绑定水鱼", aliases={"绑定df", "dfbind"}, block=True)
df_user = on_command("绑定水鱼用户名", aliases={"dfuser", "绑定df用户名"}, block=True)
df_token = on_command("绑定水鱼token", aliases={"dftoken"}, block=True)
lx_bind = on_command("绑定落雪", aliases={"绑定lx", "lxbind"}, block=True)
lx_code = on_command("落雪授权码", aliases={"lxcode"}, block=True)
df_code = on_command("水鱼授权码", aliases={"dfcode"}, block=True)
net_bind = on_command("绑定日服", aliases={"绑定net", "netbind"}, block=True)
unbind = on_command("解绑", block=True)
set_provider = on_command("数据源", block=True)
set_theme = on_command("主题", block=True)
my_bind = on_command("我的绑定", block=True)


_CODE_SERVICES: tuple[tuple[str, Callable[[str], str | None]], ...] = (
    ("lxns", lxns_ext.extract_authorization_code),
    ("divingfish", df_ext.extract_confirmation_code),
)
"""回填会话覆盖的数据源 → 授权码提取函数（单一来源：_code_fill_state 与
回填 handler 均由本表派生；两家授权码长相一样，靠会话 kind 区分归属）。"""

_CODE_EXTRACTORS: dict[str, Callable[[str], str | None]] = dict(_CODE_SERVICES)


async def _code_fill_state(
    bot: Bot, event: Event
) -> "tuple[tuple[str, str], str, str] | None":
    """回填消息全量判定：返回 ((platform, user_id), service, "pending"|"expired")，
    非回填消息 None。

    pending 优先：会话活跃且文本为对应授权码 → 回填；会话刚超时仍发码 →
    给重发指引而非静默。四条近似 rule 收进一个 matcher（原 2×2 组合）。
    """
    keys = await _keys_of(bot, event)
    if keys is None:
        return None
    event_text = event.get_plaintext()
    for service, extract in _CODE_SERVICES:
        if extract(event_text) is None:
            continue
        if pending_bindings.is_active(*keys, service):
            return keys, service, "pending"
        if pending_bindings.expired_recently(*keys, service):
            return keys, service, "expired"
    return None


async def _is_code_fill(bot: Bot, event: Event) -> bool:
    """回填 matcher 的 rule（checker 须返回 bool；具体状态 handler 再判定）。"""
    # 空会话表先短路：免每条消息白构造 uninfo Session（L-29）
    if not pending_bindings.any_active():
        return False
    return await _code_fill_state(bot, event) is not None


async def _keys_of(bot: Bot, event: Event) -> tuple[str, str] | None:
    from nonebot_plugin_uninfo import get_session

    session = await get_session(bot, event)
    if session is None:
        return None
    return session_keys(session)


bind_code_fill = on_message(rule=Rule(_is_code_fill), priority=0, block=True)


@bind_code_fill.handle()
@handle_errors("绑定失败，请稍后再试")
async def _(bot: Bot, event: Event):
    state = await _code_fill_state(bot, event)
    assert state is not None
    keys, service, phase = state
    if phase == "expired":
        zh = SERVICE_ZH[service]
        cmd = "「绑定落雪」" if service == "lxns" else "「绑定水鱼」"
        await UniMessage.text(
            f" {zh}授权已超时，请重新发送{cmd}获取新的授权链接"
        ).finish(at_sender=True)
    text = event.get_plaintext()
    code = _CODE_EXTRACTORS[service](text)
    assert code is not None
    if service == "lxns":
        await _complete_lxns(*keys, code)
    else:
        await _complete_df(*keys, code)


@df_code.handle()
@handle_errors("绑定失败，请稍后再试")
async def _(session: Session = UniSession(), message: Message = CommandArg()):
    platform, user_id = session_keys(session)
    code = df_ext.extract_confirmation_code(message.extract_plain_text())
    if code is None:
        await UniMessage.text(" " + DIVINGFISH_INVALID_CODE_MSG).finish(at_sender=True)
    await _complete_df(platform, user_id, code)


@df_bind.handle()
@handle_errors("绑定失败，请稍后再试")
async def _(session: Session = UniSession(), message: Message = CommandArg()):
    """「绑定水鱼」：OAuth 设备码授权（handoff=code 确认码回填，对齐
    Hoshino 上游与落雪同构 UX）；带确认码 → 回填收尾；其他参数一律软引导
    （用户名档走「绑定水鱼用户名」，拒绝输入不落行）。"""
    platform, user_id = session_keys(session)
    arg = message.extract_plain_text().strip()
    if arg:
        code = df_ext.extract_confirmation_code(arg)
        if code is not None:
            if pending_bindings.is_active(platform, user_id, "divingfish"):
                await _complete_df(platform, user_id, code)
            await UniMessage.text(" " + DIVINGFISH_NO_SESSION_MSG).finish(
                at_sender=True
            )
        if df_ext.looks_like_import_token(arg):
            # 误投 token 会把凭据存成用户名、b50 按用户名查必败（Q51）；
            # 软引导到 token 指令，且不 ensure（拒绝输入不落行）
            await UniMessage.text(" " + DIVINGFISH_TOKEN_LOOKALIKE_MSG).finish(
                at_sender=True
            )
        await UniMessage.text(
            " 「绑定水鱼」是 OAuth 授权指令，不接收参数。\n"
            f"绑定用户名公开查询请发送「绑定水鱼用户名 {arg}」"
        ).finish(at_sender=True)
    if platform in QQ_PLATFORMS and df_ext.oauth_ready():
        # 设备码授权（sunset 文档 §3.2 完整版）：handoff=code 确认码回填，
        # scope 一次带齐 read+write（水鱼已要求所有写入走 OAuth）。
        # 仅 QQ 平台提供：非 QQ 平台用户没有 QQ 身份标识可派生 subject
        # （导分写目标缺口，已知边界，见审查报告 §七）。
        binding = await binding_service.ensure(platform, user_id)
        ref = binding_service.divingfish_subject(binding)
        if ref is None:  # pragma: no cover —— QQ 平台必可派生
            await UniMessage.text(
                " 当前绑定缺少水鱼授权所需的身份标识，请重新发送「绑定水鱼」"
            ).finish(at_sender=True)
        label = df_ext.binding_label(user_id)
        try:
            device = await df_ext.device_authorize(ref[4:], label)
        except df_ext.ExtError as e:
            await UniMessage.text(f" 水鱼授权发起失败：{e}").finish(at_sender=True)
        expires_in = int(device.get("expires_in", 1200))
        # 本地会话窗与服务端有效期取小：服务端更短时提前过期，
        # 免得用户在死会话里反复回填（服务端兜底靠 invalid_grant）
        pending_bindings.start(
            platform, user_id, "divingfish", ttl=min(1200, expires_in)
        )
        link = device.get("verification_uri_complete") or device.get(
            "verification_uri", ""
        )
        minutes = max(expires_in // 60, 1)
        await UniMessage.text(
            "水鱼已要求所有成绩写入走 OAuth 授权，请完成一次绑定：\n\n"
            "1. 打开以下链接并登录水鱼账号，授权本 BOT 访问您的水鱼查分器数据\n"
            "=======================\n"
            f"{link}\n"
            "=======================\n"
            f"2. 确认页面显示的绑定身份为「{label}」后点击「同意授权」\n"
            "3. 复制页面给出的确认码，直接发送给我（无需任何前缀）\n\n"
            f"本次绑定 {minutes} 分钟内有效，确认码只能使用一次；"
            "超时或失效后请重新发送「绑定水鱼」。\n"
            "=======================\n"
            "请注意！！链接与确认码都仅供您本人使用，请勿转发他人。\n"
            "确认码建议在与 BOT 的私聊中发送，避免被他人看到。\n"
            f"如需取消授权，请前往 {df_ext.REVOKE_URL}"
        ).finish(at_sender=True)
    if platform in QQ_PLATFORMS:
        await UniMessage.text(
            " BOT 管理员尚未配置水鱼 OAuth\n"
            "（AWMC_DIVINGFISH_OAUTH_CLIENT_ID/SECRET）。\n"
            "仍可直接绑定：绑定水鱼用户名 <水鱼用户名>（公开查询）"
        ).finish(at_sender=True)
    await UniMessage.text(
        " 水鱼 OAuth 授权需要 QQ 身份标识，当前平台暂不支持。\n"
        "请使用「绑定水鱼用户名 <水鱼用户名>」绑定公开查询"
    ).finish(at_sender=True)


@df_user.handle()
@handle_errors("绑定失败，请稍后再试")
async def _(session: Session = UniSession(), message: Message = CommandArg()):
    """「绑定水鱼用户名」：用户名公开查询档（玩家信息/b50/RA 排名）。"""
    platform, user_id = session_keys(session)
    arg = message.extract_plain_text().strip()
    if not arg:
        await UniMessage.text(
            " 用法：绑定水鱼用户名 <水鱼用户名>（水鱼个人页显示的用户名）"
        ).finish(at_sender=True)
    if df_ext.looks_like_import_token(arg):
        # 误投 token 会把凭据存成用户名、b50 按用户名查必败（Q51）；
        # 软引导到 token 指令，且不 ensure（拒绝输入不落行）
        await UniMessage.text(" " + DIVINGFISH_TOKEN_LOOKALIKE_MSG).finish(
            at_sender=True
        )
    binding = await binding_service.ensure(platform, user_id)
    await binding_service.bind_divingfish_username(binding, arg)
    await UniMessage.text(
        f" 已绑定水鱼账号「{arg}」（公开查询）。\n"
        "如需查询全量成绩（牌子/表格），请使用「绑定水鱼token <Import-Token>」；"
        "如需成绩写入/导分等全功能，请发送「绑定水鱼」走 OAuth 授权"
    ).finish(at_sender=True)


async def _complete_df(platform: str, user_id: str, code: str) -> None:
    """确认码回填收尾：兑换令牌（水鱼侧校验回填人 = 发起人）→ 落 OAuth 标志。"""
    binding = await binding_service.get(platform, user_id)
    if binding is None:
        await UniMessage.text(" " + DIVINGFISH_NO_SESSION_MSG).finish(at_sender=True)
    ref = binding_service.divingfish_subject(binding)
    if ref is None:  # pragma: no cover —— 会话期间标识不会消失
        await UniMessage.text(
            " 当前绑定缺少水鱼授权所需的身份标识，请重新发送「绑定水鱼」"
        ).finish(at_sender=True)
    try:
        result = await df_ext.redeem(ref[4:], code)
    except df_ext.DivingFishSubjectMismatch:
        await UniMessage.text(" " + DIVINGFISH_MISMATCH_MSG).finish(at_sender=True)
    except df_ext.ExtError as e:
        await UniMessage.text(f" {e}").finish(at_sender=True)
    sub = df_ext.token_subject(result.get("access_token", "")) or result.get("sub")
    await binding_service.bind_divingfish_oauth(binding, sub=sub)
    pending_bindings.discard(platform, user_id)
    await UniMessage.text(" " + DIVINGFISH_BIND_SUCCESS_MSG).finish(at_sender=True)


@df_token.handle()
@handle_errors("绑定失败，请稍后再试")
async def _(session: Session = UniSession(), message: Message = CommandArg()):
    platform, user_id = session_keys(session)
    token = message.extract_plain_text().strip()
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
    arg = message.extract_plain_text().strip()
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
    code = lxns_ext.extract_authorization_code(message.extract_plain_text())
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
        binding,
        token=token.access_token,
        friend_code=token.friend_code,
        refresh_token=token.refresh_token,
    )
    pending_bindings.discard(platform, user_id)
    fc = f"，好友码 {token.friend_code}" if token.friend_code else ""
    await UniMessage.text(f" 落雪绑定成功{fc}").finish(at_sender=True)


@net_bind.handle()
@handle_errors("绑定失败，请稍后再试")
async def _(session: Session = UniSession(), message: Message = CommandArg()):
    """绑定日服 NET：`绑定日服 <SEGA ID> <密码>`（一次完成，绑定即验证登录）。

    仅限私聊：SEGA 账号密码敏感级别高于查分器 token，非私聊场景（群聊/
    GUILD 频道）提交都会把密码留在聊天记录（协议端也有留存），凭据本体
    不回显、不在完成消息中出现。
    """
    if not is_private_session(session):
        await UniMessage.text(
            " 绑定日服需要提交 SEGA 账号密码，请私聊机器人操作"
        ).finish(at_sender=True)
    platform, user_id = session_keys(session)
    arg = message.extract_plain_text().strip()
    sega_id, sep, password = arg.partition(" ")
    if not arg or not sep or not password.strip():
        await UniMessage.text(
            "用法：绑定日服 <SEGA ID> <密码>\n\n"
            "⚠️ 该数据源需提供 SEGA 账号密码（仅存于本机数据库，用于登录"
            "官方 maimai NET 抓取成绩）。密码级别敏感，不要使用与其他服务"
            "相同的密码。"
        ).finish(at_sender=True)
    binding = await binding_service.ensure(platform, user_id)
    password = password.strip()
    # 绑定即验证：能区分密码错误与临时故障（域模块 net，见本包 net.py）
    ok, verify_note = await net.verify_net_credentials(sega_id, password)
    if not ok:
        await UniMessage.text(net.INVALID_CREDENTIALS).finish(at_sender=True)
    await binding_service.bind_net(binding, sega_id=sega_id, password=password)
    # 支持指令清单从数据源注册表派生（能力扩充后自动跟进，不手写）
    await UniMessage.text(
        f" 已绑定日服 NET（SEGA ID：{sega_id}），当前数据源已切换为日服。"
        f"支持指令：{command_hints(SERVICE_NET)}" + verify_note
    ).finish(at_sender=True)


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
    arg = message.extract_plain_text().strip()
    service = {"0": SERVICE_DIVINGFISH, "1": SERVICE_LXNS, "2": SERVICE_NET}.get(arg)
    if service is None:
        await UniMessage.text(
            " 用法：数据源 <0|1|2>（0 = 水鱼，1 = 落雪，2 = 日服 NET）"
        ).finish(at_sender=True)
    platform, user_id = session_keys(session)
    binding = await binding_service.ensure(platform, user_id)
    try:
        await binding_service.set_service(binding, service)
    except Exception as e:
        await UniMessage.text(f" {e}").finish(at_sender=True)
    await UniMessage.text(f" 数据源已切换为{SERVICE_ZH.get(service, service)}").finish(
        at_sender=True
    )


@set_theme.handle()
@handle_errors("设置失败，请稍后再试")
async def _(session: Session = UniSession(), message: Message = CommandArg()):
    arg = message.extract_plain_text().strip()
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
    service_name = SERVICE_ZH.get(binding.service, binding.service)
    lines = [f" 数据源：{service_name}"]
    if binding.divingfish_username:
        lines.append(f"水鱼用户名：{binding.divingfish_username}")
    if binding.divingfish_import_token:
        lines.append(f"水鱼 Import-Token：{binding.divingfish_import_token[:4]}****")
    if binding.divingfish_oauth:
        lines.append("水鱼 OAuth：已授权（写入走 OAuth 统一凭据）")
    if binding.lxns_friend_code:
        lines.append(f"落雪好友码：{binding.lxns_friend_code}")
    if binding.lxns_token:
        lines.append(f"落雪 Token：{binding.lxns_token[:4]}****")
    if binding.net_sega_id:
        shown = binding.net_sega_id
        lines.append(
            f"日服 NET SEGA ID：{shown[:2]}****"
            if len(shown) > 4
            else "日服 NET：已绑定"
        )
    lines.append(f"主题：{'prism_plus' if binding.theme == 'prism_plus' else 'circle'}")
    await UniMessage.text("\n".join(lines)).finish(at_sender=True)


# ---------------------------------------------------------------- 帮助声明

help_registry.declare(
    plugin="awmc.bind",
    title="绑定与设置",
    category="bind",
    description="查分器绑定与个人设置（绑定类指令建议私聊操作）",
    commands=[
        CommandSpec(
            matcher=df_bind,
            name="绑定水鱼",
            aliases=("绑定df", "dfbind"),
            scope="QQ 平台",
            brief="水鱼 OAuth 设备码授权（成绩写入/导分必需）",
            detail=(
                "发送「绑定水鱼」获取授权链接与确认码，\n"
                "完成后用「水鱼授权码 <确认码>」回填。"
            ),
        ),
        CommandSpec(
            matcher=df_user,
            name="绑定水鱼用户名",
            aliases=("dfuser", "绑定df用户名"),
            brief="绑定水鱼用户名（公开查询档，不能写入成绩）",
            detail="格式：绑定水鱼用户名 <用户名>",
        ),
        CommandSpec(
            matcher=df_token,
            name="绑定水鱼token",
            aliases=("dftoken",),
            brief="Import-Token 全量读取档（牌子/表格）；写入成绩请用 绑定水鱼 授权",
            detail=(
                "格式：绑定水鱼token <Import-Token>\n"
                "水鱼写入权限已收敛到 OAuth：导入 Token 仅保留全量成绩/牌子的读取，"
                "导分/写成绩需发「绑定水鱼」完成授权。"
            ),
        ),
        CommandSpec(
            matcher=lx_bind,
            name="绑定落雪",
            aliases=("绑定lx", "lxbind"),
            brief="落雪 OAuth 授权或好友码/Token 直绑",
            detail=(
                "无参发起落雪授权，授权码 90 秒内直接回复给 bot 即可；\n"
                "带参直绑好友码（9-12 位数字）或个人 Token。"
            ),
        ),
        CommandSpec(
            matcher=lx_code,
            name="落雪授权码",
            aliases=("lxcode",),
            brief="回填落雪 OAuth 授权码完成绑定",
        ),
        CommandSpec(
            matcher=df_code,
            name="水鱼授权码",
            aliases=("dfcode",),
            brief="回填水鱼 OAuth 确认码完成绑定",
        ),
        CommandSpec(
            matcher=net_bind,
            name="绑定日服",
            aliases=("绑定net", "netbind"),
            scope="仅私聊",
            brief="绑定日服 NET（SEGA ID+密码，绑定即验证登录）",
            detail="格式：绑定日服 <SEGA ID> <密码>；完成后自动切换数据源为日服。",
        ),
        CommandSpec(matcher=unbind, name="解绑", brief="解除当前用户全部绑定"),
        CommandSpec(
            matcher=set_provider,
            name="数据源",
            brief="切换默认查分数据源",
            detail="格式：数据源 <0|1|2>（水鱼/落雪/日服）；目标源未绑定会被拒绝。",
        ),
        CommandSpec(
            matcher=set_theme,
            name="主题",
            brief="切换成绩卡主题",
            detail="格式：主题 <0|1>（prism_plus/circle）。",
        ),
        CommandSpec(
            matcher=my_bind,
            name="我的绑定",
            brief="查看当前绑定详情（敏感信息打码）",
        ),
    ],
)
