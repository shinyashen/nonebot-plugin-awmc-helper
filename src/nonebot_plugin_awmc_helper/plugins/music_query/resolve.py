"""查歌解析链：数字 id / 别名 → 谱面类型条目。

指令无关的解析编排：查分器 id 形状推断、宴谱按 diff_id 定位、别名查询链
（国服 → 日服 → 标题兜底）与谱面前缀过滤。
"""

from nonebot import logger

from ...constants import CHART_TYPE_BY_PREFIX
from ...core.songs import (
    song_service,
    chart_entries_many,
    prefer_type_from_raw_id,
)
from ...core.types import Song, SongType, SongDifficultyUtage


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
    标准/标/宴）时定位到对应类型条目。条目单源 core ``chart_entries``
    （SD/DX/宴三族 id 语义与其升序排列均以它为准）。
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
    entries = chart_entries_many(songs)
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
                    entries = [e for e in chart_entries_many(ut_songs) if e[2] is None]
    return entries


def _entry_cn_flags(
    entries: list,
    cn_songs: dict[int, Song | None],
) -> list[bool]:
    """逐条目日服限定标注：国服也有的曲回取国服对象（定数口径/封面/B50 一致）。"""
    return [cn_songs[s.id] is None for _, s, _ in entries]
