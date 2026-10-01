"""表格指令入口：定数表 / 完成表 / 进度 / 牌子 / 分数列表 / 底图更新。"""

from nonebot import on_regex, on_command, on_fullmatch
from nonebot.params import RegexGroup
from nonebot.permission import SUPERUSER
from nonebot_plugin_alconna.uniseg import UniMessage

from .sheet import (
    plate_shape,
    combo_progress_card,
    combo_score_list_card,
    plate_completion_sheet,
    plate_progress_overview,
)
from ...constants import DEFAULT_THEME
from ...core.help import CommandSpec, help_registry
from ...core.combo import (
    ComboEmpty,
    OutputKind,
    ComboAmbiguity,
    plan_of,
    conds_title,
    parse_combo,
    inapplicable,
    combo_chart_entries,
    ensure_designer_rules,
)
from ...core.score import UserScoreError, score_service
from ...core.songs import song_service
from ...core.store import UserBinding
from ...core.utils import parse_page, slow_notice, handle_errors
from ...core.plates import (
    norm_plate,
    is_valid_plate,
    plate_kinds_hint,
)
from ...core.binding import SessionQueryBinding, at_tolerant
from ...core.sources import Capability
from ...core.render.tools import text_image_bytes
from ...core.render.rating_table import draw_rating_table_cond

# 表格条件化统一入口（P2-c 收编：定数表/完成表尾缀并入，触发文本逐字不变；
# 类别词/页码为尾缀附带参数）
progress_cmd = on_regex(
    at_tolerant(
        r"^(.+?)(已完成|未完成|未游玩|未开始)?(进度|完成表|定数表)\s?([0-9]+)?$"
    ),
    block=True,
)

plate_help = on_fullmatch("牌子条件", block=True)
score_list_cmd = on_regex(at_tolerant(r"^(.+?)分数列表\s?([0-9]+)?$"), block=True)
# priority=0：条件化表格 matcher（priority=1 的 `(.+?)(完成表|定数表)` 万能
# 捕获在同层先注册先跑，会吞掉「更新完成表」——「更新」的单字解析曾命中
# newness 条件；提权后 permission 不满足时非阻塞跳过，不误伤普通用户
update_rating = on_command("更新定数表", permission=SUPERUSER, block=True, priority=0)
update_plate = on_command("更新完成表", permission=SUPERUSER, block=True, priority=0)


@progress_cmd.handle()
@handle_errors("生成表格失败", except_with_message=(UserScoreError,))
async def _(
    binding: UserBinding = SessionQueryBinding(),
    groups: tuple = RegexGroup(),
):
    """条件化表格（进度/完成表/定数表统一入口，P2-c 收编）：

    牌组合文本经形状回认走牌子专用渲染（非法牌保持旧拒绝文案）；完成表/
    定数表的单等级条件走文件底图与既有版式（收编等价 + 同速），其余条件
    底图现算（不缓存，2026-09-30 拍板）。
    """
    from ...core.combo import CondType
    from ...core.render import table_template

    cond_text, category, suffix, page_raw = groups
    page = parse_page(page_raw)
    await ensure_designer_rules()

    # 牌形状回认：进度（无类别）与完成表；定数表无牌子语义不回认
    if suffix != "定数表" and category is None:
        shape = plate_shape(cond_text)
        if shape is not None:
            version, kind = norm_plate(shape[0]), norm_plate(shape[1])
            if not is_valid_plate(version, kind):
                await UniMessage.text(
                    f" 没有找到「{version}{kind}」牌子。{plate_kinds_hint(version)}"
                ).finish(at_sender=True)
            if suffix == "进度":
                plates = await score_service.get_plates(
                    binding, f"{version}{kind}", notify_slow=slow_notice()
                )
                await plate_progress_overview(binding, plates, version, kind, page)
            else:
                await plate_completion_sheet(binding, version, kind, page)
            return

    # 中文尾缀（进度/完成表/定数表）排除纯数字闲聊，裸数字等级/定数一律
    # 放行（S-8 拍板「级/定数必带仅限数字尾缀 50/40」；曾把进度语境排除在
    # 外——14+sss+进度 的「14+」被丢弃，整表退化为纯 sss+ 过滤）
    parsed = parse_combo(cond_text, numeric_level=True)
    if parsed is None:
        return
    if isinstance(parsed, ComboAmbiguity):
        await UniMessage.text(f" {parsed.message}").finish(at_sender=True)
    output = OutputKind.DS_TABLE if suffix == "定数表" else OutputKind.TABLE
    if bad := inapplicable(parsed, output):
        await UniMessage.text(
            f" {'、'.join(c.label for c in bad)} 不适用于{suffix}"
        ).finish(at_sender=True)

    if suffix == "进度":
        await combo_progress_card(binding, parsed, cond_text, category, page)
        return

    entries = await combo_chart_entries(parsed, binding)
    if isinstance(entries, ComboEmpty):
        await UniMessage.text(f" {entries.message}").finish(at_sender=True)

    # 表头 = 规范化 label 串（评级档大写等，QoL：s 强制大写）
    header = conds_title(parsed) or cond_text

    if suffix == "定数表":
        # 单等级条件走文件底图 + Level. 前缀（收编等价）；其余现算
        if len(parsed) == 1 and parsed[0].ctype is CondType.LEVEL:
            png = await table_template.rating_table_text_bytes(parsed[0].value, entries)
        else:
            png = await table_template.rating_table_cond_text_bytes(entries, header)
        await UniMessage.image(raw=png).finish(at_sender=True)

    # 完成表：单等级条件优先文件底图（同速同像素），lv15 单条件走既有三列
    # 大图管线；其余条件现算底图。checker 驱动盖章三态背景（QoL）
    checker, plan_word, _ = plan_of(parsed)
    plan = plan_word if plan_word in ("fc", "fcp", "ap", "fs", "fdx", "fsp") else None
    theme = binding.theme or DEFAULT_THEME
    scores = await score_service.get_scores_all(binding, notify_slow=slow_notice())
    level_conds = [c for c in parsed if c.ctype is CondType.LEVEL]
    # 恰一个等级条件且无其他谱面条件 → 等价旧「<等级>完成表」，走文件底图
    other_chart = [
        c for c in parsed if c.chart is not None and c.ctype is not CondType.LEVEL
    ]
    single_level = (
        level_conds[0].value if len(level_conds) == 1 and not other_chart else None
    )
    if single_level == "15":
        png = await table_template.draw_rating_table_with_fallback(
            "15",
            plan,
            scores.scores,
            entries,
            theme=theme,
            song_service=song_service,
            checker=checker,
        )
    else:
        im = await table_template.rating_table_base_image(entries, single_level)
        png = draw_rating_table_cond(
            im,
            plan,
            scores.scores,
            entries,
            # 单等级表头 = Level. 前缀 + 等级（「Level. xx」整段日文字体口径，
            # 2026-10-01）；条件版 = 规范化条件串，无前缀。表型说明「完成表」
            # 由渲染层补并整体居中
            header_text=single_level if single_level else header,
            header_prefix="Level." if single_level else None,
            theme=theme,
            checker=checker,
            # 盖章分组与底图一致：单等级+判型混合（14+sss+完成表）底图为
            # 定数节文件底图，仅跨等级条件（single_level=None）为标级大类
            by_level=single_level is None,
        )
        if png is None:
            await UniMessage.text(" 完成表底图生成失败，请稍后再试").finish(
                at_sender=True
            )
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


@plate_help.handle()
@handle_errors()
async def _():
    from ...constants import PLATE_KIND_ZH

    lines = ["牌子达成条件说明："]
    lines += [f"{kind}：{desc}" for kind, desc in PLATE_KIND_ZH.items()]
    lines.append("舞/霸：旧作（含 Re:MASTER 单列）全曲谱面")
    png = text_image_bytes("\n".join(lines))
    await UniMessage.image(raw=png).finish(at_sender=True)


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
            matcher=progress_cmd,
            name="<条件>进度|完成表|定数表",
            capability=Capability.SCORES_ALL,
            brief="条件化进度/完成表/定数表（@某人=代查）",
            detail=(
                "格式：<条件串>进度|完成表|定数表 [页]，条件同条件50"
                "（辉/东方/中二/音击/13级/紫谱/fc…）。\n"
                "进度可加类别：已完成|未完成|未游玩（如 13fc进度 2、"
                "东方未完成进度），无达标条件按达成率 ≥80% 盖章；\n"
                "完成表同款盖章（如 13fc完成表、东方完成表）；\n"
                "定数表为谱面网格（13+定数表、雪辉dx定数表）；\n"
                "牌组合（真将进度/暁将完成表）走牌子专用渲染，"
                "达成条件见「牌子条件」。"
            ),
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
