"""awmc.score_tools linfo 指令：单谱面定数历史（图片出时间轴卡）。

`linfo <难度色><id/别名/曲名>` / `linfo 帮助`：解析链与分数线共用
（:func:`resolve_color_chart`），渲染走 core.render.linfo 时间轴卡。
口径约定（2026-10-16 定稿）：

- 仅 DX 世代定数（旧框末值折 DX 基线首节点），宴谱不提供（规范表不存
  宴定数历史）；
- 按数据源分口径：日服视图（NET 等）大数字 = 日服现行定数、无「日服
  最新」概念；国服视图大数字 = 国服现行定数，日服已调至更新值时红字
  提示并粉框标记末节点；
- DX 世代全程未调整的谱面不出卡，纯文本提示。
"""

from nonebot import on_command
from nonebot.params import CommandArg
from nonebot.adapters import Message
from nonebot_plugin_alconna.uniseg import UniMessage

from .resolve import split_args, resolve_color_chart
from ...core.dan import cn_current_version
from ...constants import (
    DEFAULT_THEME,
    LEVEL_COLOR_ZH,
    ds_text,
)
from ...core.help import CommandPage, CommandSpec, page_entries, help_registry
from ...core.songs import chart_of_color
from ...core.types import SongType
from ...core.utils import handle_errors
from ...core.songdb import chart_history, dx_history_points
from ...core.binding import UserBinding, SessionBinding
from ...core.chart_card import resolve_card_view
from ...core.render.linfo import linfo_card

linfo_cmd = on_command("linfo", aliases={"定数历史"}, block=True)

_USAGE = (
    " 格式错误：linfo <难度色><id/别名/曲名>\n例：linfo 紫799（难度色：绿黄红紫白）"
)
_UTAGE_TEXT = " 宴谱不提供定数历史"


@linfo_cmd.handle()
@handle_errors()
async def _(
    binding: UserBinding = SessionBinding(),
    message: Message = CommandArg(),
):
    args = message.extract_plain_text().strip()
    if args in ("帮助", ""):
        # 文案单源：渲染注册表里的「linfo」详情页
        entry = page_entries(help_registry, CommandPage(spec=_linfo_spec))[0]
        assert isinstance(entry, str)  # 指令详情页恒为纯文本节点
        await UniMessage.text(entry).finish(at_sender=True)
    level_index, is_utage, candidates, line = split_args(args)
    if is_utage:
        # 宴谱定数不采信，规范表不建 levels 行——入口即拦截，不走解析链
        await UniMessage.text(_UTAGE_TEXT).finish(at_sender=True)
    if not candidates:
        if line is None:
            await UniMessage.text(_USAGE).finish(at_sender=True)
        # linfo 无「线」参数：纯数字输入（6 位宴 id / 裸 id）被 split_args
        # 当作线吞掉，还原为查询键（小数形态本就不是 id，仍走未找到兜底）
        candidates = [str(int(line)) if line.is_integer() else str(line)]
    resolved = await resolve_color_chart(
        level_index, False, candidates, binding=binding, command="linfo"
    )
    if resolved is None:
        await UniMessage.text(
            f" 未找到「{'」或「'.join(candidates)}」对应的乐曲，可用「查歌」确认后再试"
        ).finish(at_sender=True)
    song, prefer, jp, utage_diff = resolved
    if utage_diff is not None:
        # 6 位宴 diff_id 直查形态：解析链直出宴谱，linfo 同样不提供
        await UniMessage.text(_UTAGE_TEXT).finish(at_sender=True)
    assert level_index is not None  # 宴已拦截，此处必为色路径

    # NET 等日服视图换日服曲对象（定数/版本口径一致，与查歌卡同路由）
    card_song, jp_card = await resolve_card_view(song, binding)
    prefer_type = SongType.STANDARD if prefer == SongType.STANDARD else None
    diff = chart_of_color(card_song, level_index, prefer=prefer_type or SongType.DX)
    if diff is None:
        await UniMessage.text(" 该乐曲没有这个等级").finish(at_sender=True)

    kind = "sd" if diff.type == SongType.STANDARD else "dx"
    points = dx_history_points(
        await chart_history(song.id, kind, diff.level_index.value)
    )
    jp_view = jp or jp_card
    if len(points) <= 1:
        # DX 世代全程未调整：不出卡，纯文本告知（值取不到时回落当前定数）
        value = points[-1][1] if points else diff.level_value
        scope = "日服" if jp_view else "国服"
        await UniMessage.text(
            f" 「{card_song.title}」{LEVEL_COLOR_ZH[diff.level_index]}"
            f"谱面定数没变过（{scope}现行 {ds_text(value)}）"
        ).finish(at_sender=True)
    png = linfo_card(
        card_song,
        diff,
        points,
        cn_current=await cn_current_version(),
        jp=jp_view,
        theme=binding.theme or DEFAULT_THEME,
    )
    await UniMessage.image(raw=png).finish(at_sender=True)


# ---------------------------------------------------------------- 帮助声明

_linfo_spec = CommandSpec(
    matcher=linfo_cmd,
    name="linfo（定数历史）",
    aliases=("定数历史",),
    brief="查单谱面定数变迁（图片出时间轴卡）",
    detail=(
        "查询单个谱面的定数变化历史（图片出卡）。\n"
        "命令格式：linfo「难度色」「id/别名/曲名」（难度色与查询键可连写，\n"
        "如 紫799 / 紫琪露诺；难度色：绿/黄/红/紫/白）。\n"
        "仅统计 DX 世代定数；横轴为调整发生的版本，未调整的版本不占节点。\n"
        "日服数据源显示日服现行定数；国服数据源显示国服现行定数，日服已\n"
        "调至新值时额外提示。DX 世代未调整过的谱面直接文本告知。\n"
        "发送「linfo 帮助」可直接查看本说明。"
    ),
    example="linfo 紫799",
)
