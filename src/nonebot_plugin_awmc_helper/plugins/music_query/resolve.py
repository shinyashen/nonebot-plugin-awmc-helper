"""查歌解析链：数字 id / 别名 → 谱面类型条目。

指令无关的解析编排：数字 id 解析、别名查询链。数字 id → 谱面条目的
单源实现在 core ``song_service.resolve_raw_chart``（含宴谱 diff_id 定位、
DX 展示 id 回查与形状推类型，查分 minfo 共用）；别名/曲名 → 谱面条目的
链条（含前缀收敛）在 core ``song_service.entries_for_name``。
"""

from nonebot import logger


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
