"""分数线指令解析：参数拆分、难度色/宴前缀候选生成与共用解析链。

「难度色与别名连写」（紫琪露诺 / 紫799）在本插件自行解析。⚠️ 别名本身可能
以颜色字开头（如「白雪」「绿9」「白银」），首字符是颜色字**不等于**难度色——
归属判定在 :func:`resolve_color_chart`：剥色/整串两候选都解析后按根 id
交集反推（交集=色、仅整串=色字属于别名），本模块只负责拆线与产出候选列表。

「宴」前缀同理（宴蛸 → 查该曲的宴谱）：宴谱的 6 位 diff_id 本身即完整
规格（曲 + 谱面），无需难度色补位——宴模式下候选列表只列 diff_id。
"""

import re

from nonebot_plugin_alconna.uniseg import UniMessage

from ...constants import UTAGE_ID_BASE, DIFF_DISPLAY_NAMES, COLOR_TO_LEVEL_INDEX
from ...core.songs import (
    ChartEntry,
    cn_song_map,
    song_service,
    chart_of_color,
    entries_list_text,
)
from ...core.types import Song, SongType, LevelIndex, SongDifficultyUtage
from ...core.binding import UserBinding
from ...core.chart_card import song_lookup_reply

_LINE_RE = re.compile(r"^-?[0-9]+(\.[0-9]+)?$")
UTAGE_PREFIX = "宴"


def split_args(
    args: str,
) -> tuple[LevelIndex | None, bool, list[str], float | None]:
    """参数拆分 → (暂定难度色或 None, 宴前缀, 查询候选列表, 线或 None)。

    ``args`` 形如「紫799 100.5」「紫 琪露诺 100」「宴蛸 100」「琪露诺 100」：

    - 末个 token 为数字时视为分数线（100.5% 一类小数均可），否则线为 None
      （调用方提示格式错误）；
    - 剩余部分首字符为难度色（绿黄红紫白）时产出候选 ``[剥色查询键, 整串]``
      （剥色为空只剩整串），``暂定难度色`` 的生效与否由调用方按候选命中与
      根 id 交集判定；无色前缀则候选仅整串、难度色恒 None；
    - 首字符为「宴」时置 ``宴前缀``（宴谱查询模式，无难度色），候选产出
      规则与颜色相同（颜色优先——首字符不可能同属两者）。
    """
    tokens = args.split()
    line: float | None = None
    if tokens and _LINE_RE.match(tokens[-1]):
        line = float(tokens[-1])
        tokens = tokens[:-1]
    query = " ".join(tokens).strip()
    if query and query[0] in COLOR_TO_LEVEL_INDEX:
        level_index = COLOR_TO_LEVEL_INDEX[query[0]]
        stripped = query[1:].strip()
        candidates = [stripped, query] if stripped else [query]
        return level_index, False, candidates, line
    if query.startswith(UTAGE_PREFIX):
        stripped = query[len(UTAGE_PREFIX) :].strip()
        candidates = [stripped, query] if stripped else [query]
        return None, True, candidates, line
    return None, False, [query] if query else [], line


_COLOR_HINT = "※ 分数线需指定难度色：分数线 <绿/黄/红/紫/白><id/别名> <线>"
_PICK_HINT = "※ 请使用「分数线 <难度色><id> <线>」指定谱面"
_UTAGE_HINT = "※ 请使用「分数线 宴<id> <线>」指定谱面"


async def resolve_color_chart(
    level_index: LevelIndex | None,
    is_utage: bool,
    candidates: list[str],
    *,
    binding: UserBinding,
    command: str = "分数线",
) -> tuple[Song, SongType | None, bool, SongDifficultyUtage] | None:
    """分数线 / linfo 共用的「难度色/宴 + 曲」解析链。

    返回 ``resolved`` 四元组 ``(曲, 谱面类型偏好, 日服, 宴谱)``；未命中返回
    None（由调用方按 candidates 组织未找到回复），缺色/歧义/无该色谱面等
    情形直接 finish 回复（FinishedException 穿透 handler，与既有行为一致）。

    ``command`` 为提示语里的指令名（分数线 / linfo），三处提示随指令取对
    应文案；缺色提示的完整措辞以 :data:`_COLOR_HINT`（分数线）为基准，
    其余指令去掉「线」参数段。

    难度色归属决策（服务器合并别名库实证：358 条颜色开头别名中 22 条剥色
    后命中异曲，如 绿9→ケロ⑨destiny、白银→銀のめぐり——剥色命中不可盲
    信）：剥色/整串两候选都解析后按根 id 交集判定——都命中且有交集 →
    首字符是难度色，用剥色结果按色过滤谱面；都命中但无交集 / 仅整串命中
    → 首字符属于别名（如 白雪、绿9），用整串结果走「是什么歌」同款回复
    + 缺色提示；仅剥色命中 → 首字符是难度色（紫琪露诺 形态）。数字候选
    （紫799）按 id 解析优先（旧「色+id」格式兼容，id 无别名歧义）。宴前
    缀同理但无难度色：宴谱 6 位 diff_id 本身即完整规格（曲+谱面）。
    """
    if command == "分数线":
        color_hint, pick_hint, utage_hint = _COLOR_HINT, _PICK_HINT, _UTAGE_HINT
    else:
        color_hint = f"※ {command}需指定难度色：{command} <绿/黄/红/紫/白><id/别名>"
        pick_hint = f"※ 请使用「{command} <难度色><id>」指定谱面"
        utage_hint = f"※ 请使用「{command} 宴<id>」指定谱面"
    resolved: tuple | None = None

    async def _is_exact_alias(name: str) -> bool:
        """名称是否为别名库的精确命中（区分 白雪=别名 与 白+阳炎=色前缀）。"""
        songs, info = await song_service.by_alias_detail(name)
        if songs and info is None:
            return True
        songs, info = await song_service.jp_by_alias_detail(name)
        return bool(songs) and info is None

    async def _pick_chart(entries, line_index):
        """条目按难度色过滤 → (唯一谱面或 None, 列表文案, 是否有该色谱面)。

        列表文案仅在多谱命中（has=True）时非空；空谱面（has=False）时调用方
        再判色字是否属于别名（白雪 类）。"""
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
            return next(iter(by_key.values())), "", True
        if by_key:
            songs = [s for _, s, _ in entries]
            cn_songs = await cn_song_map(songs)
            flags = [cn_songs[s.id] is None for s in songs]
            return None, entries_list_text(entries, flags, hint=pick_hint), True
        return None, "", False

    async def _no_color_reply(entries):
        """无难度色但有命中：「是什么歌」同款回复 + 缺色提示（回复单源复用）。"""
        msg = await song_lookup_reply(entries, binding, extra_note=color_hint)
        await msg.finish(at_sender=True)

    async def _same_song(entries_a, entries_b) -> bool:
        """两组条目是否存在同曲（根 id 交集，别名按根 id 合并）。"""
        ids_a = {s.id for _, s, _ in entries_a}
        ids_b = {s.id for _, s, _ in entries_b}
        return bool(ids_a & ids_b)

    async def _no_chart_hint():
        """色前缀命中但该难度谱面不存在（如无白谱）。"""
        assert level_index is not None  # 仅色路径调用
        await UniMessage.text(
            f" 该乐曲没有{DIFF_DISPLAY_NAMES[level_index.value]}谱"
        ).finish(at_sender=True)

    async def _resolve_song_utage(song: Song, jp: bool):
        """曲对象 → 宴谱解析：唯一宴谱直出，多宴谱列 diff_id，无宴谱提示。"""
        uts = [
            d
            for d in song.get_difficulties(SongType.UTAGE)
            if isinstance(d, SongDifficultyUtage)
        ]
        if not uts:
            await UniMessage.text(" 该乐曲没有宴谱").finish(at_sender=True)
        if len(uts) == 1:
            return (song, None, jp, uts[0])
        entries: list[ChartEntry] = [(d.diff_id, song, None) for d in uts]
        entries.sort(key=lambda e: e[0])
        cn_songs = await cn_song_map([song])
        flags = [cn_songs[song.id] is None]
        await UniMessage.text(
            " " + entries_list_text(entries, flags, hint=utage_hint)
        ).finish(at_sender=True)

    if is_utage:
        # 宴模式：剥色/整串候选 → 曲 → 宴谱组（唯一直出 / 多谱列 diff_id）
        stripped, full = candidates[0], candidates[-1]
        if stripped.isdigit():
            found = await song_service.resolve_raw_chart(int(stripped))
            if found is None:
                # 「对应乐曲 + 查歌提示」本地变体：键可带色/宴前缀非纯 id，
                # 不套 core.utils.song_not_found_text 的「ID为」标准句（措辞单源见彼处）
                await UniMessage.text(
                    f" 未找到「{full}」对应的乐曲，可用「查歌」确认后再试"
                ).finish(at_sender=True)
            song, _shape, jp, utage_diff = found
            if utage_diff is not None:
                resolved = (song, None, jp, utage_diff)
            else:
                resolved = await _resolve_song_utage(song, jp)
        else:
            entries_stripped = (
                await song_service.entries_for_name(stripped, cn_title=False)
                if stripped
                else []
            )
            chosen = None
            if entries_stripped:
                ut = [e for e in entries_stripped if e[0] >= UTAGE_ID_BASE]
                if ut:
                    chosen = ut
            if chosen is None:
                entries_full = await song_service.entries_for_name(full, cn_title=False)
                if entries_full:
                    ut = [e for e in entries_full if e[0] >= UTAGE_ID_BASE]
                    if ut:
                        chosen = ut
                    else:
                        # 命中曲但无宴谱
                        await UniMessage.text(" 该乐曲没有宴谱").finish(at_sender=True)
                elif entries_stripped:
                    # 剥色候选命中但无宴谱
                    await UniMessage.text(" 该乐曲没有宴谱").finish(at_sender=True)
                else:
                    await UniMessage.text(
                        f" 未找到「{'」或「'.join(candidates)}」对应的乐曲，"
                        "可用「查歌」确认后再试"
                    ).finish(at_sender=True)
            if len(chosen) == 1:
                entry_id, song, _ = chosen[0]
                utage_diff = next(
                    (
                        d
                        for d in song.get_difficulties(SongType.UTAGE)
                        if getattr(d, "diff_id", None) == entry_id
                    ),
                    None,
                )
                cn_songs = await cn_song_map([song])
                resolved = (song, None, cn_songs[song.id] is None, utage_diff)
            else:
                songs = [s for _, s, _ in chosen]
                cn_songs = await cn_song_map(songs)
                flags = [cn_songs[s.id] is None for s in songs]
                await UniMessage.text(
                    " " + entries_list_text(chosen, flags, hint=utage_hint)
                ).finish(at_sender=True)
    elif level_index is not None and candidates and candidates[0]:
        stripped, full = candidates[0], candidates[-1]
        if stripped.isdigit():
            # 数字候选按 id 解析优先（旧「色+id」格式兼容）；未命中回退整串
            # （绿9 类别名剥色后是数字、id 不存在，整串才是真别名）
            found = await song_service.resolve_raw_chart(int(stripped))
            if found is not None:
                song, _shape, jp, utage_diff = found
                resolved = (song, None, jp, utage_diff)
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
                    # 同曲：首字符是难度色（白阳炎 → 阳+白），按色过滤谱面
                    chart, list_msg, has = await _pick_chart(
                        stripped_entries, level_index
                    )
                    if chart is not None:
                        resolved = chart
                    elif has:
                        # 双谱歧义：列条目引导指定 id
                        await UniMessage.text(" " + list_msg).finish(at_sender=True)
                    elif await _is_exact_alias(stripped):
                        # 剥色后是精确别名但无该色谱面（如无白谱）
                        await _no_chart_hint()
                    else:
                        # 色字属于别名（白雪：整串精确别名、剥色仅模糊命中）
                        await _no_color_reply(full_entries)
                else:
                    # 色字属于别名（绿9/白银 类冲突）：整串优先、无难度色
                    await _no_color_reply(full_entries)
            elif stripped_entries:
                chart, list_msg, has = await _pick_chart(stripped_entries, level_index)
                if chart is not None:
                    resolved = chart
                elif has:
                    await UniMessage.text(" " + list_msg).finish(at_sender=True)
                else:
                    await _no_chart_hint()
            elif full_entries:
                await _no_color_reply(full_entries)
    else:
        full = candidates[-1] if candidates else ""
        if full.isdigit():
            # 无色前缀数字：宴谱 diff_id 自带完整规格可直出；普通曲仍需颜色
            found = await song_service.resolve_raw_chart(int(full))
            if found is not None:
                song, _shape, jp, utage_diff = found
                if utage_diff is not None:
                    resolved = (song, None, jp, utage_diff)
                else:
                    from ...core.songs import chart_entries

                    await _no_color_reply(chart_entries(song))
        else:
            entries = (
                await song_service.entries_for_name(full, cn_title=False)
                if full
                else []
            )
            if entries:
                await _no_color_reply(entries)
    return resolved
