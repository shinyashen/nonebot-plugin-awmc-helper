"""查歌指令入口：查歌 / 是什么歌 / id 三个 matcher 及其 handler。"""

import re
from re import Match

from nonebot import on_regex
from nonebot.params import RegexMatched
from nonebot_plugin_alconna.uniseg import UniMessage

from .render import (
    NOT_FOUND,
    JP_ONLY_NOTE,
    _reply,
    _banquet_card,
    _render_query_result,
    _render_pending_result,
)
from .resolve import (
    _vote_hint,
    _split_page,
    _resolve_raw_id,
    parse_range_args,
)
from ...constants import UTAGE_ID_BASE, display_song_id
from ...core.help import CommandSpec, help_registry
from ...core.songs import cn_song_map, song_service, entries_list_text
from ...core.store import UserBinding
from ...core.types import SongType
from ...core.utils import handle_errors
from ...core.render import song as song_render
from ...core.binding import SessionBinding
from ...core.chart_card import chart_card_bytes, resolve_card_view

search = on_regex(r"(?i)^(定数|bpm|曲师|谱师)?查歌\s?(.*)", block=True)
# 无 ^ 锚是**有意沿用** Hoshino 功能基准的宽松形态（末尾「是什么歌」即触发，
# maimaiDX commands/mai_search.py 同式；本仓其余 matcher 均 ^$ 锚定，勿「顺手
# 收紧」破坏基准口径）
search_alias_song = on_regex(r"(.+)是(?:什么|啥)歌[？?]?([0-9]+)?$", block=True)
query_chart = on_regex(r"(?i)^id\s?([0-9]+)$", block=True)


@search.handle()
@handle_errors()
async def _(
    binding: UserBinding = SessionBinding(),
    match: Match[str] = RegexMatched(),
):
    cmd = match.group(1)
    rest = (match.group(2) or "").strip()
    if not cmd and not rest:
        await _reply(NOT_FOUND).finish(at_sender=True)
    a_list = rest.split()

    if cmd == "定数":
        try:
            ds1, ds2, page = parse_range_args(a_list, "定数")
        except ValueError as e:
            await _reply(str(e)).finish(at_sender=True)
        songs = await song_service.by_level_value(min(ds1, ds2), max(ds1, ds2))
        await _render_query_result(
            songs,
            lambda: song_service.jp_by_level_value(min(ds1, ds2), max(ds1, ds2)),
            page,
            binding,
            lambda: song_service.pending_by_level_value(min(ds1, ds2), max(ds1, ds2)),
        )
    elif cmd == "bpm":
        try:
            b1, b2, page = parse_range_args(a_list, "bpm")
        except ValueError as e:
            await _reply(str(e)).finish(at_sender=True)
        songs = await song_service.by_bpm(min(b1, b2), max(b1, b2))
        await _render_query_result(
            songs,
            lambda: song_service.jp_by_bpm(min(b1, b2), max(b1, b2)),
            page,
            binding,
        )
    elif cmd == "曲师":
        if not a_list:
            await _reply("曲师查歌「曲师」「页数」").finish(at_sender=True)
        name, page = _split_page(a_list)
        songs = await song_service.by_artist(name)
        await _render_query_result(
            songs,
            lambda: song_service.jp_by_artist(name),
            page,
            binding,
        )
    elif cmd == "谱师":
        if not a_list:
            await _reply("谱师查歌「谱师」「页数」").finish(at_sender=True)
        name, page = _split_page(a_list)
        songs = await song_service.by_note_designer(name)
        await _render_query_result(
            songs,
            lambda: song_service.jp_by_note_designer(name),
            page,
            binding,
        )
    else:
        if not a_list:
            await _reply(NOT_FOUND).finish(at_sender=True)
        title, page = _split_page(a_list)
        songs = await song_service.by_title_fuzzy(title)
        await _render_query_result(
            songs,
            lambda: song_service.jp_by_title_fuzzy(title),
            page,
            binding,
            lambda: song_service.pending_by_title_fuzzy(title),
        )


@search_alias_song.handle()
@handle_errors()
async def _(
    binding: UserBinding = SessionBinding(),
    match: Match[str] = RegexMatched(),
):
    name = match.group(1).strip()
    page = int(match.group(2) or 1)

    error_msg = (
        f"未找到别名为「{name}」的歌曲\n"
        "※ 可以使用「添加别名」指令给该乐曲添加别名\n"
        "※ 如果是歌名的一部分，请使用「查歌」指令查询哦。"
    )
    entries = await song_service.entries_for_name(name)
    # 逐条目回查国服视图：日服限定判定与国服对象回取共用一份 map（core 单源）
    cn_songs = await cn_song_map([s for _, s, _ in entries])
    flags = [cn_songs[s.id] is None for _, s, _ in entries]
    if len(entries) == 1:
        _entry_id, song, card_prefer = entries[0]
        jp = flags[0]
        cn_song = cn_songs[song.id]
        if _entry_id >= UTAGE_ID_BASE:
            # 宴谱条目：宿主曲即便有普通谱也渲染宴会场卡；只画命中的那张。
            # 该张可能日服限定（国服宿主曲无此 diff_id，如悪戯センセーション
            # 宴[奏]）——保留 JP 宿主对象画日服卡，不回取国服对象
            cn_diff = (
                next(
                    (
                        d
                        for d in cn_song.get_difficulties(SongType.UTAGE)
                        if getattr(d, "diff_id", None) == _entry_id
                    ),
                    None,
                )
                if cn_song is not None
                else None
            )
            if cn_song is not None and cn_diff is not None:
                song, utage_diff, jp = cn_song, cn_diff, False
            else:
                jp = True
                utage_diff = next(
                    (
                        d
                        for d in song.get_difficulties(SongType.UTAGE)
                        if getattr(d, "diff_id", None) == _entry_id
                    ),
                    None,
                )
            png = await _banquet_card(song, utage_diff, jp)
        else:
            song = cn_song or song
            card_song, jp_card = await resolve_card_view(song, binding)
            png = await chart_card_bytes(card_song, binding, card_prefer, jp or jp_card)
        # 顺序：at → 日服标注 → 卡片 → 提示语（文本不以换行开头）
        msg = _reply(JP_ONLY_NOTE) if jp else UniMessage()
        await msg.image(raw=png).text("您要找的是不是这首？").finish(at_sender=True)
    if entries:
        msg = entries_list_text(entries, flags, hint="※ 请使用「id xxxxx」查询指定谱面")
        await _reply(msg).finish(at_sender=True)

    # 柚子投票中提示（网络失败静默跳过）
    vote_msg = await _vote_hint(name)
    if vote_msg:
        await _reply(vote_msg).finish(at_sender=True)

    # 纯数字 → ID（查分器 id 形状推断谱面类型：≤4 位 SD、5 位 DX、6 位宴）
    if name.isdigit():
        hit = await _resolve_raw_id(int(name))
        if hit is not None:
            song, prefer, jp, utage_diff = hit
            card_song, jp_card = await resolve_card_view(song, binding)
            png = (
                await _banquet_card(card_song, utage_diff, jp)
                if utage_diff is not None
                else await chart_card_bytes(card_song, binding, prefer, jp or jp_card)
            )
            note = f"\n{JP_ONLY_NOTE}" if jp and utage_diff is None else ""
            await (
                UniMessage.image(raw=png)
                .text(f"\n您要找的是不是这首？{note}")
                .finish(at_sender=True)
            )
    if idm := re.match(r"^id\s?([0-9]+)$", name, re.IGNORECASE):
        hit = await _resolve_raw_id(int(idm.group(1)))
        if hit is None:
            await _reply(f"未找到ID为「{idm.group(1)}」的乐曲").finish(at_sender=True)
        song, prefer, jp_only, _utage_diff = hit
        card_song, jp_card = await resolve_card_view(song, binding)
        # 此别名入口不渲染宴会卡（与「id xxx」指令的口径差异属既有行为）
        png = await chart_card_bytes(card_song, binding, prefer, jp_only or jp_card)
        msg = _reply(JP_ONLY_NOTE) if jp_only else UniMessage()
        await msg.image(raw=png).text("您要找的是不是这首？").finish(at_sender=True)

    # pending 兜底（Q33）：输入本身是新曲歌名（id 未收录，CN/JP 视图与
    # 别名索引均不可见）——别名投票提示之后、相似标题兜底之前出临时卡
    await _render_pending_result(await song_service.pending_by_title_fuzzy(name))

    # 标题关键词兜底
    result = await song_service.by_title_fuzzy(name)
    if not result:
        await _reply(error_msg).finish(at_sender=True)
    if len(result) <= 5:
        msg = (
            f"未找到别名为「{name}」的歌曲，但找到「{len(result)}」个相似标题的曲目：\n"
        )
        msg += "".join(f"「{display_song_id(s)}」 {s.title}\n" for s in result)
        msg += "※ 请使用「id xxxxx」查询指定曲目"
        await _reply(msg.rstrip("\n")).finish(at_sender=True)
    await (
        _reply(
            f"未找到别名为「{name}」的歌曲，但找到「{len(result)}」个相似标题的曲目：\n"
        )
        .image(raw=song_render.song_list_bytes(result, page))
        .finish(at_sender=True)
    )


@query_chart.handle()
@handle_errors()
async def _(
    binding: UserBinding = SessionBinding(),
    match: Match[str] = RegexMatched(),
):
    _id = match.group(1)
    # 数字 id 解析单源 _resolve_raw_id（6 位宴 diff_id 定位 / DX 展示 id 回查 /
    # 形状推类型，与「是什么歌」别名入口同口径）
    hit = await _resolve_raw_id(int(_id))  # 正则 ^id\s?([0-9]+)$ 保证恒为数字
    if hit is None:
        await _reply(f"未找到ID为「{_id}」的乐曲").finish(at_sender=True)
    song, card_prefer, jp, utage_diff = hit
    card_song, jp_card = await resolve_card_view(song, binding)
    png = (
        await _banquet_card(card_song, utage_diff, jp)
        if utage_diff is not None
        else await chart_card_bytes(card_song, binding, card_prefer, jp or jp_card)
    )
    reply = UniMessage.image(raw=png)
    if jp:
        reply = reply.text(f"\n{JP_ONLY_NOTE}")
    await reply.finish(at_sender=True)


# ---------------------------------------------------------------- 帮助声明

help_registry.declare(
    plugin="awmc.music_query",
    title="查歌",
    category="query",
    description="按标题/定数/BPM/曲师/谱师/别名/ID 查歌",
    commands=[
        CommandSpec(
            matcher=search,
            name="查歌",
            aliases=("定数查歌", "bpm查歌", "曲师查歌", "谱师查歌"),
            brief="按标题/定数/BPM/曲师/谱师查歌（列表 25/页）",
            detail=(
                "格式：查歌 <标题> [页]｜定数查歌 <定数> [页]\n"
                "定数查歌 <最小> <最大> [页]｜bpm查歌 <最小> <最大> [页]\n"
                "曲师查歌 <曲师> [页]（大小写不敏感）｜谱师查歌 <谱师> [页]"
            ),
        ),
        CommandSpec(
            matcher=search_alias_song,
            name="<名称>是什么歌",
            brief="别名/标题/ID 反查曲目（别名优先，出谱面卡）",
            detail="格式：<名称>是什么歌；支持别名、宴谱、纯数字 id 等形态。",
        ),
        CommandSpec(
            matcher=query_chart,
            name="id <数字>",
            brief="按 ID 出谱面卡（DX 展示 id / 宴谱 diff_id）",
        ),
    ],
)
