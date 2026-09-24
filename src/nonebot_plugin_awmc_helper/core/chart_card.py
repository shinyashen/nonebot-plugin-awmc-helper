"""谱面卡组装：按绑定态拉 B50 并渲染查歌富卡（多子插件共用）。

music_query（查歌）、random_song（随个/mai什么）等子插件统一经此渲染
Hoshino/NB 的 ``draw_chart_info`` 语义：绑定且能拉到 B50 时嵌入成绩与
加分预测（calc=True），否则出纯谱面卡。
"""

from nonebot import logger
from maimai_py import Song, SongType

from .score import UserScoreError, score_service
from .render import nb_chart


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
    if nb_chart.is_banquet(song):
        # 宴曲多为日服限定：封面按需在线拉取（已有本地封面时静默跳过）
        from .render import jp_cover

        await jp_cover.ensure(song.id)
        return nb_chart.song_chart_banquet_info(song, jp=jp)
    if jp:
        # 日服限定曲本地无素材：按需在线拉取官方曲绘（代理优先，落盘缓存）
        from .render import jp_cover

        await jp_cover.ensure(song.id)
    calc, is_full, best_list = False, False, []
    theme = "prism_plus"
    if binding is not None and not jp:
        ident = binding_service_ident(binding)
        if ident is not None:
            try:
                bests = await score_service.get_b50(binding)
                prefer_dx = (
                    prefer_type == SongType.STANDARD and not song.difficulties.standard
                ) or prefer_type != SongType.STANDARD
                major_dx = prefer_dx and bool(song.difficulties.dx)
                side_type = SongType.DX if major_dx else SongType.STANDARD
                # 降序（NB b50 列表语义）
                best_list = sorted(
                    (s for s in bests.scores if s.type == side_type),
                    key=lambda s: s.dx_rating or 0,
                    reverse=True,
                )
                is_full = len(best_list) >= (15 if major_dx else 35)
                calc = True
                theme = binding.theme or "prism_plus"
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
