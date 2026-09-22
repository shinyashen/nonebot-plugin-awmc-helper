"""render/nb_chart 版本 logo 适配：裁透明留白、双下限等比缩放、槽位约束。"""

from PIL import Image, ImageDraw


def _make_logo(content_w: int, content_h: int, pad: int = 50, glow: bool = False):
    """合成 logo：居中不透明内容矩形 + 透明留白（glow 再加一圈低于阈值的淡光晕）。"""
    extra = 30 if glow else 0
    w, h = content_w + pad * 2 + extra * 2, content_h + pad * 2 + extra * 2
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    if glow:  # alpha=8 < 裁边阈值 16，应被当作留白裁掉
        d.rectangle(
            [extra - 1, extra - 1, w - extra, h - extra], fill=(200, 230, 220, 8)
        )
    d.rectangle(
        [
            extra + pad,
            extra + pad,
            extra + pad + content_w - 1,  # rectangle 坐标为闭区间，-1 才是标称尺寸
            extra + pad + content_h - 1,
        ],
        fill=(80, 160, 140, 255),
    )
    return im


def test_fit_wide_logo_width_bound_no_distortion():
    """长条 logo（AR 3.3）：等比、宽度顶到槽位 182。"""
    from nonebot_plugin_awmc_helper.core.render.nb_chart import _fit_version_logo

    out = _fit_version_logo(_make_logo(1000, 300))
    assert out.size == (182, 55)
    assert abs(out.width / out.height - 1000 / 300) < 0.05


def test_fit_tallish_logo_meets_min_width():
    """高瘦 logo（AR 1.77，如 BUDDiES）：保底宽 140，不被统一高度压小。"""
    from nonebot_plugin_awmc_helper.core.render.nb_chart import _fit_version_logo

    out = _fit_version_logo(_make_logo(790, 447))
    assert out.size == (140, 79)


def test_fit_clamps_to_box():
    """近方形 logo：宽度保底会顶破高度上限时，以槽位高 90 为准。"""
    from nonebot_plugin_awmc_helper.core.render.nb_chart import _fit_version_logo

    out = _fit_version_logo(_make_logo(700, 500))
    assert out.size == (126, 90)
    assert abs(out.width / out.height - 700 / 500) < 0.05


def test_fit_small_source_upscaled():
    """小尺寸源图（国服旧世代 332×160 级别）：等比放大到下限而非原样贴上。"""
    from nonebot_plugin_awmc_helper.core.render.nb_chart import _fit_version_logo

    out = _fit_version_logo(_make_logo(267, 131, pad=10))
    assert out.size == (147, 72)


def test_fit_trims_faint_glow_margin():
    """淡光晕（alpha<16）视为留白裁掉：带光晕与不带光晕结果完全一致。"""
    from nonebot_plugin_awmc_helper.core.render.nb_chart import _fit_version_logo

    plain = _fit_version_logo(_make_logo(790, 447))
    glowy = _fit_version_logo(_make_logo(790, 447, glow=True))
    assert plain.size == glowy.size
