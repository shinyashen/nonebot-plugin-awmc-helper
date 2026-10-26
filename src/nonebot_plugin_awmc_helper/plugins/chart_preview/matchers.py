"""谱面预览指令入口与帮助声明。"""

import re

from nonebot import on_command
from nonebot.params import CommandArg
from nonebot_plugin_alconna.uniseg import UniMessage

from .preview import cached_path, build_preview
from ...config import plugin_config
from ...core.ext import ExtError
from ...constants import display_song_id
from ...core.help import CommandSpec, help_registry
from ...core.songs import song_service
from ...core.types import LevelIndex
from ...core.utils import handle_errors
from ...core.chart_query import split_color_query
from ...core.render.chart_range import parse_range_token
from ...core.render.chart_video import DEFAULT_HI_SPEED, normalize_hi_speed

chart_preview = on_command("谱面预览", block=True)

_USAGE = (
    "用法：谱面预览 <难度色>+<歌名/别名/id> [谱面流速] [时间范围]\n"
    "难度色：绿/黄/红/紫/白（宴谱用 宴+<id>）\n"
    "谱面流速：0.25–10、步长 0.25，默认 7.0（非法回退 7.0）\n"
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


@chart_preview.handle()
@handle_errors(except_with_message=(ValueError, ExtError))
async def _(args=CommandArg()):
    if not plugin_config.awmc_chart_preview_enabled:
        await UniMessage.text(" 谱面预览功能未开启").finish(at_sender=True)
    text = args.extract_plain_text().strip()
    if not text:
        await UniMessage.text(_USAGE).finish(at_sender=True)
    level, _is_utage, name, speed, range_token = _parse_args(text)
    index = 3 if level is None else int(level.value)

    # 曲目定位：数字 id 直查（含宴谱 diff_id/DX 展示 id），否则别名/标题链
    entry = None
    if name.isdigit():
        hit = await song_service.resolve_raw_chart(int(name))
        if hit is not None:
            song, prefer, _jp, _utage = hit
            entry = (int(name), song, prefer)
    if entry is None:
        entries = await song_service.entries_for_name(name)
        if not entries:
            await UniMessage.text(_NOT_FOUND).finish(at_sender=True)
        if len(entries) > 1:
            lines = "找到多首匹配曲目，请用 id 指定：\n" + "\n".join(
                f"「{display_song_id(s)}」{s.title}" for _i, s, _p in entries[:10]
            )
            await UniMessage.text(lines).finish(at_sender=True)
        entry = entries[0]

    if cached_path(entry, index, range_token, speed) is None:
        hint = (
            " 片段渲染中：约需 20–60 秒（视 CPU），完成后自动发送；"
            "同一片段再次请求命中缓存秒回"
            if range_token
            else " 谱面预览渲染中：整曲约需 30–90 秒（视 CPU），完成后自动发送；"
            "同一谱面再次请求命中缓存秒回"
        )
        await UniMessage.text(hint).send(at_sender=True)
    out, _label = await build_preview(entry, index, range_token, speed)
    await UniMessage.video(path=str(out)).finish(at_sender=True)


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
            brief="渲染谱面预览视频（simai+整曲音频+打击音效，可指定片段，命中缓存秒回）",
            detail=(
                "格式：谱面预览 <难度色>+<歌名/别名/id> [谱面流速] [时间范围]\n"
                "难度色：绿/黄/红/紫/白（宴谱用 宴+<id>）。\n"
                "谱面流速：0.25–10、步长 0.25，默认 7.0；非法值回退默认。\n"
                "时间范围：0:35-1:20 / -1:20 / 0:35-（- 前后可省略一侧），"
                "自动按小节对齐；片段会带 2 小节前置（音乐渐入、打击音效从内容"
                "起点开始）。\n"
                "数据/音频热链落雪素材站；带站内打击音效（含提前 50ms 音效偏移），响度"
                "归一（-16 LUFS）。国服先行曲等无谱面数据的曲目不支持。"
                "渲染结果有缓存，重复请求秒回。"
            ),
        ),
    ],
)
