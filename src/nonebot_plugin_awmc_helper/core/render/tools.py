"""PIL 公共工具：文本转图、圆角、渐变、图片字节化。"""

import io
from typing import Literal

from PIL import Image, ImageDraw, ImageFont

from .fonts import font

DEFAULT_TEXT_SIZE = 28


def image_to_bytes(img: Image.Image, fmt: Literal["PNG", "JPEG"] = "PNG") -> bytes:
    """Image → bytes（机器人发送统一出口）。"""
    buf = io.BytesIO()
    if fmt == "JPEG":
        img = img.convert("RGB")
    img.save(buf, fmt)
    return buf.getvalue()


def text_size(text: str, f: ImageFont.FreeTypeFont) -> tuple[int, int]:
    left, top, right, bottom = f.getbbox(text)
    return int(right - left), int(bottom - top)


def fit_text(text: str, f: ImageFont.FreeTypeFont, max_width: int) -> str:
    """超宽文本截断加省略号。"""
    if text_size(text, f)[0] <= max_width:
        return text
    while text and text_size(text + "…", f)[0] > max_width:
        text = text[:-1]
    return text + "…"


def text_to_image(
    text: str,
    size: int = DEFAULT_TEXT_SIZE,
    padding: int = 16,
    font_name: str | None = None,
    fg: str = "#49444d",
    bg: str = "#ffffff",
    max_width: int = 1200,
) -> Image.Image:
    """多行长文本转图（白底黑字），自动换行与宽度截断。"""
    f = font(size, font_name or "ResourceHanRoundedCN-Bold.ttf")
    line_spacing = int(size * 0.4)
    lines: list[str] = []
    for raw in text.splitlines() or [""]:
        current = ""
        for ch in raw:
            if text_size(current + ch, f)[0] > max_width:
                lines.append(current)
                current = ch
            else:
                current += ch
        lines.append(current)
    width = max((text_size(line, f)[0] for line in lines), default=0)
    height = len(lines) * size + (len(lines) - 1) * line_spacing
    img = Image.new("RGBA", (width + padding * 2, height + padding * 2), bg)
    draw = ImageDraw.Draw(img)
    y = padding
    for line in lines:
        draw.text((padding, y), line, font=f, fill=fg)
        y += size + line_spacing
    return img


def draw_rounded_rect(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    radius: int,
    fill: str | tuple | None = None,
    outline: str | tuple | None = None,
    width: int = 1,
) -> None:
    """圆角矩形。"""
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    """圆角蒙版（贴图裁圆角用）。"""
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size[0] - 1, size[1] - 1), radius, fill=255
    )
    return mask


def vertical_gradient(size: tuple[int, int], top: str, bottom: str) -> Image.Image:
    """纵向渐变背景。"""
    import numpy as np

    t = [int(top[i : i + 2], 16) for i in (1, 3, 5)]
    b = [int(bottom[i : i + 2], 16) for i in (1, 3, 5)]
    rows = np.linspace(t, b, size[1]).astype("uint8")
    arr = np.repeat(rows[:, None, :], size[0], axis=1)
    return Image.fromarray(arr, "RGB").convert("RGBA")
