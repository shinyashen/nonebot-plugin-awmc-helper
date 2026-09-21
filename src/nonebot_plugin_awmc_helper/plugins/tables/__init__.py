"""awmc.tables：定数表 / 完成表 / 牌子 / 进度 / 分数列表。

指令（对齐原版 maimaiDX）：
- `<等级>定数表`（如 13+定数表）
- `<等级><评价>完成表`（如 13fc完成表 / 14sssp完成表）
- `<等级><评价>进度 [页]`（如 13fc进度）
- `<版本><牌种>完成表 / 进度`（如 真将完成表 / 樱极进度）
- `牌子条件`
- `<等级|定数>分数列表 [页]`
- `更新定数表 / 更新完成表`（SUPERUSER，预渲染底图；查询时叠加成绩）
"""

from nonebot import on_regex, on_command, on_fullmatch
from maimai_py import FCType, FSType, SongType
from nonebot.params import RegexGroup
from nonebot.plugin import PluginMetadata
from nonebot.permission import SUPERUSER
from nonebot_plugin_uninfo import Session, UniSession
from nonebot_plugin_alconna.uniseg import UniMessage

from ...config import plugin_config
from ...core.score import UserScoreError, score_service
from ...core.songs import song_service
from ...core.utils import paginate, handle_errors
from ...core.binding import binding_service
from ...core.render.table import completion_grid_bytes
from ...core.render.tools import text_to_image, image_to_bytes

__plugin_meta__ = PluginMetadata(
    name="awmc.tables",
    description="舞萌DX 完成度表格：定数表/完成表/牌子/进度/分数列表",
    usage="13+定数表｜13fc完成表｜13fc进度｜真将完成表｜牌子条件｜13+分数列表",
    type="application",
    homepage="https://github.com/shinyashen/nonebot-plugin-awmc-helper",
)

from nonebot import get_driver as _get_driver


@_get_driver().on_startup
async def _warn_missing_templates() -> None:
    """NB 方案：底图未生成时启动告警（提示 SUPERUSER 执行更新指令）。"""
    if not plugin_config.awmc_startup_tasks:
        return
    from ...core.render.table_template import plate_table_dir, rating_table_dir

    if not rating_table_dir().exists() or not any(rating_table_dir().iterdir()):
        from nonebot import logger

        logger.warning("定数表底图未生成，请 SUPERUSER 执行「更新定数表」")
    if not plate_table_dir().exists() or not any(plate_table_dir().iterdir()):
        from nonebot import logger

        logger.warning("完成表底图未生成，请 SUPERUSER 执行「更新完成表」")


# 评价计划 → 判定函数（achievement / fc / fs）
PLANS: dict[str, str] = {
    "sssp": "rate:100.5",
    "sss": "rate:100",
    "ssp": "rate:99.5",
    "ss": "rate:99",
    "sp": "rate:98",
    "s": "rate:97",
    "spp": "rate:97",
    "ap": "fc:ap",
    "fc": "fc:fc",
    "fs": "fs:fs",
    "fdx": "fs:fsd",
    "fcp": "fc:fcp",
    "fsp": "fs:fsp",
}

PLATE_CHARS = "舞霸真超檄橙晓桃樱紫堇白雪辉熊华爽煌星宙祭祝双宴镜彩丸"
PLATE_KINDS = "舞舞|将|者|极|神"

LEVEL_RE = r"([0-9]+\+?)"
DS_RE = r"([0-9]+(?:\.[0-9]+)?\+?)"
PLAN_RE = r"(sssp|sss|ssp|ss|spp|sp|s|ap|fcp|fc|fsp|fs|fdx)"

ds_table_cmd = on_regex(rf"^{LEVEL_RE}定数表$", block=True)
score_table_cmd = on_regex(rf"^{LEVEL_RE}{PLAN_RE}\+?完成表$", block=True)
progress_cmd = on_regex(rf"^{LEVEL_RE}{PLAN_RE}\+?进度\s?([0-9]+)?$", block=True)
plate_cmd = on_regex(
    rf"^([{PLATE_CHARS}])({PLATE_KINDS})(完成表|进度)\s?([0-9]+)?$",
    block=True,
)
plate_help = on_fullmatch("牌子条件", block=True)
score_list_cmd = on_regex(rf"^{DS_RE}\s?分数列表\s?([0-9]+)?$", block=True)
update_rating = on_command("更新定数表", permission=SUPERUSER, block=True)
update_plate = on_command("更新完成表", permission=SUPERUSER, block=True)


def _plan_checker(plan: str):
    """评价计划 → (判定, 说明)。"""
    kind, value = PLANS[plan].split(":")
    if kind == "rate":
        return (lambda ach, fc, fs: (ach or 0) >= float(value)), f"达成率 ≥ {value}%"
    if kind == "fc":
        order = {
            "fc": (FCType.FC, FCType.FCP, FCType.AP, FCType.APP),
            "fcp": (FCType.FCP, FCType.AP, FCType.APP),
            "ap": (FCType.AP, FCType.APP),
        }
        allow = order[value]
        return (lambda ach, fc, fs: fc in allow), {
            "fc": "Full Combo",
            "fcp": "Full Combo+",
            "ap": "All Perfect",
        }[value]
    allow_fs = {
        "fs": (FSType.FS, FSType.FSP, FSType.FSD, FSType.FSDP),
        "fsp": (FSType.FSP, FSType.FSD, FSType.FSDP),
        "fsd": (FSType.FSD, FSType.FSDP),
    }
    if kind == "fs":
        allow = allow_fs[value]
        return (lambda ach, fc, fs: fs in allow), "Full Sync+"
    allow = allow_fs[value]
    names = {"fdx": "Full Sync DX"}
    return (lambda ach, fc, fs: fs in allow), names.get(value, value.upper())


@ds_table_cmd.handle()
@handle_errors("生成定数表失败")
async def _(groups: tuple = RegexGroup()):
    (level,) = groups
    entries = []
    for song in await song_service.get_all():
        for d in song.get_difficulties():
            if d.type != SongType.UTAGE and d.level == level:
                entries.append((d.level_value, song, d))
    if not entries:
        await UniMessage.text(f"没有找到等级为「{level}」的谱面").finish(at_sender=True)
    entries.sort(key=lambda x: -x[0])
    lines = [f"定数表 {level}（共 {len(entries)} 谱面）"]
    for ds, song, d in entries:
        type_abbr = "DX" if d.type == SongType.DX else "SD"
        lines.append(f"{ds:.1f}  {type_abbr} 「{song.id}」{song.title}")
    # 分列文本过长，直接文本转图
    png = image_to_bytes(text_to_image("\n".join(lines), size=20))
    await UniMessage.image(raw=png).finish(at_sender=True)


@score_table_cmd.handle()
@handle_errors("生成完成表失败", except_with_message=(UserScoreError,))
async def _(session: Session = UniSession(), groups: tuple = RegexGroup()):
    level, plan = groups
    checker, plan_name = _plan_checker(plan)
    binding = await binding_service.ensure(
        session.platform or "unknown", str(session.user.id)
    )
    scores = await score_service.get_scores_all(binding)
    score_map = {(s.id, s.type, s.level_index): s for s in scores.scores}
    items = []
    for song in await song_service.get_all():
        for d in song.get_difficulties():
            if d.type == SongType.UTAGE or d.level != level:
                continue
            sc = score_map.get((song.id, d.type, d.level_index))
            done = checker(
                sc.achievements if sc else None,
                sc.fc if sc else None,
                sc.fs if sc else None,
            )
            state = "done" if done else ("played" if sc else "new")
            items.append((song, d, state))
    if not items:
        await UniMessage.text(f"没有找到等级为「{level}」的谱面").finish(at_sender=True)
    done_count = sum(1 for _, _, st in items if st == "done")

    # Q7 NB 方案：底图存在则叠加印章，否则回退实时网格
    from ...core.render import table_template

    state_list = [(song, d, st == "done") for song, d, st in items]
    png = await table_template.overlay_rating(
        level, plan_name, state_list, page=1, per_page=len(state_list)
    )
    if png is None:
        png = completion_grid_bytes(
            f"{level} {plan_name} 完成表（{done_count}/{len(items)}）", items
        )
    await UniMessage.image(raw=png).finish(at_sender=True)


@progress_cmd.handle()
@handle_errors("生成进度失败", except_with_message=(UserScoreError,))
async def _(session: Session = UniSession(), groups: tuple = RegexGroup()):
    level, plan, page_raw = groups
    page = int(page_raw) if page_raw else 1
    checker, plan_name = _plan_checker(plan)
    binding = await binding_service.ensure(
        session.platform or "unknown", str(session.user.id)
    )
    scores = await score_service.get_scores_all(binding)
    score_map = {(s.id, s.type, s.level_index): s for s in scores.scores}
    done_list, remain_list, new_list = [], [], []
    for song in await song_service.get_all():
        for d in song.get_difficulties():
            if d.type == SongType.UTAGE or d.type != SongType.DX or d.level != level:
                continue
            sc = score_map.get((song.id, d.type, d.level_index))
            done = checker(
                sc.achievements if sc else None,
                sc.fc if sc else None,
                sc.fs if sc else None,
            )
            (done_list if done else (remain_list if sc else new_list)).append(song)
    total = len(done_list) + len(remain_list) + len(new_list)
    if total == 0:
        await UniMessage.text(f"没有找到等级为「{level}」的 DX 谱面").finish(
            at_sender=True
        )
    page_data, total_pages = paginate(remain_list or new_list, page, 80)
    lines = [
        f"{level} {plan_name} 进度：{len(done_list)}/{total}",
        f"未完成 {len(remain_list)}（未游玩 {len(new_list)}），"
        f"第 {min(max(page, 1), total_pages)}/{total_pages} 页",
    ]
    lines += [f"「{s.id}」{s.title}" for s in page_data]
    png = image_to_bytes(text_to_image("\n".join(lines), size=20))
    await UniMessage.image(raw=png).finish(at_sender=True)


@plate_cmd.handle()
@handle_errors("查询牌子失败", except_with_message=(UserScoreError,))
async def _(session: Session = UniSession(), groups: tuple = RegexGroup()):
    version, kind, mode, page_raw = groups
    binding = await binding_service.ensure(
        session.platform or "unknown", str(session.user.id)
    )
    plates = await score_service.get_plates(binding, f"{version}{kind}")
    if mode == "完成表":
        cleared = await plates.get_cleared()
        cleared_keys = set()
        for plate in cleared:
            for level_index in plate.levels:
                cleared_keys.add((plate.song.id, SongType.STANDARD, level_index))
                cleared_keys.add((plate.song.id, SongType.DX, level_index))
        total_levels = await plates.count_all()
        cleared_levels = await plates.count_cleared()

        # Q7 NB 方案：底图存在则叠加印章
        from ...core.render import table_template

        all_items = []
        for song in await song_service.get_all():
            for d in song.get_difficulties():
                if d.type == SongType.UTAGE:
                    continue
                all_items.append((song, d))
        png = await table_template.overlay_plate(
            version, kind, cleared_keys, all_items, cleared_levels
        )
        if png is not None:
            await UniMessage.image(raw=png).finish(at_sender=True)
        # 回退：实时网格（仅达成项展示）
        items = []
        for plate in cleared:
            for level_index in sorted(plate.levels, key=lambda x: x.value):
                d = next(
                    (
                        x
                        for x in plate.song.get_difficulties()
                        if x.level_index == level_index
                    ),
                    None,
                )
                if d is not None:
                    items.append((plate.song, d, "done"))
        png = completion_grid_bytes(
            f"{version}{kind} 完成表（{cleared_levels}/{total_levels}）", items
        )
        await UniMessage.image(raw=png).finish(at_sender=True)
    # 进度
    total_levels = await plates.count_all()
    cleared_levels = await plates.count_cleared()
    remained = await plates.get_remained()
    lines = [
        f"{version}{kind} 进度：{cleared_levels}/{total_levels}"
        f"（{cleared_levels / total_levels * 100:.1f}%）"
        if total_levels
        else "暂无谱面",
    ]
    page = int(page_raw) if page_raw else 1
    remain_flat = [
        (p.song, li) for p in remained for li in sorted(p.levels, key=lambda x: x.value)
    ]
    page_data, total_pages = paginate(remain_flat, page, 60)
    real = min(max(page, 1), total_pages)
    lines.append(f"未达成 {len(remain_flat)} 个，第 {real}/{total_pages} 页：")
    lines += [f"「{s.id}」{s.title} {li.name}" for s, li in page_data]
    png = image_to_bytes(text_to_image("\n".join(lines), size=20))
    await UniMessage.image(raw=png).finish(at_sender=True)


@plate_help.handle()
@handle_errors()
async def _():
    from ...constants import PLATE_KIND_ZH

    lines = ["牌子达成条件说明："]
    lines += [f"{kind}：{desc}" for kind, desc in PLATE_KIND_ZH.items()]
    lines.append("舞/霸：旧作（含 Re:MASTER 单列）全曲谱面")
    png = image_to_bytes(text_to_image("\n".join(lines)))
    await UniMessage.image(raw=png).finish(at_sender=True)


@score_list_cmd.handle()
@handle_errors("查询失败", except_with_message=(UserScoreError,))
async def _(session: Session = UniSession(), groups: tuple = RegexGroup()):
    ds_raw, page_raw = groups
    page = int(page_raw) if page_raw else 1
    binding = await binding_service.ensure(
        session.platform or "unknown", str(session.user.id)
    )
    scores = await score_service.get_scores_all(binding)
    if "." in ds_raw:  # 定数
        ds = float(ds_raw)
        matched = [s for s in scores.scores if abs(s.level_value - ds) < 0.05]
        title = f"定数 {ds_raw} 分数列表"
    else:
        matched = [s for s in scores.scores if s.level == ds_raw]
        title = f"{ds_raw} 分数列表"
    matched.sort(key=lambda s: s.achievements or 0, reverse=True)
    if not matched:
        await UniMessage.text("没有找到符合条件的成绩").finish(at_sender=True)
    from ...core.render.best50 import score_list_bytes

    png = score_list_bytes(title, matched, page)
    await UniMessage.image(raw=png).finish(at_sender=True)


@update_rating.handle()
@handle_errors("生成底图失败")
async def _():
    from ...constants import LEVEL_LIST
    from ...core.render import table_template

    await UniMessage.text("正在生成定数表底图，请稍候……").finish(at_sender=True)
    total = 0
    for lv in LEVEL_LIST[6:]:  # lv7-15
        total += await table_template.generate_rating_template(lv, song_service)
    await UniMessage.text(f"定数表底图生成完成（{total} 谱面次）。").finish(
        at_sender=True
    )


@update_plate.handle()
@handle_errors("生成底图失败")
async def _():
    from ...core.render import table_template

    await UniMessage.text("正在生成完成表底图，需要一些时间，请稍候……").finish(
        at_sender=True
    )
    kinds = ("将", "者", "极", "神", "舞舞")
    count = 0
    for v in PLATE_CHARS:
        if v in ("舞", "霸"):
            continue
        for k in kinds:
            count += await table_template.generate_plate_template(v, k, song_service)
    await UniMessage.text(f"完成表底图生成完成（{count} 谱面次）。").finish(
        at_sender=True
    )
