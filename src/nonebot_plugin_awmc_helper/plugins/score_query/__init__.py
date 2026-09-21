"""awmc.score_query：查分子插件（b50 / ap50 / minfo / ginfo）。

- 支持 @某人 代查（b50/minfo/ginfo）；
- 未绑定时按部署默认源以 QQ 公开查询（仅水鱼、QQ 平台）；
- ap50 为本地过滤实现（maimai-py 无 AP50 端点）：全量成绩中取 FC=AP/APP 的 RA 前 50。
"""

from nonebot import on_regex, on_command
from maimai_py import SongType, LevelIndex
from nonebot.params import CommandArg, RegexGroup
from nonebot.plugin import PluginMetadata
from nonebot.adapters import Event, Message
from nonebot_plugin_uninfo import Session, UniSession
from nonebot_plugin_alconna.uniseg import UniMessage

from ...constants import COLOR_TO_LEVEL_INDEX
from ...core.score import UserScoreError, score_service
from ...core.songs import song_service
from ...core.utils import handle_errors
from ...core.render import pie as pie_render
from ...core.render import song as song_render
from ...core.render import best50 as b50_render
from ...core.binding import binding_service
from ...core.render.tools import text_to_image, image_to_bytes

__plugin_meta__ = PluginMetadata(
    name="awmc.score_query",
    description="舞萌DX 查分：b50/ap50/minfo/ginfo",
    usage="b50 [水鱼用户名]｜ap50｜minfo <曲目ID|曲名|别名>｜ginfo <[难度色]曲目>",
    type="application",
    homepage="https://github.com/shinyashen/nonebot-plugin-awmc-helper",
)

AP_FC_VALUES = (1, 0)  # FCType.AP / FCType.APP 的枚举值（越小越好）


def _at_target(event: Event) -> str | None:
    """消息中被 @ 的目标用户（代查），仅取第一个非全体 at。"""
    for seg in event.message:
        if seg.type == "at" and str(seg.data.get("qq")) != "all":
            return str(seg.data["qq"])
    return None


async def _get_binding(session: Session, event: Event, *, required: bool = True):
    """取（@目标 或 发送者的）绑定；required 时无可用凭据则提示。"""
    user_id = _at_target(event) or str(session.user.id)
    binding = await binding_service.ensure(session.platform or "unknown", user_id)
    if required and binding_service.identifier_or_none(binding) is None:
        await UniMessage.text(
            "尚未绑定查分器，请先使用「绑定水鱼」或「绑定落雪」进行绑定"
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
            await UniMessage.text(f"未找到ID为「{key}」的乐曲").finish(at_sender=True)
        return song
    songs = await song_service.by_alias(key)
    if len(songs) == 1:
        return songs[0]
    if not songs:
        songs = await song_service.by_title_fuzzy(key)
    if not songs:
        await UniMessage.text(f"没有找到「{key}」对应的乐曲").finish(at_sender=True)
    if len(songs) > 1:
        msg = f"找到{len(songs)}首相关乐曲：\n"
        msg += "".join(f"{s.id}：{s.title}\n" for s in songs[:10])
        msg += "※ 请使用「minfo <ID>」指定曲目"
        await UniMessage.text(msg.rstrip("\n")).finish(at_sender=True)
    return songs[0]


@b50.handle()
@handle_errors("查询失败，请稍后再试", except_with_message=(UserScoreError,))
async def _(
    session: Session = UniSession(),
    event: Event = None,
    message: Message = CommandArg(),
):  # type: ignore[assignment]
    username = str(message).strip()
    if username:  # 水鱼公开代查：b50 <水鱼用户名>
        player, bests = await score_service.get_b50_by_username(username)
    else:
        binding = await _get_binding(session, event)
        player = await score_service.get_player(binding)
        bests = await score_service.get_b50(binding)
    png = b50_render.best50_bytes(
        player_name=player.name,
        rating=bests.rating,
        rating_b35=bests.rating_b35,
        rating_b15=bests.rating_b15,
        scores_b35=bests.scores_b35,
        scores_b15=bests.scores_b15,
    )
    await UniMessage.image(raw=png).finish(at_sender=True)


@ap50.handle()
@handle_errors("查询失败，请稍后再试", except_with_message=(UserScoreError,))
async def _(session: Session = UniSession(), event: Event = None):  # type: ignore[assignment]
    binding = await _get_binding(session, event)
    scores = await score_service.get_scores_all(binding)
    # 本地过滤 AP/APP 成绩，按 RA 排序取前 50
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
        await UniMessage.text("没有查到 AP/APP 成绩").finish(at_sender=True)
    png = b50_render.score_list_bytes("AP50", ap_scores)
    await UniMessage.image(raw=png).finish(at_sender=True)


@minfo.handle()
@handle_errors("查询失败，请稍后再试", except_with_message=(UserScoreError,))
async def _(
    session: Session = UniSession(),
    event: Event = None,
    message: Message = CommandArg(),  # type: ignore[assignment]
):
    key = str(message).strip()
    if not key:
        await UniMessage.text("用法：minfo <曲目ID|曲名|别名>").finish(at_sender=True)
    song = await _resolve_song(key)
    binding = await _get_binding(session, event, required=False)
    info = await score_service.get_minfo(song, binding)

    bests = None
    if binding is not None:
        try:
            bests = await score_service.get_b50(binding)
        except UserScoreError:
            bests = None
    b50_min_ra = (
        min((s.dx_rating or 0 for s in bests.scores_b35 + bests.scores_b15), default=0)
        if bests
        else 0
    )
    b50_keys = {(s.id, s.type, s.level_index) for s in bests.scores} if bests else set()

    lines = []
    for score in info.scores:
        in_b50 = (score.id, score.type, score.level_index) in b50_keys
        ra = int(score.dx_rating or 0)
        tip = (
            f"（可进B50，替换后总 RA +{ra - b50_min_ra}）"
            if (bests and not in_b50 and ra > b50_min_ra)
            else ""
        )
        lines.append(
            f"{score.level_index.name} {score.level}（{score.level_value:.1f}）："
            f"{score.achievements or 0:.4f}%  FC:{score.fc.name if score.fc else '-'}"
            f"  DX:{score.dx_score or 0}/{score.level_dx_score}  RA {ra}{tip}"
        )
    if not info.scores:
        lines.append("尚未游玩该曲目（或无权限查看）")
    png = _minfo_image(song, lines)
    await UniMessage.image(raw=png).finish(at_sender=True)


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
        await UniMessage.text("该曲目没有此难度谱面").finish(at_sender=True)
    curve = diff.curve
    if curve is None:
        await UniMessage.text(
            "暂无该谱面的游玩统计（需部署配置水鱼开发者 Token 以启用曲线数据）"
        ).finish(at_sender=True)
    type_abbr = "DX" if diff.type == SongType.DX else "SD"
    lines = [
        f"「{song.id}」{song.title}",
        f"谱面：{type_abbr} {diff.level}（{diff.level_value:.1f}）",
        f"样本数：{curve.sample_size}",
        f"拟合定数：{curve.fit_level_value:.1f}",
        f"平均达成率：{curve.avg_achievements:.2f}%"
        f"（σ {curve.stdev_achievements:.2f}）",
        f"平均 DX 分：{curve.avg_dx_score:.0f}",
    ]
    rate_data = sorted(
        ((rate.name, int(cnt)) for rate, cnt in curve.rate_sample_size.items()),
        key=lambda x: -x[1],
    )
    pie_png = pie_render.pie_bytes(f"{song.title} [{diff.level}] 评级分布", rate_data)
    png = _minfo_image(song, lines, extra_png=pie_png)
    await UniMessage.image(raw=png).finish(at_sender=True)


def _minfo_image(song, lines: list[str], extra_png: bytes | None = None) -> bytes:
    """谱面信息卡 + 文本行（+ 可选统计图）纵向拼接。"""
    from PIL import Image

    card = song_render.draw_song_card(song)
    text_img = text_to_image("\n".join(lines), size=22, padding=14)
    extras: list[Image.Image] = [card, text_img]
    if extra_png:
        from io import BytesIO

        extras.append(Image.open(BytesIO(extra_png)).convert("RGBA"))
    total_h = sum(im.size[1] for im in extras) + 8 * (len(extras) - 1)
    w = max(im.size[0] for im in extras)
    out = Image.new("RGBA", (w, total_h), "#f2f3f5")
    y = 0
    for im in extras:
        out.paste(im, (0, y))
        y += im.size[1] + 8
    return image_to_bytes(out)
