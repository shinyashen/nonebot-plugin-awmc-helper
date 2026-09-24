"""awmc.arcade：机厅排卡（原版独立 Service 的完整复刻）。

指令（对齐原版 maimaiDX排卡）：
- `帮助maimaiDX排卡`
- `添加机厅 <店名> <地址> <机台数> [别称...]`（SUPERUSER，自定义 ID≥10000）
- `删除机厅 <店名>`（SUPERUSER）
- `添加机厅别名 <店名|ID> <别名>` / `删除机厅别名 <别名>`
- `修改机厅 <店名|ID> 数量 <数量>`
- `订阅机厅 / 取消订阅机厅 <店名|ID>`、`查看订阅`
- `查找机厅 <关键词>`（店名/地址/别称模糊，≥5 条转图）
- `<店名|别称>设置/=/增加/加/+/减少/减/- <人数>[人|卡]`（仅本群订阅机厅，±上限可配）
- `机厅几人 / jtj`、`<店名|别称>有多少人/几人/几卡...`
- 每日 4 点：同步华立官方机厅数据并清零排卡人数
"""

import re
from datetime import datetime

from nonebot import logger, on_regex, on_command, on_fullmatch
from nonebot.params import Command, CommandArg, RegexGroup
from nonebot.plugin import PluginMetadata
from nonebot.adapters import Bot, Event, Message
from nonebot.permission import SUPERUSER
from nonebot_plugin_uninfo import ADMIN, Session, SceneType, UniSession
from nonebot_plugin_alconna.uniseg import UniMessage

from ...core import store
from ...config import plugin_config
from ...core.ext import wahlap as wahlap_ext
from ...core.utils import handle_errors
from ...core.render.tools import text_to_image, image_to_bytes

try:
    from nonebot_plugin_apscheduler import scheduler

    _SCHED = True
except ImportError:  # pragma: no cover
    scheduler = None  # type: ignore[assignment]
    _SCHED = False

__plugin_meta__ = PluginMetadata(
    name="awmc.arcade",
    description="舞萌DX 机厅排卡",
    usage=(
        "添加机厅 <店名> <地址> <机台数> [别称...]｜订阅机厅 <店名>｜"
        "查找机厅 <关键词>｜XX店+2人｜机厅几人｜帮助maimaiDX排卡"
    ),
    type="application",
    homepage="https://github.com/shinyashen/nonebot-plugin-awmc-helper",
)

ARCADE_HELP = (
    "排卡指令如下：\n"
    "添加机厅 <店名> <地址> <机台数量> 添加机厅信息\n"
    "删除机厅 <店名> 删除机厅信息\n"
    "修改机厅 <店名> 数量 <数量> 修改机厅信息\n"
    "添加机厅别名 <店名> <别名>\n"
    "订阅机厅 <店名> 订阅机厅，简化后续指令\n"
    "查看订阅 查看群组订阅机厅的信息\n"
    "取消订阅机厅 <店名> 取消群组机厅订阅\n"
    "查找机厅 <关键词> 查询对应机厅信息\n"
    "<店名/别名>人数设置,=,增加,+,减少,-<人数> 操作排卡人数\n"
    "<店名/别名>有多少人,几人,几卡 查看排卡人数\n"
    "机厅几人 查看已订阅机厅排卡人数"
)

SEARCH_PREFIXES = (
    "查找机厅",
    "查询机厅",
    "机厅查找",
    "机厅查询",
    "搜索机厅",
    "机厅搜索",
)
PERSON_SUFFIXES = (
    "有多少人",
    "有几人",
    "有几卡",
    "多少人",
    "多少卡",
    "几人",
    "jr",
    "几卡",
)
SET_OPS = ("设置", "设定", "＝", "=")
INCREASE_OPS = ("增加", "添加", "加", "＋", "+")
DECREASE_OPS = ("减少", "降低", "减", "－", "-")
# 操作符单一来源：正则交替由操作符表派生（增/减两表的长词均先于短词，
# 与原手写正则等价），漏改 regex 导致「减」落成「加」的问题不再可能
_ARCADE_OP_RE = "|".join(re.escape(op) for op in SET_OPS + INCREASE_OPS + DECREASE_OPS)

arcade_help = on_fullmatch(("帮助maimaiDX排卡", "帮助maimaidx排卡"), block=True)
arcade_add = on_command(
    "添加机厅", aliases={"新增机厅"}, permission=SUPERUSER, block=True
)
arcade_del = on_command(
    "删除机厅", aliases={"移除机厅"}, permission=SUPERUSER, block=True
)
arcade_alias_set = on_command("添加机厅别名", aliases={"删除机厅别名"}, block=True)
arcade_set = on_command("修改机厅", aliases={"编辑机厅"}, block=True)
arcade_sub = on_command("订阅机厅", aliases={"取消订阅机厅", "取消订阅"}, block=True)
arcade_show_sub = on_fullmatch(("查看订阅", "查看订阅机厅"), block=True)
arcade_search = on_command(
    SEARCH_PREFIXES[0], aliases=set(SEARCH_PREFIXES[1:]), block=True
)
arcade_add_person = on_regex(
    rf"^(.+)?\s?({_ARCADE_OP_RE})\s?([0-9]+|＋|\+|－|-)(人|卡)?$",
    block=True,
    priority=3,
)
arcade_person_num = on_fullmatch(("机厅几人", "jtj"), block=True)
arcade_person_num_2 = on_regex(
    r"^(.+?)(?:" + "|".join(PERSON_SUFFIXES) + r")$",
    block=True,
    priority=3,
)


def _group_of(session: Session) -> str | None:
    if session.scene and session.scene.type == SceneType.GROUP:
        return str(session.scene.id)
    return None


def _user_of(session: Session) -> str:
    return str(session.user.id)


_admin_perm = ADMIN()
"""群管/群主权限（uninfo，多适配器通用）。"""


async def _find_arcade(keyword: str) -> store.Arcade | None:
    """按 ID / 全名 / 别名 精确定位机厅。"""
    keyword = keyword.strip()
    if keyword.isdigit():
        by_id = await store.get_arcade(int(keyword))
        if by_id:
            return by_id
    found = await store.get_arcades_by_name(keyword)
    for a in found:
        if a.name == keyword:
            return a
    return found[0] if found else None


def _arcade_msg(a: store.Arcade) -> str:
    return (
        f"店名：{a.name}\n"
        f"    - 地址：{a.address}\n"
        f"    - ID：{a.id}\n"
        f"    - 机台：{a.machines}\n"
        f"    - 排卡：{a.person} 人"
    )


@arcade_help.handle()
async def _():
    await UniMessage.image(raw=image_to_bytes(text_to_image(ARCADE_HELP))).finish(
        at_sender=True
    )


@arcade_add.handle()
@handle_errors("添加机厅失败")
async def _(session: Session = UniSession(), message: Message = CommandArg()):
    args = str(message).strip().split()
    if len(args) < 3:
        await UniMessage.text(
            "格式：添加机厅 <店名> <地址> <机台数量> [别称...]"
        ).finish(at_sender=True)
    name, address, count_raw, *aliases = args
    if not count_raw.isdigit():
        await UniMessage.text(" 机台数量需为数字").finish(at_sender=True)
    arcades = await store.get_all_arcades()
    # 自定义 id 段（≥10000）内部自增：与官方段隔离，不撞未来官方新 id
    new_id = max((a.id for a in arcades if a.id >= 10000), default=9999) + 1
    arcade = store.Arcade(
        id=new_id,
        name=name,
        address=address,
        machines=int(count_raw),
        is_custom=True,
        updated_by=_user_of(session),
    )
    await store.save_arcade(arcade)
    for al in aliases:
        await store.add_arcade_alias(arcade.id, al)
    await UniMessage.text(f" 已添加机厅「{name}」（ID {arcade.id}）").finish(
        at_sender=True
    )


@arcade_del.handle()
@handle_errors("删除机厅失败")
async def _(message: Message = CommandArg()):
    keyword = str(message).strip()
    arcade = await _find_arcade(keyword) if keyword else None
    if arcade is None:
        await UniMessage.text(" 没有这样的机厅哦").finish(at_sender=True)
    await store.delete_arcade(arcade.id)
    await UniMessage.text(f" 已删除机厅「{arcade.name}」").finish(at_sender=True)


@arcade_alias_set.handle()
@handle_errors("操作失败")
async def _(message: Message = CommandArg()):
    text = str(message).strip()
    args = text.split(maxsplit=1)
    if len(args) < 2:
        await UniMessage.text(
            "格式：添加机厅别名 <店名|ID> <别名> / 删除机厅别名 <别名>"
        ).finish(at_sender=True)
    if text.startswith("删除"):
        ok = await store.remove_arcade_alias_by_name(args[1].strip())
        await UniMessage.text(" 已删除别名" if ok else "未找到该别名").finish(
            at_sender=True
        )
    arcade = await _find_arcade(args[0])
    if arcade is None:
        await UniMessage.text(" 没有这样的机厅哦").finish(at_sender=True)
    alias = args[1].strip()
    if await store.add_arcade_alias(arcade.id, alias):
        await UniMessage.text(f" 已为「{arcade.name}」添加别名「{alias}」").finish(
            at_sender=True
        )
    await UniMessage.text(" 该别名已存在").finish(at_sender=True)


@arcade_set.handle()
@handle_errors("修改失败")
async def _(message: Message = CommandArg()):
    parts = str(message).strip().split()
    if len(parts) != 3 or parts[1] != "数量":
        await UniMessage.text(" 格式：修改机厅 <店名|ID> 数量 <数量>").finish(
            at_sender=True
        )
    arcade = await _find_arcade(parts[0])
    if arcade is None:
        await UniMessage.text(" 没有这样的机厅哦").finish(at_sender=True)
    if not parts[2].isdigit():
        await UniMessage.text(" 数量需为数字").finish(at_sender=True)
    arcade.machines = int(parts[2])
    await store.save_arcade(arcade)
    await UniMessage.text(
        f"已修改机厅「{arcade.name}」机台数量为「{parts[2]}」"
    ).finish(at_sender=True)


@arcade_sub.handle()
@handle_errors("操作失败")
async def _(
    bot: Bot,
    event: Event,
    session: Session = UniSession(),
    message: Message = CommandArg(),
    command: tuple = Command(),
):
    group_id = _group_of(session)
    if group_id is None:
        await UniMessage.text(" 订阅仅群聊可用").finish(at_sender=True)
    if not (await SUPERUSER(bot, event) or await _admin_perm(bot, event)):
        await UniMessage.text(" 权限不足：仅群管理员可用").finish(at_sender=True)
    keyword = str(message).strip()
    arcade = await _find_arcade(keyword) if keyword else None
    if arcade is None:
        await UniMessage.text(" 没有这样的机厅哦").finish(at_sender=True)
    if "取消" in "".join(command):  # 命中的指令/别名（明文包含会被参数误触）
        await store.unsubscribe(group_id, arcade.id)
        await UniMessage.text(f" 已取消订阅「{arcade.name}」").finish(at_sender=True)
    await store.subscribe(group_id, arcade.id)
    await UniMessage.text(f" 已订阅「{arcade.name}」").finish(at_sender=True)


@arcade_show_sub.handle()
@handle_errors("查询失败")
async def _(session: Session = UniSession()):
    group_id = _group_of(session)
    ids = await store.get_subscriptions(group_id) if group_id else []
    if not ids:
        await UniMessage.text(" 该群未订阅任何机厅").finish(at_sender=True)
    lines = [
        f"「{a.name}」（ID {a.id}，机台 {a.machines}，排卡 {a.person} 人）"
        for a in await store.get_arcade_by_ids(ids)
    ]
    await UniMessage.text(" 本群订阅的机厅：\n" + "\n".join(lines)).finish(
        at_sender=True
    )


@arcade_search.handle()
@handle_errors("查询失败")
async def _(message: Message = CommandArg()):
    keyword = str(message).strip()
    if not keyword:
        await UniMessage.text(" 格式：查找机厅 <关键词>").finish(at_sender=True)
    found = await store.get_arcades_by_name(keyword)
    if not found:
        await UniMessage.text(" 没有这样的机厅哦").finish(at_sender=True)
    result = [" 为您找到以下机厅："] + [_arcade_msg(a) for a in found]
    if len(found) < 5:
        await UniMessage.text("\n==========\n".join(result)).finish(at_sender=True)
    await UniMessage.image(raw=image_to_bytes(text_to_image("\n".join(result)))).finish(
        at_sender=True
    )


async def _alias_matches(arcade_id: int, name: str) -> bool:
    return any(al.alias == name for al in await store.get_arcade_aliases(arcade_id))


@arcade_add_person.handle()
@handle_errors()
async def _(
    bot: Bot,
    event: Event,
    session: Session = UniSession(),
    groups: tuple = RegexGroup(),
):
    group_id = _group_of(session)
    if group_id is None:
        await UniMessage.text(" 排卡操作仅群聊可用").finish(at_sender=True)
    if not (await SUPERUSER(bot, event) or await _admin_perm(bot, event)):
        await UniMessage.text(" 权限不足：仅群管理员可用").finish(at_sender=True)
    name_raw, op, amount_raw, unit = groups
    if not name_raw:
        await UniMessage.text(" 格式：<店名|别称>设置/=/+/- <人数>").finish(
            at_sender=True
        )
    sub_ids = await store.get_subscriptions(group_id)
    if not sub_ids:
        await UniMessage.text(" 该群未订阅机厅，无法更改机厅人数").finish(
            at_sender=True
        )
    name = name_raw.strip()
    if name.endswith("人数"):
        name = name[:-2]
    elif name.endswith("卡"):
        name = name[:-1]
    subs = await store.get_arcade_by_ids(sub_ids)
    aliases_of = {al.arcade_id: al for al in await store.get_arcade_aliases()}
    arcade = next(
        (
            a
            for a in subs
            if a.name == name
            or any(
                al.alias == name for al in aliases_of.values() if al.arcade_id == a.id
            )
        ),
        None,
    )
    if arcade is None:
        await UniMessage.text(" 已订阅的机厅中未找到该机厅").finish(at_sender=True)
    if amount_raw in ("＋", "+", "－", "-"):
        amount = 1
    elif amount_raw.isdigit():
        amount = int(amount_raw)
    else:
        await UniMessage.text(" 请输入正确的数字").finish(at_sender=True)
    if unit == "卡":
        # 「+N卡」改机台数，不动排卡人数（原来单位被吞、卡数按人数入账）
        if op in SET_OPS:
            new_machines = amount
        elif op in DECREASE_OPS:
            new_machines = max(arcade.machines - amount, 0)
        else:
            new_machines = arcade.machines + amount
        delta = 0
        arcade.machines = new_machines
        reply = f" 「{arcade.name}」当前机台 {new_machines} 卡"
    else:
        if op in SET_OPS:
            new_person = amount
        elif op in DECREASE_OPS:
            new_person = max(arcade.person - amount, 0)
        else:
            new_person = arcade.person + amount
        delta = new_person - arcade.person
        arcade.person = new_person
        reply = f" 「{arcade.name}」当前排卡 {new_person} 人"
    if abs(delta) > plugin_config.awmc_arcade_max_delta:
        await UniMessage.text(
            f"单次变更不能超过 {plugin_config.awmc_arcade_max_delta} 人"
        ).finish(at_sender=True)
    arcade.updated_by = _user_of(session)
    arcade.updated_at = datetime.now()
    await store.save_arcade(arcade)
    await store.add_count_log(arcade.id, delta, arcade.machines, _user_of(session))
    await UniMessage.text(reply).finish(at_sender=True)


@arcade_person_num.handle()
@handle_errors("查询失败")
async def _(session: Session = UniSession()):
    group_id = _group_of(session)
    ids = await store.get_subscriptions(group_id) if group_id else []
    if not ids:
        await UniMessage.text(" 该群未订阅任何机厅").finish(at_sender=True)
    lines = []
    for i in ids:
        a = await store.get_arcade(i)
        if a:
            lines.append(f"「{a.name}」排卡 {a.person} 人（机台 {a.machines}）")
    await UniMessage.text(" \n".join(lines)).finish(at_sender=True)


@arcade_person_num_2.handle()
@handle_errors("查询失败")
async def _(groups: tuple = RegexGroup()):
    name = (groups[0] or "").strip()
    if not name:
        await UniMessage.text(" 格式：<店名|别称>有多少人").finish(at_sender=True)
    found = await store.get_arcades_by_name(name)
    if not found:
        await UniMessage.text(" 没有这样的机厅哦").finish(at_sender=True)
    await UniMessage.text(
        "\n".join(f"「{a.name}」排卡 {a.person} 人" for a in found)
    ).finish(at_sender=True)


# ---------------------------------------------------------------------------
# 每日 4 点：同步华立官方数据 + 排卡人数清零
# ---------------------------------------------------------------------------


async def sync_and_reset() -> int:
    """同步华立机厅并清零排卡人数，返回官方机厅数。"""
    try:
        official = await wahlap_ext.fetch_locations()
    except wahlap_ext.ExtError as e:
        logger.warning(f"华立机厅同步失败：{e}")
        return 0
    # 单事务批量 upsert（原先每机厅独立 session 串行两次事务）
    await store.upsert_arcades(
        store.Arcade(
            id=w.id,
            name=w.name,
            address=w.address,
            province=w.province,
            mall=w.mall,
            machines=w.machine_count,
            is_custom=False,
        )
        for w in official
    )
    count = await store.reset_all_persons()
    logger.info(
        f"maimaiDX排卡数据更新完毕"
        f"（{len(official)} 台官方机厅，{count} 个机厅人数清零）"
    )
    return len(official)


if _SCHED and scheduler is not None:

    async def _daily_job() -> None:
        await sync_and_reset()

    scheduler.add_job(_daily_job, "cron", hour=4, minute=0)
