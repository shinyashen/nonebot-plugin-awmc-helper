"""表格指令入口：定数表 / 完成表 / 进度 / 牌子 / 分数列表 / 底图更新。"""

from nonebot import on_regex, on_command, on_fullmatch
from nonebot.params import RegexGroup
from nonebot.permission import SUPERUSER
from nonebot_plugin_uninfo import Session, UniSession
from nonebot_plugin_alconna.uniseg import UniMessage

from .sheet import (
    PLANS,
    _plan_checker,
    _level_entries,
    _plate_completion_sheet,
    _plate_progress_overview,
)
from ...constants import PLATE_CHARS, DEFAULT_THEME, chart_display_id
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
from ...core.binding import (
    SessionBinding,
    service_display,
)
from ...core.render.score import DrawScore, score_list_height
from ...core.render.tools import text_image_bytes

# 牌种正则（牌种并集取自 core.plates 单源；正则交替最长优先，
# 防将来新增牌种被单字牌种遮蔽；繁体/和制牌种字一并入交替）
PLATE_KIND_ALT = "|".join(
    sorted((*PLATE_KINDS, *PLATE_KIND_ALIAS_CHARS), key=len, reverse=True)
)

LEVEL_RE = r"([0-9]+\+?)"
DS_RE = r"([0-9]+(?:\.[0-9]+)?\+?)"
PLAN_RE = r"(sssp|sss|ssp|ss|sp|s|ap|fcp|fc|fsp|fs|fdx)"

ds_table_cmd = on_regex(rf"^{LEVEL_RE}定数表$", block=True)
score_table_cmd = on_regex(rf"^{LEVEL_RE}{PLAN_RE}\+?完成表$", block=True)
progress_cmd = on_regex(
    rf"^{LEVEL_RE}{PLAN_RE}\+?(已完成|未完成|未开始|未游玩)?进度\s?([0-9]+)?$",
    block=True,
)
plate_cmd = on_regex(
    rf"^([{PLATE_CHARS}{PLATE_VERSION_ALIAS_CHARS}])({PLATE_KIND_ALT})(完成表|进度)\s?([0-9]+)?$",
    block=True,
)
plate_help = on_fullmatch("牌子条件", block=True)
score_list_cmd = on_regex(rf"^{DS_RE}\s?分数列表\s?([0-9]+)?$", block=True)
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
    session: Session = UniSession(),
    binding: UserBinding = SessionBinding(),
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
    session: Session = UniSession(),
    binding: UserBinding = SessionBinding(),
    groups: tuple = RegexGroup(),
):
    """等级进度（R4，NB DrawScore.draw_plan/draw_category 版式）。

    - `13fc进度`：三段总览（已完成 30/未完成 30/未游玩 100 网格）；
    - `13fc已完成进度 [页]` / `未完成进度 [页]`：80/页成绩行卡；
    - `13fc未游玩进度`：未游玩封面网格。
    """
    level, plan, category, page_raw = groups
    page = int(page_raw) if page_raw else 1
    checker = _plan_checker(plan)
    scores = await score_service.get_scores_all(binding, notify_slow=slow_notice())
    score_map = {(s.id, s.type, s.level_index): s for s in scores.scores}

    completed: list = []
    unfinished: list = []
    notplayed: list[tuple[int, int, float]] = []
    for song, d in await _level_entries(level):
        # NB by_plan 含 SD+DX 全部谱面（与本插件完成表口径一致），宴谱除外
        sc = score_map.get((song.id, d.type, d.level_index))
        if sc is None:
            # 未游玩网格显示游戏内 per-type id（DX 曲 10231 形状，NB 同款）
            notplayed.append(
                (chart_display_id(song, d), d.level_index.value, d.level_value)
            )
        elif checker(sc.achievements, sc.fc, sc.fs):
            completed.append(sc)
        else:
            unfinished.append(sc)
    total = len(completed) + len(unfinished) + len(notplayed)
    if total == 0:
        await UniMessage.text(f" 没有找到等级为「{level}」的谱面").finish(
            at_sender=True
        )

    # NB 排序：按计划类型取值降序（fc/fs 枚举值越大越好，rate 按达成率）
    kind = PLANS[plan].split(":")[0]

    def _sort_key(sc):
        if kind == "rate":
            return sc.achievements or 0
        if kind == "fc":
            return sc.fc.value if sc.fc else -1
        return sc.fs.value if sc.fs else -1

    completed.sort(key=_sort_key, reverse=True)
    unfinished.sort(key=_sort_key, reverse=True)
    notplayed.sort(key=lambda x: x[2], reverse=True)

    service = service_display(binding)

    def played_rows(count: int) -> int:
        return max(4, -(-count // 5))

    if category is None:
        # 三段总览（comp_limit 语义对齐 NB：仅完成时放宽到 60）
        comp_limit = 60 if not unfinished and not notplayed else 30
        c_y = played_rows(len(completed[:comp_limit])) * 109 + 140
        u_y = played_rows(len(unfinished[:30])) * 109 + 140
        n_y = max(4, -(-len(notplayed[:100]) // 20)) * 65 + 140
        card = DrawScore(150 + c_y + u_y + n_y, service=service)
        png = card.draw_plan(
            level, completed, c_y, unfinished, u_y, notplayed, plan, comp_limit
        )
    elif category in ("已完成", "未完成"):
        data = completed if category == "已完成" else unfinished
        total_pages = max(1, -(-(len(data)) // 80))
        real = min(max(page, 1), total_pages)
        display = data[(real - 1) * 80 : real * 80]
        y_size = played_rows(len(display)) * 109
        card = DrawScore(240 + y_size + 120, service=service)
        png = card.draw_category(
            "completed" if category == "已完成" else "unfinished",
            data,
            real,
            total_pages,
        )
    else:
        y_size = max(4, -(-len(notplayed) // 20)) * 65
        card = DrawScore(max(240 + y_size + 120, 600), service=service)
        png = card.draw_category("notplayed", notplayed)
    await UniMessage.image(raw=png).finish(at_sender=True)


@plate_cmd.handle()
@handle_errors("查询牌子失败", except_with_message=(UserScoreError,))
async def _(
    session: Session = UniSession(),
    binding: UserBinding = SessionBinding(),
    groups: tuple = RegexGroup(),
):
    version, kind, mode, page_raw = groups
    # 繁体/和制牌字先归一（正则层只负责识别）：校验/查库/渲染全按简体口径
    version, kind = norm_plate(version), norm_plate(kind)
    # 牌单按真实牌表收紧（素材包 mai/plate_version 全量实证）：舞代四牌、
    # 霸仅者、真无将、初整代无牌
    if not is_valid_plate(version, kind):
        await UniMessage.text(
            f" 没有找到「{version}{kind}」牌子。{plate_kinds_hint(version)}"
        ).finish(at_sender=True)
    page = parse_page(page_raw)
    plates = await score_service.get_plates(
        binding, f"{version}{kind}", notify_slow=slow_notice()
    )
    if mode == "完成表":
        await _plate_completion_sheet(binding, version, kind, page)
        return
    await _plate_progress_overview(binding, plates, version, kind, page)


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
    session: Session = UniSession(),
    binding: UserBinding = SessionBinding(),
    groups: tuple = RegexGroup(),
):
    """分数列表（R5，NB DrawScore.draw_score_list 行卡版式，80/页）。"""
    ds_raw, page_raw = groups
    page = parse_page(page_raw)
    scores = await score_service.get_scores_all(binding, notify_slow=slow_notice())
    if "." in ds_raw:  # 定数
        ds = float(ds_raw)
        matched = [s for s in scores.scores if abs(s.level_value - ds) < 0.05]
        title = ds_raw
    else:
        matched = [s for s in scores.scores if s.level == ds_raw]
        title = ds_raw
    matched.sort(key=lambda s: s.achievements or 0, reverse=True)
    if not matched:
        await UniMessage.text(" 没有找到符合条件的成绩").finish(at_sender=True)
    end_page = max(1, -(-len(matched) // 80))
    real = min(max(page, 1), end_page)
    # NB 高度公式已下沉 core（pc 列表等第三方扩展共用）
    plc = score_list_height(len(matched), real, end_page)
    service = service_display(binding)
    card = DrawScore(280 + plc, service=service)
    png = card.draw_score_list(title, matched, real, end_page)
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
