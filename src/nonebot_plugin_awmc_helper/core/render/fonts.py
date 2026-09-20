"""字体加载（static/font，素材包自带 4 个常用字体）。"""

from pathlib import Path
from functools import lru_cache

from PIL import ImageFont

from ...config import plugin_config

# 素材包字体（另两种见 static/font）：ResourceHanRoundedCN / Torus /
# FOT-NewRodin Pro EB / ShangguMonoSC
FONT_HAN = "ResourceHanRoundedCN-Bold.ttf"  # 通用中文
FONT_NUM = "Torus SemiBold.otf"  # 数字/英文
FONT_RODIN = "FOT-NewRodin Pro EB.otf"  # 大标题
FONT_MONO = "ShangguMonoSC-Regular.otf"  # 等宽


@lru_cache(maxsize=32)
def _load_font(name: str, size: int):
    path = Path(plugin_config.awmc_static_path) / "font" / name
    if not path.exists():
        return ImageFont.load_default(size)
    return ImageFont.truetype(str(path), size)


def font(size: int, name: str = FONT_HAN):
    """按字体名与字号取字体（进程级缓存）。"""
    return _load_font(name, size)
