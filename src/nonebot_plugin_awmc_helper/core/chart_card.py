"""谱面卡组装：按绑定态拉 B50 并渲染查歌富卡（多子插件共用）。

music_query（查歌）、random_song（随个/mai什么）等子插件统一经此渲染
Hoshino/NB 的 ``draw_chart_info`` 语义：绑定且能拉到 B50 时嵌入成绩与
加分预测（calc=True），否则出纯谱面卡。
"""

from nonebot import logger
from maimai_py import Song, SongType
from nonebot_plugin_alconna.uniseg import UniMessage

from .score import UserScoreError, score_service

# 单结果命中的日服限定标注：再导出 core.songs 的单源（random_song 直连
# 同名常量），plugins 层消费方经本模块 import 不动
from .songs import SINGLE_JP_NOTE as JP_ONLY_NOTE
from .songs import cn_song_map, song_service, utage_diff_of, entries_list_text
from .render import nb_chart
from ..constants import DEFAULT_THEME, UTAGE_ID_BASE


async def resolve_card_view(song: Song, binding=None) -> "tuple[Song, bool]":
    """出卡路由（L-5 拍板，查歌/随机曲共用单源）：NET 等日服视图数据源的
    用户，曲对象换 JP 视图（缺失回退原对象）并以日服口径出卡（jp=True，
    不嵌国服 B50——NET 成绩 id 形状与 CN 视图 B50 消费侧失配）；其余绑定
    原样返回 ``(song, False)``。视图判定走 score_service.view_of（数据源
    注册表单源）。

    日服限定**提示**与该路由解耦：提示只看「该曲是否国服缺席」，NET 用户
    查到国服在架曲不因本路由误弹提示。
    """
    if binding is None or score_service.view_of(binding.service) != "jp":
        return song, False
    return (await song_service.jp_by_id(song.id)) or song, True


async def chart_card_bytes(
    song: Song,
    binding=None,
    prefer_type: SongType | None = None,
    jp: bool = False,
) -> bytes:
    """谱面卡：绑定时拉 B50 嵌成绩与加分预测（Q3 选项 B，对齐 NB 版）。

    ``prefer_type``：别名带谱面前缀（标准/标 → SD，dx → DX）或数字 id 形状
    时指定卡片主类型；``jp=True``：日服视图渲染（日服 logo，不嵌国服 B50
    ——谱面与定数可能不同，混算无意义）。
    """
    is_banquet = nb_chart.is_banquet(song)
    if is_banquet or jp:
        # 宴曲多为日服限定 / 日服限定曲本地无素材：封面按需在线拉取
        # （代理优先，落盘缓存；已有本地封面时静默跳过）
        from .render import jp_cover

        await jp_cover.ensure(song.id)
    if is_banquet:
        return nb_chart.song_chart_banquet_info(song, jp=jp)
    calc, is_full, best_list = False, False, []
    theme = DEFAULT_THEME
    if binding is not None and not jp:
        # 主题取用户偏好且不受 B50 拉取成败影响：失败只丢成绩嵌入，不退默认配色
        theme = binding.theme or DEFAULT_THEME
        ident = binding_service_ident(binding)
        if ident is not None:
            try:
                bests = await score_service.get_b50(binding)
                # 主类型判定单源 nb_chart.major_type_of（布尔派生与旧内联在
                # 「dx 组空且 prefer≠STANDARD」形态取值不同，但下游
                # chart_version_of 组空回落标准组，输出等价）
                prefer_sd = (
                    nb_chart.major_type_of(song, prefer_type) == SongType.STANDARD
                )
                # b50 的 b35/b15 分段按谱面登场版本（旧版本→b35、当前版本→b15，
                # SD/DX 均可，老曲补的 DX 谱也在 b35）：按谱面类型过滤拍平列表
                # 会剔掉 b35 里的 DX 条目，is_full 误判 False、入线线失真，
                # 加分预测全量虚高——侧别对齐 NB 的 song.isnew 选段语义
                is_new_chart = nb_chart.is_new_chart(
                    nb_chart.chart_version_of(song, prefer_sd)
                )
                # 降序（NB b50 列表语义）
                best_list = sorted(
                    bests.scores_b15 if is_new_chart else bests.scores_b35,
                    key=lambda s: s.dx_rating or 0,
                    reverse=True,
                )
                is_full = len(best_list) >= (15 if is_new_chart else 35)
                calc = True
            except UserScoreError as e:
                # 成绩卡照常出（不带 B50 信息），但留痕排障
                logger.debug(f"谱面卡 B50 信息拉取失败（song={song.id}）：{e}")
    return nb_chart.song_chart_info(
        song, calc, is_full, best_list, theme, prefer_type, jp
    )


def binding_service_ident(binding):
    """绑定 → maimai_py identifier（无凭据 None）。延迟导入避免循环依赖。"""
    from .binding import binding_service

    return binding_service.identifier_or_none(binding)


async def song_lookup_reply(
    entries, binding, *, extra_note: str | None = None
) -> UniMessage:
    """谱面类型条目 → 「是什么歌」同款回复（多子插件共用，文案单源）。

    单条目出谱面卡（宴谱条目出宴会卡，日服限定宿主保留 JP 对象）、多条目出
    条目列表（cn_song_map 逐条标注日服限定）；``extra_note`` 追加在末尾
    （如分数线无难度色时的缺失提示），不改变既有文案与顺序。

    返回 UniMessage 不发送，调用方自行 ``finish``——music_query
    「<名称>是什么歌」与 score_tools「分数线」无难度色回退共用本函数。
    """
    cn_songs = await cn_song_map([s for _, s, _ in entries])
    flags = [cn_songs[s.id] is None for _, s, _ in entries]
    if len(entries) == 1:
        entry_id, song, prefer = entries[0]
        if entry_id >= UTAGE_ID_BASE:
            # 宴谱条目：宿主曲即便有普通谱也渲染宴会场卡；该张可能日服限定
            # （国服宿主曲无此 diff_id）——保留 JP 宿主对象画日服卡
            from .render import jp_cover

            await jp_cover.ensure(song.id)
            cn_song = cn_songs[song.id]
            cn_diff = utage_diff_of(cn_song, entry_id) if cn_song is not None else None
            if cn_song is not None and cn_diff is not None:
                song, utage_diff, jp = cn_song, cn_diff, False
            else:
                jp = True
                utage_diff = utage_diff_of(song, entry_id)
            diffs = [utage_diff] if utage_diff is not None else None
            png = nb_chart.song_chart_banquet_info(song, diffs, jp=jp)
        else:
            cn_song = cn_songs[song.id]
            song = cn_song or song
            card_song, jp_card = await resolve_card_view(song, binding)
            jp = flags[0]
            # 追加谱面组回退（与 resolve_raw_chart 同口径，2026-10-06 居並ぶ
            # SD 追加实测）：偏好类型谱面 CN 对象缺而日服视图有 → 日服对象
            # 出卡（jp=True 不嵌国服 B50——该类型谱面国服无成绩可嵌）
            if (
                prefer is not None
                and cn_song is not None
                and not cn_song.get_difficulties(prefer)
                and (jp_song := await song_service.jp_by_id(song.id)) is not None
                and jp_song.get_difficulties(prefer)
            ):
                card_song, jp = jp_song, True
            png = await chart_card_bytes(card_song, binding, prefer, jp or jp_card)
        msg = UniMessage.text(f" {JP_ONLY_NOTE}") if jp else UniMessage()
        text = "您要找的是不是这首？"
        if extra_note:
            text += f"\n{extra_note}"
        return msg.image(raw=png).text(text)
    hint = "※ 请使用「id xxxxx」查询指定谱面"
    if extra_note:
        hint += f"\n{extra_note}"
    return UniMessage.text(" " + entries_list_text(entries, flags, hint=hint))
