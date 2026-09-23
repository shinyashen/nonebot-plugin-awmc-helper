"""awmc.alias：别名子插件。

指令（对齐原版 maimaiDX）：
- `<名称>有什么别名` / `id <数字>有什么别名`
- `添加本地别名 <id> <别名>`（写本地库，热更新）
- `添加别名 <id> <别名>`（向柚子提交公开申请）
- `同意别名 <TAG>`
- `当前投票 [页]`
- `开启/关闭别名推送`（群管）、`全局开启/关闭别名推送`（SUPERUSER）
- `更新别名库`（SUPERUSER）
"""

import asyncio

from nonebot import logger, get_bots, on_regex, get_driver, on_command
from nonebot.params import CommandArg, RegexGroup
from nonebot.plugin import PluginMetadata
from nonebot.adapters import Bot, Event, Message
from nonebot.exception import MatcherException
from nonebot.permission import SUPERUSER
from nonebot_plugin_uninfo import ADMIN, Session, SceneType, UniSession
from nonebot_plugin_alconna.uniseg import Reference, CustomNode, UniMessage

from ...core import store
from ...config import plugin_config
from ...core.ext import yuzu as yuzu_ext
from ...core.songs import song_service
from ...core.utils import paginate, handle_errors
from ...core.render.tools import text_to_image, image_to_bytes

try:  # OneBot v11 可用时提供合并转发能力
    from nonebot.adapters.onebot.v11 import Bot as OB11Bot
    from nonebot.adapters.onebot.v11 import Message as OB11Message
    from nonebot.adapters.onebot.v11 import MessageSegment as OB11Segment

    _OB11 = True
except ImportError:  # pragma: no cover
    OB11Bot = None
    OB11Message = None
    OB11Segment = None
    _OB11 = False

PUSH_FEATURE = "alias_push"

__plugin_meta__ = PluginMetadata(
    name="awmc.alias",
    description="舞萌DX 别名：查询/本地别名/申请/投票/推送",
    usage="<名称>有什么别名｜添加本地别名 <id> <别名>｜添加别名 <id> <别名>｜"
    "同意别名 <TAG>｜当前投票 [页]｜(全局)开启/关闭别名推送｜更新别名库",
    type="application",
    homepage="https://github.com/shinyashen/nonebot-plugin-awmc-helper",
)


def _group_id_of(session: Session) -> str | None:
    if session.scene and session.scene.type == SceneType.GROUP:
        return str(session.scene.id)
    return None


def _user_id_of(session: Session) -> str:
    return str(session.user.id)


_admin_perm = ADMIN()
"""群管/群主权限（uninfo 提供，多适配器通用）。"""


NOT_FOUND_ALIAS = " 未找到此歌曲\n可以使用「添加别名」指令给该乐曲添加别名"

alias_song = on_regex(
    r"^(?:id\s?(?P<qid>[0-9]+)|(?P<name>.+?))\s?有什么别[名称]$", block=True
)
alias_local_apply = on_command("添加本地别名", aliases={"添加本地别称"}, block=True)
alias_apply = on_command(
    "添加别名", aliases={"增加别名", "增添别名", "添加别称"}, block=True
)
alias_agree = on_command("同意别名", aliases={"同意别称"}, block=True)
alias_status = on_command(
    "当前投票", aliases={"当前别名投票", "当前别称投票"}, block=True
)
alias_switch = on_regex(r"^(开启|关闭)别名推送$", block=True)
alias_global_switch = on_regex(
    r"^全局(开启|关闭)别名推送$", permission=SUPERUSER, block=True
)
update_alias = on_command("更新别名库", permission=SUPERUSER, block=True)


def _ids_text(song) -> str:
    """曲目的可用 id 展示文本（如 363、10363、100363），按谱面组实际存在取舍。"""
    return "、".join(map(str, song_service.available_ids(song)))


async def _send_song_aliases(song_id: int, hint: str = "") -> None:
    """发送某曲目的全部别名（柚子 + 落雪 + 本地合并视图），ID 展示全部可用 id。"""
    song = await song_service.by_id(song_id)
    if song is None:
        await UniMessage.text(NOT_FOUND_ALIAS).finish(at_sender=True)
    aliases = await song_service.aliases_of(song_id)
    if not aliases:
        await UniMessage.text(" 该曲目没有别名").finish(at_sender=True)
    suffix = f"\n{hint}" if hint else ""
    await UniMessage.text(
        f" 该曲目有以下别名：\nID：{_ids_text(song)}\n" + "\n".join(aliases) + suffix
    ).finish(at_sender=True)


async def _finish_multi_forward(header: str, blocks: list[str], bot: Bot) -> bool:
    """多曲命中时以合并转发发送（首条命中数量，之后每曲一条）。

    仅 OB11 走合并转发（与别名推送同口径）；适配器不支持或协议端发送失败
    返回 False，由调用方降级为普通消息。
    """
    if not (_OB11 and OB11Bot is not None and isinstance(bot, OB11Bot)):
        return False
    nodes = [
        CustomNode(uid=bot.self_id, name="Bot", content=text)
        for text in (header, *blocks)
    ]
    try:
        await UniMessage(Reference(nodes=nodes)).finish()
    except MatcherException:
        raise  # finish 的控制流异常（已发送成功），原样上抛
    except Exception:
        logger.warning("别名多曲命中合并转发发送失败，降级为普通消息")
    return False


@alias_song.handle()
@handle_errors("查询别名失败，请稍后再试")
async def _(bot: Bot, groups: tuple = RegexGroup()):
    qid, name = groups
    if qid:
        await _send_song_aliases(int(qid))
        return
    assert name is not None
    keyword = name.strip()
    songs, strip_info = await song_service.by_alias_detail(keyword)
    hint = ""
    if strip_info:
        # 前缀剥离命中：提醒别名库已合并，无需再加谱面前缀（Q31）
        stripped, prefix = strip_info
        hint = (
            f"提示：别名库已合并同一歌曲的标准/DX/宴谱面别名，"
            f"无需添加「{prefix}」前缀，直接搜索「{stripped}」即可。"
        )
    if len(songs) > 1:
        blocks = []
        for item in songs:
            aliases = await song_service.aliases_of(item.id)
            blocks.append(f"ID：{_ids_text(item)}\n" + "\n".join(aliases or []))
        if hint:
            blocks[-1] += f"\n{hint}"
        header = f"找到{len(songs)}个相同别名的曲目："
        if not await _finish_multi_forward(header, blocks, bot):
            msg = header + "\n" + "\n======\n".join(blocks)
            await UniMessage.text(msg).finish(at_sender=True)
        return
    if songs:
        await _send_song_aliases(songs[0].id, hint)
        return
    if keyword.isdigit():
        await _send_song_aliases(int(keyword))
        return
    await UniMessage.text(NOT_FOUND_ALIAS).finish(at_sender=True)


@alias_local_apply.handle()
@handle_errors("添加本地别名失败，请稍后再试")
async def _(session: Session = UniSession(), message: Message = CommandArg()):
    args = str(message).strip().split(maxsplit=1)
    if len(args) < 2:
        await UniMessage.text(" 参数错误：添加本地别名 <id> <别名>").finish(
            at_sender=True
        )
    song_id_raw, alias_name = args[0], args[1].strip()
    if not song_id_raw.isdigit():
        await UniMessage.text(" 请输入正确的ID").finish(at_sender=True)
    song_id = int(song_id_raw)
    if await song_service.by_id(song_id) is None:
        await UniMessage.text(f" 未找到ID为「{song_id}」的曲目").finish(at_sender=True)

    try:
        server = await yuzu_ext.yuzu_client.get_alias(song_id)
    except yuzu_ext.ExtError as e:
        server = None
        logger.warning(f"查询柚子别名失败（忽略并继续本地添加）：{e}")
    if server is not None and server.has(alias_name):
        await UniMessage.text(f" 该曲目的别名「{alias_name}」已存在别名服务器").finish(
            at_sender=True
        )

    if await store.add_local_alias(song_id, alias_name, _user_id_of(session)):
        await song_service.reload_alias_index()
        await UniMessage.text(
            f" 已成功为ID「{song_id}」添加别名「{alias_name}」到本地别名库"
        ).finish(at_sender=True)
    await UniMessage.text(" 本地别名库已存在该别名").finish(at_sender=True)


@alias_apply.handle()
@handle_errors("添加别名失败，请稍后再试")
async def _(session: Session = UniSession(), message: Message = CommandArg()):
    args = str(message).strip().split(maxsplit=1)
    if len(args) < 2:
        await UniMessage.text(" 参数错误：添加别名 <id> <别名>").finish(at_sender=True)
    song_id_raw, alias_name = args[0], args[1].strip()
    if not song_id_raw.isdigit():
        await UniMessage.text(" 请输入正确的ID").finish(at_sender=True)
    song_id = int(song_id_raw)
    if await song_service.by_id(song_id) is None:
        await UniMessage.text(f" 未找到ID为「{song_id}」的曲目").finish(at_sender=True)
    try:
        server = await yuzu_ext.yuzu_client.get_alias(song_id)
        if server is not None and server.has(alias_name):
            await UniMessage.text(
                f" 该曲目的别名「{alias_name}」已存在别名服务器"
            ).finish(at_sender=True)
        msg = await yuzu_ext.yuzu_client.apply_alias(
            song_id, alias_name, _user_id_of(session), _group_id_of(session) or ""
        )
    except yuzu_ext.ExtError as e:
        msg = str(e)
    await UniMessage.text(" " + msg).finish(at_sender=True)


@alias_agree.handle()
@handle_errors("投票失败，请稍后再试")
async def _(session: Session = UniSession(), message: Message = CommandArg()):
    tag = str(message).strip().upper()
    if not tag:
        await UniMessage.text(" 参数错误：同意别名 <TAG>").finish(at_sender=True)
    try:
        msg = await yuzu_ext.yuzu_client.agree_alias(tag, _user_id_of(session))
    except yuzu_ext.ExtError as e:
        msg = str(e)
    await UniMessage.text(" " + msg).finish(at_sender=True)


@alias_status.handle()
@handle_errors("查询投票失败，请稍后再试")
async def _(message: Message = CommandArg()):
    args = str(message).strip()
    try:
        status = await yuzu_ext.yuzu_client.get_status()
    except yuzu_ext.ExtError as e:
        await UniMessage.text(str(e)).finish(at_sender=True)
    if not status:
        await UniMessage.text(" 未查询到正在进行的别名投票").finish(at_sender=True)

    page_size = 25
    page = int(args) if args.isdigit() else 1
    page_data, total = paginate(status, page, page_size)
    if not page_data:
        await UniMessage.text(f" 页码超出范围（共 {total} 页）").finish(at_sender=True)
    real_page = min(max(page, 1), total)
    lines: list[str] = []
    for s in page_data:
        apply_alias = (
            s.apply_alias[:15] + "..." if len(s.apply_alias) > 15 else s.apply_alias
        )
        lines.append(
            f"- {s.tag}：\n- ID：{s.song_id}"
            f"\n- 别名：{apply_alias}\n- 票数：{s.agree_votes}/{s.votes}"
        )
    lines.append(f"第「{real_page}」页，共「{total}」页")
    png = image_to_bytes(text_to_image("\n".join(lines)))
    await UniMessage.image(raw=png).finish(at_sender=True)


@alias_switch.handle()
@handle_errors("设置失败，请稍后再试")
async def _(
    bot: Bot,
    event: Event,
    session: Session = UniSession(),
    groups: tuple = RegexGroup(),
):
    action = groups[0]
    group_id = _group_id_of(session)
    if group_id is None:
        await UniMessage.text(" 别名推送开关仅群聊可用").finish(at_sender=True)
    if not (await SUPERUSER(bot, event) or await _admin_perm(bot, event)):
        await UniMessage.text(" 权限不足：仅群管理员可用").finish(at_sender=True)

    enabled = action == "开启"
    await store.set_group_switch(group_id, PUSH_FEATURE, enabled)
    if enabled and not plugin_config.awmc_alias_push:
        await UniMessage.text(
            "已开启本群别名推送，但部署未启用推送（AWMC_ALIAS_PUSH=false），无法接收"
        ).finish(at_sender=True)
    state = "开启" if enabled else "关闭"
    await UniMessage.text(f" 已{state}maimai别名推送").finish(at_sender=True)


@alias_global_switch.handle()
@handle_errors("设置失败，请稍后再试")
async def _(groups: tuple = RegexGroup()):
    enabled = groups[0] == "开启"
    count = 0
    for bot in list(get_bots().values()):
        if _OB11 and OB11Bot is not None and isinstance(bot, OB11Bot):
            try:
                group_list = await bot.get_group_list()
            except Exception:
                continue
            for g in group_list:
                await store.set_group_switch(str(g["group_id"]), PUSH_FEATURE, enabled)
                count += 1
    state = "开启" if enabled else "关闭"
    await UniMessage.text(f" 已全局{state}maimai别名推送（{count} 个群）").finish(
        at_sender=True
    )


@update_alias.handle()
@handle_errors("更新别名库失败")
async def _():
    ok = await song_service.refresh()
    if ok:
        logger.info("手动更新别名库成功")
        await UniMessage.text(" 手动更新别名库成功").finish(at_sender=True)
    await UniMessage.text(" 手动更新别名库失败，请检查网络").finish(at_sender=True)


# ---------------------------------------------------------------------------
# SSE 推送：别名申请事件分发到开启推送的群（合并转发优先，失败降级普通消息）
# ---------------------------------------------------------------------------


async def push_apply(push: yuzu_ext.AliasPush) -> None:
    if not push.status:
        return
    lines = [
        "检测到新的别名申请，可使用同意别名指令进行投票，"
        f"点击下方链接查看详情：「{yuzu_ext.VOTE_URL}」\n"
        "如果不需要接收推送消息，请使用「关闭别名推送」指令关闭推送"
    ]
    for item in push.status:
        song = await song_service.by_id(item.song_id)
        title = song.title if song else str(item.song_id)
        lines.append(
            f"{item.tag}：\nID：{item.song_id}\n标题：{title}\n别名：{item.apply_alias}"
        )
    text = "\n======\n".join(lines)

    default = plugin_config.awmc_alias_push
    if not _OB11 or OB11Bot is None or OB11Message is None or OB11Segment is None:
        return
    for bot in list(get_bots().values()):
        if not isinstance(bot, OB11Bot):
            continue
        try:
            group_list = await bot.get_group_list()
        except Exception:
            continue
        for g in group_list:
            gid = str(g["group_id"])
            if not await store.get_switch(gid, PUSH_FEATURE, default):
                continue
            try:
                forward = OB11Message(
                    [
                        OB11Segment.node_custom(
                            int(bot.self_id), "Bot", OB11Message(text)
                        )
                    ]
                )
                await bot.call_api(
                    "send_group_forward_msg", group_id=int(gid), message=forward
                )
            except Exception:
                try:
                    await bot.send_group_msg(
                        group_id=int(gid), message=OB11Message(text)
                    )
                except Exception:
                    logger.exception(f"别名推送到群 {gid} 失败")
            await asyncio.sleep(5)


@get_driver().on_startup
async def _startup_alias_push() -> None:
    if plugin_config.awmc_alias_push and plugin_config.awmc_startup_tasks:
        yuzu_ext.start_alias_push(push_apply)
        logger.info("别名推送 SSE 已启动")
