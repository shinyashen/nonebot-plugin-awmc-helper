"""牌子（版本字 × 牌种）谱面范围判定：core 公开接口。

完成表/进度渲染与 tables 插件共用；版本依据 maimai_py ``plate_to_version``
（CN 口径：华/煌/宙/祝/宴并 PLUS，回 = CiRCLE PLUS，舞/霸为旧作全集特判）。
"""

from maimai_py import Song, Version, SongType, SongDifficulty, plate_to_version
from maimai_py.enums import plate_aliases

from ..constants import PLATE_CHARS

_LEGACY_PLATES = ("舞", "霸")
"""旧作全集牌：横跨全部旧框版本，主类型为 SD。"""


def norm_plate(text: str) -> str:
    """繁体/和制牌字 → 简体牌单口径（单源 maimai_py ``plate_aliases``）。

    本地牌单与 ``plate_to_version`` 键均为简体；maimai_py ``MaimaiPlates``
    入口同样先归一再查表——本地先行校验若不归一，会把上游认可的繁体写法
    （暁将/櫻極）判否。
    """
    return plate_aliases.get(text, text)


_PLATE_KINDS_ROSTER: dict[str, tuple[str, ...]] = {
    "舞": ("将", "极", "神", "舞舞"),
    "霸": ("者",),
    "真": ("极", "神", "舞舞"),
    "初": (),
}
"""牌单例外表（2026-09-27 素材包 ``mai/plate_version/`` 全量实证，26 组牌头）：

除下列四项外各版本均为 将/极/神/舞舞 四牌齐全——舞代四牌；霸仅 者（全游戏唯一
「者」尾牌）；**真无将**（仅 极/神/舞舞）；**初整代无牌**（国服牌单自真起）。
用户口径与素材一致（2026-09-27：没有初、真没有将牌）。"""

_DEFAULT_PLATE_KINDS = ("将", "极", "神", "舞舞")

_SD_FIRST_PLATES = {"真": "初"}
"""下界前移的版本：**真牌含初代曲**——库侧 ``MaimaiPlates._configure`` 把
初+真 并作真牌范围，原版 Hoshino 的 ``VERSION_MAP["真"]`` 亦为 [真, 初]；
国服无初牌（牌单自真起），初代曲只能落在真牌范围内，否则「牌子」进度
（库算）与「完成表」底图（本地算）会差出一整代曲目。"""


PLATE_KINDS = tuple(
    dict.fromkeys(
        _DEFAULT_PLATE_KINDS
        + tuple(k for kinds in _PLATE_KINDS_ROSTER.values() for k in kinds)
    )
)
"""全部牌种字符并集（输入解析/正则用；各版本**真实牌单**见 :func:`plate_kinds`）。"""


PLATE_VERSION_ALIAS_CHARS = "".join(
    ch for ch, target in plate_aliases.items() if target in set(PLATE_CHARS)
)
"""繁体/和制牌**版本字**（暁櫻菫輝華鏡；归一后须落入 PLATE_CHARS——回/丸等
未实装代不收）。仅供指令正则识别，预渲染迭代仍走 PLATE_CHARS 不受影响。"""

PLATE_KIND_ALIAS_CHARS = "".join(
    ch for ch, target in plate_aliases.items() if target in PLATE_KINDS
)
"""繁体/和制牌**牌种字**（極將；归一后落入 PLATE_KINDS）。同上仅正则识别用。"""


def plate_kinds(version: str) -> tuple[str, ...]:
    """该版本**真实存在**的牌种（牌单例外表；未知版本按四牌齐全兜底）。"""
    return _PLATE_KINDS_ROSTER.get(norm_plate(version), _DEFAULT_PLATE_KINDS)


def plate_kinds_of(version: str) -> tuple[str, ...]:
    """完成表**预渲染**枚举口径：舞/霸全牌、真无将故跳过，其余版本只出「将」。

    与 :func:`plate_kinds`（真实牌单）区分——真实牌单各版本都是四牌，只预渲染
    「将」是既有的省时口径（其余牌种查询时兜底生成，见
    ``table_template.draw_plate_table_with_fallback``）。
    """
    kinds = plate_kinds(version)
    if version in _LEGACY_PLATES:
        return kinds
    return ("将",) if "将" in kinds else ()


def plate_kinds_hint(version: str) -> str:
    """牌名不存在时的牌单提示（读例外表；初代整代无牌另有文案）。"""
    version = norm_plate(version)
    kinds = plate_kinds(version)
    if not kinds:
        return f"国服没有「{version}」代牌子"
    return f"{version}代牌为 " + "/".join(f"{version}{kind}" for kind in kinds)


def is_valid_plate(version: str, kind: str) -> bool:
    """（版本字, 牌种）是否为真实存在的牌子（牌单例外表为准）。

    舞/霸/真 按真实牌表收紧（舞者/霸将/真将/樱者 等组合均不存在）；其余版本
    四牌齐全，超出四牌的牌种（如 樱者）同样判否。繁体/和制牌字先归一
    （:func:`norm_plate`）再查表。
    """
    return norm_plate(kind) in plate_kinds(version)


def major_type_of_plate(version: str) -> SongType:
    """牌子主类型（maimai_py MaimaiPlates._major_type 同语义）。

    DX 世代牌推 DX 谱，旧作牌（含舞/霸）推 SD 谱——决定 per-type 游戏 id
    与定数取哪侧谱面。
    """
    if version in _LEGACY_PLATES:
        return SongType.STANDARD
    pv = plate_to_version.get(version)
    return (
        SongType.DX if pv is not None and pv >= Version.MAIMAI_DX else SongType.STANDARD
    )


def plate_version_range(version: str) -> tuple[int, int] | None:
    """牌子覆盖的谱面版本码闭区间（未知牌字返回 None）。"""
    if version in _LEGACY_PLATES:
        vals = [v.value for v in plate_to_version.values() if v < Version.MAIMAI_DX]
        return (min(vals), max(vals)) if vals else None
    pv = plate_to_version.get(version)
    if pv is None:
        return None
    # 上界 = 下一牌字版本码 -1（未/FUTURE 为占位枚举，作上界即 29999，与库
    # ``MaimaiPlates._configure`` 的 [本代, 下一代) 半开区间口径一致）；
    # 下界默认为本代，真牌前移到初（_SD_FIRST_PLATES）——只动下界，上界仍按真。
    lo = pv.value
    if first := _SD_FIRST_PLATES.get(version):
        lo = plate_to_version[first].value
    nxt = [v.value for v in plate_to_version.values() if v.value > pv.value]
    hi = (
        (min(nxt) - 1)
        if nxt
        else max(
            v.value
            for v in plate_to_version.values()
            if v.value < Version.MAIMAI_DX_FUTURE.value
        )
    )
    return (lo, hi)


def version_code_of(diff: SongDifficulty) -> int | None:
    """谱面版本 → 归一代码（``Version.from_value`` carry-forward）。

    otoge-db 的 version 字段是**代内逐曲递增值**（如 11007、19999），非枚举
    基码——等值/区间比较必须先归一到所属代（from_value(11007)=11000），
    否则「舞」类全集码等值匹配与牌子区间上界大量漏曲（19992–19999 的 FiNALE
    末期 lv15 ReM 谱被 19900 上界排除，舞将完成表缺 15）。
    """
    if diff.version is None:
        return None
    return Version.from_value(diff.version).value


def in_plate_scope(
    song: Song, diff: SongDifficulty, lo: int, hi: int, major_type: SongType
) -> bool:
    """谱面是否落在牌子范围内（主类型 + 版本区间；version 归一后比较）。"""
    code = version_code_of(diff)
    return diff.type == major_type and code is not None and lo <= code <= hi
