"""素材访问：static 素材包路径、主题子目录、类级内存缓存、曲绘路径。

素材包由用户按 README 下载（不入库），经 ``awmc_static_path`` 配置；
曲绘缺失时回退 ``cover/0.png``。
"""

from typing import ClassVar
from pathlib import Path

from PIL import Image
from nonebot_plugin_localstore import get_data_dir

from ...config import plugin_config
from ...constants import DEFAULT_THEME

# 牌头素材文件名口径（按素材包实测）：版本字仅 晓/樱/堇/辉/华 用繁体，
# 牌种仅「极」用「極」。fork 的 plate_aliases 是**用户输入归一**口径（含
# 將/鏡/廻），与素材包命名不一致，不能反推——曾致 樱将 等牌头全 miss
_S2T_VERSION = str.maketrans(
    {"晓": "暁", "樱": "櫻", "堇": "菫", "辉": "輝", "华": "華"}
)
_S2T_KIND = str.maketrans({"极": "極"})


def jp_cache_dir() -> Path:
    """日服在线封面缓存目录（localstore 缓存区；static 素材目录永不写入）。"""
    return get_data_dir("nonebot_plugin_awmc_helper") / "jp_covers"


def online_item_cache_dir(kind: str) -> Path:
    """收藏品（牌子/头像）在线素材缓存目录（localstore 数据区，按类分子目录）。"""
    return get_data_dir("nonebot_plugin_awmc_helper") / "online_items" / kind


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
    def pic_optional(cls, name: str, theme: str = DEFAULT_THEME) -> Image.Image | None:
        """同 pic，但主题子目录与根目录都不存在时返回 None（调用方走回退/跳过）。"""
        base = cls.static_path() / "mai" / "pic"
        for path in (base / theme / name, base / name):
            if path.exists():
                return cls.get(path)
        return None

    @classmethod
    def cover_candidates(cls, song_id: int) -> tuple[Path, ...]:
        """曲绘候选链：static 素材 → 日服封面缓存（jp_cover 在线拉取落盘）→ 0.png。"""
        return (
            cls.static_path() / "mai" / "cover" / f"{song_id}.png",
            jp_cache_dir() / f"{song_id}.png",
            cls.static_path() / "mai" / "cover" / "0.png",
        )

    @classmethod
    def cover(cls, song_id: int) -> Image.Image:
        """曲绘（400×400），按候选链取第一个存在的；全缺时返回占位图。"""
        for path in cls.cover_candidates(song_id):
            if path.exists():
                return cls.get(path)
        return Image.new("RGBA", (400, 400), "#666666")

    @classmethod
    def type_badge(cls, abbr: str, size: tuple[int, int]) -> Image.Image | None:
        """SD/DX 类型徽章（``mai/pic/{abbr}.png``）缩放到 size；缺失返回 None。"""
        path = cls.static_path() / "mai" / "pic" / f"{abbr}.png"
        if not path.exists():
            return None
        return cls.get(path).resize(size)

    @classmethod
    def plate_version(cls, version: str, kind: str) -> Image.Image | None:
        """牌子表头素材（``{版本}{牌种}.png``）。

        素材包文件名为繁体（牌种「極」、版本字 暁/櫻/菫/輝/華），先按繁体名
        查找再回退原始输入；舞舞牌「舞舞舞」、霸者「霸者」自然命中。
        """
        kind_t = kind.translate(_S2T_KIND)
        version_t = version.translate(_S2T_VERSION)
        for name in (f"{version_t}{kind_t}.png", f"{version}{kind}.png"):
            path = cls.static_path() / "mai" / "plate_version" / name
            if path.exists():
                return cls.get(path)
        return None


assets = Assets()
"""模块级素材访问单例。"""
