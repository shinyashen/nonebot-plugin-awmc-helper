"""牌子（版本字 × 牌种）谱面范围判定：core 公开接口。

完成表/进度渲染与 tables 插件共用；版本依据 maimai_py ``plate_to_version``
（CN 口径：华/煌/宙/祝/宴并 PLUS，回 = CiRCLE PLUS，舞/霸为旧作全集特判）。
"""

from maimai_py import Song, SongType, SongDifficulty, plate_to_version

_LEGACY_PLATES = ("舞", "霸")
"""旧作全集牌：横跨全部旧框版本，主类型为 SD。"""


def major_type_of_plate(version: str) -> SongType:
    """牌子主类型（maimai_py MaimaiPlates._major_type 同语义）。

    DX 世代牌推 DX 谱，旧作牌（含舞/霸）推 SD 谱——决定 per-type 游戏 id
    与定数取哪侧谱面。
    """
    if version in _LEGACY_PLATES:
        return SongType.STANDARD
    pv = plate_to_version.get(version)
    return SongType.DX if pv is not None and pv.value >= 20000 else SongType.STANDARD


def plate_version_range(version: str) -> tuple[int, int] | None:
    """牌子覆盖的谱面版本码闭区间（未知牌字返回 None）。"""
    if version in _LEGACY_PLATES:
        vals = [v.value for v in plate_to_version.values() if v.value < 20000]
        return (min(vals), max(vals)) if vals else None
    pv = plate_to_version.get(version)
    if pv is None:
        return None
    nxt = [v.value for v in plate_to_version.values() if v.value > pv.value]
    # 末段上界 = 当前最新版本码（FUTURE 30000 为未实装占位，不计）
    hi = (
        (min(nxt) - 1)
        if nxt
        else max(v.value for v in plate_to_version.values() if v.value < 30000)
    )
    return (pv.value, hi)


def in_plate_scope(
    song: Song, diff: SongDifficulty, lo: int, hi: int, major_type: SongType
) -> bool:
    """谱面是否落在牌子范围内（主类型 + 版本区间）。"""
    return (
        diff.type == major_type
        and diff.version is not None
        and lo <= diff.version <= hi
    )
