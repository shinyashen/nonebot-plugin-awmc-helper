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


_RANGE_USAGE = (
    "{label}查歌参数错误，请输入正确格式：\n"
    "{label}查歌「{label}」（单值）\n"
    "{label}查歌「最小{label}-最大{label}」（短横线范围）\n"
    "页码作第二参数：{label}查歌 <{label}|最小{label}-最大{label}> <页数>"
)


def parse_range_args(a_list: list[str], label: str) -> "tuple[float, float, int]":
    """数值范围查歌参数解析（定数/bpm 两分支同构，报错文案按 label 定制）。

    形态（2026-10-31 定案，有意偏离基准 Hoshino 的「两数=范围、大值在前=
    单值+页」约定——该歧义形态由「首参数=值或短横线范围、第二参数=页码」
    无歧义承接）：首参数为「值」（单值=上下限相同）或「最小-最大」，可选
    第二参数为页码。不合法抛 ``ValueError``（文案面向用户，handler 转
    finish）。
    """
    if not 1 <= len(a_list) <= 2:
        raise ValueError(_RANGE_USAGE.format(label=label))
    head = a_list[0]
    if "-" in head:
        lo, _, hi = head.partition("-")
        if not _is_float(lo) or not _is_float(hi) or float(lo) > float(hi):
            raise ValueError(_RANGE_USAGE.format(label=label))
        v1, v2 = float(lo), float(hi)
    elif _is_float(head):
        v1 = v2 = float(head)
    else:
        raise ValueError(_RANGE_USAGE.format(label=label))
    page = 1
    if len(a_list) == 2:
        if not a_list[1].isdigit() or int(a_list[1]) < 1:
            raise ValueError(_RANGE_USAGE.format(label=label))
        page = int(a_list[1])
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
