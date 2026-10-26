"""谱面预览指令入口与帮助声明。"""

from nonebot import on_command
from nonebot.params import CommandArg
from nonebot_plugin_alconna.uniseg import UniMessage

from .preview import cached_path, build_preview
from ...config import plugin_config
from ...core.ext import ExtError
from ...constants import (
    LEVEL_INDEX_BY_EN,
    DIFF_DISPLAY_NAMES,
    COLOR_TO_LEVEL_INDEX,
    display_song_id,
)
from ...core.help import CommandSpec, help_registry
from ...core.songs import song_service
from ...core.types import LevelIndex
from ...core.utils import handle_errors

chart_preview = on_command("谱面预览", block=True)

_USAGE = (
    "用法：谱面预览 <曲名/别名/id> [难度]\n"
    "难度可省略（默认 Master）；支持 大师/宗师/专家/高级/基础、"
    "紫/白/红/黄/绿 或英文难度名"
)

_NOT_FOUND = (
    "未找到该歌曲，请确认曲名/别名，或先用「查歌」确认 id 后用「谱面预览 id xxxxx」指定"
)


def _parse_difficulty(text: str) -> tuple[str, int]:
    """拆出末尾难度词，返回 (关键词, 难度序 0-4)。缺省 Master(3)。"""
    parts = text.rsplit(maxsplit=1)
    if len(parts) == 2:
        head, tail = parts[0], parts[1].lower()
        level: LevelIndex | None = None
        if tail in COLOR_TO_LEVEL_INDEX:
            level = COLOR_TO_LEVEL_INDEX[tail]
        elif tail in LEVEL_INDEX_BY_EN:
            level = LEVEL_INDEX_BY_EN[tail]
        else:
            for idx, name in enumerate(DIFF_DISPLAY_NAMES):
                if tail == name.lower():
                    level = LevelIndex(idx)
        if level is not None:
            return head, int(level.value)
    return text, 3


@chart_preview.handle()
@handle_errors(except_with_message=(ValueError, ExtError))
async def _(args=CommandArg()):
    if not plugin_config.awmc_chart_preview_enabled:
        await UniMessage.text(" 谱面预览功能未开启").finish(at_sender=True)
    text = args.extract_plain_text().strip()
    if not text:
        await UniMessage.text(_USAGE).finish(at_sender=True)
    name, index = _parse_difficulty(text)

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

    if cached_path(entry, index) is None:
        await UniMessage.text(
            " 谱面预览渲染中：整曲约需 30–90 秒（视 CPU），完成后自动发送；"
            "同一谱面再次请求命中缓存秒回"
        ).send(at_sender=True)
    out, _label = await build_preview(entry, index)
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
            brief="渲染谱面预览视频（simai+整曲音频+打击音效，首次约半分钟，之后命中缓存）",
            detail=(
                "格式：谱面预览 <曲名/别名/id> [难度]\n"
                "难度默认 Master；支持 大师/宗师/专家/高级/基础、紫/白/红/黄/绿、"
                "英文难度名。\n"
                "数据/音频热链落雪素材站；带站内打击音效（含提前 50ms 音效偏移），响度"
                "归一（-16 LUFS）。国服先行曲等无谱面数据的曲目不支持。"
                "渲染结果有缓存，重复请求秒回。"
            ),
        ),
    ],
)
