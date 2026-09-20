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
from maimai_py import SongType
from nonebot.params import CommandArg, RegexGroup
from nonebot.plugin import PluginMetadata
from nonebot.adapters import Message
from nonebot_plugin_uninfo import Session, UniSession
from nonebot_plugin_alconna.uniseg import UniMessage

from ...core.ext import divingfish as df_ext
from ...constants import LEVEL_INDEX_ZH, COLOR_TO_LEVEL_INDEX
from ...core.calc import score_line, rise_recommend
from ...core.score import UserScoreError, score_service
from ...core.songs import song_service
from ...core.utils import paginate, handle_errors
from ...core.binding import binding_service
from ...core.render.tools import text_to_image, image_to_bytes

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

SCORE_LINE_HELP = (
    "此功能为查找某首歌分数线设计。\n"
    "命令格式：分数线「难度+歌曲id」「分数线」\n"
    "例如：分数线 紫799 100\n"
    "命令将返回分数线允许的「TAP」「GREAT」容错，\n"
    "以及「BREAK」50落等价的「TAP」「GREAT」数。\n"
    "以下为「TAP」「GREAT」的对应表：\n"
    "        GREAT / GOOD / MISS\n"
    "TAP         1 / 2.5  / 5\n"
    "HOLD        2 / 5    / 10\n"
    "SLIDE       3 / 7.5  / 15\n"
    "TOUCH       1 / 2.5  / 5\n"
    "BREAK       5 / 12.5 / 25 (外加200落)"
)

score_line_cmd = on_command("分数线", block=True)
rise_score = on_regex(r"^我要在?([0-9]+\+?)?[上加\+]([0-9]+)?分$", block=True)
rating_ranking = on_command("查看排名", aliases={"查看排行"}, block=True)
my_rating_ranking = on_command("我的排名", block=True)


@score_line_cmd.handle()
@handle_errors()
async def _(message: Message = CommandArg()):
    args = str(message).strip()
    if args in ("帮助", ""):
        png = image_to_bytes(text_to_image(SCORE_LINE_HELP))
        await UniMessage.image(raw=png).finish()
    m = re.search(r"([绿黄红紫白])\s?([0-9]+)", args)
    if not m:
        await UniMessage.text("格式错误，输入「分数线 帮助」以查看帮助信息").finish()
    level_index = COLOR_TO_LEVEL_INDEX[m.group(1)]
    chart_id = int(m.group(2))
    try:
        line = float(args.split()[-1])
    except ValueError:
        await UniMessage.text("格式错误，输入「分数线 帮助」以查看帮助信息").finish()
    song = await song_service.by_id(chart_id)
    if song is None:
        await UniMessage.text(f"未找到ID为「{chart_id}」的乐曲").finish()
    diff = song.get_difficulty(SongType.DX, level_index) or song.get_difficulty(
        SongType.STANDARD, level_index
    )
    if diff is None:
        await UniMessage.text("该乐曲没有这个等级").finish()
    result = score_line(diff, line)
    if result is None:
        await UniMessage.text("分数线参数有误（应为 0-100 之间）").finish()
    msg = (
        f"{song.title}「{LEVEL_INDEX_ZH[level_index]}」\n"
        f"分数线「{line}%」\n允许的最多「TAP」「GREAT」数量为\n"
        f"「{result['tap_great']:.2f}」(每个-{result['per_tap_pct']:.4f}%),\n"
        f"「BREAK」50落(一共「{result['breaks']}」个)\n"
        f"等价于「{result['break_50_tap']:.3f}」个「TAP」"
        f"「GREAT」(-{result['break_50_pct']:.4f}%)"
    )
    await UniMessage.text(msg).finish()


@rise_score.handle()
@handle_errors("推分推荐失败，请稍后再试")
async def _(session: Session = UniSession(), groups: tuple = RegexGroup()):
    from ...core.songs import song_service

    level, target_raw = groups
    target = int(target_raw) if target_raw else 1
    binding = await binding_service.ensure(
        session.platform or "unknown", str(session.user.id)
    )
    try:
        bests = await score_service.get_b50(binding)
    except UserScoreError as e:
        await UniMessage.text(str(e)).finish()

    # 候选：指定等级时按等级过滤，否则按 B50 末位 RA 推算定数区间
    lowest_ra = min(
        (s.dx_rating or 0 for s in bests.scores_b35 + bests.scores_b15), default=0
    )
    if level:
        candidates = [
            s
            for s in await song_service.get_all()
            if any(d.level == level for d in s.get_difficulties())
        ]
    else:
        min_ds = math.ceil((lowest_ra + target) / 22.4 * 10) / 10
        candidates = [
            s
            for s in await song_service.get_all()
            if any(min_ds <= d.level_value <= min_ds + 1 for d in s.get_difficulties())
        ]
    rec = rise_recommend(bests.scores, candidates, level=level, target=target)
    if not rec:
        await UniMessage.text("没有找到可以提升 RA 的曲目，换一个目标试试吧").finish()

    lines = [f"当前 B50 最低 RA {lowest_ra}，推荐曲目（目标 +{target}）："]
    for r in rec:
        song, diff = r["song"], r["diff"]
        type_abbr = "DX" if diff.type == SongType.DX else "SD"
        lines.append(
            f"「{song.id}」{song.title}\n"
            f"  {type_abbr} {diff.level}（{diff.level_value:.1f}）"
            f" → 打到 {r['achievements']:.1f}%（{r['rate']}）"
            f" RA {r['new_ra']}（+{r['gain']}）"
        )
    png = image_to_bytes(text_to_image("\n".join(lines), size=24))
    await UniMessage.image(raw=png).finish()


@rating_ranking.handle()
@handle_errors("查询失败，请稍后再试")
async def _(message: Message = CommandArg()):
    args = str(message).strip()
    users = await df_ext.rating_ranking()
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    if args and not args.isdigit():  # 用户名查询
        found = next(
            (
                (i + 1, u)
                for i, u in enumerate(users)
                if u.username.lower() == args.lower()
            ),
            None,
        )
        if found is None:
            await UniMessage.text("未在查分器排行榜中找到该玩家。").finish()
        rank, u = found
        msg = (
            f"截止至「{now}」玩家「{u.username}」\n"
            f"在查分器已注册用户 RA 排行第「{rank}」位（RA {u.ra}）"
        )
        png = image_to_bytes(text_to_image(msg))
        await UniMessage.image(raw=png).finish()
    page = int(args) if args.isdigit() else 1
    page_data, total = paginate(users, page, 50)
    if not page_data:
        await UniMessage.text(f"页码超出范围（共 {total} 页）").finish()
    real_page = min(max(page, 1), total)
    lines = [f"水鱼 RA 排行榜（第 {real_page}/{total} 页，共 {len(users)} 人）"]
    lines += [
        f"{(real_page - 1) * 50 + i + 1:5d}  {u.username[:16]}  {u.ra}"
        for i, u in enumerate(page_data)
    ]
    png = image_to_bytes(text_to_image("\n".join(lines), size=22))
    await UniMessage.image(raw=png).finish()


@my_rating_ranking.handle()
@handle_errors("查询失败，请稍后再试", except_with_message=(UserScoreError,))
async def _(session: Session = UniSession()):
    from ...core.binding import SERVICE_DIVINGFISH

    binding = await binding_service.ensure(
        session.platform or "unknown", str(session.user.id)
    )
    if binding.service != SERVICE_DIVINGFISH:
        await UniMessage.text("水鱼排行榜仅支持水鱼数据源（数据源 0）查询").finish()
    ident = binding_service.identifier_or_none(binding)
    if ident is None or (ident.username is None and ident.qq is None):
        await UniMessage.text("请先绑定水鱼查分器后再查询排名").finish()
    # query/player 响应含 username（DivingFishPlayer.name），qq 查询同样可用
    player = await score_service.get_player(binding)
    username = player.name
    users = await df_ext.rating_ranking()
    for i, u in enumerate(users):
        if u.username.lower() == username.lower():
            await UniMessage.text(
                f"您的 Rating 为「{u.ra}」，排名第「{i + 1}」名"
            ).finish()
    await UniMessage.text("未在查分器排行榜中找到您的记录。").finish()
