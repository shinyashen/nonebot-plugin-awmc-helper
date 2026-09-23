"""谱面全局统计卡（R9，NB chart.py::song_global_data 的 PIL 自绘替代）。

基准为 pyecharts 双环饼图（内环全连分布、外环达成率分布）+ playwright
截图；按既定饼图决策（DEVELOPMENT_PLAN §6）改 PIL 自绘，免除浏览器依赖，
并把 ginfo 的统计文本行（样本数/拟合定数/平均达成率/σ/平均 DX）一并画入。
调用方须保证 ``diff.curve`` 非空（曲线数据来自水鱼 chart_stats）。
"""

from PIL import Image, ImageDraw
from maimai_py import Song, FCType, SongDifficulty

from .fonts import FONT_HAN, FONT_NUM, FONT_RODIN, font
from .tools import image_to_bytes

W, H = 1000, 900
TITLE_COLOR = (44, 52, 60, 255)
MUTED = (120, 128, 138, 255)
DIFF_NAMES = ["Basic", "Advanced", "Expert", "Master", "Re:Master"]

# 全连分布配色（Not FC 灰 + 全连档位渐进）
_FC_ITEMS: list[tuple[str, FCType | None, str]] = [
    ("Not FC", None, "#d9d9d9"),
    ("FC", FCType.FC, "#54d9a6"),
    ("FCP", FCType.FCP, "#22bb5b"),
    ("AP", FCType.AP, "#ffd45c"),
    ("APP", FCType.APP, "#f6a13c"),
]

# 达成率分布配色（评级越高越暖；键为 RateType 枚举名）
_RATE_COLORS = {
    "SSSP": "#8a5cc4",
    "SSS": "#b35c9e",
    "SSP": "#e8605c",
    "SS": "#f07f4a",
    "SP": "#f6a13c",
    "S": "#ffd45c",
    "AAA": "#c3e05c",
    "AA": "#7fd95c",
    "A": "#4fc3d9",
    "BBB": "#5f8fe0",
    "BB": "#4f6fe0",
    "B": "#8a9bb5",
    "C": "#aab3bf",
    "D": "#d9d9d9",
}

_DIFF_NAMES = DIFF_NAMES


def _ring(im: Image.Image, cx: int, cy: int, r_in: int, r_out: int, data: list) -> None:
    """环形分段（data: [(label, count, color)]，占比自动）。"""
    draw = ImageDraw.Draw(im)
    total = sum(v for _, v, _ in data)
    if total <= 0:
        return
    start = -90.0
    box = (cx - r_out, cy - r_out, cx + r_out, cy + r_out)
    for _label, value, color in data:
        if value <= 0:
            continue
        extent = 360.0 * value / total
        draw.pieslice(box, start, start + extent, fill=color)
        start += extent
    if r_in > 0:
        draw.ellipse((cx - r_in, cy - r_in, cx + r_in, cy + r_in), fill="#ffffff")


def song_global_data(song: Song, diff: SongDifficulty) -> bytes:
    """绘制谱面游玩统计卡（内环全连分布、外环达成率分布）。"""
    curve = diff.curve
    if curve is None:
        raise ValueError("diff.curve 为空，无法绘制统计卡")

    im = Image.new("RGBA", (W, H), "#ffffff")
    dr = ImageDraw.Draw(im)

    # 标题与统计行
    li = diff.level_index.value
    dr.text(
        (W // 2, 42),
        f"{song.id} {song.title} 「{_DIFF_NAMES[li]}」",
        font=font(30, FONT_RODIN),
        fill=TITLE_COLOR,
        anchor="mm",
    )
    stat_line = (
        f"样本数 {curve.sample_size}　拟合定数 {curve.fit_level_value:.1f}　"
        f"平均达成率 {curve.avg_achievements:.2f}%"
        f"（σ {curve.stdev_achievements:.2f}）　"
        f"平均DX {curve.avg_dx_score:.0f}"
    )
    dr.text((W // 2, 92), stat_line, font=font(18, FONT_HAN), fill=MUTED, anchor="mm")

    # 内环：全连分布（Not FC = 样本数 - 各全连档之和）
    fc_played = sum(curve.fc_sample_size.values())
    fc_data = [
        (
            label,
            int(
                curve.sample_size - fc_played
                if fc is None
                else curve.fc_sample_size.get(fc, 0)
            ),
            color,
        )
        for label, fc, color in _FC_ITEMS
    ]
    # 外环：达成率分布（RateType 值升序 = 评级从高到低）
    rate_data = [
        (rate_name(rate.name), int(cnt), _RATE_COLORS.get(rate.name, "#cccccc"))
        for rate, cnt in sorted(
            curve.rate_sample_size.items(), key=lambda kv: kv[0].value
        )
    ]

    cx, cy = 380, 520
    _ring(im, cx, cy, 120, 200, fc_data)
    _ring(im, cx, cy, 200, 285, rate_data)
    dr.text(
        (cx, cy),
        f"n={curve.sample_size}",
        font=font(20, FONT_NUM),
        fill=MUTED,
        anchor="mm",
    )

    # 图例（右侧两节）
    def legend(title: str, data: list, y0: int) -> None:
        dr.text(
            (720, y0), title, font=font(20, FONT_HAN), fill=TITLE_COLOR, anchor="lm"
        )
        y = y0 + 34
        for label, value, color in data:
            if value <= 0:
                continue
            pct = value / max(curve.sample_size, 1) * 100
            dr.rounded_rectangle((720, y + 2, 738, y + 20), 3, fill=color)
            dr.text(
                (748, y + 11),
                label,
                font=font(16, FONT_NUM),
                fill=TITLE_COLOR,
                anchor="lm",
            )
            dr.text(
                (970, y + 11),
                f"{value} ({pct:.1f}%)",
                font=font(16, FONT_NUM),
                fill=MUTED,
                anchor="rm",
            )
            y += 28

    legend("全连等级", fc_data, 210)
    legend("达成率等级", rate_data, 430)
    return image_to_bytes(im)


_RATE_DISPLAY = {
    "SSSP": "SSS+",
    "SSS": "SSS",
    "SSP": "SS+",
    "SS": "SS",
    "SP": "S+",
    "S": "S",
    "AAA": "AAA",
    "AA": "AA",
    "A": "A",
    "BBB": "BBB",
    "BB": "BB",
    "B": "B",
    "C": "C",
    "D": "D",
}


def rate_name(enum_name: str) -> str:
    """RateType 枚举名 → 显示名（SSSP → SSS+）。"""
    return _RATE_DISPLAY.get(enum_name, enum_name)
