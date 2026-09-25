"""查分结果增强渲染：minfo 上分提示与 ginfo 统计卡拼接。

被 matchers 的 minfo / ginfo handler 调用；不注册 matcher。
"""

import io

from ...core.score import UserScoreError, score_service
from ...core.types import SongType
from ...core.render.tools import image_to_bytes


async def _b50_rise_tips(scores, binding, bests=None) -> list[str]:
    """「可进 B50」增强提示（我方独有，基准以谱面卡上分预测区表达）。

    对不在 B50 且 RA 高于入线最低 RA 的成绩，给出替换后总 RA 提升量；
    ``bests`` 传入调用方已取的 B50（minfo 同请求并发拉取，避免二次远端），
    缺省时自查；拉取失败（未绑定/无权限）静默跳过。
    """
    if binding is None or not scores:
        return []
    if bests is None:
        try:
            bests = await score_service.get_b50(binding)
        except UserScoreError:
            return []
    min_ra = min(
        (s.dx_rating or 0 for s in bests.scores_b35 + bests.scores_b15), default=0
    )
    b50_keys = {(s.id, s.type, s.level_index) for s in bests.scores}
    tips = []
    for score in scores:
        ra = int(score.dx_rating or 0)
        if (score.id, score.type, score.level_index) in b50_keys or ra <= min_ra:
            continue
        type_abbr = "DX" if score.type == SongType.DX else "SD"
        tips.append(
            f"{type_abbr} {score.level_index.name}（{score.level_value:.1f}）"
            f" {score.achievements or 0:.4f}% RA {ra}"
            f"：可进 B50，替换后总 RA +{ra - min_ra}"
        )
    return tips


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
