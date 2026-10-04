"""awmc.random_song：随机谱面子插件。

指令（对齐原版）：
- `来个/随个/给个 [dx|sd|标准][色]<等级>`（如 随个紫13+ / 来个dx14）
- `随个/来个/给个 <分类>`（如 随个流行 / 来个东方；宴会場分类含宴谱）
- `mai什么`（随机曲目）
- `mai什么加分/推分/上分`：基于 B50 的轻量推分单曲推荐（NB 版 get_mai_what 算法）
"""

import re
import random as _random

from nonebot import on_regex, on_command
from nonebot.params import RegexGroup
from nonebot.plugin import PluginMetadata
from nonebot_plugin_alconna.uniseg import UniMessage

from ...constants import ZH_TO_GENRE, COLOR_TO_LEVEL_INDEX
from ...core.calc import min_ds_of_ra, rise_candidates
from ...core.help import CommandSpec, help_registry
from ...core.score import UserScoreError, score_service
from ...core.songs import SINGLE_JP_NOTE, song_service
from ...core.store import UserBinding
from ...core.types import Song, Genre, SongType, ScoreExtend
from ...core.utils import slow_notice, handle_errors
from ...core.binding import SessionBinding
from ...core.chart_card import chart_card_bytes, resolve_card_view

__plugin_meta__ = PluginMetadata(
    name="awmc.random_song",
    description="舞萌DX 随机谱面：随个/来个/mai什么",
    usage="来个紫13+｜随个dx14｜给个标准10｜随个流行｜mai什么｜mai什么加分",
    type="application",
    homepage="https://github.com/shinyashen/nonebot-plugin-awmc-helper",
)

random_chart = on_regex(
    r"(?i)^[随来给]个((?:dx|sd|标准))?([绿黄红紫白]?)([0-9]+\+?)$",
    block=True,
)
# 分类名交替（穷举 ZH_TO_GENRE 键；最长优先防前缀遮蔽：音击&中二节奏 先于 音击、
# 东方Project 先于 东方）——不得用 (.+) 宽匹配，否则与等级随机指令重复响应
_GENRE_ALT = "|".join(re.escape(k) for k in sorted(ZH_TO_GENRE, key=len, reverse=True))
genre_random = on_regex(f"^[随来给]个({_GENRE_ALT})$", block=True)
mai_what = on_command("mai什么", block=True)
mai_what_rise = on_command(
    "mai什么加分", aliases={"mai什么推分", "mai什么上分"}, block=True
)


async def _card_bytes(song: Song, binding: UserBinding) -> tuple[bytes, str]:
    """出卡：路由经 core ``resolve_card_view`` 单源（L-5 拍板）；日服限定
    标注只按「该曲是否国服缺席」拼接（与路由解耦，NET 用户随到国服在架曲
    不弹提示）。返回 ``(图, 日服限定提示)``。
    """
    card_song, jp = await resolve_card_view(song, binding)
    note = SINGLE_JP_NOTE if jp and await song_service.by_id(song.id) is None else ""
    return await chart_card_bytes(card_song, binding, jp=jp), note


async def _finish_chart_card(song: Song, binding: UserBinding) -> None:
    """随机结果收尾三连（出卡 → 日服限定提示拼头 → 出图 finish），
    四个随机指令共用（Hoshino/NB 同设计：随机结果渲染通常的谱面卡）。"""
    png, note = await _card_bytes(song, binding)
    msg = UniMessage.text(f" {note}") if note else UniMessage()
    await msg.image(raw=png).finish(at_sender=True)


@random_chart.handle()
@handle_errors("随机失败，请稍后再试")
async def _(
    binding: UserBinding = SessionBinding(),
    groups: tuple = RegexGroup(),
):
    type_raw, color, level = groups
    song_type = None
    if type_raw:
        song_type = (
            SongType.STANDARD if type_raw.lower() in ("sd", "标准") else SongType.DX
        )
    level_index = COLOR_TO_LEVEL_INDEX.get(color or "")
    got = await song_service.random(
        song_type=song_type,
        level=level,
        level_index=level_index,
        exclude_utage=True,
        jp=score_service.view_of(binding.service) == "jp",
    )
    if got is None:
        await UniMessage.text(" 没有符合条件的谱面，换一个试试吧").finish(
            at_sender=True
        )
    song, _diff = got
    await _finish_chart_card(song, binding)


@genre_random.handle()
@handle_errors("随机失败，请稍后再试")
async def _(
    binding: UserBinding = SessionBinding(),
    groups: tuple = RegexGroup(),
):
    """按分类随机谱面（随个流行等）。宴会場分类允许宴谱入池——
    宴谱是該分类的全部内容，按其余分类的排除口径会必然落空。"""
    genre = ZH_TO_GENRE[groups[0]]
    got = await song_service.random(
        genre=genre,
        exclude_utage=genre != Genre.宴会場,
        jp=score_service.view_of(binding.service) == "jp",
    )
    if got is None:
        await UniMessage.text(" 没有符合条件的谱面，换一个试试吧").finish(
            at_sender=True
        )
    song, _diff = got
    await _finish_chart_card(song, binding)


@mai_what.handle()
@handle_errors("随机失败，请稍后再试")
async def _(binding: UserBinding = SessionBinding()):
    got = await song_service.random(
        exclude_utage=True, jp=score_service.view_of(binding.service) == "jp"
    )
    if got is None:
        await UniMessage.text(" 曲库为空，请稍后再试").finish(at_sender=True)
    song, _diff = got
    await _finish_chart_card(song, binding)


@mai_what_rise.handle()
@handle_errors("推荐失败，请稍后再试", except_with_message=(UserScoreError,))
async def _(binding: UserBinding = SessionBinding()):
    """mai什么加分：NB 版 get_mai_what 语义——基于 B50 末位 RA 反推定数区间随机推荐单曲。

    未绑定 / B50 拉取失败 / 无候选时退化为普通随机曲目（与原版行为一致）。
    """
    song = None
    jp = score_service.view_of(binding.service) == "jp"
    try:
        bests = await score_service.get_b50(binding, notify_slow=slow_notice())
        song = await _pick_rise_song(bests.scores, jp)
    except UserScoreError:
        song = None
    if song is None:  # 未绑定或无候选 → 普通随机
        got = await song_service.random(exclude_utage=True, jp=jp)
        if got is None:
            await UniMessage.text(" 曲库为空，请稍后再试").finish(at_sender=True)
        song, _diff = got
    await _finish_chart_card(song, binding)


async def _pick_rise_song(scores: list[ScoreExtend], jp: bool = False):
    """NB get_mai_what：随机侧 → 末位 RA 反推定数 [ds, ds+1] → 排除 SSS+ → 随机单曲。

    定数候选池跟随数据源视图（``jp``）：NET 成绩按日服定数反推区间，
    对国服视图查池会口径错位（日服限定曲缺失、定数不同步）。
    """
    is_dx = _random.randint(0, 1) == 1
    target_type = SongType.DX if is_dx else SongType.STANDARD
    side = [s for s in scores if s.type == target_type]
    if not side:
        other = SongType.STANDARD if is_dx else SongType.DX
        side = [s for s in scores if s.type == other]
        if not side:
            return None
    side.sort(key=lambda s: s.dx_rating or 0)  # 升序，取末位应为最低
    # 末位 RA 口径刻意与 score_tools 推分推荐不同：此处取**同谱面类型一侧**
    # 的 B50 末位（随机出的谱面与该侧同池，才有替换意义），见 rise_candidates 注
    lowest_ra = side[0].dx_rating or 0

    ds = round(min_ds_of_ra(lowest_ra), 1)
    candidates = rise_candidates(
        await song_service.by_level_value(ds, ds + 1, scope="jp" if jp else "cn"),
        ds_range=(ds, ds + 1),
        song_type=target_type,
        scores=scores,
    )
    if not candidates:
        return None
    return _random.choice(candidates)


# ---------------------------------------------------------------- 帮助声明

help_registry.declare(
    plugin="awmc.random_song",
    title="随机谱面",
    category="fun",
    commands=[
        CommandSpec(
            matcher=random_chart,
            name="来个/随个/给个 <谱面>",
            brief="按类型/颜色/等级随机谱面（排除宴谱）",
            detail="格式：来个 [dx|sd|标准][色]<等级>，如 随个紫13+、来个dx14。",
        ),
        CommandSpec(
            matcher=genre_random,
            name="来个/随个/给个 <分类>",
            brief="按分类随机（流行/东方/宴会場等）",
            detail="按分类随机（流行/东方/宴会場等），如 随个流行；宴会場分类含宴谱。",
        ),
        CommandSpec(
            matcher=mai_what,
            name="mai什么",
            brief="全库随机一首（排除宴谱）",
        ),
        CommandSpec(
            matcher=mai_what_rise,
            name="mai什么加分",
            aliases=("mai什么推分", "mai什么上分"),
            brief="基于个人 B50 末位的推分单曲推荐",
            detail="基于个人 B50 末位推荐单曲；未绑定或拉取失败时退化为普通随机。",
        ),
    ],
)
