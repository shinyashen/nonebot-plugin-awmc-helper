"""ginfo 饼图：评级分布 PIL 自绘（原版用 pyecharts+playwright，此处免除浏览器依赖）。"""

from PIL import Image, ImageDraw

from .fonts import font
from .tools import text_size, image_to_bytes

PALETTE = [
    "#ffd45c",
    "#ffcf3f",
    "#f6a13c",
    "#f07f4a",
    "#e8605c",
    "#d94f70",
    "#b35c9e",
    "#8a5cc4",
    "#5f6fe0",
    "#4f9fe0",
    "#4fc3d9",
    "#54d9a6",
    "#7fd95c",
    "#b8d94f",
]


def draw_pie(title: str, data: list[tuple[str, int]], size: int = 420) -> Image.Image:
    """评级分布饼图（data 为 (标签, 数量)，自动占比）。"""
    header_h = 64
    legend_w = 190
    img = Image.new("RGBA", (size + legend_w, size + header_h + 16), "#ffffff")
    draw = ImageDraw.Draw(img)
    draw.text((20, 18), title, font=font(26), fill="#333")

    total = sum(v for _, v in data) or 1
    cx, cy, r = size // 2 + 10, header_h + size // 2, size // 2 - 24
    start = -90.0
    for i, (_, value) in enumerate(data):
        if value <= 0:
            continue
        extent = 360.0 * value / total
        draw.pieslice(
            (cx - r, cy - r, cx + r, cy + r),
            start,
            start + extent,
            fill=PALETTE[i % len(PALETTE)],
        )
        start += extent
    # 中心挖空成环形
    inner = r - 52
    draw.ellipse((cx - inner, cy - inner, cx + inner, cy + inner), fill="#ffffff")

    # 图例
    lx, ly = size + 16, header_h + 8
    f_label = font(17)
    f_num = font(16)
    for i, (label, value) in enumerate(data):
        y = ly + i * 26
        draw.rounded_rectangle(
            (lx, y + 3, lx + 14, y + 17), 3, fill=PALETTE[i % len(PALETTE)]
        )
        draw.text((lx + 22, y), label[:8], font=f_label, fill="#555")
        vw = text_size(str(value), f_num)[0]
        draw.text((lx + legend_w - 30 - vw, y + 1), str(value), font=f_num, fill="#999")
    return img


def pie_bytes(title: str, data: list[tuple[str, int]]) -> bytes:
    return image_to_bytes(draw_pie(title, data))
