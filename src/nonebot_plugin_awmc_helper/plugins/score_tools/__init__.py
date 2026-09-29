"""awmc.score_tools：分数线 / 推分推荐 / 水鱼 RA 排名。

指令（对齐原版）：
- `分数线 <难度色><id> <线>` / `分数线 帮助`
- `我要上N分` / `我要在<等级>上加N分` / `mai什么加分`（带推分后缀）
- `查看排名 [页|用户名]` / `我的排名`
"""

import re
import math
import time

from nonebot import on_regex, on_command
from nonebot.params import CommandArg, RegexGroup
from nonebot.plugin import PluginMetadata
from nonebot.adapters import Message
from nonebot_plugin_uninfo import Session, UniSession
from nonebot_plugin_alconna.uniseg import UniMessage

from ...core.ext import divingfish as df_ext
from ...constants import LEVEL_INDEX_ZH, COLOR_TO_LEVEL_INDEX
from ...core.calc import score_line, min_ds_of_ra, rise_recommend, rise_candidates
from ...core.help import CommandPage, CommandSpec, page_entries, help_registry
from ...core.score import UserScoreError, score_service
from ...core.songs import song_service
from ...core.store import UserBinding
from ...core.types import SongType
from ...core.utils import paginate, parse_page, slow_notice, handle_errors
from ...core.binding import SessionBinding, service_display
from ...core.render.tools import text_image_bytes

__plugin_meta__ = PluginMetadata(
    name="awmc.score_tools",
    description="舞萌DX 分数线/推分/排名",
    usage=(
        "分数线 <难度色><id> <线>｜我要上N分｜我要在<等级>上加N分｜"
        "查看排名 [页|用户名]｜我的排名"
    ),
    type="application",
    homepage="https://github.com/shinyashen/nonebot-plugin-awmc-helper",
)

score_line_cmd = on_command("分数线", block=True)
rise_score = on_regex(r"^我要在?([0-9]+\+?)?[上加\+]([0-9]+)?分$", block=True)
rating_ranking = on_command("查看排名", aliases={"查看排行"}, block=True)
my_rating_ranking = on_command("我的排名", block=True)


@score_line_cmd.handle()
@handle_errors()
async def _(message: Message = CommandArg()):
    args = message.extract_plain_text().strip()
    if args in ("帮助", ""):
        # 文案单源：渲染注册表里的「分数线」详情页（旧 SCORE_LINE_HELP 已迁入）
        await UniMessage.text(
            page_entries(help_registry, CommandPage(spec=_score_line_spec))[0]
        ).finish(at_sender=True)
    m = re.search(r"([绿黄红紫白])\s?([0-9]+)", args)
    if not m:
        await UniMessage.text(" 格式错误，输入「分数线 帮助」以查看帮助信息").finish(
            at_sender=True
        )
    level_index = COLOR_TO_LEVEL_INDEX[m.group(1)]
    chart_id = int(m.group(2))
    try:
        line = float(args.split()[-1])
    except ValueError:
        await UniMessage.text(" 格式错误，输入「分数线 帮助」以查看帮助信息").finish(
            at_sender=True
        )
    song = await song_service.by_id(chart_id)
    if song is None:
        await UniMessage.text(f" 未找到ID为「{chart_id}」的乐曲").finish(at_sender=True)
    diff = song.get_difficulty(SongType.DX, level_index) or song.get_difficulty(
        SongType.STANDARD, level_index
    )
    if diff is None:
        await UniMessage.text(" 该乐曲没有这个等级").finish(at_sender=True)
    result = score_line(diff, line)
    if result is None:
        await UniMessage.text(" 分数线参数有误（应为 0-100 之间）").finish(
            at_sender=True
        )
    msg = (
        f"{song.title}「{LEVEL_INDEX_ZH[level_index]}」\n"
        f"分数线「{line}%」\n允许的最多「TAP」「GREAT」数量为\n"
        f"「{result['tap_great']:.2f}」(每个-{result['per_tap_pct']:.4f}%),\n"
        f"「BREAK」50落(一共「{result['breaks']}」个)\n"
        f"等价于「{result['break_50_tap']:.3f}」个「TAP」"
        f"「GREAT」(-{result['break_50_pct']:.4f}%)"
    )
    await UniMessage.text(" " + msg).finish(at_sender=True)


@rise_score.handle()
@handle_errors("推分推荐失败，请稍后再试", except_with_message=(UserScoreError,))
async def _(
    session: Session = UniSession(),
    binding: UserBinding = SessionBinding(),
    groups: tuple = RegexGroup(),
):
    from ...core.render.score import DrawScore

    level, target_raw = groups
    target = int(target_raw) if target_raw else 1
    bests = await score_service.get_b50(binding, notify_slow=slow_notice())

    # 候选：指定等级时按等级过滤，否则按 B50 末位 RA 推算定数区间。
    # 末位 RA 口径刻意与 random_song 随机推分不同：此处取 b35+b15 全体 min
    # （推荐列表按版本分侧排序，用全局入线基准），见 rise_candidates 注
    lowest_ra = min(
        (s.dx_rating or 0 for s in bests.scores_b35 + bests.scores_b15), default=0
    )
    if level:
        candidates = rise_candidates(await song_service.get_all(), level=level)
    else:
        min_ds = math.ceil(min_ds_of_ra(lowest_ra + target) * 10) / 10
        candidates = rise_candidates(
            await song_service.get_all(), ds_range=(min_ds, min_ds + 1)
        )
    rec = rise_recommend(bests.scores, candidates, level=level, target=target)
    if not rec:
        await UniMessage.text(" 没有找到可以提升 RA 的曲目，换一个目标试试吧").finish(
            at_sender=True
        )
    # R3：NB DrawScore 行卡版式——双栏按版本划分（用户口径）：旧版本 =
    # 当前版本以前全部谱面（b35 侧），新版本 = 当前版本谱面（b15 侧）
    old_rec = [r for r in rec if r["side"] == "old"]
    new_rec = [r for r in rec if r["side"] == "new"]
    service = service_display(binding)
    card = DrawScore(960, service=service)
    png = card.draw_rise(old_rec, new_rec, 960)
    await UniMessage.image(raw=png).finish(at_sender=True)


@rating_ranking.handle()
@handle_errors("查询失败，请稍后再试")
async def _(message: Message = CommandArg()):
    args = message.extract_plain_text().strip()
    users = await df_ext.rating_ranking()
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    # 精确用户名优先于页码（水鱼用户名可以是纯数字；榜单已在手，判定零开销）
    found = next(
        (
            (i + 1, u)
            for i, u in enumerate(users)
            if args and u.username.lower() == args.lower()
        ),
        None,
    )
    if found is not None:
        rank, u = found
        msg = (
            f"截止至「{now}」玩家「{u.username}」\n"
            f"在查分器已注册用户 RA 排行第「{rank}」位（RA {u.ra}）"
        )
        png = text_image_bytes(msg)
        await UniMessage.image(raw=png).finish(at_sender=True)
    if args and not args.isdigit():
        await UniMessage.text(" 未在查分器排行榜中找到该玩家。").finish(at_sender=True)
    page = parse_page(args)
    page_data, total = paginate(users, page, 50)
    if not page_data:
        await UniMessage.text(f" 页码超出范围（共 {total} 页）").finish(at_sender=True)
    lines = [f"水鱼 RA 排行榜（第 {page}/{total} 页，共 {len(users)} 人）"]
    lines += [
        f"{(page - 1) * 50 + i + 1:5d}  {u.username[:16]}  {u.ra}"
        for i, u in enumerate(page_data)
    ]
    png = text_image_bytes("\n".join(lines), size=22)
    await UniMessage.image(raw=png).finish(at_sender=True)


@my_rating_ranking.handle()
@handle_errors("查询失败，请稍后再试", except_with_message=(UserScoreError,))
async def _(session: Session = UniSession(), binding: UserBinding = SessionBinding()):
    # 数据源适配：仅水鱼支持（其余数据源由适配器基类给统一「暂不支持」文案）
    hit = await score_service.get_my_ranking(binding, notify_slow=slow_notice())
    if hit is None:
        await UniMessage.text(" 未在查分器排行榜中找到您的记录。").finish(
            at_sender=True
        )
    entry, rank = hit
    await UniMessage.text(
        f"您的 Rating 为「{entry.ra}」，排名第「{rank}」名"
    ).finish(at_sender=True)


# ---------------------------------------------------------------- 帮助声明

_score_line_spec = CommandSpec(
    matcher=score_line_cmd,
    name="分数线",
    brief="查询指定谱面达标分数线允许的容错",
    detail=(
        "此功能为查找某首歌分数线设计。\n"
        "命令格式：分数线「难度+歌曲id」「分数线」\n"
        "命令将返回分数线允许的「TAP」「GREAT」容错，\n"
        "以及「BREAK」50落等价的「TAP」「GREAT」数。\n"
        "以下为「TAP」「GREAT」的对应表：\n"
        "        GREAT / GOOD / MISS\n"
        "TAP         1 / 2.5  / 5\n"
        "HOLD        2 / 5    / 10\n"
        "SLIDE       3 / 7.5  / 15\n"
        "TOUCH       1 / 2.5  / 5\n"
        "BREAK       5 / 12.5 / 25 (外加200落)"
    ),
    example="分数线 紫799 100",
)

help_registry.declare(
    plugin="awmc.score_tools",
    title="工具",
    category="tools",
    description="分数线/推分推荐/水鱼 RA 排名",
    commands=[
        _score_line_spec,
        CommandSpec(
            matcher=rise_score,
            name="我要上N分",
            aliases=("我要在<等级>上加N分",),
            brief="基于全体最低 RA 反推定数区间的推分推荐",
            detail="格式：我要上N分 / 我要在<等级>上加N分（如 我要在13+上5分）。",
        ),
        CommandSpec(
            matcher=rating_ranking,
            name="查看排名",
            aliases=("查看排行",),
            brief="水鱼 RA 排行榜（50/页；参数为用户名时精确报名次）",
        ),
        CommandSpec(
            matcher=my_rating_ranking,
            name="我的排名",
            scope="仅水鱼数据源",
            brief="在 RA 榜单中定位自己的名次",
        ),
    ],
)
