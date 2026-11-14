"""理论最大 best50/40（max50 家族）：视图曲库上界卡编排。

与用户成绩无关——绑定源视图曲库全部已上线标准/DX 谱面按满分计 RA
（:func:`core.calc.max_scores`），经公共 :func:`core.calc.build_bests` 拆分
组装；版本分界与真实 b50 卡同口径（CN ``current_version`` / JP
``current_version_jp``，随绑定视图，未绑定默认 CN）。
"""

from maimai_py import PlayerBests, current_version, current_version_jp

from .calc import max_scores, build_bests, max_sort_key
from .score import score_service
from .songs import song_service
from .render import jp_cover


async def max_bests(binding, *, legacy: bool = False) -> PlayerBests:
    """绑定源视图的理论最大 best50（``binding=None`` = CN 视图）。

    ``legacy=True``：b40max——旧系数（FiNALE）口径重算 + 恒拆 25/15；
    否则现行系数 35/15。返回结构供渲染层与真实 b50 卡直接同构消费。
    """
    jp = binding is not None and score_service.view_of(binding.service) == "jp"
    songs = await (song_service.jp_all() if jp else song_service.get_all())
    return build_bests(
        max_scores(songs, legacy=legacy),
        key=max_sort_key,
        latest_version_value=current_version_jp.value if jp else current_version.value,
        old_cap=25 if legacy else 35,
    )


async def prefetch_covers(bests) -> None:
    """出图前预取封面（render/best50 行卡按文件直读，无在线链）。

    经 ``jp_cover.get_cover_path`` 唯一入口：static 命中即时返回，缺失才走
    在线链落缓存（JP-only 曲 / 素材包未收录的新曲补上真实封面，失败自然
    落占位图）。
    """
    for score in bests.scores:
        await jp_cover.get_cover_path(score.id)
