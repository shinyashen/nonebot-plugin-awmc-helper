"""maimai_py 类型门面：子插件由此导入，禁止直接 ``import maimai_py``。

仅做再导出，不承载逻辑；子插件需要新的 maimai_py 类型/常量时在此补一行。
"""

from maimai_py import (
    Song,
    Genre,
    FCType,
    FSType,
    SongType,
    LevelIndex,
    ScoreExtend,
    current_version,
)
from maimai_py.models import SongDifficultyUtage

__all__ = [
    "FCType",
    "FSType",
    "Genre",
    "LevelIndex",
    "ScoreExtend",
    "Song",
    "SongDifficultyUtage",
    "SongType",
    "current_version",
]
