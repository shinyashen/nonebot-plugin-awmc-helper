"""谱面预览编排：难度词解析 → 曲目定位 → 抓取/解析/渲染/缓存。

渲染是 CPU 密集任务（整曲约半分钟，视 CPU），调用方必须在线程池执行
（``asyncio.to_thread``）；产物按 (根 id, 谱面类型, 难度) 落盘缓存，
命中直接发送。
"""

import json
import asyncio
from pathlib import Path
from dataclasses import dataclass

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
    fetch_hit_sounds,
)
from ...core.render.chart_notes import parse_site_chart
from ...core.render.chart_range import (
    snap_range,
    add_preroll,
    format_time,
    parse_range_token,
)
from ...core.render.chart_stats import ChartStats, compute_chart_stats
from ...core.render.chart_video import render_chart_video

_SUFFIX_BY_INDEX = {0: 2, 1: 3, 2: 4, 3: 5, 4: 6}
"""难度序（LevelIndex 0-4）→ simai 难度后缀（2-6）。"""

_INDEX_NAMES = ("Basic", "Advanced", "Expert", "Master", "Re:Master")

_CACHE_SUBDIR = "chart_preview"
_CACHE_REVISION = 7


@dataclass(frozen=True)
class PreviewResult:
    """渲染结果 + 合并转发所需的歌曲/视频信息。"""

    path: Path
    label: str
    stats: ChartStats
    range_text: str | None = None
    preroll_measures: int = 0


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
        _stats_sidecar(p).unlink(missing_ok=True)
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


def _range_suffix(range_token: str | None) -> str:
    """片段缓存键后缀：用请求的原始范围（对齐前的输入）保证稳定。"""
    if not range_token:
        return ""
    if parse_range_token(range_token) is None:
        return ""
    safe = range_token.replace(":", "m").replace(".", "p")
    pre = max(0, plugin_config.awmc_chart_preview_preroll_measures)
    return f"_t{safe}_p{pre}"


def _speed_suffix(hi_speed: float) -> str:
    return f"_v{hi_speed:g}"


def _stats_sidecar(video: Path) -> Path:
    return video.with_suffix(".stats.json")


def _write_stats(result: PreviewResult) -> None:
    payload = {
        "label": result.label,
        "range_text": result.range_text,
        "preroll_measures": result.preroll_measures,
        "chart_ms": result.stats.chart_ms,
        "measures": result.stats.measures,
        "counts": result.stats.counts,
        "ex_counts": result.stats.ex_counts,
    }
    _stats_sidecar(result.path).write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def _read_stats(video: Path) -> PreviewResult | None:
    sidecar = _stats_sidecar(video)
    if not sidecar.exists():
        return None
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        stats = ChartStats(
            chart_ms=float(payload["chart_ms"]),
            measures=int(payload["measures"]),
            counts={k: int(v) for k, v in payload["counts"].items()},
            ex_counts={k: int(v) for k, v in payload["ex_counts"].items()},
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return PreviewResult(
        path=video,
        label=str(payload.get("label", "")),
        stats=stats,
        range_text=payload.get("range_text"),
        preroll_measures=int(payload.get("preroll_measures", 0)),
    )


def cached_path(
    entry: ChartEntry,
    index: int,
    range_token: str | None = None,
    hi_speed: float = 7.0,
) -> Path | None:
    """缓存命中查询（供 matcher 决定是否发「渲染中」提示）。"""
    _display_id, song, prefer = entry
    is_utage = prefer is None
    type_key = "ut" if is_utage else ("dx" if prefer is SongType.DX else "sd")
    suffix = _range_suffix(range_token) + _speed_suffix(hi_speed)
    out = cache_dir() / f"{song.id}_{type_key}_{index}_r{_CACHE_REVISION}{suffix}.mp4"
    return out if out.exists() else None


async def build_preview(
    entry: ChartEntry,
    index: int,
    range_token: str | None = None,
    hi_speed: float = 7.0,
) -> PreviewResult:
    """谱面类型条目 + 难度序 → 渲染好的 mp4 路径与难度标签。

    失败抛 ValueError/ExtError（message 面向用户）。``index`` 为 LevelIndex
    序号；宴谱条目（prefer=None）忽略该参数，谱面由条目 diff_id 定位。
    """
    display_id, song, prefer = entry
    is_utage = prefer is None
    dx = prefer is SongType.DX

    utage_diff = utage_diff_of(song, display_id) if is_utage else None
    diff = None
    if is_utage:
        level_label = utage_diff.kanji if utage_diff else "宴"
    else:
        diff = song.get_difficulty(prefer, LevelIndex(index))
        if diff is None:
            raise ValueError(f"该曲目没有 {_INDEX_NAMES[index]} 难度")
        level_label = diff.level

    type_key = "ut" if is_utage else ("dx" if dx else "sd")
    suffix = _range_suffix(range_token) + _speed_suffix(hi_speed)
    out = cache_dir() / f"{song.id}_{type_key}_{index}_r{_CACHE_REVISION}{suffix}.mp4"
    if out.exists():
        cached = _read_stats(out)
        if cached is not None:
            return cached

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

    stats = compute_chart_stats(site_chart, utage_diff or diff)
    clip = None
    clip_start = 0.0
    clip_end = None
    notes_start = None
    range_text: str | None = None
    preroll_measures = 0
    if range_token:
        parsed = parse_range_token(range_token)
        if parsed is None:
            raise ValueError("片段范围格式应形如 0:35-1:20 或 35-80（单位秒）")
        clip = snap_range(site_chart, parsed[0], parsed[1])
        preroll = max(0, plugin_config.awmc_chart_preview_preroll_measures)
        clip_start, notes_start, clip_end = add_preroll(site_chart, clip, preroll)
        preroll_measures = preroll
        start_label = "0:00" if parsed[0] is None else format_time(clip.start_ms)
        end_label = "结尾" if parsed[1] is None else format_time(clip.end_ms)
        range_text = f"{start_label}-{end_label}"
        level_label = f"{level_label} · {range_text}"

    if out.exists():
        # 旧缓存（或无侧车）补建统计侧车后直接返回，不重复渲染
        result = PreviewResult(
            path=out,
            label=level_label,
            stats=stats,
            range_text=range_text,
            preroll_measures=preroll_measures,
        )
        _write_stats(result)
        return result

    try:
        audio = await fetch_music(song.id)
    except ChartNotAvailableError:
        audio = None  # 个别无音频场景：出无声视频
    hit_sounds = None
    if plugin_config.awmc_chart_preview_hit_sounds:
        hit_sounds = await fetch_hit_sounds()

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
        sound_effects=hit_sounds,
        sound_offset_ms=plugin_config.awmc_chart_preview_sound_offset_ms,
        loudness_lufs=plugin_config.awmc_chart_preview_loudness_lufs,
        start_ms=clip_start,
        end_ms=clip_end,
        notes_start_ms=notes_start,
        hi_speed=hi_speed,
        ffmpeg=plugin_config.awmc_chart_preview_ffmpeg,
    )
    result = PreviewResult(
        path=out,
        label=level_label,
        stats=stats,
        range_text=range_text,
        preroll_measures=preroll_measures,
    )
    _write_stats(result)
    _sweep_cache(plugin_config.awmc_chart_preview_cache_mb)
    return result
