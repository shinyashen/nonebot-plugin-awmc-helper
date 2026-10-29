"""ffmpeg 定位/可用性探测与响度探测（subprocess 调用，不渲染）。"""

from __future__ import annotations

import math
from pathlib import Path
from dataclasses import dataclass


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
