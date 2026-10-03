"""awmc.dan 指令入口：段位查询 / 随机段位规则 / 手动刷新。

段位名解析用内置中文别名表（初段..裏皆传、随机档位）；普通/真段位默认当前
版本（version 最大的段位表）渲染卡片。未绑定/数据源不可用走降级（达成率
0.0000%、底分无括号，与歌曲卡一致），因此绑定解析用 ``resolve_session_query``
（不 finish），凭据不可用视同未绑定。
"""

from nonebot import on_command
from nonebot.params import CommandArg
from nonebot.adapters import Event, Message
from nonebot.permission import SUPERUSER
from nonebot_plugin_uninfo import UniSession
from nonebot_plugin_alconna.uniseg import UniMessage

from ...core import dan
from ...config import plugin_config
from ...core.help import CommandSpec, help_registry
from ...core.utils import handle_errors
from ...core.binding import binding_service, resolve_session_query
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

dan_cmd = on_command("段位", block=True)
dan_refresh = on_command("刷新段位", permission=SUPERUSER, block=True)

_NOT_LOADED = "段位数据尚未加载，请稍后再试或联系管理员「刷新段位」"


def _resolve_dan_id(arg: str) -> str | None:
    """段位别名 → 段位种名 id；随机档位（难度 + 档名任意组合/省略）亦在此解析。"""
    arg = arg.strip()
    if arg in _DAN_ALIASES:
        return _DAN_ALIASES[arg]
    tokens = arg.replace("级", "級").split()
    diff = next((t for t in tokens if t.lower() in ("expert", "master")), None)
    tier = next((t for t in tokens if t in dan.RANDOM_TIERS), None)
    if arg in ("随机", "隨機", "random") or diff or tier:
        diff = (diff or "master").lower()
        tier = tier or dan.RANDOM_TIERS[3]
        return f"random_{diff}_{dan.RANDOM_TIERS.index(tier) + 1}"
    return None


def _logo():
    """奖励区版本 logo（国服区素材，缺失返回 None 由渲染跳过）。"""
    from ..render.assets import assets

    path = plugin_config.awmc_static_path / "mai" / "pic" / "dan" / "DX_2026_Logo.png"
    return assets.get(path) if path.exists() else None


async def _random_text() -> str:
    """随机段位规则文本（含实测定数区间来源标注）。"""
    tiers = await dan.random_tiers()
    if not tiers:
        return _NOT_LOADED
    lines = ["【随机段位认定】（四曲独立随机抽取，重复不回避）"]
    for t in sorted(tiers, key=lambda r: (r.difficulty != "expert", r.dan_id)):
        tag = "实测" if t.ds_source == "measured" else "推导"
        lines.append(
            f"{t.difficulty.upper()} {t.name_ja}：Lv {t.level_range}"
            f"（定数 {t.ds_lo}~{t.ds_hi}，{tag}）\n"
            f"❤{t.life} -{t.damage_great}/-{t.damage_good}/-{t.damage_miss}"
            f" 每曲 +{t.clear_bonus}"
        )
    lines.append("通关奖励：1.5 倍奖励票")
    return "\n".join(lines)


async def _usable_binding(session, event: Event | None):
    """会话绑定（不 finish）：无绑定或凭据不可用返回 None → 走无数据降级。"""
    binding, _ = await resolve_session_query(session, event)
    if binding is None or not binding_service.has_usable_credentials(binding):
        return None
    return binding


@dan_cmd.handle()
@handle_errors("段位查询失败，请稍后再试")
async def _(
    message: Message = CommandArg(),
    session=UniSession(),
    event: Event | None = None,
):
    await dan.ensure_loaded()
    arg = message.extract_plain_text().strip()

    if dan_id := _resolve_dan_id(arg):
        if dan_id.startswith("random_"):
            await UniMessage.text(await _random_text()).finish(at_sender=True)
        binding = await _usable_binding(session, event)
        data = await dan.card_data(await dan.latest_gallery_id(), dan_id, binding)
        if data is None:
            await UniMessage.text(_NOT_LOADED).finish(at_sender=True)
        png = render_dan_card(data)
        await UniMessage.image(raw=png).finish(at_sender=True)

    if arg in ("", "列表", "帮助"):
        gallery_id = await dan.latest_gallery_id()
        if gallery_id is None:
            await UniMessage.text(_NOT_LOADED).finish(at_sender=True)
        grades = await dan.grades_of(gallery_id)
        names = " / ".join(g.name_ja for g, _ in grades)
        await UniMessage.text(
            f"可用段位（当前版本）：{names}\n"
            "「段位 <段位名>」查看段位表（绑定后含达成率/底分预测）；"
            "「段位 随机」查看随机段位规则"
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
            brief="段位认定查询：段位 / 段位 <段位名> / 段位 随机",
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
