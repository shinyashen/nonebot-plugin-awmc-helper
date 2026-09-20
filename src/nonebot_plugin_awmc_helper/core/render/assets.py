"""素材访问：static 素材包路径、主题子目录、类级内存缓存、曲绘路径。

素材包由用户按 README 下载（不入库），经 ``awmc_static_path`` 配置；
曲绘缺失时回退 ``cover/0.png``。
"""

from typing import ClassVar
from pathlib import Path
from functools import cache

from PIL import Image

from ...config import plugin_config

THEMES = ("prism_plus", "circle")
DEFAULT_THEME = "prism_plus"


class Assets:
    """素材访问入口（类级缓存，``awmc_save_in_memory=false`` 时不缓存）。"""

    _cache: ClassVar[dict[Path, Image.Image]] = {}

    @staticmethod
    def static_path() -> Path:
        return Path(plugin_config.awmc_static_path)

    @classmethod
    def get(cls, path: Path) -> Image.Image:
        """读取素材图片（RGBA），按配置决定是否常驻内存。"""
        if plugin_config.awmc_save_in_memory and path in cls._cache:
            return cls._cache[path]
        img = Image.open(path).convert("RGBA")
        if plugin_config.awmc_save_in_memory:
            cls._cache[path] = img
        return img

    @classmethod
    def pic(cls, name: str, theme: str = DEFAULT_THEME) -> Image.Image:
        """mai/pic 下的 UI 素材，优先主题子目录，其次根目录。"""
        base = cls.static_path() / "mai" / "pic"
        themed = base / theme / name
        if themed.exists():
            return cls.get(themed)
        return cls.get(base / name)

    @classmethod
    def cover(cls, song_id: int) -> Image.Image:
        """曲绘（400×400），缺失回退 0.png；素材缺失时返回占位图。"""
        path = cls.static_path() / "mai" / "cover" / f"{song_id}.png"
        if not path.exists():
            path = cls.static_path() / "mai" / "cover" / "0.png"
        if not path.exists():
            return Image.new("RGBA", (400, 400), "#666666")
        return cls.get(path)

    @classmethod
    def cover_path(cls, song_id: int) -> Path | None:
        """曲绘文件路径（缺失时回退 0.png；两者都缺失返回 None）。"""
        path = cls.static_path() / "mai" / "cover" / f"{song_id}.png"
        if path.exists():
            return path
        fallback = cls.static_path() / "mai" / "cover" / "0.png"
        return fallback if fallback.exists() else None

    @classmethod
    def plate_version(cls, version: str, kind: str) -> Image.Image | None:
        """牌子表头素材（``{版本}{牌种}.png``）。"""
        for name in (
            f"{version}{kind}.png",
            f"{version}{kind.replace('舞舞', '舞舞')}舞舞.png",
        ):
            path = cls.static_path() / "mai" / "plate_version" / name
            if path.exists():
                return cls.get(path)
        return None


assets = Assets()
"""模块级素材访问单例。"""


@cache
def _warn_missing_static() -> None:
    from nonebot import logger

    logger.warning(
        f"素材目录 {plugin_config.awmc_static_path} 不存在，"
        "绘图功能不可用；请按 README 下载素材包并配置 awmc_static_path"
    )


def ensure_static_exists() -> bool:
    """素材目录存在性检查（不存在时只告警一次）。"""
    if not Path(plugin_config.awmc_static_path).is_dir():
        _warn_missing_static()
        return False
    return True
