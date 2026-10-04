"""牌子（版本字 × 牌种）谱面范围判定：core 公开接口。

完成表/进度渲染与 tables 插件共用；版本依据 maimai_py ``plate_to_version``
（CN 口径：华/煌/宙/祝/宴并 PLUS，回 = CiRCLE PLUS，舞/霸为旧作全集特判）。
日服口径（``plate_to_version_jp``：PLUS 各代独立成牌）由 ``jp=True`` 参数
启用——库 ``MaimaiPlates`` 只认 CN 口径，日服数据源的牌子在插件侧本地判
（:func:`build_local_plates`）。
"""

from dataclasses import dataclass

from maimai_py import (
    Song,
    FCType,
    FSType,
    Version,
    SongType,
    LevelIndex,
    SongDifficulty,
    plate_to_version,
    current_version_jp,
    plate_to_version_jp,
)
from maimai_py.enums import plate_aliases
from maimai_py.models import PlateObject

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

_PLATE_CAP_JP = max(
    v for v in plate_to_version_jp.values() if v.value < current_version_jp.value
)
"""日服可查牌子的版本上限：``current_version_jp`` 的**前一个枚举成员**
（CiRCLE PLUS，2026-10 口径）——现行 MAGiCAL 代尚无牌字，牌单到「回」为止。"""

PLATE_CHARS_JP = "舞霸" + "".join(
    ch for ch, ver in plate_to_version_jp.items() if ver <= _PLATE_CAP_JP and ch != "初"
)
"""日服牌单字符（舞/霸置首同 PLATE_CHARS；日服 PLUS 各代独立成牌，含 丸/回）。

口径 = maimai_py ``plate_to_version_jp`` 键序（发售序）按「版本 ≤
``_PLATE_CAP_JP``」派生；「初」不在牌单（与国服同规：素材包无初代牌）。
库推进 ``current_version_jp`` 后上限随枚举自动前移，新代牌字自动纳入。"""


PLATE_VERSION_ALIAS_CHARS = "".join(
    ch
    for ch, target in plate_aliases.items()
    if target in set(PLATE_CHARS) | set(PLATE_CHARS_JP)
)
"""繁体/和制牌**版本字**（暁櫻菫輝華鏡廻；归一后落入 CN∪JP 牌单——形状回认
不分数据源，口径合法性由调用方经 :func:`plate_in_roster` 收口）。仅供指令
形状识别，预渲染迭代仍走 PLATE_CHARS 不受影响。"""

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


def plate_in_roster(version: str, *, jp: bool) -> bool:
    """牌子是否在对应口径的**可查牌单**内（数据源限制，2026-10-04 定案）。

    国服口径 = :data:`PLATE_CHARS`（≤ ``current_version``），日服口径 =
    :data:`PLATE_CHARS_JP`（≤ CiRCLE PLUS，即 ``current_version_jp`` 前一
    枚举成员）；舞/霸双口径通用。真实存在的牌子也可能不在当前数据源口径内
    （国服查 丸将——CiRCLE 是日服版本），由调用方给口径提示拒绝。
    """
    version = norm_plate(version)
    if version in _LEGACY_PLATES:
        return True
    return version in (PLATE_CHARS_JP if jp else PLATE_CHARS)


def plate_roster_hint(jp: bool) -> str:
    """口径限制的用户提示（牌单越界拒绝文案用；末位字符 = 最新可查代）。"""
    roster = PLATE_CHARS_JP if jp else PLATE_CHARS
    return f"{'日服' if jp else '国服'}数据源可查至「{roster[-1]}」代牌子"


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


def plate_version_range(version: str, *, jp: bool = False) -> tuple[int, int] | None:
    """牌子覆盖的谱面版本码闭区间（未知牌字返回 None）。

    ``jp=True`` 走日服口径（``plate_to_version_jp``：PLUS 各代独立成牌，
    区间到下一代牌字为止；与 CN 口径同函数，靠映射切换）。
    """
    mapping = plate_to_version_jp if jp else plate_to_version
    if version in _LEGACY_PLATES:
        vals = [v.value for v in mapping.values() if v < Version.MAIMAI_DX]
        return (min(vals), max(vals)) if vals else None
    pv = mapping.get(version)
    if pv is None:
        return None
    # 上界 = 下一牌字版本码 -1（未/FUTURE 为占位枚举，作上界即 29999，与库
    # ``MaimaiPlates._configure`` 的 [本代, 下一代) 半开区间口径一致）；
    # 下界默认为本代，真牌前移到初（_SD_FIRST_PLATES）——只动下界，上界仍按真。
    lo = pv.value
    if first := _SD_FIRST_PLATES.get(version):
        lo = plate_to_version[first].value
    nxt = [v.value for v in mapping.values() if v.value > pv.value]
    hi = (
        (min(nxt) - 1)
        if nxt
        else max(
            v.value
            for v in mapping.values()
            if v.value < Version.MAIMAI_DX_FUTURE.value
        )
    )
    if jp:
        # MAGiCAL（current_version_jp）尚无牌字，回 的上界推不出下一牌字，
        # 必须封到现行版本：MAGiCAL 曲目不得落入回（CiRCLE PLUS）牌范围
        hi = min(hi, current_version_jp.value - 1)
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
    ver = Version.from_value(diff.version)
    return ver.value if ver is not None else None


def in_plate_scope(
    song: Song, diff: SongDifficulty, lo: int, hi: int, major_type: SongType
) -> bool:
    """谱面是否落在牌子范围内（主类型 + 版本区间；version 归一后比较）。"""
    code = version_code_of(diff)
    return diff.type == major_type and code is not None and lo <= code <= hi


def plate_score_ok(kind: str, score) -> bool:
    """单谱面是否达成牌子要求（库 ``MaimaiPlates`` 判牌语义，单一事实）。

    者 = 达成率 ≥80（A）；将 = ≥100（SSS，SSS+ 同档）；极 = FC 及以上；
    神 = AP 及以上；舞舞 = FSD 及以上。完成表盖章与本地判牌
    （:func:`build_local_plates`）共用，不得各写一份。
    """
    if score is None:
        return False
    if kind == "者":
        return (score.achievements or 0) >= 80
    if kind == "将":
        # 将 = 全谱面 SSS（100.0）以上（SSS+=100.5 为自定义大将）；SS 档
        # 口径：S=97/S+=98/SS=99/SS+=99.5/SSS=100/SSS+=100.5
        return (score.achievements or 0) >= 100
    if kind == "极":
        return score.fc is not None and score.fc.value <= FCType.FC.value
    if kind == "神":
        return score.fc is not None and score.fc.value <= FCType.AP.value
    if kind == "舞舞":
        # 舞舞要求 FSD/FSDp：FSType 枚举 SYNC<FS<FSP<FSD<FSDP，取高端两档
        # （曾写成 <= FSD，把 Sync/FS/FSP 全误判达标、FSDp 反而漏判）
        return score.fs is not None and score.fs.value >= FSType.FSD.value
    return False


@dataclass
class LocalPlates:
    """本地判牌结果：库 ``MaimaiPlates`` 的 duck-type 替身。

    进度总览（sheet.plate_progress_overview）只消费 ``get_cleared`` /
    ``get_remained``（``PlateObject(song, levels)``），与库返回同构。
    """

    cleared: list[PlateObject]
    remained: list[PlateObject]

    async def get_cleared(self) -> list[PlateObject]:
        return self.cleared

    async def get_remained(self) -> list[PlateObject]:
        return self.remained


def build_local_plates(
    version: str, kind: str, songs: list[Song], scores: list
) -> LocalPlates:
    """日服口径本地判牌（库 ``MaimaiPlates`` 只认 CN 版本映射与曲库版本缓存）。

    范围/主类型走 :func:`plate_version_range`（jp）/ :func:`major_type_of_plate`，
    曲集为**日服视图**（JP 限定曲在牌范围内，缺席会永远差曲）；谱面级范围匹配
    （:func:`in_plate_scope`），要求槽 = 该曲主类型全部谱面（Re:MASTER 仅
    舞/霸 计入，库 ``no_remaster`` 同语义），达成判定 = :func:`plate_score_ok`。
    """
    version, kind = norm_plate(version), norm_plate(kind)
    major = major_type_of_plate(version)
    rng = plate_version_range(version, jp=True)
    matched: dict[int, Song] = {}
    if rng is not None:
        lo, hi = rng
        for song in songs:
            if any(
                in_plate_scope(song, d, lo, hi, major) for d in song.get_difficulties()
            ):
                matched[song.id] = song
    no_remaster = version not in _LEGACY_PLATES
    cleared: dict[int, set[LevelIndex]] = {}
    required: dict[int, set[LevelIndex]] = {}
    for song in matched.values():
        levels = {d.level_index for d in song.get_difficulties(major)}
        if no_remaster:
            levels.discard(LevelIndex.ReMASTER)
        cleared[song.id] = set()
        required[song.id] = levels
    for score in scores:
        if score.id not in required or score.type != major:
            continue
        if score.level_index not in required[score.id]:
            continue
        if plate_score_ok(kind, score):
            cleared[score.id].add(score.level_index)
    remained = {
        sid: levels - cleared[sid] for sid, levels in required.items() if levels
    }
    return LocalPlates(
        cleared=[
            PlateObject(song=matched[sid], levels=levels, scores=[])
            for sid, levels in cleared.items()
            if levels
        ],
        remained=[
            PlateObject(song=matched[sid], levels=levels, scores=[])
            for sid, levels in remained.items()
            if levels
        ],
    )
