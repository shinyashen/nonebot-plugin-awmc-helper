"""曲绘 FFT 频域权重裁剪（算法照搬原版 maimaiDX）。

灰度 FFT → 幅度谱平方归一化权重 → 随机 scale(0.15~0.4) 定裁剪尺寸 →
按 top_p 分位以上的权重采样左上角，让裁剪落在曲绘「信息量大」的区域。
"""

import random

import numpy as np
from PIL import Image


def frequency_weights(image: Image.Image) -> np.ndarray:
    """灰度图 → FFT 频率权重矩阵。"""
    gray = np.array(image.convert("L"))
    freq = np.fft.fft2(gray)
    freq_shift = np.fft.fftshift(freq)
    magnitude = np.abs(freq_shift)
    peak = magnitude.max()
    # 纯黑图峰值恒 0：归一会得 NaN，下游采样崩溃；回退全零权重（均匀采样）
    normalized = magnitude / peak if peak > 0 else np.zeros_like(magnitude)
    return normalized**2


def select_crop_region(
    weights: np.ndarray,
    crop_w: int,
    crop_h: int,
    top_p: float,
    rng: random.Random | None = None,
) -> tuple[int, int]:
    """按权重分布选择裁剪左上角（top_p 为百分位 0-100）。

    ``rng`` 提供时用它做离散采样（裁剪区域可复现）；权重全零（纯黑图）
    回退均匀采样。
    """
    h, w = weights.shape
    valid = weights[: h - crop_h + 1, : w - crop_w + 1]
    flattened = valid.flatten()
    threshold = np.percentile(flattened, top_p)
    indices = np.where(flattened >= threshold)[0]
    probabilities = flattened[indices]
    total = probabilities.sum()
    if rng is not None:
        if total <= 0:
            chosen = indices[rng.randrange(len(indices))]
        else:
            cum = np.cumsum(probabilities)
            r = rng.random() * float(cum[-1])
            pos = min(int(np.searchsorted(cum, r, side="right")), len(indices) - 1)
            chosen = indices[pos]
    else:
        if total <= 0:
            chosen = np.random.choice(indices)
        else:
            chosen = np.random.choice(indices, p=probabilities / total)
    return int(chosen % valid.shape[1]), int(chosen // valid.shape[1])


def crop_cover_randomly(
    cover: Image.Image, rng: random.Random | None = None
) -> Image.Image:
    """随机裁剪曲绘（猜歌第 7 提示 / 猜曲绘）。"""
    rng = rng if rng is not None else random.Random()
    w, h = cover.size
    weights = frequency_weights(cover)
    scale = rng.uniform(0.15, 0.4)
    w2, h2 = max(1, int(w * scale)), max(1, int(h * scale))
    top_p = min(1.3 - np.power(scale, 0.4), 0.95) * 100
    x, y = select_crop_region(weights, w2, h2, top_p, rng)
    return cover.crop((x, y, x + w2, y + h2))
