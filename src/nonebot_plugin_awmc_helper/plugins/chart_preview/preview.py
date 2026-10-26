"""谱面预览编排：难度词解析 → 曲目定位 → 抓取/解析/渲染/缓存。

渲染是 CPU 密集任务（整曲约 3–8 分钟，视 CPU），调用方必须在线程池执行
（``asyncio.to_thread``）；产物按 (根 id, 谱面类型, 难度) 落盘缓存，
命中直接发送。
"""

import asyncio
from pathlib import Path

from nonebot import logger
from nonebot_plugin_localstore import get_cache_dir

from ...config import plugin_config
from ...core.simai import SimaiDifficulty, parse_simai
from ...core.songs import ChartEntry, utage_diff_of
from ...core.types import SongType, LevelIndex
from ...core.ext.lxns_assets import (
    ChartNotAvailableError,
    fetch_music,
    fetch_chart_text,
)
from ...core.render.chart_notes import parse_site_chart
from ...core.render.chart_video import render_chart_video

_SUFFIX_BY_INDEX = {0: 2, 1: 3, 2: 4, 3: 5, 4: 6}
"""难度序（LevelIndex 0-4）→ simai 难度后缀（2-6）。"""

_INDEX_NAMES = ("Basic", "Advanced", "Expert", "Master", "Re:Master")

_CACHE_SUBDIR = "chart_preview"
_CACHE_REVISION = 2
"""渲染器版本：算法变更时递增，避免命中旧渲染器产物。"""


def cache_dir() -> Path:
    d = get_cache_dir("nonebot_plugin_awmc_helper") / _CACHE_SUBDIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sweep_cache(cap_mb: int) -> None:
    """缓存总量超限时按 mtime 从旧到新清理（渲染产物大，防止无限增长）。"""
    cap = cap_mb * 1024 * 1024
    files = sorted(cache_dir().glob("*.mp4"), key=lambda p: p.stat().st_mtime)
    total = sum(p.stat().st_size for p in files)
    for p in files:
        if total <= cap:
            break
        total -= p.stat().st_size
        p.unlink(missing_ok=True)
        logger.debug(f"chart_preview 缓存清理：{p.name}")


def _pick_utage_suffix(chart: dict[int, SimaiDifficulty], utage_diff) -> int | None:
    """宴谱 simai 后缀定位：优先按规范表物量对拍，兜底取最小后缀。"""
    if utage_diff is not None:
        expect = (utage_diff.tap_num, utage_diff.hold_num, utage_diff.slide_num)
        for suffix, diff in sorted(chart.items()):
            c = diff.counts()
            if (c["tap"], c["hold"], c["slide"]) == expect:
                return suffix
    return min(chart) if chart else None


def cached_path(entry: ChartEntry, index: int) -> Path | None:
    """缓存命中查询（供 matcher 决定是否发「渲染中」提示）。"""
    _display_id, song, prefer = entry
    is_utage = prefer is None
    type_key = "ut" if is_utage else ("dx" if prefer is SongType.DX else "sd")
    out = cache_dir() / f"{song.id}_{type_key}_{index}_r{_CACHE_REVISION}.mp4"
    return out if out.exists() else None


async def build_preview(entry: ChartEntry, index: int) -> tuple[Path, str]:
    """谱面类型条目 + 难度序 → 渲染好的 mp4 路径与难度标签。

    失败抛 ValueError/ExtError（message 面向用户）。``index`` 为 LevelIndex
    序号；宴谱条目（prefer=None）忽略该参数，谱面由条目 diff_id 定位。
    """
    display_id, song, prefer = entry
    is_utage = prefer is None
    dx = prefer is SongType.DX

    utage_diff = utage_diff_of(song, display_id) if is_utage else None
    if is_utage:
        level_label = utage_diff.kanji if utage_diff else "宴"
    else:
        diff = song.get_difficulty(prefer, LevelIndex(index))
        if diff is None:
            raise ValueError(f"该曲目没有 {_INDEX_NAMES[index]} 难度")
        level_label = diff.level

    type_key = "ut" if is_utage else ("dx" if dx else "sd")
    out = cache_dir() / f"{song.id}_{type_key}_{index}_r{_CACHE_REVISION}.mp4"
    if out.exists():
        return out, level_label

    simai_text, _official_id = await fetch_chart_text(song.id, dx=dx)
    if is_utage:
        # 宴谱后缀与难度无固定对应：先用物量对拍（simai.py 的水鱼口径物量）
        # 定位后缀，再用站内渲染口径的解析器重建 note
        parsed = parse_simai(simai_text)
        suffix = _pick_utage_suffix(parsed.difficulties, utage_diff)
        if suffix is None:
            raise ValueError("该宴谱没有可渲染的谱面数据")
    else:
        suffix = _SUFFIX_BY_INDEX[index]
    site_charts = parse_site_chart(simai_text, difficulty=suffix)
    site_chart = site_charts.get(suffix)
    if site_chart is None:
        raise ValueError("素材站的谱面数据缺少该难度")

    try:
        audio = await fetch_music(song.id)
    except ChartNotAvailableError:
        audio = None  # 个别无音频场景：出无声视频

    limit = plugin_config.awmc_chart_preview_limit_seconds or None
    await asyncio.to_thread(
        render_chart_video,
        site_chart,
        audio=audio,
        out_path=out,
        limit_seconds=limit,
        workers=plugin_config.awmc_chart_preview_workers,
        supersample=plugin_config.awmc_chart_preview_supersample,
        fps=plugin_config.awmc_chart_preview_fps,
    )
    _sweep_cache(plugin_config.awmc_chart_preview_cache_mb)
    return out, level_label
