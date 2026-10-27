"""谱面预览编排：难度词解析 → 曲目定位 → 抓取/解析/渲染/缓存。

渲染是 CPU 密集任务（整曲约半分钟，视 CPU），调用方必须在线程池执行
（``asyncio.to_thread``）；产物按 (根 id, 谱面类型, 难度) 落盘缓存，
命中直接发送。
"""

import json
import asyncio
import hashlib
from pathlib import Path
from functools import lru_cache
from dataclasses import dataclass

from nonebot import logger
from nonebot_plugin_localstore import get_cache_dir

from ...config import plugin_config
from ...core.simai import parse_simai
from ...core.songs import ChartEntry, utage_diff_of
from ...core.types import SongType, LevelIndex
from ...core.ext.lxns_assets import (
    ChartNotAvailableError,
    fetch_music,
    fetch_chart_text,
    fetch_hit_sounds,
    fetch_chart_text_by_id,
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


# 渲染引擎源码文件：内容变化即视为输出可能变化（缓存指纹的一部分）。
_RENDER_ENGINE_FILES = (
    "chart_video.py",
    "chart_notes.py",
    "chart_audio.py",
    "chart_range.py",
    "fonts.py",
)
# 输出相关配置项（与帧内容/编码有关；workers 不影响输出故不含）。
_OUTPUT_CONFIG_KEYS = (
    "awmc_chart_preview_fps",
    "awmc_chart_preview_supersample",
    "awmc_chart_preview_hit_sounds",
    "awmc_chart_preview_sound_offset_ms",
    "awmc_chart_preview_loudness_lufs",
    "awmc_chart_preview_preroll_measures",
    "awmc_chart_preview_limit_seconds",
    "awmc_chart_preview_ffmpeg",
)
_FONT_FILES = (
    "ResourceHanRoundedCN-Bold.ttf",
    "Torus SemiBold.otf",
    "FOT-NewRodin Pro EB.otf",
    "ShangguMonoSC-Regular.otf",
)


def _hash_file(digest, path: Path) -> None:
    digest.update(path.name.encode())
    try:
        digest.update(path.read_bytes())
    except OSError:
        digest.update(b"<missing>")


@lru_cache(maxsize=16)
def _render_fingerprint(hi_speed: float) -> str:
    """渲染指纹：源码/字体/输出相关配置/流速任一变化 → 缓存键变化并重渲。

    不再用硬编码 ``_CACHE_REVISION``（容易忘记 bump）：改渲染逻辑、字体或
    fps/超采样/音效/响度/前置等配置后，指纹自动变化，旧缓存自然失效。
    """
    digest = hashlib.sha256()
    render_dir = Path(__file__).resolve().parents[2] / "core" / "render"
    for name in _RENDER_ENGINE_FILES:
        _hash_file(digest, render_dir / name)
    _hash_file(digest, Path(__file__).resolve())
    font_dir = Path(plugin_config.awmc_static_path) / "font"
    for name in _FONT_FILES:
        _hash_file(digest, font_dir / name)
    params = [hi_speed] + [getattr(plugin_config, key) for key in _OUTPUT_CONFIG_KEYS]
    digest.update(repr(params).encode())
    return digest.hexdigest()[:12]


@dataclass(frozen=True)
class PreviewResult:
    """渲染结果 + 合并转发所需的歌曲/视频信息。"""

    path: Path
    label: str
    stats: ChartStats
    range_text: str | None = None
    preroll_measures: int = 0
    buddy_stats: ChartStats | None = None  # buddy 宴谱 2P 侧统计（1P 在 stats）


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


_BUDDY_LEFT_SUFFIX = 2  # 落雪 buddy 谱面拆分约定：&inote_2 = 1P
_BUDDY_RIGHT_SUFFIX = 102  # &inote_102 = 2P（落雪独创：1p/2p 两张谱面）


def _counts_tuple(counts: dict[str, int]) -> tuple[int, int, int, int, int]:
    """统一取 5 类物量：兼容 simai 解析的小写键与 ChartStats 的大写键。"""
    lower = {key.lower(): value for key, value in counts.items()}

    def get(name: str) -> int:
        return int(counts[name] if name in counts else lower[name.lower()])

    return (get("TAP"), get("HOLD"), get("SLIDE"), get("TOUCH"), get("BREAK"))


def _official_counts_tuple(diff) -> tuple[int, int, int, int, int]:
    return (
        int(diff.tap_num),
        int(diff.hold_num),
        int(diff.slide_num),
        int(diff.touch_num),
        int(diff.break_num),
    )


def _counts_distance(left: tuple[int, ...], right: tuple[int, ...]) -> int:
    return sum(abs(a - b) for a, b in zip(left, right, strict=True))


def _pick_utage_suffixes(simai_text: str, utage_diff) -> tuple[int, int | None]:
    """宴谱后缀定位：返回 (1P 后缀, 2P 后缀或 None)。

    普通宴谱只有一张（通常 &inote_2）；buddy 宴谱按落雪约定拆成两张：
    &inote_2 = 1P、&inote_102 = 2P。物量与规范表可能差 1–2（官方计数与
    解析口径差异），按距离最小分配左右；多个候选却对不上时抛错，绝不
    回落成 BASIC（历史 bug：宿主曲的普通谱 &inote_1 被当成宴谱渲染）。
    """
    parsed = parse_simai(simai_text).difficulties
    if not parsed:
        raise ValueError("该宴谱没有可渲染的谱面数据")
    is_buddy = bool(getattr(utage_diff, "is_buddy", False))
    buddy_notes = getattr(utage_diff, "buddy_notes", None) if is_buddy else None
    if is_buddy and buddy_notes is not None:
        left = (
            buddy_notes.left_tap_num,
            buddy_notes.left_hold_num,
            buddy_notes.left_slide_num,
            buddy_notes.left_touch_num,
            buddy_notes.left_break_num,
        )
        right = (
            buddy_notes.right_tap_num,
            buddy_notes.right_hold_num,
            buddy_notes.right_slide_num,
            buddy_notes.right_touch_num,
            buddy_notes.right_break_num,
        )
        ordered = (
            _BUDDY_LEFT_SUFFIX,
            _BUDDY_RIGHT_SUFFIX,
        )
        pair = (
            ordered if ordered[0] in parsed and ordered[1] in parsed else tuple(parsed)
        )
        if len(pair) != 2:
            raise ValueError("该 buddy 宴谱缺少 1P/2P 两张谱面数据")
        candidates = [
            (
                _counts_distance(_counts_tuple(parsed[pair[0]].counts()), left)
                + _counts_distance(_counts_tuple(parsed[pair[1]].counts()), right),
                pair[0],
                pair[1],
            ),
            (
                _counts_distance(_counts_tuple(parsed[pair[1]].counts()), left)
                + _counts_distance(_counts_tuple(parsed[pair[0]].counts()), right),
                pair[1],
                pair[0],
            ),
        ]
        _score, left_suffix, right_suffix = min(candidates)
        return left_suffix, right_suffix

    expect = _official_counts_tuple(utage_diff) if utage_diff is not None else None
    if expect is not None:
        exact = [
            suffix
            for suffix, diff in parsed.items()
            if _counts_tuple(diff.counts()) == expect
        ]
        if len(exact) == 1:
            return exact[0], None
        distances = [
            (_counts_distance(_counts_tuple(diff.counts()), expect), suffix)
            for suffix, diff in parsed.items()
        ]
        best_distance, best_suffix = min(distances)
        if best_distance <= 5:
            return best_suffix, None
    if len(parsed) == 1:
        return next(iter(parsed)), None
    raise ValueError("无法定位该宴谱的谱面（物量与规范表不一致），暂不支持渲染")


def _stats_with_counts(site_chart, counts: dict[str, int]) -> ChartStats:
    """用官方物量计数 + 解析出的 EX 计数组装统计（buddy 1P/2P 各一份）。"""
    parsed = compute_chart_stats(site_chart)
    return ChartStats(
        chart_ms=parsed.chart_ms,
        measures=parsed.measures,
        counts=counts,
        ex_counts=parsed.ex_counts,
    )


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
        "buddy_stats": (
            {
                "chart_ms": result.buddy_stats.chart_ms,
                "measures": result.buddy_stats.measures,
                "counts": result.buddy_stats.counts,
                "ex_counts": result.buddy_stats.ex_counts,
            }
            if result.buddy_stats is not None
            else None
        ),
    }
    _stats_sidecar(result.path).write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def _read_stats(video: Path) -> PreviewResult | None:
    sidecar = _stats_sidecar(video)
    if not sidecar.exists():
        return None

    def parse_stats(raw: dict) -> ChartStats:
        return ChartStats(
            chart_ms=float(raw["chart_ms"]),
            measures=int(raw["measures"]),
            counts={k: int(v) for k, v in raw["counts"].items()},
            ex_counts={k: int(v) for k, v in raw["ex_counts"].items()},
        )

    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        stats = parse_stats(payload)
        buddy_raw = payload.get("buddy_stats")
        buddy_stats = parse_stats(buddy_raw) if buddy_raw else None
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return PreviewResult(
        path=video,
        label=str(payload.get("label", "")),
        stats=stats,
        range_text=payload.get("range_text"),
        preroll_measures=int(payload.get("preroll_measures", 0)),
        buddy_stats=buddy_stats,
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
    fp = _render_fingerprint(hi_speed)
    out = cache_dir() / f"{song.id}_{type_key}_{index}_r{fp}{suffix}.mp4"
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
    fp = _render_fingerprint(hi_speed)
    out = cache_dir() / f"{song.id}_{type_key}_{index}_r{fp}{suffix}.mp4"
    if out.exists():
        cached = _read_stats(out)
        if cached is not None:
            return cached

    buddy_chart = None
    buddy_stats: ChartStats | None = None
    if is_utage:
        # 宴谱文件以 diff_id 命名（宿主曲 id 对应的是普通谱，不能拿来渲染）
        simai_text = await fetch_chart_text_by_id(display_id)
        suffix, buddy_suffix = _pick_utage_suffixes(simai_text, utage_diff)
        site_chart = parse_site_chart(simai_text, difficulty=suffix).get(suffix)
        if site_chart is None:
            raise ValueError("素材站的谱面数据缺少该难度")
        if buddy_suffix is not None:
            buddy_chart = parse_site_chart(simai_text, difficulty=buddy_suffix).get(
                buddy_suffix
            )
            if buddy_chart is None:
                raise ValueError("素材站缺少该 buddy 宴谱的 2P 谱面数据")
    else:
        simai_text, _official_id = await fetch_chart_text(song.id, dx=dx)
        suffix = _SUFFIX_BY_INDEX[index]
        site_chart = parse_site_chart(simai_text, difficulty=suffix).get(suffix)
        if site_chart is None:
            raise ValueError("素材站的谱面数据缺少该难度")

    buddy_notes = getattr(utage_diff, "buddy_notes", None) if is_utage else None
    if buddy_chart is not None and buddy_notes is not None:
        # buddy：1P/2P 分别用官方左右物量（解析口径与官方可能差 1–2）
        stats = _stats_with_counts(
            site_chart,
            {
                "TAP": buddy_notes.left_tap_num,
                "HOLD": buddy_notes.left_hold_num,
                "SLIDE": buddy_notes.left_slide_num,
                "TOUCH": buddy_notes.left_touch_num,
                "BREAK": buddy_notes.left_break_num,
            },
        )
        buddy_stats = _stats_with_counts(
            buddy_chart,
            {
                "TAP": buddy_notes.right_tap_num,
                "HOLD": buddy_notes.right_hold_num,
                "SLIDE": buddy_notes.right_slide_num,
                "TOUCH": buddy_notes.right_touch_num,
                "BREAK": buddy_notes.right_break_num,
            },
        )
    else:
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
            buddy_stats=buddy_stats,
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
        buddy_chart=buddy_chart,
    )
    result = PreviewResult(
        path=out,
        label=level_label,
        stats=stats,
        range_text=range_text,
        preroll_measures=preroll_measures,
        buddy_stats=buddy_stats,
    )
    _write_stats(result)
    _sweep_cache(plugin_config.awmc_chart_preview_cache_mb)
    return result
