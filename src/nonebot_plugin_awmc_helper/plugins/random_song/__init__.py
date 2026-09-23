"""awmc.random_song：随机谱面子插件。

指令（对齐原版）：
- `来个/随个/给个 [dx|sd|标准][色]<等级>`（如 随个紫13+ / 来个dx14）
- `mai什么`（随机曲目）
- `mai什么加分/推分/上分`：基于 B50 的轻量推分单曲推荐（NB 版 get_mai_what 算法）
"""

import random as _random

from nonebot import on_regex, on_command
from maimai_py import SongType, ScoreExtend
from nonebot.params import RegexGroup
from nonebot.plugin import PluginMetadata
from nonebot_plugin_uninfo import Session, UniSession
from nonebot_plugin_alconna.uniseg import UniMessage

from ...constants import COLOR_TO_LEVEL_INDEX
from ...core.score import UserScoreError, score_service
from ...core.songs import song_service
from ...core.utils import handle_errors
from ...core.binding import session_keys, binding_service
from ...core.chart_card import chart_card_bytes

__plugin_meta__ = PluginMetadata(
    name="awmc.random_song",
    description="舞萌DX 随机谱面：随个/来个/mai什么",
    usage="来个紫13+｜随个dx14｜给个标准10｜mai什么｜mai什么加分",
    type="application",
    homepage="https://github.com/shinyashen/nonebot-plugin-awmc-helper",
)

random_chart = on_regex(
    r"(?i)^[随来给]个((?:dx|sd|标准))?([绿黄红紫白]?)([0-9]+\+?)$",
    block=True,
)
mai_what = on_command("mai什么", block=True)
mai_what_rise = on_command(
    "mai什么加分", aliases={"mai什么推分", "mai什么上分"}, block=True
)


@random_chart.handle()
@handle_errors("随机失败，请稍后再试")
async def _(
    session: Session = UniSession(), groups: tuple = RegexGroup()
):
    type_raw, color, level = groups
    song_type = None
    if type_raw:
        song_type = (
            SongType.STANDARD if type_raw.lower() in ("sd", "标准") else SongType.DX
        )
    level_index = COLOR_TO_LEVEL_INDEX.get(color or "")
    got = await song_service.random(
        song_type=song_type, level=level, level_index=level_index, exclude_utage=True
    )
    if got is None:
        await UniMessage.text(" 没有符合条件的谱面，换一个试试吧").finish(
            at_sender=True
        )
    song, _diff = got
    # Hoshino/NB 同设计：随机结果渲染通常的谱面卡（draw_chart_info 语义）
    binding = await binding_service.ensure(*session_keys(session))
    await UniMessage.image(raw=await chart_card_bytes(song, binding)).finish(
        at_sender=True
    )


@mai_what.handle()
@handle_errors("随机失败，请稍后再试")
async def _(session: Session = UniSession()):
    got = await song_service.random(exclude_utage=True)
    if got is None:
        await UniMessage.text("  曲库为空，请稍后再试").finish(at_sender=True)
    song, _diff = got
    # Hoshino/NB 同设计：mai什么 同样渲染通常的谱面卡
    binding = await binding_service.ensure(*session_keys(session))
    await UniMessage.image(raw=await chart_card_bytes(song, binding)).finish(
        at_sender=True
    )


@mai_what_rise.handle()
@handle_errors("推荐失败，请稍后再试", except_with_message=(UserScoreError,))
async def _(session: Session = UniSession()):
    """mai什么加分：NB 版 get_mai_what 语义——基于 B50 末位 RA 反推定数区间随机推荐单曲。

    未绑定 / B50 拉取失败 / 无候选时退化为普通随机曲目（与原版行为一致）。
    """
    binding = await binding_service.ensure(*session_keys(session))
    song = None
    try:
        bests = await score_service.get_b50(binding)
        song = await _pick_rise_song(bests.scores)
    except UserScoreError:
        song = None
    if song is None:  # 未绑定或无候选 → 普通随机
        got = await song_service.random(exclude_utage=True)
        if got is None:
            await UniMessage.text("  曲库为空，请稍后再试").finish(at_sender=True)
        song, _diff = got
    binding = await binding_service.ensure(*session_keys(session))
    await UniMessage.image(raw=await chart_card_bytes(song, binding)).finish(
        at_sender=True
    )


async def _pick_rise_song(scores: list[ScoreExtend]):
    """NB get_mai_what：随机侧 → 末位 RA 反推定数 [ds, ds+1] → 排除 SSS+ → 随机单曲。"""
    side = []
    is_dx = _random.randint(0, 1) == 1
    target_type = SongType.DX if is_dx else SongType.STANDARD
    side = [s for s in scores if s.type == target_type]
    if not side:
        other = SongType.STANDARD if is_dx else SongType.DX
        side = [s for s in scores if s.type == other]
        if not side:
            return None
    side.sort(key=lambda s: s.dx_rating or 0)  # 升序，取末位应为最低
    lowest_ra = side[0].dx_rating or 0
    ignore_ids = {s.id for s in scores if (s.achievements or 0) >= 100.5}

    ds = round(lowest_ra / 22.4, 1)
    candidates = []
    for song in await song_service.by_level_value(ds, ds + 1):
        if song.id in ignore_ids:
            continue
        if any(
            d.type == target_type and ds <= d.level_value <= ds + 1
            for d in song.get_difficulties()
        ):
            candidates.append(song)
    if not candidates:
        return None
    return _random.choice(candidates)
