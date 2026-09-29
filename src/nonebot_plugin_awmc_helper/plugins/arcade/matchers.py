"""排卡指令入口：机厅管理 / 订阅 / 查询 / 排卡人数操作。"""

import re

from nonebot import on_regex, on_command, on_fullmatch
from nonebot.rule import Rule
from nonebot.params import Command, CommandArg, RegexGroup
from nonebot.adapters import Bot, Event, Message
from nonebot.permission import SUPERUSER
from nonebot_plugin_uninfo import Session, UniSession
from nonebot_plugin_alconna.uniseg import UniMessage

from ...core import store
from ...config import plugin_config
from ...core.utils import user_id_of, group_id_of, handle_errors, ensure_group_admin
from ...core.render.tools import text_image_bytes

ARCADE_HELP = (
    "排卡指令如下：\n"
    "开启排卡/关闭排卡 群管开关本群排卡（部署默认关）\n"
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


def _op_mode(op: str) -> str:
    """操作符 → store.update_arcade_count 的更新模式（set/dec/inc）。"""
    if op in SET_OPS:
        return "set"
    if op in DECREASE_OPS:
        return "dec"
    return "inc"


ARCADE_FEATURE = "arcade"
"""group_switch 表中的排卡特征名。"""


async def _arcade_enabled(session: Session = UniSession()) -> bool:
    """排卡群级门禁：群内取群级覆盖（缺省部署默认），私聊取部署默认。

    放在 rule 而非 handler 开头：关闭群中匹配失败即不消费事件，
    宽正则（XX店+2人/XX店有几人）不会吞掉本属于其他插件的消息。
    """
    group_id = group_id_of(session)
    if group_id is None:
        return plugin_config.awmc_arcade_enabled
    return await store.get_switch(
        group_id, ARCADE_FEATURE, plugin_config.awmc_arcade_enabled
    )


# 对齐 Hoshino 原版 Service(…, enable_on_default=False) 的按群显式开通；
# 同一事件的依赖缓存贯穿 权限→rule→handler（nonebot handle_event 级），
# 此处构建的 Session 与 handler 的 UniSession 共享，无额外 API 开销
arcade_gate = Rule(_arcade_enabled)

# 帮助不设门禁：精确全文匹配无误触风险，未开通群可经此发现「开启排卡」入口
arcade_help = on_fullmatch(("帮助maimaiDX排卡", "帮助maimaidx排卡"), block=True)
arcade_add = on_command(
    "添加机厅",
    aliases={"新增机厅"},
    permission=SUPERUSER,
    block=True,
    rule=arcade_gate,
)
arcade_del = on_command(
    "删除机厅", aliases={"移除机厅"}, permission=SUPERUSER, block=True, rule=arcade_gate
)
arcade_alias_set = on_command(
    "添加机厅别名", aliases={"删除机厅别名"}, block=True, rule=arcade_gate
)
arcade_set = on_command("修改机厅", aliases={"编辑机厅"}, block=True, rule=arcade_gate)
arcade_sub = on_command(
    "订阅机厅",
    aliases={"取消订阅机厅", "取消订阅"},
    block=True,
    rule=arcade_gate,
)
arcade_show_sub = on_fullmatch(
    ("查看订阅", "查看订阅机厅"), block=True, rule=arcade_gate
)
arcade_search = on_command(
    SEARCH_PREFIXES[0], aliases=set(SEARCH_PREFIXES[1:]), block=True, rule=arcade_gate
)
arcade_add_person = on_regex(
    rf"^(.+)?\s?({_ARCADE_OP_RE})\s?([0-9]+|＋|\+|－|-)(人|卡)?$",
    block=True,
    priority=3,
    rule=arcade_gate,
)
arcade_person_num = on_fullmatch(("机厅几人", "jtj"), block=True, rule=arcade_gate)
arcade_person_num_2 = on_regex(
    r"^(.+?)(?:" + "|".join(PERSON_SUFFIXES) + r")$",
    block=True,
    priority=3,
    rule=arcade_gate,
)
arcade_switch = on_regex(r"^(开启|关闭)排卡$", block=True)


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
@handle_errors()
async def _():
    await UniMessage.image(raw=text_image_bytes(ARCADE_HELP)).finish(at_sender=True)


@arcade_switch.handle()
@handle_errors()
async def _(
    bot: Bot,
    event: Event,
    session: Session = UniSession(),
    groups: tuple = RegexGroup(),
):
    group_id = await ensure_group_admin(session, bot, event, feature="排卡开关")
    enabled = groups[0] == "开启"
    await store.set_group_switch(group_id, ARCADE_FEATURE, enabled)
    state = "开启" if enabled else "关闭"
    await UniMessage.text(f" 已{state}本群排卡").finish(at_sender=True)


@arcade_add.handle()
@handle_errors("添加机厅失败")
async def _(session: Session = UniSession(), message: Message = CommandArg()):
    args = message.extract_plain_text().strip().split()
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
        updated_by=user_id_of(session),
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
    keyword = message.extract_plain_text().strip()
    arcade = await _find_arcade(keyword) if keyword else None
    if arcade is None:
        await UniMessage.text(" 没有这样的机厅哦").finish(at_sender=True)
    await store.delete_arcade(arcade.id)
    await UniMessage.text(f" 已删除机厅「{arcade.name}」").finish(at_sender=True)


@arcade_alias_set.handle()
@handle_errors("操作失败")
async def _(message: Message = CommandArg()):
    text = message.extract_plain_text().strip()
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
    parts = message.extract_plain_text().strip().split()
    if len(parts) != 3 or parts[1] != "数量":
        await UniMessage.text(" 格式：修改机厅 <店名|ID> 数量 <数量>").finish(
            at_sender=True
        )
    arcade = await _find_arcade(parts[0])
    if arcade is None:
        await UniMessage.text(" 没有这样的机厅哦").finish(at_sender=True)
    if not parts[2].isdigit():
        await UniMessage.text(" 数量需为数字").finish(at_sender=True)
    # 只改机台数（绝对值），不整行回写——快照里的排卡人数可能已过期
    new_machines = await store.update_arcade_count(
        arcade.id, "machines", "set", int(parts[2]), updated_by=None, touch=False
    )
    if new_machines is None:
        await UniMessage.text(" 机厅数据已变更，请刷新后重试").finish(at_sender=True)
    await UniMessage.text(
        f"已修改机厅「{arcade.name}」机台数量为「{new_machines}」"
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
    group_id = await ensure_group_admin(session, bot, event, feature="订阅")
    keyword = message.extract_plain_text().strip()
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
    group_id = group_id_of(session)
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
    keyword = message.extract_plain_text().strip()
    if not keyword:
        await UniMessage.text(" 格式：查找机厅 <关键词>").finish(at_sender=True)
    found = await store.get_arcades_by_name(keyword)
    if not found:
        await UniMessage.text(" 没有这样的机厅哦").finish(at_sender=True)
    result = [" 为您找到以下机厅："] + [_arcade_msg(a) for a in found]
    if len(found) < 5:
        await UniMessage.text("\n==========\n".join(result)).finish(at_sender=True)
    await UniMessage.image(raw=text_image_bytes("\n".join(result))).finish(
        at_sender=True
    )


@arcade_add_person.handle()
@handle_errors()
async def _(
    session: Session = UniSession(),
    groups: tuple = RegexGroup(),
):
    group_id = group_id_of(session)
    if group_id is None:
        await UniMessage.text(" 排卡操作仅群聊可用").finish(at_sender=True)
    name_raw, op, amount_raw, unit = groups
    if not name_raw:
        # 无店名（如「+1」「=5」）静默忽略，对齐 Hoshino 原版（逻辑全在
        # if match.group(1) 内）；「1+1」属有店名路径，照常查订阅机厅
        return
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
    # 订阅机厅的别称集合（按机厅分组；多别称必须全部参与匹配）
    alias_by_arcade: dict[int, set[str]] = {}
    for al in await store.get_arcade_aliases():
        if al.arcade_id in sub_ids:
            alias_by_arcade.setdefault(al.arcade_id, set()).add(al.alias)
    arcade = next(
        (a for a in subs if a.name == name or name in alias_by_arcade.get(a.id, set())),
        None,
    )
    if arcade is None:
        await UniMessage.text(" 已订阅的机厅中未找到该机厅").finish(at_sender=True)
    # 正则已约束 amount_raw ∈ 数字/＋/+/－/-：else 分支不可达（死代码清理）
    if amount_raw in ("＋", "+", "－", "-"):
        amount = 1
    else:
        amount = int(amount_raw)
    mode = _op_mode(op)
    if unit == "卡":
        # 「+N卡」改机台数，不动排卡人数（原来单位被吞、卡数按人数入账）；
        # 相对增量原子写入，回复值取 DB 实况（机台数变更不设 max 守卫，同旧版）
        new_machines = await store.update_arcade_count(
            arcade.id, "machines", mode, amount, updated_by=user_id_of(session)
        )
        if new_machines is None:
            await UniMessage.text(" 机厅数据已变更，请刷新后重试").finish(
                at_sender=True
            )
        await store.add_count_log(arcade.id, 0, new_machines, user_id_of(session))
        reply = f" 「{arcade.name}」当前机台 {new_machines} 卡"
    else:
        # 守卫沿用快照口径估 delta 上界（与旧实现一致）；真实写入是相对增量，
        # 「+1」们各自叠加而非整行回写，两人同时 +1 净加 2 不丢更新
        if op in SET_OPS:
            delta_bound = abs(amount - max(arcade.person, 0))
        elif op in DECREASE_OPS:
            delta_bound = min(amount, max(arcade.person, 0))
        else:
            delta_bound = amount
        if delta_bound > plugin_config.awmc_arcade_max_delta:
            await UniMessage.text(
                f"单次变更不能超过 {plugin_config.awmc_arcade_max_delta} 人"
            ).finish(at_sender=True)
        new_person = await store.update_arcade_count(
            arcade.id, "person", mode, amount, updated_by=user_id_of(session)
        )
        if new_person is None:
            await UniMessage.text(" 机厅数据已变更，请刷新后重试").finish(
                at_sender=True
            )
        # 计数日志口径与旧实现一致：以本次操作者的快照为基线估 delta
        await store.add_count_log(
            arcade.id,
            new_person - max(arcade.person, 0),
            arcade.machines,
            user_id_of(session),
        )
        reply = f" 「{arcade.name}」当前排卡 {new_person} 人"
    await UniMessage.text(reply).finish(at_sender=True)


@arcade_person_num.handle()
@handle_errors("查询失败")
async def _(session: Session = UniSession()):
    group_id = group_id_of(session)
    ids = await store.get_subscriptions(group_id) if group_id else []
    if not ids:
        await UniMessage.text(" 该群未订阅任何机厅").finish(at_sender=True)
    lines = [
        f"「{a.name}」排卡 {a.person} 人（机台 {a.machines}）"
        for a in await store.get_arcade_by_ids(ids)
    ]
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
