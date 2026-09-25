"""awmc.music_query：查歌子插件。

指令（对齐原版 maimaiDX）：
- `查歌 <标题> [页]`
- `定数查歌 <定数|最小 最大> [页]`
- `bpm查歌 <bpm|最小 最大> [页]`
- `曲师查歌 <曲师> [页]` / `谱师查歌 <谱师> [页]`
- `<名称>是什么歌 [页]`（别名优先，其次关键词）
- `id <数字>`
"""

import re
import asyncio
from re import Match

from nonebot import logger, on_regex
from nonebot.params import RegexMatched
from nonebot.plugin import PluginMetadata
from nonebot_plugin_uninfo import Session, UniSession
from nonebot_plugin_alconna.uniseg import UniMessage

from ...constants import CHART_TYPE_BY_PREFIX, display_song_id
from ...core.songs import song_service, prefer_type_from_raw_id
from ...core.types import Song, SongType, SongDifficultyUtage
from ...core.utils import handle_errors
from ...core.render import song as song_render
from ...core.render import jp_cover, nb_chart
from ...core.songdb import PendingSong
from ...core.binding import session_keys, binding_service
from ...core.chart_card import chart_card_bytes


def _reply(text: str) -> UniMessage:
    """at 发送者的文本回复（at 与文本之间留一个空格，保持可读性）。"""
    return UniMessage.text(f" {text}")


def _type_entries(
    songs: "list[Song]",
) -> "list[tuple[int, Song, SongType | None]]":
    """合并曲目 → 谱面类型条目列表 (查分器 id, 类型标签, 曲目, 卡片偏好)。

    NB 原版双条目语义：SD 条目 id=曲 id、DX 条目 id=曲 id+10000、宴条目
    id=6 位机台内部 id；同根 id 条目共享别名，搜索全部列出供用户按 id 选择。
    """
    entries: list[tuple[int, Song, SongType | None]] = []
    for song in songs:
        if song.difficulties.standard:
            entries.append((song.id, song, SongType.STANDARD))
        if song.difficulties.dx:
            entries.append((song.id + 10000, song, SongType.DX))
        for diff in song.get_difficulties(SongType.UTAGE):
            if isinstance(diff, SongDifficultyUtage):
                entries.append((diff.diff_id, song, None))
    return entries


__plugin_meta__ = PluginMetadata(
    name="awmc.music_query",
    description="舞萌DX 查歌：标题/定数/BPM/曲师/谱师/别名/ID",
    usage=(
        "查歌 <标题> [页]｜定数查歌 <定数> [页]｜bpm查歌｜曲师查歌｜谱师查歌｜"
        "<名称>是什么歌｜id <数字>"
    ),
    type="application",
    homepage="https://github.com/shinyashen/nonebot-plugin-awmc-helper",
)

NOT_FOUND = "没有找到这样的乐曲。\n※ 如果是别名请使用「XXX是什么歌」指令进行查询哦。"
JP_ONLY_NOTE = "此歌曲为日服限定"
"""单结果命中的日服限定标注；多结果列表用 _list_jp_note 的列表级措辞。"""

search = on_regex(r"(?i)^(定数|bpm|曲师|谱师)?查歌\s?(.*)", block=True)
search_alias_song = on_regex(r"(.+)是(?:什么|啥)歌[？?]?([0-9]+)?$", block=True)
query_chart = on_regex(r"(?i)^id\s?([0-9]+)$", block=True)


def _split_page(args: list[str]) -> tuple[str, int]:
    """末尾为纯数字时视为页数，其余整体作为关键词（支持含空格）。"""
    if len(args) >= 2 and args[-1].isdigit():
        return " ".join(args[:-1]), int(args[-1])
    return " ".join(args), 1


def _is_float(value: str) -> bool:
    try:
        float(value)
        return True
    except ValueError:
        return False


async def _banquet_card(song, utage_diff=None, jp: bool = False) -> bytes:
    """宴谱条目卡：宿主曲可能同时有普通谱（is_banquet 判定不成立），
    但条目 id ≥ 100000 时必须渲染宴会场卡（Hoshino 按 song_id ≥ 100000
    路由同语义）；日服宴曲封面在线兜底。

    ``utage_diff``：要画的宴谱（id/条目召唤传命中的那张；一个宴谱 id 只
    对应一张谱面），缺省取宿主曲第一张。``jp=True``：日服限定宿主曲
    （JP 视图对象），宴会卡不渲染国服口径的新曲标。
    """
    await jp_cover.ensure(song.id)
    diffs = [utage_diff] if utage_diff is not None else None
    return nb_chart.song_chart_banquet_info(song, diffs, jp=jp)


async def _utage_jp_only(song_id: int, diff_id: int) -> bool:
    """该张宴谱是否日服限定：国服视图无宿主曲，或宿主曲无此 diff_id。

    宿主曲在国服有普通谱不代表宴谱也在国服（悪戯センセーション DX 国服
    21004、宴[奏] 仅日服 26509）——按 diff_id 逐张判定，宿主曲级判定会
    误出日服宴谱的国服卡。
    """
    cn_song = await song_service.by_id(song_id)
    return cn_song is None or not any(
        getattr(d, "diff_id", None) == diff_id
        for d in cn_song.get_difficulties(SongType.UTAGE)
    )


async def _binding_of(session):
    return await binding_service.ensure(*session_keys(session))


async def _render_result(songs, page: int, binding=None) -> None:
    """1 首出卡片；≤5 文本；更多列表图（25/页）。"""
    if not songs:
        await _reply(NOT_FOUND).finish(at_sender=True)
    if len(songs) == 1:
        png = await chart_card_bytes(songs[0], binding)
        await UniMessage.image(raw=png).finish(at_sender=True)
    if len(songs) <= 5:
        text = "".join(f"「{display_song_id(s)}」 {s.title}\n" for s in songs)
        await _reply(text.rstrip("\n")).finish(at_sender=True)
    await (
        UniMessage.image(raw=song_render.song_list_bytes(songs, page))
        .text(f"第 {page} 页，共 {len(songs)} 首，可用「查歌 <标题> {page + 1}」翻页")
        .finish(at_sender=True)
    )


@search.handle()
@handle_errors()
async def _(session: Session = UniSession(), match: Match[str] = RegexMatched()):
    binding = await _binding_of(session)
    cmd = match.group(1)
    rest = (match.group(2) or "").strip()
    if not cmd and not rest:
        await _reply(NOT_FOUND).finish(at_sender=True)
    a_list = rest.split()

    if cmd == "定数":
        page = 1
        if len(a_list) >= 2 and _is_float(a_list[0]) and _is_float(a_list[1]):
            ds1, ds2 = float(a_list[0]), float(a_list[1])
            if len(a_list) >= 3 and a_list[2].isdigit():
                page = int(a_list[2])
        elif len(a_list) == 1 and _is_float(a_list[0]):
            ds1 = ds2 = float(a_list[0])
        else:
            await _reply(
                "定数查歌参数错误，请输入正确格式，页数为可选：\n"
                "定数查歌「定数」「页数」\n"
                "定数查歌「最小定数」「最大定数」「页数」"
            ).finish(at_sender=True)
        songs = await song_service.by_level_value(min(ds1, ds2), max(ds1, ds2))
        await _render_query_result(
            songs,
            lambda: song_service.jp_by_level_value(min(ds1, ds2), max(ds1, ds2)),
            page,
            binding,
            lambda: song_service.pending_by_level_value(min(ds1, ds2), max(ds1, ds2)),
        )
    elif cmd == "bpm":
        page = 1
        if len(a_list) >= 2 and _is_float(a_list[0]) and _is_float(a_list[1]):
            b1, b2 = float(a_list[0]), float(a_list[1])
            if len(a_list) >= 3 and a_list[2].isdigit():
                page = int(a_list[2])
        elif len(a_list) == 1 and _is_float(a_list[0]):
            b1 = b2 = float(a_list[0])
        else:
            await _reply(
                "bpm查歌参数错误，请输入正确格式，页数为可选：\n"
                "bpm查歌「bpm」「页数」\n"
                "bpm查歌「最小bpm」「最大bpm」「页数」"
            ).finish(at_sender=True)
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


def _list_jp_note(flags: list[bool]) -> str:
    """多结果列表的日服限定说明：混合列表与全日服列表措辞不同。"""
    if not any(flags):
        return ""
    return "列表中曲目均为日服限定歌曲" if all(flags) else "列表中包含日服限定歌曲"


async def _render_jp_result(songs, page: int, binding=None) -> None:
    """日服 fallback 结果：逐曲判定日服限定，混合列表只标注限定曲。

    日服视图含国服也有的曲（标题子串、日服定数口径变更等场景可命中）：
    整列表国服都有时按普通结果渲染，混合时国服曲回取国服对象。
    """
    # 逐条目日服判定须回查国服视图：去重后一次并发建 map 复用（判定与回取共用）
    unique_ids = {s.id for s in songs}
    hits = await asyncio.gather(*(song_service.by_id(i) for i in unique_ids))
    cn_songs = dict(zip(unique_ids, hits))
    flags = [cn_songs[s.id] is None for s in songs]
    if not any(flags):
        await _render_result([cn_songs[s.id] or s for s in songs], page, binding)
        return
    note = _list_jp_note(flags)
    if len(songs) == 1:
        png = await chart_card_bytes(songs[0], None, None, True)
        await _reply(JP_ONLY_NOTE).image(raw=png).finish(at_sender=True)
    if len(songs) <= 5:
        text = "".join(
            f"「{display_song_id(s)}」 {s.title}{'（日服限定）' if f else ''}\n"
            for s, f in zip(songs, flags)
        )
        await _reply(text.rstrip("\n") + "\n" + note).finish(at_sender=True)
    await (
        UniMessage.image(raw=song_render.song_list_bytes(songs, page))
        .text(f"第 {page} 页，共 {len(songs)} 首\n" + note)
        .finish(at_sender=True)
    )


async def _render_query_result(
    songs, jp_fetch, page: int, binding=None, pending_fetch=None
) -> None:
    """查询结果渲染：国服命中走普通结果，未命中走日服 fallback（Q32），
    日服也未命中走 pending 兜底（id 未收录新曲，Q33）。

    返回值驱动：fallback 未命中明确落到「未找到」，不再依赖
    ``_render_jp_result`` 内部必 finish 的控制流（避免未来加 return 路径双发）。
    """
    if not songs:
        songs = await jp_fetch()
        if songs:
            await _render_jp_result(songs, page, binding)
            return
        if pending_fetch is not None and await _render_pending_result(
            await pending_fetch()
        ):
            return
    await _render_result(songs, page, binding)


PENDING_NOTE = "※ 该曲机台 id 尚未收录，为新曲暂存信息（缺项以 - 显示）"


async def _pending_card(pending: PendingSong) -> bytes:
    """pending 临时卡：物化为临时 Song 走共享查歌卡渲染（「0 即 -」约定）。

    ID 行覆写为「ID —」（不猜 id）；分类行用 catcode 映射（未知画 -）；
    封面按 payload 的官方文件名在线拉取，缺则占位图。
    """
    from ...core.songdb import pending_to_song

    cover = None
    if pending.image_url:
        cover = await jp_cover.ensure_image(pending.image_url, pending.cover_key)
    return nb_chart.song_chart_info(
        pending_to_song(pending),
        calc=False,
        is_full=False,
        best_list=[],
        jp=True,
        id_text="ID —",
        genre_text=pending.genre_display or "-",
        cover_path=cover,
    )


async def _render_pending_result(pending: "list[PendingSong]") -> bool:
    """pending 结果渲染：单首出临时卡，多首文本列表。

    返回是否已发送（finish）；空列表返回 False 交回调用方继续兜底。
    """
    if not pending:
        return False
    if len(pending) == 1:
        png = await _pending_card(pending[0])
        await _reply(PENDING_NOTE).image(raw=png).finish(at_sender=True)
    lines = []
    for p in pending:
        charts = p.major_charts()
        ds = "、".join(
            f"{'DX' if c.is_dx else 'SD'}{c.level_id + 1} {c.level_value:.1f}"
            for c in charts
        )
        lines.append(f"「{p.title}」 {ds}" if ds else f"「{p.title}」")
    await _reply("\n".join(lines) + f"\n{PENDING_NOTE}").finish(at_sender=True)


async def _resolve_raw_id(
    raw_id: int,
) -> tuple[Song, SongType | None, bool, SongDifficultyUtage | None] | None:
    """数字 id → (song, 卡片主类型偏好, 仅日服, 宴谱或 None)；未命中返回 None。

    6 位宴谱机台 id 按 diff_id 定位**该张**宴谱（by_id 取模会错配同号普通曲），
    渲染走宴会卡；其余按查分器 id 形状推断偏好，5 位 DX 展示 id 回查国服
    对象定日服标注。别名/查歌的数字解析共用本函数。
    """
    if raw_id > 99999:
        utage_hit = await song_service.by_utage_id(raw_id)
        if utage_hit is None:
            return None
        ut_host, ut_diff = utage_hit
        # 宴谱可能日服限定（国服宿主曲无此 diff_id）：日服卡渲染口径
        jp = await _utage_jp_only(ut_host.id, ut_diff.diff_id)
        return ut_host, None, jp, ut_diff
    song = await song_service.by_id(raw_id) or await song_service.jp_by_id(raw_id)
    if song is None:
        return None
    # raw_id 可能是 DX 展示 id：按解析出的根 id 回查国服视图定标注
    cn_song = await song_service.by_id(song.id)
    return cn_song or song, prefer_type_from_raw_id(raw_id), cn_song is None, None


async def _vote_hint(name: str) -> str | None:
    """柚子投票中提示（属柚子扩展；接口不可用/未命中时返回 None）。"""
    from ...core.ext.yuzu import yuzu_client

    try:
        found = await yuzu_client.get_apply_songs(name)
    except Exception as e:
        logger.debug(f"投票提示拉取失败（不影响查询）：{e}")
        return None
    if found is None or not found.votes:
        return None
    msg = f"未找到别名为「{name}」的歌曲，但找到与此相同别名的投票：\n"
    for s in found.votes:
        msg += f"- {s.tag}\n    ID {s.song_id}: {s.apply_alias}\n"
    msg += "※ 可以使用指令「同意别名 XXXXX」进行投票"
    return msg


async def _expand_alias_entries(name: str) -> list:
    """别名解析 → 谱面类型条目（查询链 + 前缀偏好过滤 + 宴重查）。

    查询链：国服别名 → 日服别名 → 日服标题兜底（Q32）；带谱面前缀（dx/
    标准/标/宴）时定位到对应类型条目。
    """
    songs, strip_info = await song_service.by_alias_detail(name)
    if not songs:
        # 国服视图未命中 → 日服视图 fallback（Q32：日服作为国服查歌的兜底）
        songs, strip_info = await song_service.jp_by_alias_detail(name)
    if not songs:
        # 别名全网未命中：输入本身可能就是曲目名（如新曲尚无人录别名），
        # 日服标题兜底（国服侧标题按设计走「查歌」指令）——日服视图含国服
        # 也有的曲（标题子串命中，如实测 ROND），混合列表按逐曲标注区分
        songs = await song_service.jp_by_title_fuzzy(name)
    hit_word = strip_info[1].lower() if strip_info else None
    prefer_type = CHART_TYPE_BY_PREFIX.get(hit_word) if hit_word else None
    entries = _type_entries(songs)
    if strip_info:
        if prefer_type is not None:
            typed = [e for e in entries if e[2] == prefer_type]
            if typed:
                entries = typed
        elif strip_info[1] == "宴":
            ut_only = [e for e in entries if e[2] is None]
            if ut_only:
                entries = ut_only
            else:
                # 剥「宴」后命中的曲无宴谱：按关键词在含宴谱的曲中再查
                # （如「宴牛奶」的牛奶是宴曲别名而非普通曲别名）
                ut_songs = await song_service.utage_by_keyword(strip_info[0])
                if ut_songs:
                    entries = [e for e in _type_entries(ut_songs) if e[2] is None]
    return entries


def _entry_cn_flags(
    entries: list,
    cn_songs: dict[int, Song | None],
) -> list[bool]:
    """逐条目日服限定标注：国服也有的曲回取国服对象（定数口径/封面/B50 一致）。"""
    return [cn_songs[s.id] is None for _, s, _ in entries]


@search_alias_song.handle()
@handle_errors()
async def _(session: Session = UniSession(), match: Match[str] = RegexMatched()):
    binding = await _binding_of(session)
    name = match.group(1).strip()
    page = int(match.group(2) or 1)

    error_msg = (
        f"未找到别名为「{name}」的歌曲\n"
        "※ 可以使用「添加别名」指令给该乐曲添加别名\n"
        "※ 如果是歌名的一部分，请使用「查歌」指令查询哦。"
    )
    entries = await _expand_alias_entries(name)
    # SD/DX 条目同根曲共用一次查询：先去重再并发（原列表推导逐条串行且重复查）
    _unique_ids = {s.id for _, s, _ in entries}
    _hits = await asyncio.gather(*(song_service.by_id(i) for i in _unique_ids))
    cn_songs = dict(zip(_unique_ids, _hits))
    flags = _entry_cn_flags(entries, cn_songs)
    if len(entries) == 1:
        _entry_id, song, card_prefer = entries[0]
        jp = flags[0]
        if _entry_id >= 100000:
            # 宴谱条目：宿主曲即便有普通谱也渲染宴会场卡；只画命中的那张。
            # 该张可能日服限定（国服宿主曲无此 diff_id，如悪戯センセーション
            # 宴[奏]）——保留 JP 宿主对象画日服卡，不回取国服对象
            cn_song = cn_songs[song.id]
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
            song = cn_songs[song.id] or song
            png = await chart_card_bytes(song, binding, card_prefer, jp)
        # 顺序：at → 日服标注 → 卡片 → 提示语（文本不以换行开头）
        msg = _reply(JP_ONLY_NOTE) if jp else UniMessage()
        await msg.image(raw=png).text("您要找的是不是这首？").finish(at_sender=True)
    if entries:
        msg = f"找到{len(entries)}个谱面：\n"
        msg += "".join(
            f"{eid}：{s.title}{'（日服限定）' if f else ''}\n"
            for (eid, s, _), f in zip(entries, flags)
        )
        msg += "※ 请使用「id xxxxx」查询指定谱面"
        if list_note := _list_jp_note(flags):
            msg += f"\n{list_note}"
        await _reply(msg.rstrip("\n")).finish(at_sender=True)

    # 柚子投票中提示（网络失败静默跳过）
    vote_msg = await _vote_hint(name)
    if vote_msg:
        await _reply(vote_msg).finish(at_sender=True)

    # 纯数字 → ID（查分器 id 形状推断谱面类型：≤4 位 SD、5 位 DX、6 位宴）
    if name.isdigit():
        hit = await _resolve_raw_id(int(name))
        if hit is not None:
            song, prefer, jp, utage_diff = hit
            png = (
                await _banquet_card(song, utage_diff, jp)
                if utage_diff is not None
                else await chart_card_bytes(song, binding, prefer, jp)
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
        # 此别名入口不渲染宴会卡（与「id xxx」指令的口径差异属既有行为）
        png = await chart_card_bytes(song, binding, prefer, jp_only)
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
async def _(session: Session = UniSession(), match: Match[str] = RegexMatched()):
    _id = match.group(1)
    # 数字 id 解析单源 _resolve_raw_id（6 位宴 diff_id 定位 / DX 展示 id 回查 /
    # 形状推类型，与「是什么歌」别名入口同口径）
    hit = await _resolve_raw_id(int(_id))  # 正则 ^id\s?([0-9]+)$ 保证恒为数字
    if hit is None:
        await _reply(f"未找到ID为「{_id}」的乐曲").finish(at_sender=True)
    song, card_prefer, jp, utage_diff = hit
    binding = await _binding_of(session)
    png = (
        await _banquet_card(song, utage_diff, jp)
        if utage_diff is not None
        else await chart_card_bytes(song, binding, card_prefer, jp)
    )
    reply = UniMessage.image(raw=png)
    if jp:
        reply = reply.text(f"\n{JP_ONLY_NOTE}")
    await reply.finish(at_sender=True)
