"""查歌结果渲染：国服命中 → 日服 fallback → pending 兜底的编排与出图。

1 首出卡片、≤5 首文本、更多列表图（25/页）；各链路收口到 ``finish``。
"""

from nonebot_plugin_alconna.uniseg import UniMessage

from ...constants import display_song_id
from ...core.songs import cn_song_map, list_jp_note
from ...core.render import song as song_render
from ...core.render import jp_cover, nb_chart
from ...core.songdb import PendingSong
from ...core.chart_card import JP_ONLY_NOTE, chart_card_bytes


def _reply(text: str) -> UniMessage:
    """at 发送者的文本回复（at 与文本之间留一个空格，保持可读性）。"""
    return UniMessage.text(f" {text}")


NOT_FOUND = "没有找到这样的乐曲。\n※ 如果是别名请使用「XXX是什么歌」指令进行查询哦。"


async def _banquet_card(song, utage_diff=None, jp: bool = False) -> bytes:
    """宴谱条目卡：宿主曲可能同时有普通谱（is_banquet 判定不成立），
    但条目 id ≥ 100000 时必须渲染宴会场卡（Hoshino 按 song_id ≥ 100000
    路由同语义）；日服宴曲封面在线兜底。

    ``utage_diff``：要画的宴谱（id/条目召唤传命中的那张；一个宴谱 id 只
    对应一张谱面），缺省取宿主曲第一张。``jp=True``：日服限定宿主曲
    （JP 视图对象），宴会卡不渲染国服口径的新曲标。
    """
    await jp_cover.ensure(song.id)
    diffs = [utage_diff] if utage_diff is not None else None
    return nb_chart.song_chart_banquet_info(song, diffs, jp=jp)


async def _render_result(songs, page: int, binding=None) -> None:
    """1 首出卡片；≤5 文本；更多列表图（25/页）。"""
    if not songs:
        await _reply(NOT_FOUND).finish(at_sender=True)
    if len(songs) == 1:
        png = await chart_card_bytes(songs[0], binding)
        await UniMessage.image(raw=png).finish(at_sender=True)
    if len(songs) <= 5:
        text = "".join(f"「{display_song_id(s)}」 {s.title}\n" for s in songs)
        await _reply(text.rstrip("\n")).finish(at_sender=True)
    await (
        UniMessage.image(raw=song_render.song_list_bytes(songs, page))
        .text(f"第 {page} 页，共 {len(songs)} 首，可用「查歌 <标题> {page + 1}」翻页")
        .finish(at_sender=True)
    )


async def _render_jp_result(songs, page: int, binding=None) -> None:
    """日服 fallback 结果：逐曲判定日服限定，混合列表只标注限定曲。

    日服视图含国服也有的曲（标题子串、日服定数口径变更等场景可命中）：
    整列表国服都有时按普通结果渲染，混合时国服曲回取国服对象。
    """
    # 逐曲日服判定须回查国服视图：去重后一次并发建 map（判定与回取共用，见 core）
    cn_songs = await cn_song_map(songs)
    flags = [cn_songs[s.id] is None for s in songs]
    if not any(flags):
        await _render_result([cn_songs[s.id] or s for s in songs], page, binding)
        return
    note = list_jp_note(flags)
    if len(songs) == 1:
        png = await chart_card_bytes(songs[0], None, None, True)
        await _reply(JP_ONLY_NOTE).image(raw=png).finish(at_sender=True)
    if len(songs) <= 5:
        text = "".join(
            f"「{display_song_id(s)}」 {s.title}{'（日服限定）' if f else ''}\n"
            for s, f in zip(songs, flags)
        )
        await _reply(text.rstrip("\n") + "\n" + note).finish(at_sender=True)
    await (
        UniMessage.image(raw=song_render.song_list_bytes(songs, page))
        .text(f"第 {page} 页，共 {len(songs)} 首\n" + note)
        .finish(at_sender=True)
    )


async def _render_query_result(
    songs, jp_fetch, page: int, binding=None, pending_fetch=None
) -> None:
    """查询结果渲染：国服命中走普通结果，未命中走日服 fallback（Q32），
    日服也未命中走 pending 兜底（id 未收录新曲，Q33）。

    返回值驱动：fallback 未命中明确落到「未找到」，不再依赖
    ``_render_jp_result`` 内部必 finish 的控制流（避免未来加 return 路径双发）。
    """
    if not songs:
        songs = await jp_fetch()
        if songs:
            await _render_jp_result(songs, page, binding)
            return
        if pending_fetch is not None and await _render_pending_result(
            await pending_fetch()
        ):
            return
    await _render_result(songs, page, binding)


PENDING_NOTE = "※ 该曲机台 id 尚未收录，为新曲暂存信息（缺项以 - 显示）"


async def _pending_card(pending: PendingSong) -> bytes:
    """pending 临时卡：物化为临时 Song 走共享查歌卡渲染（「0 即 -」约定）。

    ID 行覆写为「ID —」（不猜 id）；分类行用 catcode 映射（未知画 -）；
    封面按 payload 的官方文件名在线拉取，缺则占位图。
    """
    from ...core.songdb import pending_to_song

    cover = None
    if pending.image_url:
        cover = await jp_cover.ensure_image(pending.image_url, pending.cover_key)
    return nb_chart.song_chart_info(
        pending_to_song(pending),
        calc=False,
        is_full=False,
        best_list=[],
        jp=True,
        id_text="ID —",
        genre_text=pending.genre_display or "-",
        cover_path=cover,
    )


async def _render_pending_result(pending: "list[PendingSong]") -> bool:
    """pending 结果渲染：单首出临时卡，多首文本列表。

    返回是否已发送（finish）；空列表返回 False 交回调用方继续兜底。
    """
    if not pending:
        return False
    if len(pending) == 1:
        png = await _pending_card(pending[0])
        await _reply(PENDING_NOTE).image(raw=png).finish(at_sender=True)
    lines = []
    for p in pending:
        charts = p.major_charts()
        ds = "、".join(
            f"{'DX' if c.is_dx else 'SD'}{c.level_id + 1} {c.level_value:.1f}"
            for c in charts
        )
        lines.append(f"「{p.title}」 {ds}" if ds else f"「{p.title}」")
    await _reply("\n".join(lines) + f"\n{PENDING_NOTE}").finish(at_sender=True)
