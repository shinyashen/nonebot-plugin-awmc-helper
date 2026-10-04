"""别名指令入口：查询 / 本地别名 / 申请 / 投票 / 投票列表 / 推送开关。"""

from nonebot import logger, get_bots, on_regex, on_command
from nonebot.params import CommandArg, RegexGroup
from nonebot.adapters import Bot, Event, Message
from nonebot.permission import SUPERUSER
from nonebot_plugin_uninfo import Session, UniSession
from nonebot_plugin_alconna.uniseg import UniMessage

from .push import PUSH_FEATURE
from ...core import store
from ...config import plugin_config
from ...core.ext import yuzu as yuzu_ext
from ...core.help import CommandSpec, help_registry
from ...core.songs import song_service
from ...core.utils import (
    paginate,
    parse_page,
    user_id_of,
    group_id_of,
    handle_errors,
    apply_group_switch,
    song_not_found_text,
)
from ...core.forward import is_ob11, try_send_forward_session
from ...core.render.tools import text_image_bytes

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
    """发送某曲目的全部别名（柚子 + 落雪 + MuNET + 本地合并视图），ID 展示全部可用 id。

    国服视图未命中（仅日服曲目）时回退日服视图取曲、合并库取别名（Q32 同口径），
    ID 行追加「（日服限定）」标注。
    """
    song = await song_service.by_id(song_id)
    aliases: list[str] | None
    if song is not None:
        aliases = await song_service.aliases_of(song_id)
        jp_note = ""
    else:
        song = await song_service.jp_by_id(song_id)
        if song is None:
            await UniMessage.text(NOT_FOUND_ALIAS).finish(at_sender=True)
        aliases = await song_service.jp_aliases_of(song_id)
        jp_note = "（日服限定）"
    if not aliases:
        await UniMessage.text(" 该曲目没有别名").finish(at_sender=True)
    suffix = f"\n{hint}" if hint else ""
    await UniMessage.text(
        f" 该曲目有以下别名：\nID：{_ids_text(song)}{jp_note}\n"
        + "\n".join(aliases)
        + suffix
    ).finish(at_sender=True)


async def _finish_multi_forward(
    header: str, blocks: list[str], bot: Bot, session: Session
) -> bool:
    """多曲命中时以合并转发发送（首条命中数量，之后每曲一条）。

    仅 OB11 走合并转发（core/forward 统一构造，与别名推送同口径）；
    适配器不支持或协议端发送失败返回 False，由调用方降级为普通消息。
    """
    return await try_send_forward_session(bot, [header, *blocks], session)


async def _parse_alias_args(message: Message, usage: str) -> tuple[int, str]:
    """申请类指令公共前置校验：参数拆分 → id 合法性 → 曲目存在性。"""
    args = message.extract_plain_text().strip().split(maxsplit=1)
    if len(args) < 2:
        await UniMessage.text(f" 参数错误：{usage}").finish(at_sender=True)
    song_id_raw, alias_name = args[0], args[1].strip()
    if not song_id_raw.isdigit():
        await UniMessage.text(" 请输入正确的ID").finish(at_sender=True)
    song_id = int(song_id_raw)
    # CN 未命中回退 JP 视图判存在：日服限定曲同样可申请/本地添加别名
    # （别名库按曲目 id 全局存储，与视图无关；查询侧 jp 兜底见 by_alias_detail）
    if (
        await song_service.by_id(song_id) is None
        and await song_service.jp_by_id(song_id) is None
    ):
        await UniMessage.text(f" {song_not_found_text(song_id)}").finish(at_sender=True)
    return song_id, alias_name


async def _yuzu_server_has(song_id: int, alias_name: str) -> bool | None:
    """柚子同名判重（查询失败返回 None，调用方按各自口径降级）。"""
    try:
        server = await yuzu_ext.yuzu_client.get_alias(song_id)
    except yuzu_ext.ExtError as e:
        logger.warning(f"查询柚子别名失败（忽略并继续本地添加）：{e}")
        return None
    return server is not None and server.has(alias_name)


@alias_song.handle()
@handle_errors("查询别名失败，请稍后再试")
async def _(bot: Bot, session: Session = UniSession(), groups: tuple = RegexGroup()):
    qid, name = groups
    if qid:
        await _send_song_aliases(int(qid))
        return
    assert name is not None
    keyword = name.strip()
    songs, strip_info = await song_service.by_alias_detail(keyword)
    jp_only = False
    if not songs:
        # 国服视图未命中 → 日服视图别名兜底（仅日服曲目，Q32 同口径）
        songs, strip_info = await song_service.jp_by_alias_detail(keyword)
        jp_only = True
    hint = ""
    if strip_info:
        # 前缀/后缀剥离命中：提醒别名库已合并，无需再加谱面前后缀（Q31）
        stripped, matched, kind = strip_info
        pos = "后缀" if kind == "suffix" else "前缀"
        hint = (
            f"提示：别名库已合并同一歌曲的标准/DX/宴谱面别名，"
            f"无需添加「{matched}」{pos}，直接搜索「{stripped}」即可。"
        )
    if len(songs) > 1:
        if jp_only:
            alias_map = await song_service.jp_aliases_of_many(
                [item.id for item in songs]
            )
        else:
            alias_map = await song_service.aliases_of_many([item.id for item in songs])
        blocks = [
            f"ID：{_ids_text(item)}{'（日服限定）' if jp_only else ''}\n"
            + "\n".join(alias_map.get(item.id) or [])
            for item in songs
        ]
        if hint:
            blocks[-1] += f"\n{hint}"
        header = f"找到{len(songs)}个相同别名的曲目："
        if not await _finish_multi_forward(header, blocks, bot, session):
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
    song_id, alias_name = await _parse_alias_args(message, "添加本地别名 <id> <别名>")
    if await _yuzu_server_has(song_id, alias_name):
        await UniMessage.text(f" 该曲目的别名「{alias_name}」已存在别名服务器").finish(
            at_sender=True
        )

    if await store.add_local_alias(song_id, alias_name, user_id_of(session)):
        await song_service.reload_alias_index()
        await UniMessage.text(
            f" 已成功为ID「{song_id}」添加别名「{alias_name}」到本地别名库"
        ).finish(at_sender=True)
    await UniMessage.text(" 本地别名库已存在该别名").finish(at_sender=True)


@alias_apply.handle()
@handle_errors("添加别名失败，请稍后再试")
async def _(session: Session = UniSession(), message: Message = CommandArg()):
    song_id, alias_name = await _parse_alias_args(message, "添加别名 <id> <别名>")
    if await _yuzu_server_has(song_id, alias_name):
        await UniMessage.text(f" 该曲目的别名「{alias_name}」已存在别名服务器").finish(
            at_sender=True
        )
    try:
        msg = await yuzu_ext.yuzu_client.apply_alias(
            song_id, alias_name, user_id_of(session), group_id_of(session) or ""
        )
    except yuzu_ext.ExtError as e:
        msg = str(e)
    await UniMessage.text(" " + msg).finish(at_sender=True)


@alias_agree.handle()
@handle_errors("投票失败，请稍后再试")
async def _(session: Session = UniSession(), message: Message = CommandArg()):
    tag = message.extract_plain_text().strip().upper()
    if not tag:
        await UniMessage.text(" 参数错误：同意别名 <TAG>").finish(at_sender=True)
    try:
        msg = await yuzu_ext.yuzu_client.agree_alias(tag, user_id_of(session))
    except yuzu_ext.ExtError as e:
        msg = str(e)
    await UniMessage.text(" " + msg).finish(at_sender=True)


@alias_status.handle()
@handle_errors("查询投票失败，请稍后再试")
async def _(message: Message = CommandArg()):
    args = message.extract_plain_text().strip()
    try:
        status = await yuzu_ext.yuzu_client.get_status()
    except yuzu_ext.ExtError as e:
        await UniMessage.text(str(e)).finish(at_sender=True)
    if not status:
        await UniMessage.text(" 未查询到正在进行的别名投票").finish(at_sender=True)

    page_size = 25
    page = parse_page(args)
    page_data, total = paginate(status, page, page_size)
    if not page_data:
        await UniMessage.text(f" 页码超出范围（共 {total} 页）").finish(at_sender=True)
    lines: list[str] = []
    for s in page_data:
        apply_alias = (
            s.apply_alias[:15] + "..." if len(s.apply_alias) > 15 else s.apply_alias
        )
        lines.append(
            f"- {s.tag}：\n- ID：{s.song_id}"
            f"\n- 别名：{apply_alias}\n- 票数：{s.agree_votes}/{s.votes}"
        )
    lines.append(f"第「{page}」页，共「{total}」页")
    png = text_image_bytes("\n".join(lines))
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
    enabled = action == "开启"
    await apply_group_switch(
        session,
        bot,
        event,
        switch_key=PUSH_FEATURE,
        enabled=enabled,
        feature="别名推送开关",
    )

    if enabled and not plugin_config.awmc_alias_push:
        # 群级显式开启覆盖部署默认值（推送分发按 get_switch 群级优先），
        # 部署关默认只影响「未显式设置」的群，勿再误导为收不到
        await UniMessage.text(
            "已开启本群别名推送（部署默认未开启 AWMC_ALIAS_PUSH，本群按群级覆盖生效）"
        ).finish(at_sender=True)
    state = "开启" if enabled else "关闭"
    await UniMessage.text(f" 已{state}maimai别名推送").finish(at_sender=True)


@alias_global_switch.handle()
@handle_errors("设置失败，请稍后再试")
async def _(groups: tuple = RegexGroup()):
    enabled = groups[0] == "开启"
    count = 0
    for bot in list(get_bots().values()):
        if is_ob11(bot):
            try:
                group_list = await bot.get_group_list()
            except Exception as e:
                logger.debug(f"群列表拉取失败（bot={bot}），跳过该连接：{e}")
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


# ---------------------------------------------------------------- 帮助声明

help_registry.declare(
    plugin="awmc.alias",
    title="别名",
    category="alias",
    description="柚子/落雪/MuNET/本地合并视图的曲目别名查询与协作",
    commands=[
        CommandSpec(
            matcher=alias_song,
            name="<名称|id>有什么别名",
            brief="查询某曲全部别名（多曲命中走合并转发）",
        ),
        CommandSpec(
            matcher=alias_local_apply,
            name="添加本地别名",
            aliases=("添加本地别称",),
            brief="写入本地别名库，立即热更新生效",
            detail="格式：添加本地别名 <id> <别名>",
        ),
        CommandSpec(
            matcher=alias_apply,
            name="添加别名",
            aliases=("增加别名", "增添别名", "添加别称"),
            brief="向柚子提交公开别名申请（进入投票流程）",
            detail="格式：添加别名 <id> <别名>",
        ),
        CommandSpec(
            matcher=alias_agree,
            name="同意别名",
            aliases=("同意别称",),
            brief="为进行中的别名投票赞成",
            detail="格式：同意别名 <TAG>",
        ),
        CommandSpec(
            matcher=alias_status,
            name="当前投票",
            aliases=("当前别名投票", "当前别称投票"),
            brief="查看进行中的别名投票（25 条/页）",
        ),
        CommandSpec(
            matcher=alias_switch,
            name="开启/关闭别名推送",
            scope="群管",
            brief="本群别名申请推送开关（部署默认关）",
        ),
        CommandSpec(
            matcher=alias_global_switch,
            name="全局开启/关闭别名推送",
            scope="SUPERUSER",
            hidden=True,
            brief="批量设置所有群的别名推送开关",
        ),
        CommandSpec(
            matcher=update_alias,
            name="更新别名库",
            scope="SUPERUSER",
            hidden=True,
            brief="手动重拉曲库与别名数据",
        ),
    ],
)
