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
from nonebot.params import RegexMatched
from nonebot.plugin import PluginMetadata
from nonebot_plugin_alconna.uniseg import UniMessage

from ...core.songs import song_service
from ...core.utils import handle_errors
from ...core.render import song as song_render

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


async def _render_result(songs, page: int) -> None:
    """1 首出卡片；≤5 文本；更多列表图（25/页）。"""
    if not songs:
        await UniMessage.text(NOT_FOUND).finish()
    if len(songs) == 1:
        await UniMessage.image(raw=song_render.song_card_bytes(songs[0])).finish()
    if len(songs) <= 5:
        text = "".join(f"「{s.id}」 {s.title}\n" for s in songs)
        await UniMessage.text(text.rstrip("\n")).finish()
    await (
        UniMessage.image(raw=song_render.song_list_bytes(songs, page))
        .text(f"\n第 {page} 页，共 {len(songs)} 首，可用「查歌 <标题> {page + 1}」翻页")
        .finish()
    )


@search.handle()
@handle_errors()
async def _(match: Match[str] = RegexMatched()):
    cmd = match.group(1)
    rest = (match.group(2) or "").strip()
    if not cmd and not rest:
        await UniMessage.text(NOT_FOUND).finish()
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
            ).finish()
        songs = await song_service.by_level_value(min(ds1, ds2), max(ds1, ds2))
        await _render_result(songs, page)
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
            ).finish()
        songs = await song_service.by_bpm(min(b1, b2), max(b1, b2))
        await _render_result(songs, page)
    elif cmd == "曲师":
        if not a_list:
            await UniMessage.text("曲师查歌「曲师」「页数」").finish()
        name, page = _split_page(a_list)
        songs = await song_service.by_artist(name)
        await _render_result(songs, page)
    elif cmd == "谱师":
        if not a_list:
            await UniMessage.text("谱师查歌「谱师」「页数」").finish()
        name, page = _split_page(a_list)
        songs = await song_service.by_note_designer(name)
        await _render_result(songs, page)
    else:
        if not a_list:
            await UniMessage.text(NOT_FOUND).finish()
        title, page = _split_page(a_list)
        songs = await song_service.by_title_fuzzy(title)
        await _render_result(songs, page)


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
async def _(match: Match[str] = RegexMatched()):
    name = match.group(1).strip()
    page = int(match.group(2) or 1)

    error_msg = (
        f"未找到别名为「{name}」的歌曲\n"
        "※ 可以使用「添加别名」指令给该乐曲添加别名\n"
        "※ 如果是歌名的一部分，请使用「查歌」指令查询哦。"
    )
    # 别名（柚子 + 本地）
    songs = await song_service.by_alias(name)
    if len(songs) == 1:
        await (
            UniMessage.image(raw=song_render.song_card_bytes(songs[0]))
            .text("\n您要找的是不是这首？")
            .finish()
        )
    if len(songs) > 1:
        msg = f"找到{len(songs)}个相同别名的曲目：\n"
        msg += "".join(f"{s.id}：{s.title}\n" for s in songs)
        msg += "※ 请使用「id xxxxx」查询指定曲目"
        await UniMessage.text(msg.rstrip("\n")).finish()

    # 柚子投票中提示（网络失败静默跳过）
    vote_msg = await _vote_hint(name)
    if vote_msg:
        await UniMessage.text(vote_msg).finish()

    # 纯数字 → ID；id12345 → ID
    if name.isdigit() and (song := await song_service.by_id(int(name))):
        await (
            UniMessage.image(raw=song_render.song_card_bytes(song))
            .text("\n您要找的是不是这首？")
            .finish()
        )
    if idm := re.match(r"^id([0-9]+)$", name, re.IGNORECASE):
        song = await song_service.by_id(int(idm.group(1)))
        if not song:
            await UniMessage.text(f"未找到ID为「{idm.group(1)}」的乐曲").finish()
        await (
            UniMessage.image(raw=song_render.song_card_bytes(song))
            .text("\n您要找的是不是这首？")
            .finish()
        )

    # 标题关键词兜底
    result = await song_service.by_title_fuzzy(name)
    if not result:
        await UniMessage.text(error_msg).finish()
    if len(result) <= 5:
        msg = (
            f"未找到别名为「{name}」的歌曲，但找到「{len(result)}」个相似标题的曲目：\n"
        )
        msg += "".join(f"「{s.id}」 {s.title}\n" for s in result)
        msg += "※ 请使用「id xxxxx」查询指定曲目"
        await UniMessage.text(msg.rstrip("\n")).finish()
    await (
        UniMessage.text(
            f"未找到别名为「{name}」的歌曲，但找到「{len(result)}」个相似标题的曲目：\n"
        )
        .image(raw=song_render.song_list_bytes(result, page))
        .finish()
    )


@query_chart.handle()
@handle_errors()
async def _(match: Match[str] = RegexMatched()):
    _id = match.group(1)
    song = await song_service.by_id(int(_id)) if _id.isdigit() else None
    if not song:
        await UniMessage.text(f"未找到ID为「{_id}」的乐曲").finish()
    await UniMessage.image(raw=song_render.song_card_bytes(song)).finish()
