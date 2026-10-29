"""ffmpeg rawvideo 管线：帧合成入轨、响度两遍归一、h264+aac 合成。

渲染入口 ``render_chart_video``（签名见此）；ffmpeg 定位/可用性探测
与多线程分帧渲染也在此。绘制语义见包 __init__ 模块文档与各渲染模块。
"""

from __future__ import annotations

import math
import zlib
import threading
from pathlib import Path
from dataclasses import dataclass

from PIL import Image

from . import constants
from .composer import _ChartRenderer
from .constants import _LOGICAL, _DEFAULT_HI_SPEED, _apply_hi_speed, normalize_hi_speed
from ..chart_audio import MIX_RATE, build_effect_track
from ..chart_notes import SiteChart


class FFmpegUnavailableError(ValueError):
    """服务器缺少可用的 ffmpeg（或缺少所需编码器/滤镜）：面向用户的错误。

    继承 ValueError 以便 matcher 的 handle_errors(except_with_message=(...))
    把消息原样发给用户，而不是笼统的「出错了」。
    """


def resolve_ffmpeg(ffmpeg: str | None = None) -> str:
    """定位 ffmpeg：显式配置 > 环境变量 AWMC_FFMPEG > PATH 中的 ffmpeg。

    返回可直接执行的路径/命令名；找不到时抛 :class:`FFmpegUnavailableError`
    （消息含排查提示，会原样发给用户）。
    """
    import os
    import shutil

    candidate = (ffmpeg or "").strip() or os.environ.get("AWMC_FFMPEG", "").strip()
    if candidate:
        if Path(candidate).is_file():
            return candidate
        found = shutil.which(candidate)
        if found is not None:
            return found
        raise FFmpegUnavailableError(
            f"谱面预览需要可用的 ffmpeg，但配置的路径「{candidate}」不存在；"
            "请检查 awmc_chart_preview_ffmpeg 配置或服务器 PATH。"
        )
    found = shutil.which("ffmpeg")
    if found is None:
        raise FFmpegUnavailableError(
            "谱面预览需要服务器安装 ffmpeg，但当前未找到（PATH 中没有 ffmpeg）。"
            "请安装 ffmpeg，或在 .env 配置 awmc_chart_preview_ffmpeg 指向可执行文件"
            "（如 /usr/bin/ffmpeg）后重启。"
        )
    return found


@dataclass(frozen=True)
class FFmpegStatus:
    """ffmpeg 可用性探测结果。"""

    path: str
    version: str
    missing: tuple[str, ...]  # 缺少的编码器/滤镜名（空=完全可用）

    @property
    def usable(self) -> bool:
        return not self.missing


def check_ffmpeg(ffmpeg: str | None = None) -> FFmpegStatus:
    """探测 ffmpeg 是否可执行，以及是否带渲染所需编码器/滤镜。

    谱面预览依赖：``libx264``/``aac`` 编码器、``loudnorm``/``amix`` 滤镜
    （响度归一与音乐+音效混音）。启动时调用本函数写日志，部署排查也可
    单独跑（见 local/deployment.md）。
    """
    import subprocess

    path = resolve_ffmpeg(ffmpeg)
    try:
        version = subprocess.run(
            [path, "-hide_banner", "-version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        encoders = subprocess.run(
            [path, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        filters = subprocess.run(
            [path, "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FFmpegUnavailableError(
            f"谱面预览需要可用的 ffmpeg，但「{path}」无法执行（{exc}）；"
            "请检查安装与执行权限。"
        ) from exc
    if version.returncode != 0:
        raise FFmpegUnavailableError(
            f"谱面预览需要可用的 ffmpeg，但「{path}」执行失败"
            f"（exit {version.returncode}）；请检查安装是否完整。"
        )
    version_text = (version.stdout or version.stderr).splitlines()
    first_line = version_text[0].strip() if version_text else path
    missing: list[str] = []
    encoder_text = encoders.stdout or ""
    for encoder in ("libx264", "aac"):
        if encoder not in encoder_text:
            missing.append(f"编码器 {encoder}")
    filter_text = filters.stdout or ""
    for name in ("loudnorm", "amix"):
        if name not in filter_text:
            missing.append(f"滤镜 {name}")
    return FFmpegStatus(path=path, version=first_line, missing=tuple(missing))


_THREAD_STATE = threading.local()
_CLIP_START_MS = 0.0
_WORKER_SIZE = int(_LOGICAL)


def _compose_frame(
    renderer: "_ChartRenderer",
    renderer2: "_ChartRenderer | None",
    now_ms: float,
    fps: int,
) -> Image.Image:
    """合成单帧：普通谱面 600×600；buddy 谱面 1200×600（左 1P、右 2P）。"""

    def panel(r: "_ChartRenderer") -> Image.Image:
        frame = r.render_frame(now_ms, fps)
        rgb = frame if frame.mode == "RGB" else frame.convert("RGB")
        return rgb.resize((_WORKER_SIZE, _WORKER_SIZE), Image.Resampling.BILINEAR)

    left = panel(renderer)
    if renderer2 is None:
        return left
    out = Image.new("RGB", (_WORKER_SIZE * 2, _WORKER_SIZE))
    out.paste(left, (0, 0))
    out.paste(panel(renderer2), (_WORKER_SIZE, 0))
    return out


def _thread_worker_init(
    chart: SiteChart, fps: int, buddy_chart: SiteChart | None = None
) -> None:
    """线程池 worker 初始化：每线程各持渲染器（共享只读谱面对象）。"""
    _THREAD_STATE.renderer = _ChartRenderer(chart)
    _THREAD_STATE.renderer2 = _ChartRenderer(buddy_chart) if buddy_chart else None
    _THREAD_STATE.fps = fps


def _thread_render_chunk(args: tuple[int, int]) -> list[bytes]:
    """渲染 [start, start+count) 帧（绝对时间）并返回 zlib 压缩帧。"""
    start, count = args
    renderer = _THREAD_STATE.renderer
    renderer2 = _THREAD_STATE.renderer2
    fps = _THREAD_STATE.fps
    frames: list[bytes] = []
    for index in range(start, start + count):
        now_ms = _CLIP_START_MS + index / fps * 1000.0
        frames.append(
            zlib.compress(_compose_frame(renderer, renderer2, now_ms, fps).tobytes(), 1)
        )
    return frames


def _write_frames_parallel(
    proc,
    chart: SiteChart,
    n_frames: int,
    fps: int,
    workers: int,
    start_ms: float = 0.0,
    buddy_chart: SiteChart | None = None,
) -> bool:
    """多线程分帧渲染并保序写入 ffmpeg 管道。

    用 :class:`ThreadPoolExecutor` 而不是 multiprocessing：渲染由 PIL/numpy
    的 C 层主导、GIL 大部分时间释放（实测 8 线程 ≈ 2.4× 串行），且不需要在
    多线程进程里 fork（Python 3.12 明确警告 multi-threaded fork 有死锁风险）。
    每个线程各自持有一个渲染器（线程局部），共享只读谱面对象。
    """
    import os
    from concurrent.futures import ThreadPoolExecutor

    global _CLIP_START_MS

    workers = max(1, min(workers, (os.cpu_count() or 1)))
    chunk = max(1, min(30, n_frames // (workers * 4) or 1))
    tasks = [
        (start, min(chunk, n_frames - start)) for start in range(0, n_frames, chunk)
    ]
    wrote = 0
    previous_start = _CLIP_START_MS
    _CLIP_START_MS = start_ms
    try:
        with ThreadPoolExecutor(
            max_workers=workers,
            initializer=_thread_worker_init,
            initargs=(chart, fps, buddy_chart),
        ) as pool:
            for frames in pool.map(_thread_render_chunk, tasks):
                for frame in frames:
                    proc.stdin.write(zlib.decompress(frame))
                    wrote += 1
        return True
    except (OSError, RuntimeError):
        # 已写入部分帧时不能退回串行（会重复帧），交由 finally 收尾报错；
        # 未写入任何帧则允许调用方退回串行。
        if wrote:
            raise
        return False
    finally:
        _CLIP_START_MS = previous_start


_SS_LOCK = threading.Lock()


def render_chart_video(
    chart: SiteChart,
    *,
    audio: bytes | None,
    out_path: Path,
    fps: int = 30,
    limit_seconds: float | None = None,
    workers: int = 0,
    supersample: float = 2.0,
    sound_effects: dict[str, bytes] | None = None,
    sound_offset_ms: float = -50.0,
    loudness_lufs: float = -16.0,
    start_ms: float = 0.0,
    end_ms: float | None = None,
    notes_start_ms: float | None = None,
    fade_in_ms: float | None = None,
    hi_speed: float = _DEFAULT_HI_SPEED,
    ffmpeg: str | None = None,
    buddy_chart: SiteChart | None = None,
) -> Path:
    """渲染入口：设置超采样/谱面流速/ffmpeg 路径，串行化并发渲染。"""
    clamped = max(1.0, min(2.0, float(supersample)))
    speed = normalize_hi_speed(hi_speed)
    with _SS_LOCK:
        previous_ss = constants._SS
        previous_speed = (
            constants._HI_SPEED,
            constants._APPROACH_MS,
            constants._HALF_APPROACH,
            constants._TOUCH_APPROACH_MS,
            constants._SPEED_SETTING,
        )
        constants._SS = clamped
        _apply_hi_speed(speed)
        try:
            return _render_chart_video_impl(
                chart,
                audio=audio,
                out_path=out_path,
                fps=fps,
                limit_seconds=limit_seconds,
                workers=workers,
                sound_effects=sound_effects,
                sound_offset_ms=sound_offset_ms,
                loudness_lufs=loudness_lufs,
                start_ms=start_ms,
                end_ms=end_ms,
                notes_start_ms=notes_start_ms,
                fade_in_ms=fade_in_ms,
                ffmpeg=ffmpeg,
                buddy_chart=buddy_chart,
            )
        finally:
            constants._SS = previous_ss
            (
                constants._HI_SPEED,
                constants._APPROACH_MS,
                constants._HALF_APPROACH,
                constants._TOUCH_APPROACH_MS,
                constants._SPEED_SETTING,
            ) = previous_speed


def _parse_loudnorm_json(text: str) -> dict[str, float] | None:
    """从 ffmpeg loudnorm 的 print_format=json 输出里取测量值。"""
    import re
    import json

    match = None
    for item in re.finditer(r"\{[^{}]*\}", text, re.S):
        if '"input_i"' in item.group(0):
            match = item.group(0)
    if match is None:
        return None
    try:
        data = json.loads(match)
    except json.JSONDecodeError:
        return None
    keys = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
    try:
        values = {key: float(data[key]) for key in keys}
    except (KeyError, TypeError, ValueError):
        return None
    # 静音/异常素材会测出 -inf（loudnorm JSON 里写作 "-inf"），filter 参数
    # 不接受 ±inf，直接视为测量失败、回退到不归一。
    if not all(math.isfinite(item) for item in values.values()):
        return None
    return values


def _probe_loudness(
    audio_file: Path,
    fx_file: Path | None,
    base_chain: str,
    target_lufs: float,
    ffmpeg: str = "ffmpeg",
) -> dict[str, float] | None:
    """测量「音乐+音效」混音的整体响度/真峰值（loudnorm 两遍法的第一遍）。"""
    import subprocess

    chain = (
        base_chain
        + f";[mixed]loudnorm=I={target_lufs}:TP=-1.5:LRA=11:print_format=json[out]"
    )
    # 与正式 mux 的输入序号保持一致（0=视频管道，1=音乐，2=音效）；探针这里用
    # 一个不会被 map 的 lavfi 视频占位，避免 [1:a]/[2:a] 指错输入。
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-nostats",
        "-v",
        "info",
        "-f",
        "lavfi",
        "-i",
        "color=black:s=2x2:r=1",
        "-i",
        str(audio_file),
    ]
    if fx_file is not None:
        cmd += ["-i", str(fx_file)]
    cmd += ["-filter_complex", chain, "-map", "[out]", "-f", "null", "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return _parse_loudnorm_json(proc.stderr)


def _render_chart_video_impl(
    chart: SiteChart,
    *,
    audio: bytes | None,
    out_path: Path,
    fps: int = 30,
    limit_seconds: float | None = None,
    workers: int = 0,
    sound_effects: dict[str, bytes] | None = None,
    sound_offset_ms: float = -50.0,
    loudness_lufs: float = -16.0,
    start_ms: float = 0.0,
    end_ms: float | None = None,
    notes_start_ms: float | None = None,
    fade_in_ms: float | None = None,
    ffmpeg: str | None = None,
    buddy_chart: SiteChart | None = None,
) -> Path:
    """渲染谱面预览 mp4（CPU 密集，调用方放线程池）。

    时间轴与站内播放器一致：视频从 chart beat 0 起（前 4 拍空场 lead-in），
    音频从 beat 4 起播（chartpage.js syncFrame 的 240000/bpm 偏移）。
    ``start_ms``/``end_ms`` 为 chart 显示时间轴（=成片播放时间）的片段范围：
    帧按绝对时间渲染（跨范围的 hold/slide/touch-hold 自然呈进行中状态），
    音频从对应音乐位置切入；渐入时长默认等于前置空小节时长（两小节），内容
    起点处正好满音量（``fade_in_ms`` 可显式覆盖）。
    ``notes_start_ms``（默认同 start_ms）：片段前置空小节的结束时刻（=内容
    起点）。该时刻之前画面照常渲染（谱面连续、不出现“空场/半空冒音符”），
    但**打击音效静音**，音乐在这段前置时长内渐入到满音量。
    ``limit_seconds`` 截断渲染时长；音频 None 时出无声视频。
    """
    import os
    import subprocess

    ffmpeg_bin = resolve_ffmpeg(ffmpeg)
    renderer = _ChartRenderer(chart)
    if workers <= 0:  # 0=自动：线程池受益有限，实测 2–4 线程最佳（上限 4）
        workers = max(1, min(4, os.cpu_count() or 1))
    full_total_ms = max(1.0, chart.timeline.ms_from_beat(chart.total_beats))
    clip_start = max(0.0, min(start_ms, full_total_ms))
    clip_end = full_total_ms if end_ms is None else min(end_ms, full_total_ms)
    clip_duration_ms = clip_end - clip_start
    if clip_duration_ms < 1000.0 / fps:
        raise ValueError("片段时长不足一帧，请放宽范围")
    if limit_seconds is not None:
        clip_end = min(clip_end, clip_start + limit_seconds * 1000.0)
    clip_duration_ms = clip_end - clip_start
    n_frames = max(1, int(clip_duration_ms / 1000.0 * fps))
    if notes_start_ms is None:
        notes_start = clip_start
    else:
        notes_start = max(clip_start, min(notes_start_ms, clip_end))
    lead_ms = 240000.0 / chart.bpm
    audio_delay = max(0.0, lead_ms - (chart.first_ms or 0.0))
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_bin,
        "-y",
        "-v",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        (
            f"{int(_LOGICAL) * 2}x{int(_LOGICAL)}"
            if buddy_chart is not None
            else f"{int(_LOGICAL)}x{int(_LOGICAL)}"
        ),
        "-r",
        str(fps),
        "-i",
        "pipe:",
    ]
    audio_file: Path | None = None
    fx_file: Path | None = None
    if audio:
        audio_file = out_path.with_suffix(".audio.tmp")
        audio_file.write_bytes(audio)
        # 片段起点对应的音乐位置 = clip_start - audio_delay（<0 表示音乐尚未开始）
        music_pos_ms = clip_start - audio_delay
        if music_pos_ms >= 0:
            cut = f"atrim=start={music_pos_ms / 1000.0:.6f},asetpts=PTS-STARTPTS"
        else:
            cut = f"adelay={int(-music_pos_ms)}:all=1"
        # 渐入时长默认 = 前置空小节时长（两小节），内容起点处正好到达满音量；
        # 显式传 fade_in_ms 时按传入值（0=不渐入）。
        if fade_in_ms is None:
            fade_ms = max(0.0, notes_start - clip_start)
        else:
            fade_ms = max(0.0, fade_in_ms)
        fade_s = min(fade_ms / 1000.0, clip_duration_ms / 1000.0)
        fade = f",afade=t=in:st=0:d={fade_s:.3f}" if fade_s > 0 else ""
        delay_filter = cut + fade
        effects = (
            build_effect_track(
                chart,
                sound_effects,
                offset_ms=sound_offset_ms,
                buddy_chart=buddy_chart,
            )
            if sound_effects
            else None
        )
        if effects is not None:
            fx_bytes, fx_scale = effects
            fx_file = out_path.with_suffix(".fx.tmp.wav")
            fx_file.write_bytes(fx_bytes)
            # 音效轨以 chart 绝对时间为轴：前置段要静音，但内容要落在正确位置，
            # 因此 trim 后需用 adelay 把内容平移回 (fx_from - clip_start)。
            fx_from = max(0.0, notes_start + sound_offset_ms)
            if fx_from > clip_start + 1e-6:
                fx_delay = round(fx_from - clip_start)
                fx_cut = (
                    f"atrim=start={fx_from / 1000.0:.6f},asetpts=PTS-STARTPTS,"
                    f"adelay={fx_delay}:all=1,"
                )
            elif clip_start > 0:
                fx_cut = f"atrim=start={clip_start / 1000.0:.6f},asetpts=PTS-STARTPTS,"
            else:
                fx_cut = ""
            base_chain = (
                f"[1:a]{delay_filter},aresample={MIX_RATE}[m];"
                f"[2:a]volume={fx_scale:.6f},{fx_cut}aresample={MIX_RATE}[f];"
                "[m][f]amix=inputs=2:normalize=0:duration=longest[mixed]"
            )
        else:
            base_chain = f"[1:a]{delay_filter}[mixed]"
        # 两遍 loudnorm（linear 模式）：先测「音乐+音效」整体响度，再按测量值
        # 一次性加增益，避免动态压缩破坏听感；响度不到位时退回不处理。
        measured = _probe_loudness(
            audio_file, fx_file, base_chain, loudness_lufs, ffmpeg_bin
        )
        if measured is not None:
            base_chain += (
                f";[mixed]loudnorm=I={loudness_lufs}:TP=-1.5:LRA=11:"
                f"measured_I={measured['input_i']}:"
                f"measured_TP={measured['input_tp']}:"
                f"measured_LRA={measured['input_lra']}:"
                f"measured_thresh={measured['input_thresh']}:"
                f"offset={measured['target_offset']}:linear=true[aout]"
            )
        else:
            base_chain += ";[mixed]anull[aout]"
        cmd += ["-i", str(audio_file)]
        if fx_file is not None:
            cmd += ["-i", str(fx_file)]
        cmd += [
            "-filter_complex",
            base_chain,
            "-map",
            "0:v",
            "-map",
            "[aout]",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
        ]
    cmd += [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "21",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
    ]
    if audio:
        cmd += ["-shortest"]
    cmd += [str(out_path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    assert proc.stdin is not None
    try:
        if workers > 1 and _write_frames_parallel(
            proc, chart, n_frames, fps, workers, clip_start, buddy_chart
        ):
            pass
        else:
            renderer2 = _ChartRenderer(buddy_chart) if buddy_chart else None
            for i in range(n_frames):
                now_ms = clip_start + i / fps * 1000.0
                proc.stdin.write(
                    _compose_frame(renderer, renderer2, now_ms, fps).tobytes()
                )
    finally:
        try:
            proc.stdin.close()
        except BrokenPipeError:
            pass
        code = proc.wait()
        if audio_file is not None:
            audio_file.unlink(missing_ok=True)
        if fx_file is not None:
            fx_file.unlink(missing_ok=True)
    if code != 0:
        raise RuntimeError(f"ffmpeg 渲染失败（exit {code}），输出见 {out_path}")
    return out_path
