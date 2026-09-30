"""查分指令入口：b50 / ap50 / minfo / ginfo。"""

from nonebot import on_regex, on_command
from maimai_py import current_version_jp
from nonebot.params import CommandArg, RegexGroup
from nonebot.adapters import Event, Message
from nonebot_plugin_uninfo import Session, UniSession
from nonebot_plugin_alconna.uniseg import UniMessage

from .render import _ginfo_image
from ...constants import DEFAULT_THEME, COLOR_TO_LEVEL_INDEX
from ...core.help import CommandSpec, help_registry
from ...core.combo import (
    ComboEmpty,
    OutputKind,
    ComboAmbiguity,
    run_combo,
    parse_combo,
    inapplicable,
    ensure_designer_rules,
)
from ...core.score import UserScoreError, build_bests, score_service
from ...core.songs import (
    ChartEntry,
    cn_song_map,
    song_service,
    entries_list_text,
    prefer_type_from_raw_id,
)
from ...core.types import FCType, SongType, LevelIndex
from ...core.utils import slow_notice, handle_errors, player_display_name
from ...core.render import info as info_render
from ...core.render import stats as stats_render
from ...core.render import best50 as b50_render
from ...core.render import jp_cover, nb_chart
from ...core.songdb import Scope
from ...core.binding import (
    UserBinding,
    SessionQueryBinding,
    at_tolerant,
    query_binding,
    binding_service,
    resolve_session_query,
)
from ...core.sources import Capability

AP_FC_VALUES = (FCType.AP.value, FCType.APP.value)  # 越小越好

b50 = on_command("b50", aliases={"B50"}, block=True)
ap50 = on_command("ap50", aliases={"AP50"}, block=True)
# 条件组合查询（辉50/紫谱50/神50/dx2024b40 类）：`^(.+?)(40|50)$` 松匹配，
# 「b40」的 b 由 tokenizer 未识别残片跳过自然吸收（nb40=牛逼、dx2024b40=回到
# 过去，零歧义）；priority 必须低于全部既有指令（b50/13fc完成表 等先命中即
# block，杜绝被本 matcher 吞掉）
combo50 = on_regex(at_tolerant(r"^(.+?)(40|50)$"), block=True, priority=5)
minfo = on_command(
    "minfo", aliases={"Minfo", "MINFO", "info", "Info", "INFO"}, block=True
)
ginfo = on_regex(r"^[gG]info\s?(?:([绿黄红紫白])(?=\s|\d))?(.+)$", block=True)


async def _resolve_song(key: str):
    """按 ID/别名/标题 解析曲目（ginfo 用；同一曲的多条谱面按所选难度定类型）。"""
    key = key.strip()
    if key.isdigit():
        # 数字 id 单源解析（与查歌同口径）：6 位宴谱 diff_id 命中即文本终止
        # ——宴谱没有游玩统计，且 by_id 对 6 位 id 取模会错配同号普通曲
        # （L-7：提示语引导输入 6 位 id，却渲染宿主曲统计卡的矛盾收口）；
        # 其余获得日服兜底（JP-only 曲不再直接「未找到」）
        hit = await song_service.resolve_raw_chart(int(key))
        if hit is None:
            await UniMessage.text(f" 未找到ID为「{key}」的乐曲").finish(at_sender=True)
        song, _prefer, jp, utage_diff = hit
        if utage_diff is not None:
            await UniMessage.text(" 宴谱没有游玩统计").finish(at_sender=True)
        if jp:
            # JP-only 曲封面不在本地素材包：与查歌卡同口径在线兜底落盘
            await jp_cover.ensure(song.id)
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
        msg += "※ 请使用「ginfo <ID>」指定曲目"
        await UniMessage.text(msg.rstrip(" \n")).finish(at_sender=True)
    return songs[0]


async def _minfo_entries(key: str, scope: Scope) -> "list[ChartEntry]":
    """minfo 曲目定位 → 谱面类型条目（数字 id 单条目 / 名称走 core 条目链）。

    数字 id 按其形状定类型（≤4 位 SD、5 位 DX）；名称命中 SD/DX 双条目时不猜
    主类型（交调用方列出 id 让用户指定，与「是什么歌」同格式同语义，对齐
    Hoshino 基准 info 的多 id 分支）；带谱面前缀（dx/标准/标）时条目收敛到该
    类型。未命中时直接以文案终止。
    """
    if key.isdigit():
        raw_id = int(key)
        song = (
            await song_service.jp_by_id(raw_id)
            if scope == "jp"
            else await song_service.by_id(raw_id)
        )
        if song is None:
            await UniMessage.text(f" 未找到ID为「{key}」的乐曲").finish(at_sender=True)
        return [(raw_id, song, prefer_type_from_raw_id(raw_id))]
    entries = await song_service.entries_for_name(
        key, scope=scope, cn_title=scope == "cn"
    )
    if not entries:
        await UniMessage.text(f" 没有找到「{key}」对应的乐曲").finish(at_sender=True)
    return entries


async def _finish_entry_list(entries: "list[ChartEntry]") -> None:
    """多条目歧义提示：列出谱面 id 并终止（附逐条日服限定标注）。"""
    cn_songs = await cn_song_map([s for _, s, _ in entries])
    flags = [cn_songs[s.id] is None for _, s, _ in entries]
    text = entries_list_text(
        entries, flags, hint="※ 请使用「ginfo <ID>」查询指定谱面", limit=10
    )
    await UniMessage.text(f" {text}").finish(at_sender=True)


async def _minfo_net(key: str, binding) -> None:
    """日服 minfo：JP 视图曲解析 + NET 窗口缓存成绩 + 日服谱面卡。

    与 CN minfo 的差异：曲走 JP 视图（数字 id 折根/别名/标题）、成绩来自
    NET 抓取缓存（窗口内 b50/minfo 共享一份）、渲染前补拉日服曲绘。
    """
    entries = await _minfo_entries(key, "jp")
    song_ids = {s.id for _, s, _ in entries}
    if len(song_ids) > 1:  # 关键词命中多曲：列 id 供用户指定（不查成绩）
        await _finish_entry_list(entries)
    prefer = entries[0][2] if len(entries) == 1 else None
    if score_service.needs_fetch(binding):
        await UniMessage.text(" 正在登录日服 NET 抓取成绩，请稍候…").send(
            at_sender=True
        )
    info = await score_service.get_minfo(entries[0][1], binding, prefer)
    if info is None:  # 该谱面类型无成绩（或整曲未游玩）→ 文本提示，不画空卡
        await UniMessage.text(" 尚未游玩过该曲目").finish(at_sender=True)
    if len(entries) > 1:  # 双谱曲且玩过：列 id 让用户指定看哪张
        await _finish_entry_list(entries)
    song = entries[0][1]
    await jp_cover.ensure(song.id)
    png = info_render.song_play_data(
        song,
        info.scores,
        service=binding.service,
        theme=binding.theme or DEFAULT_THEME,
        prefer_type=prefer,
    )
    await UniMessage.image(raw=png).finish(at_sender=True)


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
            player_name=player_display_name(player),
            rating=bests.rating,
            rating_b35=bests.rating_b35,
            rating_b15=bests.rating_b15,
            scores_b35=bests.scores_b35,
            scores_b15=bests.scores_b15,
            player=player,
            service="divingfish",
        )
    else:
        binding = await query_binding(
            session,
            event,
            unbound_hint=(
                "对方尚未绑定查分器，无法代查（水鱼可使用「b50 <水鱼用户名>」公开代查）"
            ),
        )
        if score_service.view_of(binding.service) == "jp":
            # 日服 NET：窗口缓存优先（首次/过期时真实抓取，约 5-15 秒）；
            # 身份卡（玩家名/称号/头像/徽章）经 core 共用链路装配
            if score_service.needs_fetch(binding):
                await UniMessage.text(" 正在登录日服 NET 抓取成绩，请稍候…").send(
                    at_sender=True
                )
            bests = await score_service.get_b50(binding)
            png = await b50_render.net_best50_card(bests, binding)
        else:
            notify_slow = slow_notice()
            player = await score_service.get_player(binding, notify_slow=notify_slow)
            bests = await score_service.get_b50(binding, notify_slow=notify_slow)
            png = await b50_render.best50_bytes(
                player_name=player_display_name(player),
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
    binding: UserBinding = SessionQueryBinding(
        unbound_hint=(
            "对方尚未绑定查分器，无法代查（水鱼可使用「b50 <水鱼用户名>」公开代查）"
        )
    ),
):
    """AP50（用户口径）：b50 的升级版——只统计 AP/APP 的 best50，渲染 B50 大图。

    maimai_py 无 AP50 端点：全量成绩本地过滤 fc∈{AP,APP} 后经公共
    build_bests 组装（Hoshino 落雪 ap50 端点 → Best50 → draw_best50 同构）。
    """

    notify_slow = slow_notice()
    jp = score_service.view_of(binding.service) == "jp"
    if jp:
        # NET：全量成绩来自窗口缓存（抓取提示先行，b50/minfo 同款交互）
        if score_service.needs_fetch(binding):
            await UniMessage.text(" 正在登录日服 NET 抓取成绩，请稍候…").send(
                at_sender=True
            )
        scores = await score_service.get_scores_all(binding)
    else:
        scores = await score_service.get_scores_all(binding, notify_slow=notify_slow)
    ap_scores = [
        s
        for s in scores.scores
        if s.fc is not None
        and s.fc.value in AP_FC_VALUES
        and s.achievements is not None
    ]
    if not ap_scores:
        await UniMessage.text(" 没有查到 AP/APP 成绩").finish(at_sender=True)
    # NET 的旧/新版本分侧用日服现行版本（与 net_score 组装同口径）
    bests = build_bests(
        ap_scores,
        key=lambda s: s.dx_rating or 0,
        latest_version_value=current_version_jp.value if jp else None,
    )
    if jp:  # NET 身份卡与 b50 共用 core 链路（CN 路径走查分器玩家资料）
        png = await b50_render.net_best50_card(bests, binding)
    else:
        player = await score_service.get_player(binding, notify_slow=notify_slow)
        png = await b50_render.best50_bytes(
            player_display_name(player),
            bests.rating,
            bests.rating_b35,
            bests.rating_b15,
            bests.scores_b35,
            bests.scores_b15,
            player=player,
            qqid=binding_service.qq_of(binding),
            service=binding.service,
            theme=binding.theme or DEFAULT_THEME,
        )
    await UniMessage.image(raw=png).finish(at_sender=True)


async def _render_combo(result, binding, output=None) -> bytes:
    """条件50/40 结果渲染：flat（条件50）走 flat 版式，拆分沿用标准 35/15
    版式（b40=25/15 旧系数，同版式卡面）。

    头部 rating 位两种模式均为所列成绩 RA 合计（不是玩家 rating；b40 为
    FiNALE 旧系数口径），称号条以「条件 · 条数 · 合计RA」口径标注防误读
    （落雪称号在场时优先显示称号，仅无称号回退口径条）；NET 源身份卡与
    b50 共用 core 链路。
    """
    if output is OutputKind.B40:
        head = f"{result.title}·" if result.title else ""
        label = f"{head}旧系数b40 · {len(result.scores)} 条 · 合计 RA {result.total_ra}"
    else:
        label = f"{result.title} · {len(result.scores)} 条 · 合计 RA {result.total_ra}"
    if score_service.view_of(binding.service) == "jp":
        return await b50_render.net_best50_card(
            result.bests, binding, flat=result.flat, label=label
        )
    player = await score_service.get_player(binding)
    if result.flat:
        return await b50_render.best50_flat_bytes(
            player_display_name(player),
            result.total_ra,
            result.bests.scores_b35,
            label=label,
            player=player,
            qqid=binding_service.qq_of(binding),
            service=binding.service,
            theme=binding.theme or DEFAULT_THEME,
        )
    return await b50_render.best50_bytes(
        player_display_name(player),
        result.bests.rating,
        result.bests.rating_b35,
        result.bests.rating_b15,
        result.bests.scores_b35,
        result.bests.scores_b15,
        player=player,
        qqid=binding_service.qq_of(binding),
        service=binding.service,
        theme=binding.theme or DEFAULT_THEME,
        trophy_name=label,
    )


@combo50.handle()
@handle_errors("查询失败，请稍后再试", except_with_message=(UserScoreError,))
async def _(
    groups: tuple = RegexGroup(),
    binding: UserBinding = SessionQueryBinding(
        unbound_hint="对方尚未绑定查分器，无法代查"
    ),
):
    """条件组合查询（解析/执行全在 core.combo，子插件只消费四态结果）：

    出图（50=b50 / 40=旧系数 b40）/ 歧义提示（裸紫白）/ 谱面集空文案 /
    静默（零条件——松匹配下以 40/50 结尾的闲聊不是查询，绝不回话防刷屏）。
    """
    # 谱师实名词随曲库动态注册（幂等；曲库未就绪时跳过——冷启动窗口暂不
    # 生效，任一查询加载曲库后下一条起生效，不在闲聊路径上触发加载）
    await ensure_designer_rules()
    cond_text, suffix = groups
    output = OutputKind.B40 if suffix == "40" else OutputKind.B50
    parsed = parse_combo(cond_text)
    if parsed is None:
        return
    if isinstance(parsed, ComboAmbiguity):
        await UniMessage.text(f" {parsed.message}").finish(at_sender=True)
    if bad := inapplicable(parsed, output):
        # §9.7 适用矩阵（era_year/ideal/fit 等 D/C 类仅 b50/40）
        await UniMessage.text(
            f" {'、'.join(c.label for c in bad)} 不适用于条件{suffix}"
        ).finish(at_sender=True)
    if score_service.view_of(binding.service) == "jp" and score_service.needs_fetch(
        binding
    ):
        await UniMessage.text(" 正在登录日服 NET 抓取成绩，请稍候…").send(
            at_sender=True
        )
    result = await run_combo(parsed, binding, output=output, notify_slow=slow_notice())
    if isinstance(result, ComboEmpty):
        await UniMessage.text(f" {result.message}").finish(at_sender=True)
    png = await _render_combo(result, binding, output)
    await UniMessage.image(raw=png).finish(at_sender=True)


@minfo.handle()
@handle_errors("查询失败，请稍后再试", except_with_message=(UserScoreError,))
async def _(
    session: Session = UniSession(),
    event: Event | None = None,
    message: Message = CommandArg(),  # type: ignore[assignment]
):
    # extract_plain_text 丢弃 at 段：minfo 231 @某人 → 代查某人该曲成绩
    key = message.extract_plain_text().strip()
    if not key:
        await UniMessage.text(" 用法：minfo <曲目ID|曲名|别名>").finish(at_sender=True)
    binding, _ = await resolve_session_query(session, event)
    if binding is not None and score_service.view_of(binding.service) == "jp":
        # 数据源为日服视图（现即 NET）：曲走 JP 视图解析 + NET 成绩链路
        await _minfo_net(key, binding)
    entries = await _minfo_entries(key, "cn")
    if len({s.id for _, s, _ in entries}) > 1:  # 关键词命中多曲：列 id（不查成绩）
        await _finish_entry_list(entries)
    prefer = entries[0][2] if len(entries) == 1 else None
    info = await score_service.get_minfo(
        entries[0][1], binding, prefer, notify_slow=slow_notice()
    )
    if info is None:  # 该谱面类型无成绩（或整曲未游玩）→ 文本提示，不画空卡
        await UniMessage.text(" 尚未游玩过该曲目").finish(at_sender=True)
    if len(entries) > 1:  # 双谱曲且玩过：列 id 让用户指定看哪张
        await _finish_entry_list(entries)
    song = entries[0][1]

    # R1：按基准 info.py 版式渲染真实成绩卡（主类型在定位时定好：数字 id 按形状、
    # 唯一命中条目按其类型；双条目/多曲已在上方列出 id 终止）
    png = info_render.song_play_data(
        song,
        info.scores,
        service=binding.service if binding is not None else None,
        theme=(binding.theme or DEFAULT_THEME)
        if binding is not None
        else DEFAULT_THEME,
        prefer_type=prefer,
    )
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


# ---------------------------------------------------------------- 帮助声明

help_registry.declare(
    plugin="awmc.score_query",
    title="查分",
    category="score",
    description="b50 / ap50 / 单曲成绩 / 谱面统计",
    commands=[
        CommandSpec(
            matcher=b50,
            name="b50",
            aliases=("B50",),
            capability=Capability.B50,
            brief="B50 成绩大图（带参数=水鱼用户名代查，@某人=代查）",
        ),
        CommandSpec(
            matcher=ap50,
            name="ap50",
            aliases=("AP50",),
            capability=Capability.SCORES_ALL,
            brief="AP-only best50（全 AP 成绩组装出图）",
        ),
        CommandSpec(
            matcher=combo50,
            name="条件50",
            capability=Capability.SCORES_ALL,
            brief="条件组合查分：辉50 / 紫谱50 / dx2024b40 / 寸50 等（@某人=代查）",
            detail=(
                "格式：<条件串>50，条件可任意叠加（同类「或」、跨类「且」），"
                "末尾以 50 结尾即触发。条件词：\n"
                "谱面：版本字（辉/雪辉/真超檄…可多代连写）、dx/旧框、标准/dx谱、"
                "新版本/旧版本、分类（东方/中二/音击/音击中二/流行动漫/其他游戏/maimai）、"
                "紫谱/白谱/绿/黄/红、13级、14.5定数、谱师名、宴谱\n"
                "成绩：fc/全连/极、ap/神、理论/ap+、舞舞/fdx、将/鸟/sss、"
                "大将/鸟加/sss+、纯<档>/仅<档>、牛逼、越级、一星~五星、寸、"
                "锁/名刀\n"
                "修改：理想（升一档重算）、拟合（拟合定数重算）\n"
                "回到过去：dx2024/舞萌dx2024/2024/dx无印（分界移到该年，定数取时点值）\n"
                "输出：尾缀 50=b50、40/b40=旧系数 b40（25+15，FiNALE 口径）\n"
                "例：东方50、雪辉dx50、紫谱将50、dx2024b50、拟合理想50、nb40"
            ),
        ),
        CommandSpec(
            matcher=minfo,
            name="minfo",
            aliases=("Minfo", "MINFO", "info", "Info", "INFO"),
            capability=Capability.MINFO,
            brief="单曲成绩卡（ID/曲名/别名定位，@某人代查）",
            detail="格式：minfo <曲目ID|曲名|别名>；未绑定时降级纯谱面卡。",
        ),
        CommandSpec(
            matcher=ginfo,
            name="ginfo",
            brief="谱面游玩统计（样本/拟合/评级分布）",
            detail="格式：ginfo <[难度色]曲目>。",
        ),
    ],
)
