"""查分指令入口：b50 / ap50 / minfo / ginfo。"""

import asyncio

from nonebot import on_regex, on_command
from nonebot.params import CommandArg, RegexGroup
from nonebot.adapters import Event, Message
from nonebot_plugin_uninfo import Session, UniSession
from nonebot_plugin_alconna.uniseg import UniMessage

from .render import _ginfo_image, _b50_rise_tips
from ...constants import DEFAULT_THEME, COLOR_TO_LEVEL_INDEX
from ...core.score import UserScoreError, score_service
from ...core.songs import song_service, prefer_type_from_raw_id
from ...core.types import FCType, SongType, LevelIndex
from ...core.utils import handle_errors
from ...core.render import info as info_render
from ...core.render import stats as stats_render
from ...core.render import best50 as b50_render
from ...core.render import jp_cover, nb_chart
from ...core.binding import (
    SERVICE_NET,
    UserBinding,
    session_keys,
    binding_service,
)
from ...core.net_score import net_score_service
from ...core.render.tools import text_to_image, image_to_bytes

AP_FC_VALUES = (FCType.AP.value, FCType.APP.value)  # 越小越好

b50 = on_command("b50", aliases={"B50"}, block=True)
ap50 = on_command("ap50", aliases={"AP50"}, block=True)
minfo = on_command(
    "minfo", aliases={"Minfo", "MINFO", "info", "Info", "INFO"}, block=True
)
ginfo = on_regex(r"^[gG]info\s?(?:([绿黄红紫白])(?=\s|\d))?(.+)$", block=True)


def _at_target(event: Event | None) -> str | None:
    """消息中被 @ 的目标用户（代查），仅取第一个非全体 at。

    段形状按 OneBot v11（``seg.type == "at"``）判定；其他适配器的 at 段
    类型名不同时会静默退化为查自己，属已知限制。
    """
    message = getattr(event, "message", None)
    if message is None:
        return None
    for seg in message:
        if seg.type == "at" and str(seg.data.get("qq")) != "all":
            return str(seg.data["qq"])
    return None


async def _resolve_target(
    session: Session, event: Event | None
) -> tuple["UserBinding | None", str | None]:
    """解析查询目标（@目标 或 发送者）→ (绑定或 None, at 目标)。

    代查链（binding_service.resolve_query）：目标绑定行只读 → QQ 平台
    水鱼按 at 的 QQ 公开查询（无需对方绑定）→ 其余降级。发送者路径保持
    ensure 自动建行。
    """
    platform = session_keys(session)[0]
    at_target = _at_target(event)
    binding = await binding_service.resolve_query(
        platform, str(session.user.id), at_target
    )
    return binding, at_target


async def _get_binding(session: Session, event: Event | None) -> UserBinding:
    """b50/ap50 入口：无可用凭据则按「代查 / 自身」分别提示并终止。"""
    binding, at_target = await _resolve_target(session, event)
    if binding is None or not binding_service.has_usable_credentials(binding):
        if at_target is not None and binding is None:
            await UniMessage.text(
                " 对方尚未绑定查分器，无法代查"
                "（水鱼可使用「b50 <水鱼用户名>」公开代查）"
            ).finish(at_sender=True)
        await UniMessage.text(
            " 尚未绑定查分器，请先使用「绑定水鱼」「绑定落雪」或「绑定日服」进行绑定"
        ).finish(at_sender=True)
    return binding


async def _get_binding_or_none(
    session: Session, event: Event | None
) -> UserBinding | None:
    """minfo 入口：不强制已绑定（未绑定降级纯谱面卡）。"""
    binding, _ = await _resolve_target(session, event)
    return binding


async def _resolve_song(key: str):
    """按 ID/别名/标题 解析曲目（多条时提示用 ID）。"""
    key = key.strip()
    if key.isdigit():
        song = await song_service.by_id(int(key))
        if song is None:
            await UniMessage.text(f" 未找到ID为「{key}」的乐曲").finish(at_sender=True)
        return song
    songs = await song_service.by_alias(key)
    if len(songs) == 1:
        return songs[0]
    if not songs:
        songs = await song_service.by_title_fuzzy(key)
    if not songs:
        await UniMessage.text(f" 没有找到「{key}」对应的乐曲").finish(at_sender=True)
    if len(songs) > 1:
        msg = f"找到{len(songs)}首相关乐曲：\n"
        msg += "".join(f"{s.id}：{s.title}\n" for s in songs[:10])
        msg += "※ 请使用「minfo <ID>」指定曲目"
        await UniMessage.text(msg.rstrip(" \n")).finish(at_sender=True)
    return songs[0]


async def _resolve_song_jp(key: str):
    """net 数据源：JP 视图解析曲目（数字 id 折根 / 别名 / 标题）。"""
    if key.isdigit():
        song = await song_service.jp_by_id(int(key))
        if song is not None:
            return song
    songs, _ = await song_service.jp_by_alias_detail(key)
    if len(songs) == 1:
        return songs[0]
    if not songs:
        songs = await song_service.jp_by_title_fuzzy(key)
    if not songs:
        await UniMessage.text(f" 没有找到「{key}」对应的乐曲").finish(at_sender=True)
    if len(songs) > 1:
        msg = f"找到{len(songs)}首相关乐曲：\n"
        msg += "".join(f"{s.id}：{s.title}\n" for s in songs[:10])
        msg += "※ 请使用「minfo <ID>」指定曲目"
        await UniMessage.text(msg.rstrip(" \n")).finish(at_sender=True)
    return songs[0]


async def _minfo_net(key: str, binding) -> None:
    """日服 minfo：JP 视图曲解析 + NET 窗口缓存成绩 + 日服谱面卡。

    与 CN minfo 的差异：曲走 JP 视图（数字 id 折根/别名/标题）、成绩来自
    NET 抓取缓存（窗口内 b50/minfo 共享一份）、渲染前补拉日服曲绘。
    顺序调用 b50（缓存命中 0 请求），避免与成绩查询并发触发双抓取。
    """
    song = await _resolve_song_jp(key)
    if net_score_service.needs_fetch(binding):
        await UniMessage.text(" 正在登录日服 NET 抓取成绩，请稍候…").send(
            at_sender=True
        )
    info = await score_service.get_minfo(song, binding)
    if info is None:
        await UniMessage.text(" 尚未游玩过该曲目").finish(at_sender=True)
    try:
        bests = await score_service.get_b50(binding)
    except UserScoreError:
        bests = None  # B50 失败不阻断成绩卡，仅省略上分提示
    prefer = prefer_type_from_raw_id(int(key)) if key.isdigit() else None
    await jp_cover.ensure(song.id)
    png = info_render.song_play_data(
        song,
        info.scores,
        service=binding.service,
        theme=binding.theme or DEFAULT_THEME,
        prefer_type=prefer,
    )
    tips = await _b50_rise_tips(info.scores, binding, bests=bests)
    msg = UniMessage.image(raw=png)
    if tips:
        tips_img = text_to_image("\n".join(tips), size=22, padding=14)
        msg = msg.image(raw=image_to_bytes(tips_img))
    await msg.finish(at_sender=True)


def _display_name(player) -> str:
    """卡片显示名：水鱼 Player.name 是账号用户名，展示用昵称（原版 df_to_player
    同款）；落雪 Player 无 nickname 字段，回退 name。"""
    return getattr(player, "nickname", None) or player.name


@b50.handle()
@handle_errors("查询失败，请稍后再试", except_with_message=(UserScoreError,))
async def _(
    session: Session = UniSession(),
    event: Event | None = None,
    message: Message = CommandArg(),
):
    username = message.extract_plain_text().strip()
    if username:  # 水鱼公开代查：b50 <水鱼用户名>（extract_plain_text 丢弃 at 段，
        # 纯 at 触发下方绑定链；对齐 Hoshino 的参数取法）
        player, bests = await score_service.get_b50_by_username(username)
        png = await b50_render.best50_bytes(
            player_name=_display_name(player),
            rating=bests.rating,
            rating_b35=bests.rating_b35,
            rating_b15=bests.rating_b15,
            scores_b35=bests.scores_b35,
            scores_b15=bests.scores_b15,
            player=player,
            service="divingfish",
        )
    else:
        binding = await _get_binding(session, event)
        if binding.service == SERVICE_NET:
            # 日服 NET：窗口缓存优先（首次/过期时真实抓取，约 5-15 秒）；
            # 无 player 概念（NET 首页玩家名结构未考证，先显示 SEGA ID）
            if net_score_service.needs_fetch(binding):
                await UniMessage.text(" 正在登录日服 NET 抓取成绩，请稍候…").send(
                    at_sender=True
                )
            bests = await score_service.get_b50(binding)
            png = await b50_render.best50_bytes(
                player_name=binding.net_sega_id or "maimai NET",
                rating=bests.rating,
                rating_b35=bests.rating_b35,
                rating_b15=bests.rating_b15,
                scores_b35=bests.scores_b35,
                scores_b15=bests.scores_b15,
                player=None,
                qqid=binding_service.qq_of(binding),
                service=binding.service,
                theme=binding.theme or DEFAULT_THEME,
            )
        else:
            player = await score_service.get_player(binding)
            bests = await score_service.get_b50(binding)
            png = await b50_render.best50_bytes(
                player_name=_display_name(player),
                rating=bests.rating,
                rating_b35=bests.rating_b35,
                rating_b15=bests.rating_b15,
                scores_b35=bests.scores_b35,
                scores_b15=bests.scores_b15,
                player=player,
                qqid=binding_service.qq_of(binding),
                service=binding.service,
                theme=binding.theme or DEFAULT_THEME,
            )
    await UniMessage.image(raw=png).finish(at_sender=True)


@ap50.handle()
@handle_errors("查询失败，请稍后再试", except_with_message=(UserScoreError,))
async def _(
    session: Session = UniSession(),
    event: Event = None,  # type: ignore[assignment]
):
    """AP50（用户口径）：b50 的升级版——只统计 AP/APP 的 best50，渲染 B50 大图。

    maimai_py 无 AP50 端点：全量成绩本地过滤后按版本拆 b35/b15 两侧灌入
    B50 模板（Hoshino 落雪 ap50 端点 → Best50 → draw_best50 同构）。
    """
    from ...core.types import current_version

    binding = await _get_binding(session, event)
    scores = await score_service.get_scores_all(binding)
    ap_scores = [
        s
        for s in scores.scores
        if s.fc is not None
        and s.fc.value in AP_FC_VALUES
        and s.achievements is not None
    ]
    if not ap_scores:
        await UniMessage.text("  没有查到 AP/APP 成绩").finish(at_sender=True)
    # 两侧各自取满（旧 35 / 新 15）：先全局截 50 再切分会把一侧掏空、
    # 总 RA 与模板 35/15 行数布局对不上
    latest = current_version.value
    ap_b35 = sorted(
        (s for s in ap_scores if (s.version or 0) < latest),
        key=lambda s: s.dx_rating or 0,
        reverse=True,
    )[:35]
    ap_b15 = sorted(
        (s for s in ap_scores if (s.version or 0) >= latest),
        key=lambda s: s.dx_rating or 0,
        reverse=True,
    )[:15]
    player = await score_service.get_player(binding)
    png = await b50_render.best50_bytes(
        _display_name(player),
        sum(int(s.dx_rating or 0) for s in ap_b35 + ap_b15),
        sum(int(s.dx_rating or 0) for s in ap_b35),
        sum(int(s.dx_rating or 0) for s in ap_b15),
        ap_b35,
        ap_b15,
        player=player,
        qqid=binding_service.qq_of(binding),
        service=binding.service,
        theme=binding.theme or DEFAULT_THEME,
    )
    await UniMessage.image(raw=png).finish(at_sender=True)


@minfo.handle()
@handle_errors("查询失败，请稍后再试", except_with_message=(UserScoreError,))
async def _(
    session: Session = UniSession(),
    event: Event = None,  # type: ignore[assignment]
    message: Message = CommandArg(),  # type: ignore[assignment]
):
    # extract_plain_text 丢弃 at 段：minfo 231 @某人 → 代查某人该曲成绩
    key = message.extract_plain_text().strip()
    if not key:
        await UniMessage.text(" 用法：minfo <曲目ID|曲名|别名>").finish(at_sender=True)
    binding = await _get_binding_or_none(session, event)
    if binding is not None and binding.service == SERVICE_NET:
        await _minfo_net(key, binding)
    song = await _resolve_song(key)

    async def _safe_b50():
        # B50 拉取失败（未绑定/无权限）不阻断成绩卡，仅省略上分提示
        if binding is None:
            return None
        try:
            return await score_service.get_b50(binding)
        except UserScoreError:
            return None

    info, bests = await asyncio.gather(
        score_service.get_minfo(song, binding), _safe_b50()
    )
    if info is None:
        await UniMessage.text(" 尚未游玩过该曲目").finish(at_sender=True)

    # R1：按基准 info.py 版式渲染真实成绩卡；数字 id 按其形状推断卡片主类型
    prefer = prefer_type_from_raw_id(int(key)) if key.isdigit() else None
    png = info_render.song_play_data(
        song,
        info.scores,
        service=binding.service if binding is not None else None,
        theme=(binding.theme or DEFAULT_THEME)
        if binding is not None
        else DEFAULT_THEME,
        prefer_type=prefer,
    )
    tips = await _b50_rise_tips(info.scores, binding, bests=bests)
    msg = UniMessage.image(raw=png)
    if tips:
        tips_img = text_to_image("\n".join(tips), size=22, padding=14)
        msg = msg.image(raw=image_to_bytes(tips_img))
    await msg.finish(at_sender=True)


@ginfo.handle()
@handle_errors("查询失败，请稍后再试", except_with_message=(UserScoreError,))
async def _(groups: tuple = RegexGroup()):
    color, key = groups
    song = await _resolve_song(key.strip())
    # 默认紫谱（MASTER）
    level_index = COLOR_TO_LEVEL_INDEX.get(color or "", LevelIndex.MASTER)
    diff = song.get_difficulty(SongType.DX, level_index) or song.get_difficulty(
        SongType.STANDARD, level_index
    )
    if diff is None:
        await UniMessage.text(" 该曲目没有此难度谱面").finish(at_sender=True)
    if diff.curve is None:
        await UniMessage.text(
            "该谱面暂无游玩统计（新谱样本不足或曲线数据未加载）"
        ).finish(at_sender=True)
    # R2：与查歌同源富谱面卡（渲染器在 core/render，无跨插件问题）；
    # 卡片主类型跟随所选谱面（选 SD 色谱显示 SD 卡）
    prefer = SongType.STANDARD if diff.type == SongType.STANDARD else None
    card = nb_chart.song_chart_info(song, False, False, [], DEFAULT_THEME, prefer)
    # R9：统计信息画入双环统计卡（样本/拟合/均值/σ/DX + 全连与评级分布）
    stats_png = stats_render.song_global_data(song, diff)
    png = _ginfo_image(card, stats_png)
    await UniMessage.image(raw=png).finish(at_sender=True)
