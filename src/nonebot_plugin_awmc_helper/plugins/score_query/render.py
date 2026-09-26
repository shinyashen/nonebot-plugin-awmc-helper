"""查分结果增强渲染：ginfo 统计卡拼接。

被 matchers 的 ginfo handler 调用；不注册 matcher。
"""

import io

from ...core.render.tools import image_to_bytes


def _ginfo_image(card: bytes, stats_png: bytes) -> bytes:
    """富谱面卡 + 统计卡纵向拼接。"""
    from PIL import Image

    extras: list[Image.Image] = [
        Image.open(io.BytesIO(card)).convert("RGBA"),
        Image.open(io.BytesIO(stats_png)).convert("RGBA"),
    ]
    total_h = sum(im.size[1] for im in extras) + 8 * (len(extras) - 1)
    w = max(im.size[0] for im in extras)
    out = Image.new("RGBA", (w, total_h), "#f2f3f5")
    y = 0
    for im in extras:
        out.paste(im, (0, y))
        y += im.size[1] + 8
    return image_to_bytes(out)
