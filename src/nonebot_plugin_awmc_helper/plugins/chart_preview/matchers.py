"""谱面预览指令入口与帮助声明。"""

import re

from nonebot import on_command
from nonebot.params import CommandArg
from nonebot.adapters import Bot, Event
from nonebot.exception import FinishedException
from nonebot_plugin_alconna.uniseg import UniMessage

from .preview import PreviewResult, cached_path, build_preview
from ...config import plugin_config
from ...core.ext import ExtError
from ...core.help import CommandSpec, help_registry
from ...core.songs import song_service
from ...core.types import SongType, LevelIndex
from ...core.utils import handle_errors
from ...core.forward import is_ob11, try_send_forward
from ...core.chart_query import split_color_query
from ...core.render.chart_range import parse_range_token
from ...core.render.chart_stats import (
    format_chart_duration,
    format_note_distribution,
)
from ...core.render.chart_video import DEFAULT_HI_SPEED, normalize_hi_speed

chart_preview = on_command("谱面预览", block=True)

_USAGE = (
    "用法：谱面预览 <难度色>+<歌名/别名/id> [谱面流速] [时间范围]\n"
    "难度色：绿/黄/红/紫/白（宴谱用 宴+<id>）\n"
    "谱面流速：3–9.75、步长 0.25，默认 7.0（非法回退 7.0）\n"
    "时间范围：0:35-1:20 / -1:20 / 0:35-（- 前后可省略一侧）"
)

_SPEED_RE = re.compile(r"^\d+(?:\.\d+)?$")

_NOT_FOUND = (
    "未找到该歌曲，请确认曲名/别名，或先用「查歌」确认 id 后用"
    "「谱面预览 <难度色>+id」指定"
)


def _parse_args(
    text: str,
) -> tuple[LevelIndex | None, bool, str, float, str | None]:
    """解析「<难度色>+<歌名/id/别名> [流速] [范围]」。

    返回 (难度色或 None, 是否宴, 查询键, 流速(已校验/回退), 范围 token)。
    """
    range_token: str | None = None
    speed = DEFAULT_HI_SPEED
    parts = text.rsplit(maxsplit=1)
    if len(parts) == 2:
        tail = parts[1]
        if "-" in tail and any(ch.isdigit() for ch in tail):
            if parse_range_token(tail) is None:
                raise ValueError(
                    "时间范围格式应如 0:35-1:20 / -1:20 / 0:35-（- 前后可省一侧）"
                )
            text, range_token = parts[0], tail
    parts = text.rsplit(maxsplit=1)
    if len(parts) == 2 and _SPEED_RE.match(parts[1]):
        speed = normalize_hi_speed(float(parts[1]))
        text = parts[0]
    level, is_utage, query = split_color_query(text)
    if not query or "+" in query:
        raise ValueError("用法：谱面预览 <难度色>+<歌名/别名/id> [流速] [范围]")
    if not is_utage and level is None:
        raise ValueError(
            "需指定难度色：格式为 谱面预览 <难度色>+<歌名/别名/id>（宴谱用 宴+<id>）"
        )
    return level, is_utage, query, speed, range_token


def _dedupe_entries(entries: list) -> list:
    """按「根 id + 谱面类型」去重（CN/日服视图同曲会各出一条，展示 id 相同）。

    根 id = ``song.id % 10000``：CN 视图曲 id 带 10000 段，日服视图不带；同一
    曲在别名链里可能同时命中两个视图对象，不去重就会在「请用 id 指定」列表里
    出现重复行。SD/DX 双谱曲 prefer 不同 → 两条都保留，由用户按 id 选择。
    """
    seen: set[tuple[object, ...]] = set()
    result: list = []
    for entry in entries:
        entry_id, song, prefer = entry
        # 根 id + 类型 + 条目 id：CN/日服同曲同谱面（展示 id 相同）去重；
        # 宴谱不同 diff_id 是不同谱面，必须保留（只按根 id+类型去重会把
        # 同宿主的多张宴谱吞掉）。
        key = (song.id % 10000, prefer, entry_id)
        if key in seen:
            continue
        seen.add(key)
        result.append(entry)
    return result


def _type_label(prefer) -> str:
    return "宴" if prefer is None else ("DX" if prefer is SongType.DX else "SD")


def _entry_label(entry) -> str:
    """列表行：条目 id + 曲名 + 类型（宴谱用 6 位 diff_id，避免与 SD/DX 撞）。"""
    entry_id, song, prefer = entry
    return f"「{entry_id}」{song.title}（{_type_label(prefer)}）"


def _song_info_text(entry, result: PreviewResult) -> str:
    """合并转发第 2 条：歌曲信息（谱面真实时长 + 各类型音符/EX 分布）。"""
    _display_id, song, prefer = entry
    stats = result.stats
    head = (
        f"【歌曲信息】{song.title}（{_type_label(prefer)}）\n"
        f"难度：{result.label}\n"
        f"谱面时长：{format_chart_duration(stats.chart_ms)}"
        "（谱面时间轴，非音源时长）\n"
    )
    if result.buddy_stats is not None:
        buddy = result.buddy_stats
        return (
            head
            + f"音符分布（1P）：{format_note_distribution(stats)}\n"
            + f"音符分布（2P）：{format_note_distribution(buddy)}\n"
            + f"总音符：1P {stats.total}（EX {stats.ex_total}） / "
            + f"2P {buddy.total}（EX {buddy.ex_total}）"
        )
    return (
        head
        + f"音符分布：{format_note_distribution(stats)}\n"
        + f"总音符：{stats.total}（EX {stats.ex_total}）"
    )


def _tips_text() -> str:
    """合并转发第 3 条：纯调参提示（不展示视频参数）。"""
    return (
        "【调整提示】\n"
        "· 谱面流速：3–9.75（步长 0.25，默认 7.0），在指令后加数值，如 7.5\n"
        "· 时间范围：如 0:35-1:20、-1:20（从头）、0:35-（到结尾）\n"
        "示例：谱面预览 紫+曲名 7.5 0:35-1:20"
    )


async def _deliver(
    bot: Bot,
    event: Event,
    entry,
    result: PreviewResult,
    hi_speed: float,
) -> None:
    """发送视频 + 歌曲信息 + 视频信息：优先合并转发，失败/不支持降级普通消息。"""
    video = UniMessage.video(path=str(result.path))
    song_text = _song_info_text(entry, result)
    tips_text = _tips_text()
    if is_ob11(bot):
        group_id = getattr(event, "group_id", None)
        user_id = None if group_id is not None else getattr(event, "user_id", None)
        if await try_send_forward(
            bot,
            [video, song_text, tips_text],
            group_id=group_id,
            user_id=user_id,
        ):
            raise FinishedException
    await video.send()
    await UniMessage.text(f"{song_text}\n\n{tips_text}").send()
    raise FinishedException


async def _resolve_normal_entry(name: str):
    """普通谱面定位：数字 id 直查，否则别名/标题链（多命中列 id）。"""
    entry = None
    if name.isdigit():
        hit = await song_service.resolve_raw_chart(int(name))
        if hit is not None:
            song, prefer, _jp, _utage = hit
            entry = (int(name), song, prefer)
    if entry is None:
        entries = _dedupe_entries(await song_service.entries_for_name(name))
        if not entries:
            await UniMessage.text(_NOT_FOUND).finish(at_sender=True)
        if len(entries) > 1:
            lines = "找到多首匹配曲目，请用 id 指定：\n" + "\n".join(
                _entry_label(entry) for entry in entries[:10]
            )
            await UniMessage.text(lines).finish(at_sender=True)
        entry = entries[0]
    return entry


def _utage_diffs(song) -> list:
    """曲目的宴谱难度列表（不 import maimai_py：按 duck typing 取字段）。"""
    from ...core.types import SongType

    try:
        diffs = song.get_difficulties(SongType.UTAGE)
    except (AttributeError, ValueError):
        return []
    return [d for d in diffs if getattr(d, "diff_id", None) is not None]


async def _resolve_utage_entry(name: str):
    """宴谱定位：6 位 diff_id 直查；否则找宿主曲再选宴谱（多张列 diff_id）。"""
    if name.isdigit():
        hit = await song_service.resolve_raw_chart(int(name))
        if hit is not None:
            song, _prefer, _jp, utage_diff = hit
            if utage_diff is not None:
                return (int(name), song, None)
            uts = _utage_diffs(song)
            if len(uts) == 1:
                return (uts[0].diff_id, song, None)
            if len(uts) > 1:
                await _list_utage(uts, song)
        await UniMessage.text(_NOT_FOUND).finish(at_sender=True)

    entries = _dedupe_entries(await song_service.entries_for_name(name))
    ut_entries = [entry for entry in entries if entry[2] is None]
    if len(ut_entries) == 1:
        return ut_entries[0]
    if len(ut_entries) > 1:
        lines = "找到多张宴谱，请用 6 位 diff_id 指定：\n" + "\n".join(
            _entry_label(entry) for entry in ut_entries[:10]
        )
        await UniMessage.text(lines).finish(at_sender=True)
    songs = {entry[1].id: entry[1] for entry in entries}
    if len(songs) == 1:
        song = next(iter(songs.values()))
        uts = _utage_diffs(song)
        if len(uts) == 1:
            return (uts[0].diff_id, song, None)
        if len(uts) > 1:
            await _list_utage(uts, song)
    await UniMessage.text(
        " 该曲目没有宴谱；宴谱请用「谱面预览 宴+<6 位 diff_id>」指定"
    ).finish(at_sender=True)


async def _list_utage(uts, song) -> None:
    lines = "找到多张宴谱，请用 6 位 diff_id 指定：\n" + "\n".join(
        f"「{d.diff_id}」{song.title}（宴·{getattr(d, 'kanji', '')}）" for d in uts[:10]
    )
    await UniMessage.text(lines).finish(at_sender=True)


@chart_preview.handle()
@handle_errors(except_with_message=(ValueError, ExtError))
async def _(bot: Bot, event: Event, args=CommandArg()):
    if not plugin_config.awmc_chart_preview_enabled:
        await UniMessage.text(" 谱面预览功能未开启").finish(at_sender=True)
    text = args.extract_plain_text().strip()
    if not text:
        await UniMessage.text(_USAGE).finish(at_sender=True)
    level, is_utage, name, speed, range_token = _parse_args(text)
    index = 3 if level is None else int(level.value)

    if is_utage:
        entry = await _resolve_utage_entry(name)
    else:
        entry = await _resolve_normal_entry(name)

    if cached_path(entry, index, range_token, speed) is None:
        hint = (
            " 片段渲染中：约需 20–60 秒，完成后自动发送。"
            if range_token
            else " 谱面预览渲染中：约需 30–90 秒，完成后自动发送。"
        )
        await UniMessage.text(hint).send(at_sender=True)
    result = await build_preview(entry, index, range_token, speed)
    await _deliver(bot, event, entry, result, speed)


# ---------------------------------------------------------------- 帮助声明

help_registry.declare(
    plugin="awmc.chart_preview",
    title="谱面预览",
    category="query",
    description="渲染下落式谱面预览视频（含音频）",
    commands=[
        CommandSpec(
            matcher=chart_preview,
            name="谱面预览",
            brief="渲染下落式谱面预览视频（含音频与打击音效，可指定片段）",
            detail=(
                "格式：谱面预览 <难度色>+<歌名/别名/id> [谱面流速] [时间范围]\n"
                "难度色：绿/黄/红/紫/白（宴谱用 宴+<id>）。\n"
                "谱面流速：3–9.75、步长 0.25，默认 7.0；非法值回退默认。\n"
                "时间范围：0:35-1:20 / -1:20 / 0:35-（- 前后可省略一侧），"
                "自动按小节对齐；片段会带 2 小节前置（音乐渐入、打击音效从内容"
                "起点开始）。\n"
                "宴谱用 宴+<6 位 diff_id>（也可 宴+宿主曲名/id 自动定位）。"
                "buddy 宴谱输出 1200×600：左侧 1P、右侧 2P，歌曲信息分别给 "
                "1P/2P 物量。\n"
                "国服先行曲等暂无谱面数据的曲目无法预览。\n"
                "结果为合并转发（视频 + 歌曲信息（谱面真实时长/各类型音符与 EX "
                "分布）+ 调参提示（改流速/指定时间范围））；协议端不支持"
                "时自动降级为普通消息。"
            ),
        ),
    ],
)
