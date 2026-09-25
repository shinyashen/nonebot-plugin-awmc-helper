"""牌子（版本字 × 牌种）谱面范围判定：core 公开接口。

完成表/进度渲染与 tables 插件共用；版本依据 maimai_py ``plate_to_version``
（CN 口径：华/煌/宙/祝/宴并 PLUS，回 = CiRCLE PLUS，舞/霸为旧作全集特判）。
"""

from maimai_py import Song, Version, SongType, SongDifficulty, plate_to_version

_LEGACY_PLATES = ("舞", "霸")
"""旧作全集牌：横跨全部旧框版本，主类型为 SD。"""

_WU_PLATE_KINDS: dict[str, tuple[str, ...]] = {
    "舞": ("将", "极", "神", "舞舞"),
    "霸": ("者",),
}
"""舞/霸实际存在的牌种（游戏内与素材包一致）：舞代仅 舞将/舞极/舞神/舞舞舞，
霸前缀仅 霸者 一张（也是全游戏唯一「者」尾牌）——不存在 舞者/霸将 等组合。"""


def plate_kinds_of(version: str) -> tuple[str, ...]:
    """版本实际存在的牌种（完成表预渲染枚举口径）：舞代四牌、霸仅者。

    其余版本沿用「将」的单牌预渲染口径（查询兜底生成不限于将）。
    """
    if version in _WU_PLATE_KINDS:
        return _WU_PLATE_KINDS[version]
    return ("将",)


def is_valid_plate(version: str, kind: str) -> bool:
    """（版本字, 牌种）是否为真实存在的牌子。

    舞/霸按真实牌表收紧（舞者/霸将/霸极/霸神/霸舞舞 均不存在）；其余版本
    不设限，交由数据源判定。
    """
    if version in _WU_PLATE_KINDS:
        return kind in _WU_PLATE_KINDS[version]
    return True


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
    # 末段上界 = 当前最新版本码（FUTURE 为占位枚举、无对应真实版本，不计）
    hi = (
        (min(nxt) - 1)
        if nxt
        else max(
            v.value
            for v in plate_to_version.values()
            if v.value < Version.MAIMAI_DX_FUTURE.value
        )
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
