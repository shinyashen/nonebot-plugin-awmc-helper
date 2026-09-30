"""表格指令入口：定数表 / 完成表 / 进度 / 牌子 / 分数列表 / 底图更新。"""

from nonebot import on_regex, on_command, on_fullmatch
from nonebot.params import RegexGroup
from nonebot.permission import SUPERUSER
from nonebot_plugin_alconna.uniseg import UniMessage

from .sheet import (
    _plate_shape,
    _level_entries,
    combo_progress_card,
    combo_score_list_card,
    _plate_completion_sheet,
    _plate_progress_overview,
)
from ...constants import PLATE_CHARS, DEFAULT_THEME
from ...core.help import CommandSpec, help_registry
from ...core.combo import (
    OutputKind,
    ComboAmbiguity,
    parse_combo,
    inapplicable,
    ensure_designer_rules,
)
from ...core.score import UserScoreError, score_service
from ...core.songs import song_service
from ...core.store import UserBinding
from ...core.utils import parse_page, slow_notice, handle_errors
from ...core.plates import (
    PLATE_KINDS,
    PLATE_KIND_ALIAS_CHARS,
    PLATE_VERSION_ALIAS_CHARS,
    norm_plate,
    is_valid_plate,
    plate_kinds_hint,
)
from ...core.binding import SessionQueryBinding, at_tolerant
from ...core.sources import Capability
from ...core.render.tools import text_image_bytes

# 牌种正则（牌种并集取自 core.plates 单源；正则交替最长优先，
# 防将来新增牌种被单字牌种遮蔽；繁体/和制牌种字一并入交替）
PLATE_KIND_ALT = "|".join(
    sorted((*PLATE_KINDS, *PLATE_KIND_ALIAS_CHARS), key=len, reverse=True)
)

LEVEL_RE = r"([0-9]+\+?)"
DS_RE = r"([0-9]+(?:\.[0-9]+)?\+?)"
PLAN_RE = r"(sssp|sss|ssp|ss|sp|s|ap|fcp|fc|fsp|fs|fdx)"

ds_table_cmd = on_regex(at_tolerant(rf"^{LEVEL_RE}定数表$"), block=True)
score_table_cmd = on_regex(at_tolerant(rf"^{LEVEL_RE}{PLAN_RE}\+?完成表$"), block=True)
progress_cmd = on_regex(
    at_tolerant(r"^(.+?)(已完成|未完成|未游玩|未开始)?进度\s?([0-9]+)?$"),
    block=True,
)
plate_cmd = on_regex(
    at_tolerant(
        rf"^([{PLATE_CHARS}{PLATE_VERSION_ALIAS_CHARS}])({PLATE_KIND_ALT})完成表$"
    ),
    block=True,
)
plate_help = on_fullmatch("牌子条件", block=True)
score_list_cmd = on_regex(at_tolerant(r"^(.+?)分数列表\s?([0-9]+)?$"), block=True)
update_rating = on_command("更新定数表", permission=SUPERUSER, block=True)
update_plate = on_command("更新完成表", permission=SUPERUSER, block=True)


@ds_table_cmd.handle()
@handle_errors("生成定数表失败")
async def _(
    groups: tuple = RegexGroup(),
):
    """定数表（R8，NB DrawRatingTable(level_text=True) 版式网格图）。"""
    from ...core.render import table_template

    (level,) = groups
    entries = await _level_entries(level)
    if not entries:
        await UniMessage.text(f" 没有找到等级为「{level}」的谱面").finish(
            at_sender=True
        )
    png = await table_template.rating_table_text_bytes(level, entries)
    await UniMessage.image(raw=png).finish(at_sender=True)


@score_table_cmd.handle()
@handle_errors("生成完成表失败", except_with_message=(UserScoreError,))
async def _(
    binding: UserBinding = SessionQueryBinding(),
    groups: tuple = RegexGroup(),
):
    """等级完成表（NB DrawRatingTable 移植）：模板 + 统计头 + 逐谱面盖章。

    计划映射：fc/fcp/ap → 连击章模式（NB plan=True）；fs 族 → Sync 章
    （NB 未支持，按连击章模式扩展）；达成率计划/无计划 → 评级章模式
    （NB plan=False，盖章为各谱面实际评级）。
    """
    from ...core.render import table_template

    level, plan = groups
    entries = await _level_entries(level)
    if not entries:
        await UniMessage.text(f" 没有找到等级为「{level}」的谱面").finish(
            at_sender=True
        )
    scores = await score_service.get_scores_all(binding, notify_slow=slow_notice())

    theme = binding.theme or DEFAULT_THEME
    png = await table_template.draw_rating_table_with_fallback(
        level, plan, scores.scores, entries, theme=theme, song_service=song_service
    )
    if png is None:
        await UniMessage.text(" 定数表底图生成失败，请稍后再试").finish(at_sender=True)
    await UniMessage.image(raw=png).finish(at_sender=True)


@progress_cmd.handle()
@handle_errors("生成进度失败", except_with_message=(UserScoreError,))
async def _(
    binding: UserBinding = SessionQueryBinding(),
    groups: tuple = RegexGroup(),
):
    """条件化进度（R4 版式泛化）：条件 → 谱面集（§5 启发式）→ 盖章分三段。

    四态：出图 / 牌子身份回认（牌组合文本走牌子专用渲染）/ 不适用拒绝 /
    歧义提示 / 静默（零条件——以「进度」结尾的闲聊不是查询）。
    """
    cond_text, category, page_raw = groups
    page = int(page_raw) if page_raw else 1
    await ensure_designer_rules()
    if category is None:  # 分类进度无牌子语义，不回认
        shape = _plate_shape(cond_text)
        if shape is not None:
            # 牌组合形状：合法牌走牌子进度（回认专用版式）；非法组合保持
            # 旧拒绝文案（牌单点破，如「真将」→ 真代无将牌）
            version, kind = norm_plate(shape[0]), norm_plate(shape[1])
            if not is_valid_plate(version, kind):
                await UniMessage.text(
                    f" 没有找到「{version}{kind}」牌子。{plate_kinds_hint(version)}"
                ).finish(at_sender=True)
            plates = await score_service.get_plates(
                binding, f"{version}{kind}", notify_slow=slow_notice()
            )
            await _plate_progress_overview(binding, plates, version, kind, page)
            return
    parsed = parse_combo(cond_text)
    if parsed is None:
        return
    if isinstance(parsed, ComboAmbiguity):
        await UniMessage.text(f" {parsed.message}").finish(at_sender=True)
    if bad := inapplicable(parsed, OutputKind.TABLE):
        await UniMessage.text(
            f" {'、'.join(c.label for c in bad)} 不适用于进度"
        ).finish(at_sender=True)
    await combo_progress_card(binding, parsed, cond_text, category, page)


@plate_cmd.handle()
@handle_errors("查询牌子失败", except_with_message=(UserScoreError,))
async def _(
    binding: UserBinding = SessionQueryBinding(),
    groups: tuple = RegexGroup(),
):
    version, kind = groups
    # 繁体/和制牌字先归一（正则层只负责识别）：校验/查库/渲染全按简体口径
    version, kind = norm_plate(version), norm_plate(kind)
    # 牌单按真实牌表收紧（素材包 mai/plate_version 全量实证）：舞代四牌、
    # 霸仅者、真无将、初整代无牌
    if not is_valid_plate(version, kind):
        await UniMessage.text(
            f" 没有找到「{version}{kind}」牌子。{plate_kinds_hint(version)}"
        ).finish(at_sender=True)
    # 牌子进度经条件化进度 matcher 的身份回认承接（统一入口）
    await _plate_completion_sheet(binding, version, kind, 1)


@plate_help.handle()
@handle_errors()
async def _():
    from ...constants import PLATE_KIND_ZH

    lines = ["牌子达成条件说明："]
    lines += [f"{kind}：{desc}" for kind, desc in PLATE_KIND_ZH.items()]
    lines.append("舞/霸：旧作（含 Re:MASTER 单列）全曲谱面")
    png = text_image_bytes("\n".join(lines))
    await UniMessage.image(raw=png).finish(at_sender=True)


@score_list_cmd.handle()
@handle_errors("查询失败", except_with_message=(UserScoreError,))
async def _(
    binding: UserBinding = SessionQueryBinding(),
    groups: tuple = RegexGroup(),
):
    """条件化分数列表（R5 行卡版式泛化，80/页）：条件 → 成绩集，达成率降序。"""
    cond_text, page_raw = groups
    page = parse_page(page_raw)
    await ensure_designer_rules()
    parsed = parse_combo(cond_text, numeric_level=True)
    if parsed is None:
        return
    if isinstance(parsed, ComboAmbiguity):
        await UniMessage.text(f" {parsed.message}").finish(at_sender=True)
    if bad := inapplicable(parsed, OutputKind.SCORE_LIST):
        await UniMessage.text(
            f" {'、'.join(c.label for c in bad)} 不适用于分数列表"
        ).finish(at_sender=True)
    await combo_score_list_card(binding, parsed, cond_text, page)


@update_rating.handle()
@handle_errors("生成底图失败")
async def _():
    from ...core.render import table_template

    # 进度提示必须用 send：finish 会抛 FinishedException 终止 handler，后续逻辑不再执行
    await UniMessage.text(" 正在生成定数表底图，请稍候……").send(at_sender=True)
    total, failed = await table_template.refresh_all_rating_tables(song_service)
    extra = f"；失败 {len(failed)} 项：{'、'.join(failed)}" if failed else ""
    await UniMessage.text(f" 定数表底图生成完成（{total} 谱面次）{extra}。").finish(
        at_sender=True
    )


@update_plate.handle()
@handle_errors("生成底图失败")
async def _():
    from ...core.render import table_template

    # 进度提示必须用 send：finish 会抛 FinishedException 终止 handler，后续逻辑不再执行
    await UniMessage.text(" 正在生成完成表底图，需要一些时间，请稍候……").send(
        at_sender=True
    )
    total, failed = await table_template.refresh_all_plate_tables(song_service)
    extra = f"；失败 {len(failed)} 项：{'、'.join(failed)}" if failed else ""
    await UniMessage.text(f" 完成表底图生成完成（{total} 谱面次）{extra}。").finish(
        at_sender=True
    )


# ---------------------------------------------------------------- 帮助声明

help_registry.declare(
    plugin="awmc.tables",
    title="表格",
    category="table",
    description="定数表/完成表/牌子/进度/分数列表",
    commands=[
        CommandSpec(
            matcher=ds_table_cmd,
            name="<等级>定数表",
            brief="定数网格表（如 13+定数表）",
        ),
        CommandSpec(
            matcher=score_table_cmd,
            name="<等级><评价>完成表",
            capability=Capability.SCORES_ALL,
            brief="达成度盖章完成表（@某人=代查）",
            detail="评价支持 s/fc/fs/ap 族（如 13fc完成表）。",
        ),
        CommandSpec(
            matcher=progress_cmd,
            name="<条件>进度",
            capability=Capability.SCORES_ALL,
            brief="条件化进度：总览/已完成/未完成/未游玩（@某人=代查）",
            detail=(
                "格式：<条件串>进度 [页]，条件同条件50（辉/东方/13级/紫谱/fc…），"
                "可加类别：已完成|未完成|未游玩（如 13fc进度 2、东方未完成进度）。"
                "无达标条件时按达成率 ≥80% 盖章；牌组合（真将进度）走牌子进度。"
            ),
        ),
        CommandSpec(
            matcher=plate_cmd,
            name="<版本><牌种>完成表",
            capability=Capability.PLATES,
            brief="牌子完成表（@某人=代查）",
            detail="如 真将完成表；达成条件见「牌子条件」。牌子进度已并入"
            "「<条件>进度」（真将进度）。",
        ),
        CommandSpec(
            matcher=plate_help,
            name="牌子条件",
            brief="各代牌子达成条件说明",
        ),
        CommandSpec(
            matcher=score_list_cmd,
            name="<条件>分数列表",
            capability=Capability.SCORES_ALL,
            brief="条件化成绩列表（80/页；@某人=代查）",
            detail=(
                "格式：<条件串>分数列表 [页]，条件同条件50（13级/14.5定数/紫谱/"
                "东方/fc…），按达成率降序（如 14.5分数列表、东方分数列表 2）。"
            ),
        ),
        CommandSpec(
            matcher=update_rating,
            name="更新定数表",
            scope="SUPERUSER",
            hidden=True,
            brief="预渲染全部定数表底图",
        ),
        CommandSpec(
            matcher=update_plate,
            name="更新完成表",
            scope="SUPERUSER",
            hidden=True,
            brief="预渲染全部完成表底图",
        ),
    ],
)
