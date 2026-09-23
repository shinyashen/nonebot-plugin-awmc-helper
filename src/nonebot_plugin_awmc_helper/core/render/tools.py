"""PIL 公共工具：文本转图、圆角、渐变、图片字节化。"""

import io
from typing import Literal

from PIL import Image, ImageDraw, ImageFont, ImageFilter

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


def tricolor_gradient_prism_plus(width: int, height: int) -> Image.Image:
    """垂直 PRiSM PLUS 三色渐变底（NB core/image/tools.py 同源移植）。

    先逐行放样 1×height 再拉伸到目标宽，避免逐像素画大图。
    """
    colors_list = [
        (0.00, (255, 255, 255)),
        (0.14, (255, 255, 255)),
        (0.24, (255, 213, 207)),
        (0.46, (255, 213, 207)),
        (0.56, (255, 197, 213)),
        (0.67, (234, 171, 255)),
        (0.85, (114, 188, 254)),
        (0.95, (101, 242, 223)),
        (1.00, (101, 242, 223)),
    ]
    line = Image.new("RGBA", (1, height))
    for y in range(height):
        t = 1.0 - (y / (height - 1)) if height > 1 else 0
        for i in range(len(colors_list) - 1):
            p1, c1 = colors_list[i]
            p2, c2 = colors_list[i + 1]
            if p1 <= t <= p2:
                rel = (t - p1) / (p2 - p1)
                rgb = tuple(int(c1[j] + (c2[j] - c1[j]) * rel) for j in range(3))
                line.putpixel((0, y), rgb)
                break
    return line.resize((width, height), Image.Resampling.BICUBIC)


def generate_frosted_card(
    im: Image.Image,
    box: tuple[int, int, int, int],
    shadow_offset: tuple[int, int] = (10, 10),
    alpha: float = 0.4,
) -> Image.Image:
    """在 im 的 box 区域叠一块圆角毛玻璃卡（NB 同源移植，牌子进度总览用）。"""
    if alpha < 0 or alpha > 1:
        raise ValueError("alpha 应在 0-1 之间")
    roi = im.crop(box)
    roi_w, roi_h = roi.size

    frosted = roi.filter(ImageFilter.GaussianBlur(4))
    white_layer = Image.new("RGBA", (roi_w, roi_h), (255, 255, 255, int(255 * alpha)))
    card = Image.alpha_composite(frosted, white_layer)

    mask = Image.new("L", (roi_w, roi_h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, roi_w, roi_h), radius=25, fill=255)

    # 投影
    shadow_w = roi_w + 10 + abs(shadow_offset[0])
    shadow_h = roi_h + 10 + abs(shadow_offset[1])
    shadow = Image.new("RGBA", (shadow_w, shadow_h), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (15, 15, 15 + roi_w, 15 + roi_h), radius=25, fill=(0, 0, 0, 50)
    )
    shadow_layer = shadow.filter(ImageFilter.GaussianBlur(3))

    temp_layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    shadow_pos = (box[0] + shadow_offset[0] - 15, box[1] + shadow_offset[1] - 15)
    temp_layer.paste(shadow_layer, shadow_pos)
    temp_layer.paste(card, (box[0], box[1]), mask=mask)

    return Image.alpha_composite(im, temp_layer)
