"""awmc.score_query：查分子插件（b50 / ap50 / minfo / ginfo）。

- 支持 @某人 代查（b50/minfo/ginfo）；
- 未绑定时按部署默认源以 QQ 公开查询（仅水鱼、QQ 平台）；
- ap50 为本地过滤实现（maimai-py 无 AP50 端点）：全量成绩中取 FC=AP/APP 的 RA 前 50。
"""

import io

from nonebot import on_regex, on_command
from maimai_py import FCType, SongType, LevelIndex
from nonebot.params import CommandArg, RegexGroup
from nonebot.plugin import PluginMetadata
from nonebot.adapters import Event, Message
from nonebot_plugin_uninfo import Session, UniSession
from nonebot_plugin_alconna.uniseg import UniMessage

from ...constants import COLOR_TO_LEVEL_INDEX
from ...core.score import UserScoreError, score_service
from ...core.songs import song_service, prefer_type_from_raw_id
from ...core.utils import handle_errors
from ...core.render import info as info_render
from ...core.render import stats as stats_render
from ...core.render import best50 as b50_render
from ...core.render import nb_chart
from ...core.binding import session_keys, binding_service
from ...core.render.tools import text_to_image, image_to_bytes

__plugin_meta__ = PluginMetadata(
    name="awmc.score_query",
    description="舞萌DX 查分：b50/ap50/minfo/ginfo",
    usage="b50 [水鱼用户名]｜ap50｜minfo <曲目ID|曲名|别名>｜ginfo <[难度色]曲目>",
    type="application",
    homepage="https://github.com/shinyashen/nonebot-plugin-awmc-helper",
)

AP_FC_VALUES = (FCType.AP.value, FCType.APP.value)  # 越小越好


def _at_target(event: Event | None) -> str | None:
    """消息中被 @ 的目标用户（代查），仅取第一个非全体 at。"""
    message = getattr(event, "message", None)
    if message is None:
        return None
    for seg in message:
        if seg.type == "at" and str(seg.data.get("qq")) != "all":
            return str(seg.data["qq"])
    return None


async def _get_binding(session: Session, event: Event | None, *, required: bool = True):
    """取（@目标 或 发送者的）绑定；required 时无可用凭据则提示。"""
    platform = session_keys(session)[0]
    user_id = _at_target(event) or str(session.user.id)
    binding = await binding_service.ensure(platform, user_id)
    if required and binding_service.identifier_or_none(binding) is None:
        await UniMessage.text(
            " 尚未绑定查分器，请先使用「绑定水鱼」或「绑定落雪」进行绑定"
        ).finish(at_sender=True)
    return binding


b50 = on_command("b50", aliases={"B50"}, block=True)
ap50 = on_command("ap50", aliases={"AP50"}, block=True)
minfo = on_command(
    "minfo", aliases={"Minfo", "MINFO", "info", "Info", "INFO"}, block=True
)
ginfo = on_regex(r"^[gG]info\s?([绿黄红紫白]?)(.+)$", block=True)


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
    username = str(message).strip()
    if username:  # 水鱼公开代查：b50 <水鱼用户名>
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
            theme=binding.theme or "prism_plus",
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
    from maimai_py import current_version

    binding = await _get_binding(session, event)
    scores = await score_service.get_scores_all(binding)
    ap_scores = [
        s
        for s in scores.scores
        if s.fc is not None
        and s.fc.value in AP_FC_VALUES
        and s.achievements is not None
    ]
    ap_scores.sort(key=lambda s: s.dx_rating or 0, reverse=True)
    ap_scores = ap_scores[:50]
    if not ap_scores:
        await UniMessage.text("  没有查到 AP/APP 成绩").finish(at_sender=True)
    latest = current_version.value
    ap_b35 = sorted(
        (s for s in ap_scores if (s.version or 0) < latest),
        key=lambda s: s.dx_rating or 0,
        reverse=True,
    )
    ap_b15 = sorted(
        (s for s in ap_scores if (s.version or 0) >= latest),
        key=lambda s: s.dx_rating or 0,
        reverse=True,
    )
    player = await score_service.get_player(binding)
    png = await b50_render.best50_bytes(
        _display_name(player),
        sum(int(s.dx_rating or 0) for s in ap_scores),
        sum(int(s.dx_rating or 0) for s in ap_b35),
        sum(int(s.dx_rating or 0) for s in ap_b15),
        ap_b35,
        ap_b15,
        player=player,
        qqid=binding_service.qq_of(binding),
        service=binding.service,
        theme=binding.theme or "prism_plus",
    )
    await UniMessage.image(raw=png).finish(at_sender=True)


@minfo.handle()
@handle_errors("查询失败，请稍后再试", except_with_message=(UserScoreError,))
async def _(
    session: Session = UniSession(),
    event: Event = None,  # type: ignore[assignment]
    message: Message = CommandArg(),  # type: ignore[assignment]
):
    key = str(message).strip()
    if not key:
        await UniMessage.text(" 用法：minfo <曲目ID|曲名|别名>").finish(at_sender=True)
    song = await _resolve_song(key)
    binding = await _get_binding(session, event, required=False)
    info = await score_service.get_minfo(song, binding)
    if info is None:
        await UniMessage.text(" 尚未游玩该曲目（或无权限查看）").finish(at_sender=True)

    # R1：按基准 info.py 版式渲染真实成绩卡；数字 id 按其形状推断卡片主类型
    prefer = prefer_type_from_raw_id(int(key)) if key.isdigit() else None
    png = info_render.song_play_data(
        song,
        info.scores,
        service=binding.service if binding is not None else None,
        theme=(binding.theme or "prism_plus") if binding is not None else "prism_plus",
        prefer_type=prefer,
    )
    tips = await _b50_rise_tips(info.scores, binding)
    msg = UniMessage.image(raw=png)
    if tips:
        tips_img = text_to_image("\n".join(tips), size=22, padding=14)
        msg = msg.image(raw=image_to_bytes(tips_img))
    await msg.finish(at_sender=True)


async def _b50_rise_tips(scores, binding) -> list[str]:
    """「可进 B50」增强提示（我方独有，基准以谱面卡上分预测区表达）。

    对不在 B50 且 RA 高于入线最低 RA 的成绩，给出替换后总 RA 提升量；
    B50 拉取失败（未绑定/无权限）时静默跳过。
    """
    if binding is None or not scores:
        return []
    try:
        bests = await score_service.get_b50(binding)
    except UserScoreError:
        return []
    min_ra = min(
        (s.dx_rating or 0 for s in bests.scores_b35 + bests.scores_b15), default=0
    )
    b50_keys = {(s.id, s.type, s.level_index) for s in bests.scores}
    tips = []
    for score in scores:
        ra = int(score.dx_rating or 0)
        if (score.id, score.type, score.level_index) in b50_keys or ra <= min_ra:
            continue
        type_abbr = "DX" if score.type == SongType.DX else "SD"
        tips.append(
            f"{type_abbr} {score.level_index.name}（{score.level_value:.1f}）"
            f" {score.achievements or 0:.4f}% RA {ra}"
            f"：可进 B50，替换后总 RA +{ra - min_ra}"
        )
    return tips


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
            "暂无该谱面的游玩统计（需部署配置水鱼开发者 Token 以启用曲线数据）"
        ).finish(at_sender=True)
    # R2：与查歌同源富谱面卡（渲染器在 core/render，无跨插件问题）；
    # 卡片主类型跟随所选谱面（选 SD 色谱显示 SD 卡）
    prefer = SongType.STANDARD if diff.type == SongType.STANDARD else None
    card = nb_chart.song_chart_info(song, False, False, [], "prism_plus", prefer)
    # R9：统计信息画入双环统计卡（样本/拟合/均值/σ/DX + 全连与评级分布）
    stats_png = stats_render.song_global_data(song, diff)
    png = _ginfo_image(card, stats_png)
    await UniMessage.image(raw=png).finish(at_sender=True)


def _ginfo_image(card: bytes, stats_png: bytes) -> bytes:
    """富谱面卡 + 统计卡纵向拼接。"""
    from PIL import Image

    extras: list[Image.Image] = [
        Image.open(io.BytesIO(card)).convert("RGBA"),
        Image.open(io.BytesIO(stats_png)).convert("RGBA"),
    ]
    total_h = sum(im.size[1] for im in extras) + 8 * (len(extras) - 1)
    w = max(im.size[0] for im in extras)
    out = Image.new("RGBA", (w, total_h), "#f2f3f5")
    y = 0
    for im in extras:
        out.paste(im, (0, y))
        y += im.size[1] + 8
    return image_to_bytes(out)
