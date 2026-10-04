"""主帮助与项目地址指令（原 mai_base；帮助系统唯一入口，M10）。

根词 ``舞萌帮助``/``wmhelp``/``mai帮助``，旧根 ``帮助maimaiDX`` 保留为别名
（``(?i)`` 兼容大小写；``\\s*`` 兼容历史无空格写法 ``帮助maimaiDX排卡``——旧
复合触发词统一收敛，核心词由注册表按 指令 > 类别 > 指南 消歧）。

发送链（拍板③）：多节点页合并转发 → 失败降级 ``text_image_bytes`` 整图；
单节点纯文本页（指令详情/管理页）直接普通消息不发转发卡。「管理」节点仅
SUPERUSER **私聊**查询时附带（拍板④追加：群聊不留管理指令痕迹）。旧
HELP_TEXT 静态总览已删除，内容迁入 ``core.help`` 注册表。
"""

import re

from nonebot import on_regex, on_command
from nonebot.plugin import PluginMetadata
from nonebot.matcher import Matcher
from nonebot.adapters import Bot, Event
from nonebot.permission import SUPERUSER
from nonebot_plugin_uninfo import Session, UniSession
from nonebot_plugin_alconna.uniseg import UniMessage

from ...core.help import (
    Page,
    CommandSpec,
    NotFoundPage,
    page_text,
    page_entries,
    help_registry,
)
from ...core.utils import handle_errors, is_private_session
from ...core.forward import try_send_forward_session
from ...core.render.tools import text_image_bytes

__plugin_meta__ = PluginMetadata(
    name="awmc.base",
    description="舞萌DX 帮助与项目地址",
    usage="舞萌帮助（wmhelp/mai帮助）｜项目地址maimaiDX",
    type="application",
    homepage="https://github.com/shinyashen/nonebot-plugin-awmc-helper",
)

REPO_URL = "项目地址：https://github.com/shinyashen/nonebot-plugin-awmc-helper"

# (?i) 同时覆盖 帮助maimaidx/wmhelp/WMHelp 等大小写变体；\s* 兼容历史
# 「帮助maimaiDX排卡」式无空格二级词（旧复合触发词由注册表接管语义）
ROOT_PATTERN = r"(?i)^(?:舞萌帮助|wmhelp|mai帮助|帮助maimaiDX)\s*(?:\s?(.+))?$"

help_cmd = on_regex(ROOT_PATTERN, block=True)
repo_cmd = on_command("项目地址maimaiDX", aliases={"项目地址maimaidx"}, block=True)


async def _superuser(bot: Bot, event: Event) -> bool:
    """SUPERUSER 判定（Permission 对象可直接 await 调用）。"""
    return bool(await SUPERUSER(bot, event))


async def _send_page(
    bot: Bot, event: Event, matcher: Matcher, page: Page, session: Session
) -> None:
    """页面发送链：单节点文本直发；多节点转发→降级整图（at_sender 走事件上下文）。"""
    entries = page_entries(help_registry, page)
    if len(entries) == 1 and isinstance(entries[0], str):
        await UniMessage.text(entries[0]).finish(at_sender=True)
    # 转发目标（群聊群号/私聊用户号）判定与发送统一走 core 助手（uninfo 单源）
    if await try_send_forward_session(bot, entries, session):
        await matcher.finish()
    await UniMessage.image(
        raw=text_image_bytes(page_text(help_registry, page), size=22)
    ).finish(at_sender=True)


@help_cmd.handle()
@handle_errors()
async def _(
    bot: Bot,
    event: Event,
    matcher: Matcher,
    session: Session = UniSession(),
):
    # matcher 已保证根词命中，此处对明文二次解析取二级参数（空格可省略）
    text = event.get_plaintext().strip()
    matched = re.match(ROOT_PATTERN, text)
    query = (matched.group(1) or "").strip() if matched else ""
    # 拍板④：管理面仅 SUPERUSER 且私聊时可见（群聊不留管理指令痕迹）
    include_hidden = await _superuser(bot, event) and is_private_session(session)
    page = help_registry.resolve(query, include_hidden=include_hidden)
    if isinstance(page, NotFoundPage):
        await UniMessage.text(
            f"没有找到「{page.query}」。\n"
            "发送「舞萌帮助」查看总览；详情支持 类别/指令/指南 三类条目。"
        ).finish(at_sender=True)
    await _send_page(bot, event, matcher, page, session)


@repo_cmd.handle()
@handle_errors()
async def _():
    await UniMessage.text(REPO_URL + "\n求 star，求宣传~").finish(at_sender=True)


# ---------------------------------------------------------------- 声明块

help_registry.declare(
    plugin="awmc.base",
    title="基础",
    category="basic",
    description="帮助入口与项目地址",
    commands=[
        CommandSpec(
            matcher=help_cmd,
            name="舞萌帮助",
            aliases=("wmhelp", "mai帮助", "帮助maimaiDX"),
            brief="指令总览入口",
            detail=(
                "「舞萌帮助」查看全部类别总览"
                "（合并转发；SUPERUSER 额外可见「管理」节点）；\n"
                "「舞萌帮助 <类别>」看某类指令清单（如 舞萌帮助 查歌）；\n"
                "「舞萌帮助 <指令>」看单条指令详情（如 舞萌帮助 b50）；\n"
                "「舞萌帮助 <指南>」看完整流程（如 舞萌帮助 查分上手）；\n"
                "旧词「帮助maimaiDX」保留为根别名。"
            ),
            example="舞萌帮助 / 舞萌帮助 查分 / 舞萌帮助 b50 / 舞萌帮助 查分上手",
        ),
        CommandSpec(
            matcher=repo_cmd,
            name="项目地址maimaiDX",
            aliases=("项目地址maimaidx",),
            brief="插件仓库地址",
        ),
    ],
)
