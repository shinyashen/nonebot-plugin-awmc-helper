"""查歌解析链：数字 id / 别名 → 谱面类型条目。

指令无关的解析编排：查分器 id 形状推断、宴谱按 diff_id 定位、别名查询链。
别名/曲名 → 谱面条目的链条（含前缀收敛）在 core ``song_service.entries_for_name``
（查分 minfo 共用，格式与语义单一来源）。
"""

from nonebot import logger

from ...constants import UTAGE_ID_BASE
from ...core.songs import song_service, prefer_type_from_raw_id
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


def parse_range_args(a_list: list[str], label: str) -> "tuple[float, float, int]":
    """数值范围查歌参数解析（定数/bpm 两分支同构，报错文案按 label 定制）。

    形态：「值」（单值=上下限相同）或「最小 最大」+ 可选页数；不合法抛
    ``ValueError``（文案面向用户，handler 转 finish）。
    """
    page = 1
    if len(a_list) >= 2 and _is_float(a_list[0]) and _is_float(a_list[1]):
        v1, v2 = float(a_list[0]), float(a_list[1])
        if len(a_list) >= 3 and a_list[2].isdigit():
            page = int(a_list[2])
    elif len(a_list) == 1 and _is_float(a_list[0]):
        v1 = v2 = float(a_list[0])
    else:
        raise ValueError(
            f"{label}查歌参数错误，请输入正确格式，页数为可选：\n"
            f"{label}查歌「{label}」「页数」\n"
            f"{label}查歌「最小{label}」「最大{label}」「页数」"
        )
    return v1, v2, page


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
    if raw_id >= UTAGE_ID_BASE:
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
