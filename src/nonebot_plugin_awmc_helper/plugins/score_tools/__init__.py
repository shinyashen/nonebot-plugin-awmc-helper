"""awmc.score_tools：分数线 / 推分推荐 / 水鱼 RA 排名。

指令：
- `分数线 <难度色><id/别名/曲名> <线>` / `分数线 帮助`（图片出分数线计算卡，
  2026-10-10 升级：查歌解析链 + 等效 GREAT TAP 口径，原「难度色+id」连写
  形态兼容）
- `我要上N分` / `我要在<等级>上加N分` / `mai什么加分`（带推分后缀）
- `查看排名 [页|用户名]` / `我的排名`
"""

import math
import time

from nonebot import on_regex, on_command
from nonebot.params import CommandArg, RegexGroup
from nonebot.plugin import PluginMetadata
from nonebot.adapters import Message
from nonebot_plugin_alconna.uniseg import UniMessage

from .resolve import split_args
from ...core.ext import divingfish as df_ext
from ...constants import UTAGE_ID_BASE
from ...core.calc import (
    score_line,
    min_ds_of_ra,
    rise_recommend,
    rise_candidates,
)
from ...core.help import CommandPage, CommandSpec, page_entries, help_registry
from ...core.score import UserScoreError, score_service
from ...core.songs import cn_song_map, song_service, chart_of_color, entries_list_text
from ...core.store import UserBinding
from ...core.utils import paginate, parse_page, slow_notice, handle_errors
from ...core.binding import SessionBinding, service_display
from ...core.sources import Capability
from ...core.chart_card import resolve_card_view, song_lookup_reply
from ...core.render.tools import text_image_bytes

_COLOR_HINT = "※ 分数线需指定难度色：分数线 <绿/黄/红/紫/白><id/别名> <线>"

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
async def _(
    binding: UserBinding = SessionBinding(),
    message: Message = CommandArg(),
):
    args = message.extract_plain_text().strip()
    if args in ("帮助", ""):
        # 文案单源：渲染注册表里的「分数线」详情页（旧 SCORE_LINE_HELP 已迁入）
        entry = page_entries(help_registry, CommandPage(spec=_score_line_spec))[0]
        assert isinstance(entry, str)  # 指令详情页恒为纯文本节点
        await UniMessage.text(entry).finish(at_sender=True)
    level_index, candidates, line = split_args(args)
    if level_index is None or line is None:
        await UniMessage.text(
            " 格式错误：分数线 <难度色><id/别名/曲名> <线>\n"
            "例：分数线 紫799 100.5（难度色：绿黄红紫白）"
        ).finish(at_sender=True)

    # 难度色归属决策（服务器合并别名库实证：358 条颜色开头别名中 22 条剥色
    # 后命中异曲，如 绿9→ケロ⑨destiny、白银→銀のめぐり——剥色命中不可盲信）：
    # 剥色/整串两候选都解析后按根 id 交集判定——
    #   都命中且有交集 → 首字符是难度色，用剥色结果按色过滤谱面；
    #   都命中但无交集 / 仅整串命中 → 首字符属于别名（如 白雪、绿9），
    #     用整串结果走「是什么歌」同款回复 + 缺色提示；
    #   仅剥色命中 → 首字符是难度色（紫琪露诺 形态）。
    # 数字候选（紫799）按 id 解析优先（旧「色+id」格式兼容，id 无别名歧义）。
    resolved: tuple | None = None
    hit_stripped = False

    async def _pick_chart(entries, line_index):
        """条目按难度色过滤 → 唯一 (曲, 类型, jp, None) 或列表文案（多谱）。"""
        from ...core.types import SongType

        by_key: dict[tuple, tuple] = {}
        for entry_id, entry_song, entry_type in entries:
            if entry_id >= UTAGE_ID_BASE:
                continue  # 宴谱条目不参与（无难度色）
            prefer = entry_type or SongType.DX
            if chart_of_color(entry_song, line_index, prefer=prefer) is None:
                continue  # 该类型无此难度的谱面
            by_key.setdefault(
                (entry_song.id, entry_type), (entry_song, entry_type, False, None)
            )
        if len(by_key) == 1:
            return next(iter(by_key.values())), None
        songs = [s for _, s, _ in entries]
        cn_songs = await cn_song_map(songs)
        flags = [cn_songs[s.id] is None for s in songs]
        return None, entries_list_text(
            entries,
            flags,
            hint="※ 请使用「分数线 <难度色><id> <线>」指定谱面",
        )

    async def _no_color_reply(entries):
        """无难度色但有命中：「是什么歌」同款回复 + 缺色提示（回复单源复用）。"""
        msg = await song_lookup_reply(entries, binding, extra_note=_COLOR_HINT)
        await msg.finish(at_sender=True)

    async def _same_song(entries_a, entries_b) -> bool:
        """两组条目是否存在同曲（根 id 交集，别名按根 id 合并）。"""
        ids_a = {s.id for _, s, _ in entries_a}
        ids_b = {s.id for _, s, _ in entries_b}
        return bool(ids_a & ids_b)

    if level_index is not None and candidates and candidates[0]:
        stripped, full = candidates[0], candidates[-1]
        if stripped.isdigit():
            # 数字候选按 id 解析优先（旧「色+id」格式兼容）；未命中回退整串
            # （绿9 类别名剥色后是数字、id 不存在，整串才是真别名）
            found = await song_service.resolve_raw_chart(int(stripped))
            if found is not None:
                song, _shape, jp, utage_diff = found
                resolved, hit_stripped = (song, None, jp, utage_diff), True
            elif full != stripped:
                entries = await song_service.entries_for_name(full, cn_title=False)
                if entries:
                    await _no_color_reply(entries)
        else:
            stripped_entries = await song_service.entries_for_name(
                stripped, cn_title=False
            )
            full_entries = await song_service.entries_for_name(full, cn_title=False)
            if stripped_entries and full_entries:
                if await _same_song(stripped_entries, full_entries):
                    # 同曲：首字符是难度色（白阳炎 → 阳+白），按色过滤谱面；
                    # 该色无谱面/双谱歧义 → 列条目引导指定 id
                    chart, list_msg = await _pick_chart(stripped_entries, level_index)
                    if chart is not None:
                        resolved, hit_stripped = chart, True
                    else:
                        await UniMessage.text(" " + list_msg).finish(at_sender=True)
                else:
                    # 色字属于别名（绿9/白银 类冲突）：整串优先、无难度色
                    await _no_color_reply(full_entries)
            elif stripped_entries:
                chart, list_msg = await _pick_chart(stripped_entries, level_index)
                if chart is not None:
                    resolved, hit_stripped = chart, True
                else:
                    await UniMessage.text(" " + list_msg).finish(at_sender=True)
            elif full_entries:
                await _no_color_reply(full_entries)
    else:
        full = candidates[-1] if candidates else ""
        entries = (
            await song_service.entries_for_name(full, cn_title=False) if full else []
        )
        if entries:
            await _no_color_reply(entries)
    if resolved is None and not hit_stripped:
        await UniMessage.text(
            f" 未找到「{'」或「'.join(candidates)}」对应的乐曲，可用「查歌」确认后再试"
        ).finish(at_sender=True)

    song, prefer, jp, utage_diff = resolved
    if utage_diff is not None:
        await UniMessage.text(" 宴会场谱面没有难度色，不支持分数线查询").finish(
            at_sender=True
        )
    # NET 等日服视图用户换日服曲对象出卡（定数/版本口径一致，与查歌卡同路由）
    card_song, jp_card = await resolve_card_view(song, binding)
    from ...core.types import SongType

    prefer_type = SongType.STANDARD if prefer == SongType.STANDARD else None
    diff = chart_of_color(card_song, level_index, prefer=prefer_type or SongType.DX)
    if diff is None:
        await UniMessage.text(" 该乐曲没有这个等级").finish(at_sender=True)
    result = score_line(diff, line)
    if result is None:
        await UniMessage.text(" 分数线参数有误（应为 0-100 之间）").finish(
            at_sender=True
        )
    from ...core.render.score_line import score_line_card

    png = score_line_card(
        card_song,
        diff,
        line,
        result,
        theme=binding.theme or "prism_plus",
        jp=jp or jp_card,
    )
    await UniMessage.image(raw=png).finish(at_sender=True)


@rise_score.handle()
@handle_errors("推分推荐失败，请稍后再试", except_with_message=(UserScoreError,))
async def _(
    binding: UserBinding = SessionBinding(),
    groups: tuple = RegexGroup(),
):
    from ...core.render.score import DrawScore

    level, target_raw = groups
    target = int(target_raw) if target_raw else 1
    bests = await score_service.get_b50(binding, notify_slow=slow_notice())

    # 候选池跟随数据源视图（NET 用户 = 日服曲库 + 日服定数口径，修复原
    # 「国服候选池 × 日服成绩」的口径混用）。候选：指定等级时按等级过滤，
    # 否则按 B50 末位 RA 推算定数区间。
    # 末位 RA 口径刻意与 random_song 随机推分不同：此处取 b35+b15 全体 min
    # （推荐列表按版本分侧排序，用全局入线基准），见 rise_candidates 注
    pool = (
        await song_service.jp_all()
        if score_service.view_of(binding.service) == "jp"
        else await song_service.get_all()
    )
    lowest_ra = min(
        (s.dx_rating or 0 for s in bests.scores_b35 + bests.scores_b15), default=0
    )
    if level:
        candidates = rise_candidates(pool, level=level)
    else:
        min_ds = math.ceil(min_ds_of_ra(lowest_ra + target) * 10) / 10
        candidates = rise_candidates(pool, ds_range=(min_ds, min_ds + 1))
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
async def _(binding: UserBinding = SessionBinding()):
    # 数据源适配：仅水鱼支持（其余数据源由适配器基类给统一「暂不支持」文案）
    hit = await score_service.get_my_ranking(binding, notify_slow=slow_notice())
    if hit is None:
        await UniMessage.text(" 未在查分器排行榜中找到您的记录。").finish(
            at_sender=True
        )
    entry, rank = hit
    await UniMessage.text(f"您的 Rating 为「{entry.ra}」，排名第「{rank}」名").finish(
        at_sender=True
    )


# ---------------------------------------------------------------- 帮助声明

_score_line_spec = CommandSpec(
    matcher=score_line_cmd,
    name="分数线",
    brief="查询指定谱面达标分数线允许的容错（图片出卡）",
    detail=(
        "此功能为查询某谱面达到目标达成率的容错。\n"
        "命令格式：分数线「难度色」「id/别名/曲名」「分数线」\n"
        "难度色：绿/黄/红/紫/白（与查询键可连写，如 紫799 / 紫琪露诺）。\n"
        "输出「等效 GREAT TAP 数」：1 个 = 1 个 TAP 从 Critical Perfect\n"
        "掉到 GREAT 的损失（100 基础分），总预算与旧版「允许的 TAP GREAT\n"
        "数量」同值；判定表覆盖全部音符类型与 BREAK 七档\n"
        "（50落/100落/G-1/G-2/G-3/GOOD/MISS，含 CP 额外分折算）。\n"
        "对应表（等效 GREAT TAP）：\n"
        "        GREAT / GOOD / MISS\n"
        "TAP·TOUCH  1 / 2.5  / 5\n"
        "HOLD       2 / 5    / 10\n"
        "SLIDE      3 / 7.5  / 15"
    ),
    example="分数线 紫799 100.5",
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
            capability=Capability.B50,
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
            # 适用性标注（仅水鱼数据源）由 capability 经注册表自动派生
            capability=Capability.MY_RANKING,
            brief="在 RA 榜单中定位自己的名次",
        ),
    ],
)
