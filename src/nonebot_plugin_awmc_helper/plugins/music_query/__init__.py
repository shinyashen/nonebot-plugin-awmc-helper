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
from re import Match

from nonebot import on_regex
from maimai_py import Song, SongType
from nonebot.params import RegexMatched
from nonebot.plugin import PluginMetadata
from maimai_py.models import SongDifficultyUtage
from nonebot_plugin_uninfo import Session, UniSession
from nonebot_plugin_alconna.uniseg import UniMessage

from ...core.score import UserScoreError, score_service
from ...core.songs import song_service
from ...core.utils import handle_errors
from ...core.render import song as song_render
from ...core.render import nb_chart
from ...core.binding import binding_service

# 谱面前缀 → 卡片主类型（宴 前缀不改变卡片，宴曲本就走宴谱卡分支）
_PREFIX_TO_TYPE = {
    "dx": SongType.DX,
    "标准": SongType.STANDARD,
    "标": SongType.STANDARD,
}


def _prefer_from_raw_id(raw_id: int) -> SongType | None:
    """查分器 id 形状 → 卡片主类型：≤4 位 SD、5 位 DX；6 位宴不改变卡片。"""
    if raw_id > 99999:
        return None
    return SongType.DX if raw_id > 9999 else SongType.STANDARD


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


async def _chart_card(song, binding, prefer_type=None, jp: bool = False) -> bytes:
    """谱面卡：绑定时拉 B50 嵌成绩与加分预测（Q3 选项 B，对齐 NB 版）。

    ``prefer_type``：别名带谱面前缀（标准/标 → SD，dx → DX）时指定卡片主类型。
    ``jp=True``：日服视图渲染（日服 logo；不嵌国服 B50——谱面与定数可能不同，
    混算无意义）。
    """
    from maimai_py import SongType

    if nb_chart.is_banquet(song):
        return nb_chart.song_chart_banquet_info(song)
    calc, is_full, best_list = False, False, []
    theme = "prism_plus"
    if binding is not None and not jp:
        ident = binding_service.identifier_or_none(binding)
        if ident is not None:
            try:
                bests = await score_service.get_b50(binding)
                prefer_dx = (
                    prefer_type == SongType.STANDARD and not song.difficulties.standard
                ) or prefer_type != SongType.STANDARD
                major_dx = prefer_dx and bool(song.difficulties.dx)
                side_type = SongType.DX if major_dx else SongType.STANDARD
                best_list = [s for s in bests.scores if s.type == side_type]
                is_full = len(best_list) >= (15 if major_dx else 35)
                calc = True
                theme = binding.theme or "prism_plus"
            except UserScoreError:
                pass
    return nb_chart.song_chart_info(
        song, calc, is_full, best_list, theme, prefer_type, jp
    )


async def _binding_of(session):
    return await binding_service.ensure(
        str(session.platform or "unknown"), str(session.user.id)
    )


async def _render_result(songs, page: int, binding=None) -> None:
    """1 首出卡片；≤5 文本；更多列表图（25/页）。"""
    if not songs:
        await UniMessage.text(NOT_FOUND).finish(at_sender=True)
    if len(songs) == 1:
        png = await _chart_card(songs[0], binding)
        await UniMessage.image(raw=png).finish(at_sender=True)
    if len(songs) <= 5:
        text = "".join(f"「{s.id}」 {s.title}\n" for s in songs)
        await UniMessage.text(text.rstrip("\n")).finish(at_sender=True)
    await (
        UniMessage.image(raw=song_render.song_list_bytes(songs, page))
        .text(f"\n第 {page} 页，共 {len(songs)} 首，可用「查歌 <标题> {page + 1}」翻页")
        .finish(at_sender=True)
    )


@search.handle()
@handle_errors()
async def _(session: Session = UniSession(), match: Match[str] = RegexMatched()):
    binding = await _binding_of(session)
    cmd = match.group(1)
    rest = (match.group(2) or "").strip()
    if not cmd and not rest:
        await UniMessage.text(NOT_FOUND).finish(at_sender=True)
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
            await UniMessage.text(
                "定数查歌参数错误，请输入正确格式，页数为可选：\n"
                "定数查歌「定数」「页数」\n"
                "定数查歌「最小定数」「最大定数」「页数」"
            ).finish(at_sender=True)
        songs = await song_service.by_level_value(min(ds1, ds2), max(ds1, ds2))
        await _render_result(songs, page, binding)
    elif cmd == "bpm":
        page = 1
        if len(a_list) >= 2 and _is_float(a_list[0]) and _is_float(a_list[1]):
            b1, b2 = float(a_list[0]), float(a_list[1])
            if len(a_list) >= 3 and a_list[2].isdigit():
                page = int(a_list[2])
        elif len(a_list) == 1 and _is_float(a_list[0]):
            b1 = b2 = float(a_list[0])
        else:
            await UniMessage.text(
                "bpm查歌参数错误，请输入正确格式，页数为可选：\n"
                "bpm查歌「bpm」「页数」\n"
                "bpm查歌「最小bpm」「最大bpm」「页数」"
            ).finish(at_sender=True)
        songs = await song_service.by_bpm(min(b1, b2), max(b1, b2))
        await _render_result(songs, page, binding)
    elif cmd == "曲师":
        if not a_list:
            await UniMessage.text("曲师查歌「曲师」「页数」").finish(at_sender=True)
        name, page = _split_page(a_list)
        songs = await song_service.by_artist(name)
        await _render_result(songs, page, binding)
    elif cmd == "谱师":
        if not a_list:
            await UniMessage.text("谱师查歌「谱师」「页数」").finish(at_sender=True)
        name, page = _split_page(a_list)
        songs = await song_service.by_note_designer(name)
        await _render_result(songs, page, binding)
    else:
        if not a_list:
            await UniMessage.text(NOT_FOUND).finish(at_sender=True)
        title, page = _split_page(a_list)
        songs = await song_service.by_title_fuzzy(title)
        await _render_result(songs, page, binding)


async def _vote_hint(name: str) -> str | None:
    """柚子投票中提示（属柚子扩展；接口不可用/未命中时返回 None）。"""
    from ...core.ext.yuzu import yuzu_client

    try:
        found = await yuzu_client.get_apply_songs(name)
    except Exception:
        return None
    if found is None or not found.votes:
        return None
    msg = f"未找到别名为「{name}」的歌曲，但找到与此相同别名的投票：\n"
    for s in found.votes:
        msg += f"- {s.tag}\n    ID {s.song_id}: {s.apply_alias}\n"
    msg += "※ 可以使用指令「同意别名 XXXXX」进行投票"
    return msg


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
    # 别名（柚子 + 落雪 + 本地，去前缀合并）：展开为谱面类型条目（NB 原版
    # 双条目语义）——同根 id 的标准/DX/宴条目共享别名，搜索应全部列出供选择；
    # 带谱面前缀（dx/标准/标）时自动定位到对应类型条目，无前缀不设偏好
    songs, strip_info = await song_service.by_alias_detail(name)
    jp_mode = False
    if not songs:
        # 国服视图未命中 → 日服视图 fallback（Q32：日服作为国服查歌的兜底）
        songs, strip_info = await song_service.jp_by_alias_detail(name)
        jp_mode = bool(songs)
    prefer_type = _PREFIX_TO_TYPE.get(strip_info[1]) if strip_info else None
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
    jp_note = "\n此歌曲为日服限定" if jp_mode else ""
    if len(entries) == 1:
        _entry_id, song, card_prefer = entries[0]
        png = await _chart_card(song, binding, card_prefer, jp_mode)
        await (
            UniMessage.image(raw=png)
            .text(f"\n您要找的是不是这首？{jp_note}")
            .finish(at_sender=True)
        )
    if entries:
        msg = f"找到{len(entries)}个谱面：\n"
        msg += "".join(f"{eid}：{song.title}\n" for eid, song, _ in entries)
        msg += "※ 请使用「id xxxxx」查询指定谱面"
        if jp_mode:
            msg += jp_note
        await UniMessage.text(msg.rstrip("\n")).finish(at_sender=True)

    # 柚子投票中提示（网络失败静默跳过）
    vote_msg = await _vote_hint(name)
    if vote_msg:
        await UniMessage.text(vote_msg).finish(at_sender=True)

    # 纯数字 → ID（查分器 id 形状推断谱面类型：≤4 位 SD、5 位 DX、6 位宴）
    if name.isdigit():
        raw_id = int(name)
        song = await song_service.by_id(raw_id) or await song_service.jp_by_id(raw_id)
        if song:
            jp_hit = not await song_service.by_id(raw_id)
            note = "\n此歌曲为日服限定" if jp_hit else ""
            png = await _chart_card(song, binding, _prefer_from_raw_id(raw_id), jp_hit)
            await (
                UniMessage.image(raw=png)
                .text(f"\n您要找的是不是这首？{note}")
                .finish(at_sender=True)
            )
    if idm := re.match(r"^id([0-9]+)$", name, re.IGNORECASE):
        raw_id = int(idm.group(1))
        song = await song_service.by_id(raw_id) or await song_service.jp_by_id(raw_id)
        if not song:
            await UniMessage.text(f"未找到ID为「{idm.group(1)}」的乐曲").finish(
                at_sender=True
            )
        jp_hit = not await song_service.by_id(raw_id)
        note = "\n此歌曲为日服限定" if jp_hit else ""
        png = await _chart_card(song, binding, _prefer_from_raw_id(raw_id), jp_hit)
        await (
            UniMessage.image(raw=png)
            .text(f"\n您要找的是不是这首？{note}")
            .finish(at_sender=True)
        )

    # 标题关键词兜底
    result = await song_service.by_title_fuzzy(name)
    if not result:
        await UniMessage.text(error_msg).finish(at_sender=True)
    if len(result) <= 5:
        msg = (
            f"未找到别名为「{name}」的歌曲，但找到「{len(result)}」个相似标题的曲目：\n"
        )
        msg += "".join(f"「{s.id}」 {s.title}\n" for s in result)
        msg += "※ 请使用「id xxxxx」查询指定曲目"
        await UniMessage.text(msg.rstrip("\n")).finish(at_sender=True)
    await (
        UniMessage.text(
            f"未找到别名为「{name}」的歌曲，但找到「{len(result)}」个相似标题的曲目：\n"
        )
        .image(raw=song_render.song_list_bytes(result, page))
        .finish(at_sender=True)
    )


@query_chart.handle()
@handle_errors()
async def _(session: Session = UniSession(), match: Match[str] = RegexMatched()):
    _id = match.group(1)
    raw_id = int(_id) if _id.isdigit() else None
    song = await song_service.by_id(raw_id) if raw_id else None
    jp = False
    if song is None and raw_id:
        song = await song_service.jp_by_id(raw_id)  # 国服 miss → 日服 fallback
        jp = song is not None
    if not song:
        await UniMessage.text(f"未找到ID为「{_id}」的乐曲").finish(at_sender=True)
    binding = await _binding_of(session)
    png = await _chart_card(song, binding, _prefer_from_raw_id(raw_id or 0), jp)
    reply = UniMessage.image(raw=png)
    if jp:
        reply = reply.text("\n此歌曲为日服限定")
    await reply.finish(at_sender=True)
