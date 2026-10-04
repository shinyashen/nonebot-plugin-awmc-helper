"""awmc.dan 指令入口：段位查询 / 随机段位规则 / 手动刷新。

段位名解析用内置中文别名表（初段..裏皆传、随机 8 档如「MASTER 超上级」）；
普通/真/随机段位统一走段位卡流程（随机档位课题曲为四行「随机选曲」占位），
未绑定/数据源不可用走降级（达成率 0.0000%、底分无括号，与歌曲卡一致），
因此绑定解析用 ``resolve_session_query``（不 finish），凭据不可用视同未绑定。
"""

from nonebot import on_command
from nonebot.params import CommandArg
from nonebot.adapters import Bot, Event, Message
from nonebot.permission import SUPERUSER
from nonebot_plugin_uninfo import Session, UniSession
from nonebot_plugin_alconna.uniseg import UniMessage

from ...core import dan
from ...core.help import CommandSpec, help_registry
from ...core.score import score_service
from ...core.utils import user_id_of, group_id_of, handle_errors
from ...core.binding import binding_service, resolve_session_query
from ...core.forward import try_send_forward
from ...core.render.dan import render_dan_card

# 中文段位别名 → 段位种名 id（简繁/异写并入；跨版本查询暂只出当前版本）
_DAN_ALIASES: dict[str, str] = {
    "初段": "1dan",
    "二段": "2dan",
    "三段": "3dan",
    "四段": "4dan",
    "五段": "5dan",
    "六段": "6dan",
    "七段": "7dan",
    "八段": "8dan",
    "九段": "9dan",
    "十段": "10dan",
    "真初段": "shin_1dan",
    "真二段": "shin_2dan",
    "真三段": "shin_3dan",
    "真四段": "shin_4dan",
    "真五段": "shin_5dan",
    "真六段": "shin_6dan",
    "真七段": "shin_7dan",
    "真八段": "shin_8dan",
    "真九段": "shin_9dan",
    "真十段": "shin_10dan",
    "真皆传": "shin_kaiden",
    "真皆伝": "shin_kaiden",
    "裏皆传": "ura_kaiden",
    "里皆传": "ura_kaiden",
    "裏皆伝": "ura_kaiden",
}

# 随机档位别名（简繁/有无空格/大小写/难度颜色替代统一在 _normalize 处理）
# 难度颜色替代：expert=红、master=紫（与等级数字色号一致）
_RANDOM_DIFFS = {
    "expert": ("expert", "ex", "红"),
    "master": ("master", "mas", "紫"),
}

dan_cmd = on_command("段位", aliases={"段位表"}, block=True)
dan_refresh = on_command("刷新段位", permission=SUPERUSER, block=True)

_NOT_LOADED = "段位数据尚未加载，请稍后再试或联系管理员「刷新段位」"


def _normalize_dan_name(arg: str) -> str:
    """段位名归一：去空白、繁→简、小写拉丁、剥「级」字（档名可省略级）。"""
    return arg.strip().replace(" ", "").replace("級", "级").replace("级", "").lower()


def _resolve_dan_id(arg: str) -> str | None:
    """段位别名 → 段位种名 id（随机档位「MASTER 超上级」「紫超上」等同流程）。"""
    normalized = _normalize_dan_name(arg)
    if normalized in _DAN_ALIASES:
        return _DAN_ALIASES[normalized]
    if normalized in ("随机", "随机段位", "random"):
        return "random"  # 裸「随机」= 规则总览
    # 随机档位：「难度（可颜色替代）+ 档名（可省略级）」连写/空格均可
    for diff_key, prefixes in _RANDOM_DIFFS.items():
        for prefix in prefixes:
            if normalized.startswith(prefix):
                for tier_no, tier in enumerate(dan.RANDOM_TIERS, 1):
                    if normalized[len(prefix) :] == tier.rstrip("級级"):
                        return f"random_{diff_key}_{tier_no}"
    return None


async def _random_overview_text() -> str:
    """随机段位规则总览纯文本（合并转发不可用时的降级形态）。"""
    tiers = await dan.random_tiers()
    if not tiers:
        return _NOT_LOADED
    header, blocks = _random_overview_blocks(tiers)
    return "\n".join([header, *blocks, _RANDOM_TAIL])


_RANDOM_TAIL = "通关奖励：1.5 倍奖励票；「段位 <档名>」查看单档段位卡"


def _random_overview_blocks(tiers) -> tuple[str, list[str]]:
    """随机段位总览拆块：头部一行 + 每档一块（转发的每节点一条）。"""
    header = "【随机段位认定】（四曲独立随机抽取，重复不回避）"
    blocks = []
    for t in sorted(tiers, key=lambda r: (r.difficulty != "expert", r.dan_id)):
        tag = "实测" if t.ds_source == "measured" else "推导"
        blocks.append(
            f"{t.difficulty.upper()} {t.name_ja}：Lv {t.level_range}"
            f"（定数 {t.ds_lo}~{t.ds_hi}，{tag}）\n"
            f"❤{t.life} -{t.damage_great}/-{t.damage_good}/-{t.damage_miss}"
            f" 每曲 +{t.clear_bonus}"
        )
    return header, blocks


async def _finish_random_overview(bot: Bot, session: Session) -> None:
    """随机段位总览：OB11 走合并转发（每档一节点），不支持/失败降级单条文本。"""
    tiers = await dan.random_tiers()
    if not tiers:
        await UniMessage.text(_NOT_LOADED).finish(at_sender=True)
    header, blocks = _random_overview_blocks(tiers)
    group_id = group_id_of(session)
    sent = await try_send_forward(
        bot,
        [header, *blocks, _RANDOM_TAIL],
        group_id=group_id,
        user_id=None if group_id else user_id_of(session),
    )
    if not sent:
        await UniMessage.text("\n".join([header, *blocks, _RANDOM_TAIL])).finish(
            at_sender=True
        )


async def _usable_binding(session, event: Event | None):
    """会话绑定（不 finish）：无绑定或凭据不可用返回 None → 走无数据降级。"""
    binding, _ = await resolve_session_query(session, event)
    if binding is None or not binding_service.has_usable_credentials(binding):
        return None
    return binding


@dan_cmd.handle()
@handle_errors("段位查询失败，请稍后再试")
async def _(
    bot: Bot,
    message: Message = CommandArg(),
    session: Session = UniSession(),
    event: Event | None = None,
):
    await dan.ensure_loaded()
    arg = message.extract_plain_text().strip()

    try:
        version_code, dan_arg = dan.parse_version_prefix(arg)
    except ValueError as e:
        await UniMessage.text(f" {e}").finish(at_sender=True)
    dan_id = _resolve_dan_id(dan_arg)
    if dan_id == "random":  # 裸「随机」= 8 档规则总览（合并转发）
        await _finish_random_overview(bot, session)
    if dan_id:
        binding = await _usable_binding(session, event)
        if dan_id.startswith("random_"):
            gallery_id = None
        elif version_code is None:
            # 默认表跟数据源视图：日服视图源=日服最新，其余/未绑定=国服现行
            view = (
                score_service.view_of(binding.service) if binding is not None else "cn"
            )
            limit = None if view == "jp" else await dan.cn_current_version()
            gallery_id = await dan.latest_gallery_id(version_limit=limit)
        else:
            gallery_id = await dan.course_id_by_version(version_code)
            if gallery_id is None:
                await UniMessage.text(
                    " 该版本暂无段位数据（数据源自 Splash PLUS 起）"
                ).finish(at_sender=True)
        data = await dan.card_data(
            gallery_id, dan_id, binding, version_code=version_code
        )
        if data is None:
            await UniMessage.text(_NOT_LOADED).finish(at_sender=True)
        await UniMessage.image(raw=render_dan_card(data)).finish(at_sender=True)

    if arg in ("", "列表", "帮助"):
        gallery_id = await dan.latest_gallery_id()
        if gallery_id is None:
            await UniMessage.text(_NOT_LOADED).finish(at_sender=True)
        grades = await dan.grades_of(gallery_id)
        names = " / ".join(g.name_ja for g, _ in grades)
        await UniMessage.text(
            f"可用段位（当前版本）：{names}\n"
            "随机段位：EXPERT/MASTER × 初級~超上級（如「段位 紫超上」）；\n"
            "跨版本：段位名前加版本前缀（如「段位 bud+裏皆传」「段位 dx初段」"
            "「段位 舞萌2022十段」），不带前缀为当前版本；"
            "「段位 随机」查看随机段位规则总览"
        ).finish(at_sender=True)
    await UniMessage.text("未识别的段位名；发送「段位」查看可用段位").finish(
        at_sender=True
    )


@dan_refresh.handle()
@handle_errors("刷新段位失败")
async def _():
    await UniMessage.text(" 正在拉取段位歌单……").send(at_sender=True)
    result = await dan.refresh()
    await UniMessage.text(
        f" 段位歌单入库完成：段位表 {result['courses']} 张、"
        f"随机段位 {result['randoms']} 档、课题曲 {result['sheets']} 行"
    ).finish(at_sender=True)


help_registry.declare(
    plugin="awmc.dan",
    title="段位认定",
    category="query",
    commands=[
        CommandSpec(
            matcher=dan_cmd,
            name="段位",
            brief="段位认定查询：段位 / 段位 <段位名>（含 MASTER超上级 等随机档位）",
        ),
        CommandSpec(
            matcher=dan_refresh,
            name="刷新段位",
            scope="SUPERUSER",
            hidden=True,
            brief="手动拉取 arcade-songs 段位歌单入库",
        ),
    ],
)
