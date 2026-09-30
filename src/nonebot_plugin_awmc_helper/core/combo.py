"""条件组合查询（「辉50」「紫谱50」「神50」类）：解析与执行，core 单源。

设计权威：``local/reference/karenbot-combo-notes.md`` §8/§9/§11（2026-09-30
定稿）。四段管线：matcher 剥尾缀 → :func:`parse_combo`（分层 FMM 词法扫描 +
线性条件装配）→ :func:`run_combo`（曲库谱面键集 → 全量成绩过滤 → 组装）→
渲染层。子插件只消费结果，不触碰解析/谓词（硬性架构规则 1/2）。

- **输出四态**：``list[Cond]``（出图）/ :class:`ComboAmbiguity`（裸紫/白
  歧义提示，不出图）/ ``None``（零条件或纯数字串——**静默不回话**，
  ``^(.+?)50$`` 松匹配下以 50 结尾的闲聊不是本 bot 的消息）/ :class:`ComboEmpty`
  （谱面集空，文案终止）。
- **组合语义**：同 CondType OR、跨型 AND；版本字（S-1）/世代（S-3）/新旧
  （S-5）是三个不同 CondType——「雪辉dx50」= 雪辉版本 ∩ DX 世代（AND，非并集）。
- **n15 判定**（§8）：含谱面类条件 → 平铺（库切片语义），纯成绩类 → 拆分
  （b50 变体）；不足 15 首留白不回退。``全``/``含new`` 已剔除，无强制出口。
- **宴谱**：默认排除；S-19 宴谱条件在场时 = 仅宴谱。
- **包含式语义**（§9）：FC 族 = fc 有值（AP 必然也是全连）、AP 族 =
  ``fc ≤ AP``（FCType 值序 APP=0 最优）、FSD 族 = ``fs ≥ FSD``（FSType 值序
  相反，SYNC=0 为无徽章）；rate 一律 ``RateType._from_achievement`` 派生；
  达成率比较全走万分位整数。
- **理想（S-25）**modifier 作用于 ``dataclasses.replace`` 副本——NET 成绩是
  窗口缓存共享对象，禁止原地改。
"""

import re
from enum import Enum
from typing import Any
from dataclasses import replace, dataclass
from collections.abc import Callable

from maimai_py import (
    Song,
    Genre,
    FCType,
    FSType,
    Version,
    RateType,
    SongType,
    LevelIndex,
    ScoreExtend,
    SongDifficulty,
    current_version,
    plate_to_version,
    current_version_jp,
)

from .calc import build_bests, compute_rating, build_flat_bests
from .score import score_service
from .songs import song_service
from .plates import norm_plate
from .songdb import State, ensure_ongeki_titles
from ..constants import normalize_text

# ---------------------------------------------------------------- 模型


class CondType(Enum):
    """条件类型：同型 OR、跨型 AND；S-1/S-3/S-5 分型见模块 docstring。"""

    # 谱面类（库切片；n15 → 平铺）
    VERSION = "version"  # S-1 版本字连续段（码集）
    ERA = "era"  # S-3 世代：旧框（≤19900）/ DX（>19900）
    ERA_YEAR = "era_year"  # S-2 回到过去：版本 ≤ 年份码 + 新旧分界移到该码（恒拆分）
    CHART_TYPE = "chart_type"  # S-4 谱面类型：标准/DX 谱（≠世代）
    NEWNESS = "newness"  # S-5 新旧（按视图现行版本）
    GENRE = "genre"  # S-6 曲目分类
    GENRE_SUB = "genre_sub"  # S-6 细分：中二/音击（オンゲキCHUNITHM 大类二分）
    DIFF = "diff"  # S-7 难度色（chart+record 双谓词）
    LEVEL = "level"  # S-8 等级精确匹配
    DS = "ds"  # S-9 定数精确匹配
    DESIGNER = "designer"  # S-10 谱师（实名动态注册，归一包含式）
    UTAGE = "utage"  # S-19 仅宴谱（默认排除的反向条件）
    # 成绩类（质量过滤；n15 → 拆分）
    COMBO = "combo"  # S-12 FC 族 / AP 族 / 理论值
    SYNC = "sync"  # S-13 FSD 族 / FSD+
    RATE = "rate"  # S-14 评级档（≥ 档 / 纯·仅 == 档）
    BADGE = "badge"  # S-15 牛逼（≥100.8）/ 越级（<95）
    STAR = "star"  # S-16 DX 星数（恰好 N）
    CUN = "cun"  # S-17 寸（距里程碑不足，排序覆盖）
    KILL = "kill"  # S-18 名刀（刚过里程碑，排序覆盖）
    IDEAL = "ideal"  # S-25 理想（升一档 modifier）
    # 修改类（不过滤；n15 不参与判定）
    FIT = "fit"  # S-24 拟合定数（重算 RA 副本，排序随默认 RA 降序）


CHART_COND_TYPES = frozenset(
    {
        CondType.VERSION,
        CondType.ERA,
        CondType.ERA_YEAR,
        CondType.CHART_TYPE,
        CondType.NEWNESS,
        CondType.GENRE,
        CondType.GENRE_SUB,
        CondType.DIFF,
        CondType.LEVEL,
        CondType.DS,
        CondType.DESIGNER,
        CondType.UTAGE,
    }
)
"""谱面类条件集（条件集含任一 → n15 平铺，§8；例外：ERA_YEAR 在场 → 恒拆分，
覆盖其谱面类性质——查询目的即分界结构，§8 n15 特则）。"""


class OutputKind(str, Enum):
    """输出口径（§9.7 适用矩阵的轴）：条件 × 指令准入校验用。"""

    B50 = "b50"
    B40 = "b40"  # 与 B50 同矩阵列（§9.7 「b50/40」一列）
    SCORE_LIST = "score_list"
    TABLE = "table"
    DS_TABLE = "ds_table"


@dataclass(frozen=True)
class Cond:
    """单条条件（声明式）：谓词 + 展示名 + 可选排序覆盖/修改器。

    ``key`` 为同型去重键（规范化载荷）；``value`` 保留原始载荷（空集点破的
    静态矛盾检测读它）。``chart`` 第三参为视图现行版本码（NEWNESS 比较按
    视图分界用，其余谓词忽略）——对设计稿 ``(Song, SongDifficulty)`` 签名的
    实施修正（见笔记 §8.2）。``applicability`` 声明适用的输出口径（§9.7
    矩阵；P1 仅 b50 一种输出，全部默认适用）。
    """

    ctype: CondType
    key: str
    label: str
    chart: "Callable[[Song, SongDifficulty, int], bool] | None" = None
    record: "Callable[[ScoreExtend], bool] | None" = None
    sort_key: "Callable[[ScoreExtend], Any] | None" = None
    modifier: "Callable[[ScoreExtend], ScoreExtend] | None" = None
    value: Any = None
    single_chart: bool = False
    applicability: "frozenset[OutputKind] | None" = None
    """显式适用口径；None = 按 §9.7 矩阵（:func:`applicability_of`）派生。"""


@dataclass
class ComboAmbiguity:
    """歧义中止（裸紫/白）：整条查询不出图，回引导文案（§9.4）。"""

    message: str


@dataclass
class ComboEmpty:
    """谱面集空（§9.4）：条件语法合法但曲库无交集，文案终止（可点破矛盾）。"""

    message: str


@dataclass
class ComboResult:
    """执行产物：渲染层消费的组装结果。

    ``flat=True`` 时组装体全部位于 ``bests.scores_b35``（b15 侧置空、rating =
    合计 RA），渲染层走 flat 版式；``flat=False`` 为标准 35/15 拆分。
    ``bests`` 为 maimai_py ``PlayerBests``。
    """

    title: str  # 条件显示串（称号条「条件 · 条数 · 合计RA」口径的条件段）
    bests: Any
    flat: bool
    total_ra: int
    scores: list[ScoreExtend]


# §9.7 适用矩阵（2026-09-30 拍板：不适用=拒绝并提示，非上游静默失效）：
# A 谱面类全输出适用；B1 达标型（combo/sync/rate）进分数列表与表格盖章判型、
# 不进定数表；B2 区间型（badge/star/cun/kill）只进 b50 与分数列表；C 修改类
# （ideal/fit）只进 b50/40（拟合定数表随 P3 再议）；D 回到过去（era_year）只进
# b50/40（分数列表按达成率排、历史完成表/定数表未定义，§9.7 D 行）。
_B1_KINDS = frozenset(
    {OutputKind.B50, OutputKind.B40, OutputKind.SCORE_LIST, OutputKind.TABLE}
)
_B2_KINDS = frozenset({OutputKind.B50, OutputKind.B40, OutputKind.SCORE_LIST})
_B40_KINDS = frozenset({OutputKind.B50, OutputKind.B40})
_APPLICABILITY: "dict[CondType, frozenset[OutputKind]]" = {
    # 谱面类默认全集；era_year 收窄为 b50/40（覆盖全 entries 展开的多余项）
    **{ct: frozenset(OutputKind) for ct in CHART_COND_TYPES},
    CondType.ERA_YEAR: _B40_KINDS,
    CondType.COMBO: _B1_KINDS,
    CondType.SYNC: _B1_KINDS,
    CondType.RATE: _B1_KINDS,
    CondType.BADGE: _B2_KINDS,
    CondType.STAR: _B2_KINDS,
    CondType.CUN: _B2_KINDS,
    CondType.KILL: _B2_KINDS,
    CondType.IDEAL: _B40_KINDS,
    CondType.FIT: _B40_KINDS,
}


def applicability_of(cond: Cond) -> "frozenset[OutputKind]":
    """条件的适用输出口径（Cond 显式声明优先，缺省按 §9.7 矩阵）。"""
    return (
        cond.applicability
        if cond.applicability is not None
        else _APPLICABILITY[cond.ctype]
    )


def inapplicable(conds: "list[Cond]", output: OutputKind) -> "list[Cond]":
    """§9.7 适用矩阵校验：返回对 ``output`` 不适用的条件。"""
    return [c for c in conds if output not in applicability_of(c)]


def plan_of(conds: "list[Cond]") -> "tuple[Callable[[Any, Any, Any], bool], str, str]":
    """表格判型推导（§5）：条件集首个达标型（combo/sync/rate）条件 → 盖章
    checker 与 plan 词（进度卡提示文案/排序 kind 用）；无达标型条件 → 达成率
    ≥80%（KarenBot「否则→评级章」同款）。返回 (checker, plan 词, 排序 kind)。
    """
    for c in conds:
        if c.ctype is CondType.COMBO:
            if c.key == "fcp_all":
                return _fc_checker(FCType.FCP), "fcp", "fc"
            if c.key == "ap_all":
                return _fc_checker(FCType.AP), "ap", "fc"
            if c.key == "app":
                return _fc_checker(FCType.APP), "ap", "fc"
            return _fc_checker(FCType.FC), "fc", "fc"
        if c.ctype is CondType.SYNC:
            if c.key == "fs_all":
                return _fs_checker(FSType.FS), "fs", "fs"
            if c.key == "fsp_all":
                return _fs_checker(FSType.FSP), "fsp", "fs"
            if c.key == "fsdp":
                return _fs_checker(FSType.FSDP), "fdx", "fs"
            return _fs_checker(FSType.FSD), "fdx", "fs"
        if c.ctype is CondType.RATE:
            target, exact = c.value
            floor = _RATE_FLOOR[target]

            def checker(ach, _fc, _fs, _t=target, _eq=exact, _f=floor):
                if ach is None:
                    return False
                rate = RateType._from_achievement(ach)
                return rate == _t if _eq else (ach or 0) >= _f

            return checker, _PLAN_WORD[target], "rate"
    return (lambda ach, _fc, _fs: (ach or 0) >= 80), "", "rate"


_PLAN_WORD: "dict[RateType, str]" = {
    RateType.SSSP: "sssp",
    RateType.SSS: "sss",
    RateType.SSP: "ssp",
    RateType.SS: "ss",
    RateType.SP: "sp",
    RateType.S: "s",
    RateType.AAA: "aaa",
    RateType.AA: "aa",
    RateType.A: "a",
}
"""RATE 档 → 进度卡 plan 词（对齐既有 PLANS 键，提示文案回放用户口径）。"""


def _fc_checker(minimum: FCType):
    return lambda ach, fc, _fs: fc is not None and fc.value <= minimum.value


def _fs_checker(minimum: FSType):
    return lambda ach, _fc, fs: fs is not None and fs.value >= minimum.value


# ---------------------------------------------------------------- 词表（纯数据）
# 分层 FMM：逐位置扫描，层号小者优先、同层命中长者赢；未匹配字符跳过
# （对齐 KarenBot contains 语义）。规则表纯数据，可表驱动测试。
#
# 层 1 = 复合/消歧词（先于版本段，消解吞噬）；层 2 = 多字词；层 3 = 原子。
# P1 未注册：谱师别名（P2 动态）、回到过去「舞萌dxYYYY」（P3，层 1 预留）。

# S-6 分类词（长词在前防前缀吞噬；口语别名本地补表）。⚠️ 不收单字「烤」
# （误触发面大）；「宴」不进分类表——它是版本字（宴=双代），宴谱场景由
# 「宴谱/宴会场」全称承担。
_GENRE_WORDS: tuple[tuple[str, Genre], ...] = (
    ("音击中二", Genre.オンゲキCHUNITHM),
    ("ポップアニメ", Genre.POPSアニメ),
    ("niconico", Genre.niconicoボーカロイド),
    ("vocaloid", Genre.niconicoボーカロイド),
    ("流行动漫", Genre.POPSアニメ),
    ("其他游戏", Genre.ゲームバラエティ),
    ("术力口", Genre.niconicoボーカロイド),
    ("ボカロ", Genre.niconicoボーカロイド),
    ("二次元", Genre.POPSアニメ),
    ("variety", Genre.ゲームバラエティ),
    ("音击中二", Genre.オンゲキCHUNITHM),
    ("中二音击", Genre.オンゲキCHUNITHM),
    ("maimai", Genre.maimai),
    ("舞萌", Genre.maimai),
    ("东方", Genre.東方Project),
    ("東方", Genre.東方Project),
    ("流行", Genre.POPSアニメ),
    ("动漫", Genre.POPSアニメ),
    ("游戏", Genre.ゲームバラエティ),
    ("nico", Genre.niconicoボーカロイド),
    ("pjsk", Genre.ゲームバラエティ),
    ("车万", Genre.東方Project),
    ("v家", Genre.niconicoボーカロイド),
)
_GENRE_PAIRS = sorted(_GENRE_WORDS, key=lambda p: len(p[0]), reverse=True)
_GENRE_OF = {w.lower(): g for w, g in _GENRE_PAIRS}

# S-6 细分词（オンゲキCHUNITHM 大类二分，2026-09-30 用户穷举别名口径）：
# 中二 = 中二/中二节奏/chunithm，音击 = 音击/ongeki；全称「音击中二」仍在
# 大类词表（同层最长匹配自然先于侧别词命中）
_GENRE_SUB_WORDS: "tuple[tuple[str, str], ...]" = (
    ("中二节奏", "chunithm"),
    ("chunithm", "chunithm"),
    ("ongeki", "ongeki"),
    ("中二", "chunithm"),
    ("音击", "ongeki"),
)
_GENRE_SUB_PAIRS = sorted(_GENRE_SUB_WORDS, key=lambda p: len(p[0]), reverse=True)

# S-14 评级档位词（≥ 语义）：alternation 长度降序防前缀吞噬（sss+ 先于 sss
# 先于 ss 先于 s）；大小写不敏感（load 侧 lower 归一）。大将/鸟加 ≥SSS+、
# 鸟 ≥SSS、霸/clear ≥A。
_RATE_GE_WORDS = r"sssp|sss\+|ss\+|ssp|sss|ss|s\+|aaa|sp|s|clear|大将|鸟加|鸟|霸"
_RATE_GE: "dict[str, RateType]" = {
    "sssp": RateType.SSSP,
    "sss+": RateType.SSSP,
    "ss+": RateType.SSSP,
    "ssp": RateType.SSSP,
    "大将": RateType.SSSP,
    "鸟加": RateType.SSSP,
    "sss": RateType.SSS,
    "鸟": RateType.SSS,
    "ss": RateType.SS,
    "s+": RateType.SP,
    "sp": RateType.SP,
    "s": RateType.S,
    "aaa": RateType.AAA,
    "霸": RateType.A,
    "clear": RateType.A,
}

# S-12 连击族词：fc/全连 → FC 族；理论/ap+/app → 理论值；ap → AP 族
# fcp（FCP 族）为既有 plan 词收编（13fcp完成表 等触发文本逐字不变；
# alternation 长词在前，fcp 先于 fc）
_COMBO_ALTERNATION = r"全连|理论|fcp|fc|ap\+|app|ap"

# S-13 同步族词（层 1 复合词）：FSD+ 变体先于 FSD 族
# fsp/fs 为既有 plan 词收编（13fs完成表 等触发文本不变；2026-09-30 拍板
# 不补「同步」口语词与此无关——收编保触发 ≠ 新增口语词）
_SYNC_ALTERNATION = r"fdxp|fsdp|fdx\+|fsd\+|fdx|fsd|fsp|fs"

# S-1 版本字连续段：含繁体/和制牌字（load 侧 norm_plate 归一）；「代」尾缀
# 为布尔标记（裸字与「代」双注册同语义）；「未」不收（FUTURE 占位、两视图皆空）
_VERSION_RUN = (
    r"[初真超檄橙晓桃樱紫堇白雪辉舞熊华爽煌宙星祭祝双宴镜彩丸回廻暁櫻菫輝華鏡]+代?"
)

# S-8/S-9：数字尾缀 combo（50/40）下「级」「定数」必带——裸数字串（1350/650、
# 13.50 类）一律不解析为条件（§9.4 拍板：群聊以 50 结尾的数字不是查询）
_LEVEL_NUM = r"(\d{1,2}\+?)级"
_DS_NUM = r"(\d{1,2})\.(\d)定数"

_PURE_NUMBER = re.compile(r"[\d.]+")
"""纯数字（含小数点）整串：一律静默（§11.1 拍板）。"""

_PURE_NUMBER_LEVEL = re.compile(r"[\d.]+\+?")
"""裸数字条件形态（含 13+ 尾缀）：numeric_level 语境下的等级/定数条件。"""

_AMBIGUITY_HINT = (
    "「{ch}」有歧义：查{ch}谱（难度）请用「{ch}谱50」，查{ch}代（版本）请用「{ch}代50」"
)
"""裸紫/白的歧义引导文案（§9.4：不猜语义不出图）。"""


@dataclass(frozen=True)
class Token:
    kind: str
    value: Any
    text: str
    pos: int


@dataclass(frozen=True)
class _Rule:
    layer: int
    pattern: "re.Pattern[str]"
    kind: str
    load: "Callable[[re.Match[str]], Any]"


def _const(value: Any):
    return lambda m: value


def _genre_sub_match(song: Song, which: str) -> bool:
    """中二/音击侧别判定（§12.3 归属算法）：オンゲキCHUNITHM 大类内，
    norm_title ∈ ongeki 原创栏标题集 → 音击，其余默认中二（イロドリミドリ
    并入中二侧）。集合未加载（冷启动且 kv 无值）时音击=空集、中二=大类。"""
    if song.genre != Genre.オンゲキCHUNITHM:
        return False
    from .songdb import norm_title, ongeki_titles

    in_ongeki = norm_title(song.title) in ongeki_titles()
    return in_ongeki if which == "ongeki" else not in_ongeki


def _color_of(text: str) -> LevelIndex:
    """难度色字 → LevelIndex（紫谱/白谱/绿黄红；裸紫白不走此路，见装配）。"""
    from ..constants import COLOR_TO_LEVEL_INDEX

    return COLOR_TO_LEVEL_INDEX[text]


def _sync_kind_of(word: str) -> str:
    """同步族词 → 子型（fs 全族 / fsp 族 / fdx 族 / fsdp）。"""
    if word in ("fdxp", "fsdp", "fdx+", "fsd+"):
        return "fsdp"
    if word in ("fsp",):
        return "fsp_all"
    if word in ("fs",):
        return "fs_all"
    return "fsd"


def _combo_kind_of(word: str) -> str:
    """连击族词 → 子型（fcp 族 / fc 全族 / ap 族 / app 理论值）。"""
    if word == "fcp":
        return "fcp_all"
    if word in ("fc", "全连"):
        return "fc_all"
    if word in ("理论", "ap+", "app"):
        return "app"
    return "ap_all"


def _star_of(text: str) -> int:
    """星级词 → 星数（一~五 / 1~5）。"""
    return "一二三四五12345".index(text[0]) % 5 + 1


_NUMERIC_LEVEL_RULE = _Rule(
    2,
    re.compile(r"(\d{1,2}\+?)(?![\d.])"),
    "level",
    lambda m: m.group(),
)
r"""裸数字等级 token（13/13+；numeric_level 语境注入）。

``(?![\d.])``：后随数字/小数点不吞——``1350``（三位数）不成等级、
``13.5`` 是定数串（裸小数由 parse_combo 纯数字分支处理），均自然跳过。
"""

_RULES: "tuple[_Rule, ...]" = (
    # ---- 层 1：复合/消歧词 ----
    _Rule(
        1,
        # S-2 回到过去：dx2024/舞萌dx2024/2024（层 1 先于层 2 的 dx 世代词
        # 与层 3 版本段）；value=年份，未收录年份在装配期丢弃
        re.compile(r"(舞萌dx|dx)?20\d{2}", re.IGNORECASE),
        "era_year",
        lambda m: int(m.group()[-4:]),
    ),
    _Rule(1, re.compile(r"dx无印", re.IGNORECASE), "era_year", _const(2019)),
    _Rule(1, re.compile(r"([紫白])谱"), "diff", lambda m: _color_of(m.group(1))),
    _Rule(1, re.compile(r"([紫白])代"), "version", lambda m: ((m.group(1),), True)),
    _Rule(1, re.compile(r"舞舞"), "sync", _const("fsd")),
    _Rule(
        1,
        re.compile(_SYNC_ALTERNATION, re.IGNORECASE),
        "sync",
        lambda m: _sync_kind_of(m.group().lower()),
    ),
    # ---- 层 2：多字词 ----
    _Rule(
        2,
        re.compile("|".join(w for w, _ in _GENRE_PAIRS), re.IGNORECASE),
        "genre",
        lambda m: _GENRE_OF[m.group().lower()],
    ),
    _Rule(
        2,
        re.compile("|".join(w for w, _ in _GENRE_SUB_PAIRS), re.IGNORECASE),
        "genre_sub",
        lambda m: dict(_GENRE_SUB_PAIRS)[m.group().lower()],
    ),
    _Rule(2, re.compile(r"大将|鸟加"), "rate", _const("大将")),
    _Rule(2, re.compile(r"纯|仅"), "rate_mod", _const(None)),
    _Rule(2, re.compile(r"牛逼|nb", re.IGNORECASE), "badge", _const("nb")),
    _Rule(2, re.compile(r"丢人|招笑|越级"), "badge", _const("loser")),
    _Rule(2, re.compile(r"[一二三四五1-5]星"), "star", lambda m: _star_of(m.group())),
    _Rule(2, re.compile(r"寸"), "cun", _const(None)),
    _Rule(2, re.compile(r"锁血|名刀|血压|锁"), "kill", _const(None)),
    _Rule(2, re.compile(r"宴谱|宴会场"), "utage", _const(None)),
    _Rule(2, re.compile(r"旧框"), "era", _const("old")),
    _Rule(2, re.compile(r"dx谱", re.IGNORECASE), "chart_type", _const(SongType.DX)),
    _Rule(2, re.compile(r"标准"), "chart_type", _const(SongType.STANDARD)),
    _Rule(2, re.compile(r"旧版本"), "newness", _const("old")),
    _Rule(2, re.compile(r"新版本|新歌"), "newness", _const("new")),
    _Rule(2, re.compile(r"理想"), "ideal", _const(None)),
    _Rule(
        2,
        # S-24：KarenBot 同款别名（nh）；「拟合定数」长词先命中
        re.compile(r"拟合定数|拟合|nh", re.IGNORECASE),
        "fit",
        _const(None),
    ),
    _Rule(2, re.compile(_LEVEL_NUM), "level", lambda m: m.group(1)),
    _Rule(
        2,
        re.compile(_DS_NUM),
        "ds",
        lambda m: int(m.group(1)) + int(m.group(2)) / 10,
    ),
    _Rule(
        2,
        re.compile(_RATE_GE_WORDS, re.IGNORECASE),
        "rate",
        lambda m: m.group().lower(),
    ),
    _Rule(
        2,
        re.compile(_COMBO_ALTERNATION, re.IGNORECASE),
        "combo",
        lambda m: _combo_kind_of(m.group().lower()),
    ),
    _Rule(2, re.compile(r"dx", re.IGNORECASE), "era", _const("dx")),
    _Rule(2, re.compile(r"标"), "chart_type", _const(SongType.STANDARD)),
    _Rule(2, re.compile(r"旧"), "newness", _const("old")),
    _Rule(2, re.compile(r"新"), "newness", _const("new")),
    # ---- 层 3：原子 ----
    _Rule(
        3,
        re.compile(_VERSION_RUN),
        "version",
        lambda m: (
            tuple(norm_plate(ch) for ch in m.group().rstrip("代")),
            m.group().endswith("代"),
        ),
    ),
    _Rule(3, re.compile(r"绿|黄|红"), "diff", lambda m: _color_of(m.group())),
    _Rule(3, re.compile(r"将|极|神|者|極|將"), "kind", lambda m: norm_plate(m.group())),
)

_EXTRA_RULES: "tuple[_Rule, ...]" = ()
"""动态注册的额外词法规则（谱师实名，层 1；由 :func:`ensure_designer_rules`
装配，进程级状态）。规则表其余部分为模块常量，谱师词表随曲库动态生成。"""


def set_designer_rules(rules: "tuple[_Rule, ...]") -> None:
    """整体替换谱师词法规则（测试注入与 :func:`ensure_designer_rules` 共用）。"""
    global _EXTRA_RULES
    _EXTRA_RULES = rules


def _designer_rules_of(designers: "set[str]") -> "tuple[_Rule, ...]":
    """谱师实名集 → 层 1 动态规则（归一去重、长度降序 alternation）。

    注册口径：合作谱串（「A×B」形态）按合作符拆分出原子实名一并注册——
    输入「A」或「B」经归一包含式命中整串（包含式同时覆盖「A×B」内的
    任意相邻实名）；归一后 ≥2 字符（单字实名不注册——与难度色/条件原子
    碰撞且误触发面大）。⚠️ 只拆明确的合作符 ×/✕，不拆 ASCII 字母
    （防「box」类英文名误拆）。
    """
    names: "set[str]" = set()
    for designer in designers:
        normalized = normalize_text(designer)
        names.add(normalized)
        for part in re.split(r"[×✕]", normalized):
            if len(part) >= 2:
                names.add(part.strip())
    ordered = sorted((n for n in names if len(n) >= 2), key=len, reverse=True)
    if not ordered:
        return ()
    return (
        _Rule(
            1,
            re.compile("|".join(re.escape(n) for n in ordered)),
            "designer",
            lambda m: m.group(),
        ),
    )


async def ensure_designer_rules() -> None:
    """把曲库谱师实名注册为词法规则（幂等）。

    曲库未就绪时跳过（非阻塞窥探）——冷启动窗口谱师词暂不生效，任一查询
    加载曲库后的下一条消息起生效；不在闲聊路径上触发曲库加载。实名取
    CN/JP 视图并集（两视图谱师名差异极小，并集一次覆盖）。
    """
    if not song_service.is_loaded():
        return
    designers: "set[str]" = set()
    for songs in (await song_service.get_all(), await song_service.jp_all()):
        for song in songs:
            for diff in song.get_difficulties():
                if diff.note_designer:
                    designers.add(diff.note_designer)
    set_designer_rules(_designer_rules_of(designers))


def tokenize(text: str, *, numeric_level: bool = False) -> "list[Token]":
    """分层 FMM 扫描：层号小者优先、同层命中长者赢，未匹配字符跳过。

    ``numeric_level``：中文尾缀语境（完成表/定数表/分数列表）下裸数字
    （13/13+）解析为等级 token（S-8 拍板：仅数字尾缀 50/40 要求「级」必带）。
    """
    numeric_rule = _NUMERIC_LEVEL_RULE if numeric_level else None
    tokens: list[Token] = []
    i = 0
    while i < len(text):
        best: "tuple[int, int, re.Match[str], _Rule] | None" = None
        rules = (
            (*_RULES, *_EXTRA_RULES, numeric_rule)
            if numeric_rule
            else (*_RULES, *_EXTRA_RULES)
        )
        # 裸数字等级只在「中文语境段首」生效：前邻数字/小数点（1350→50、
        # 13.5→5，同一串碎片化）或 ASCII 字母/空格（b50→50、ab13→13、
        # 「I Love 50」→50，英文闲聊碎片）均不成立——数字等级合法形态是
        # 紧贴中文条件词（13fc、辉13）或位于串首
        if numeric_rule and i > 0 and not "\u4e00" <= text[i - 1] <= "\u9fff":
            rules = (*_RULES, *_EXTRA_RULES)
        for rule in rules:
            m = rule.pattern.match(text, i)
            if m is None:
                continue
            # 层号小者优先；同层 m.end() 更大（命中更长）者赢——两维方向
            # 相反，不能合成一个元组比较
            if (
                best is None
                or rule.layer < best[0]
                or (rule.layer == best[0] and m.end() > best[1])
            ):
                best = (rule.layer, m.end(), m, rule)
        if best is None:
            i += 1
            continue
        _, end, m, rule = best
        tokens.append(Token(rule.kind, rule.load(m), m.group(), i))
        i = end
    return tokens


# ---------------------------------------------------------------- 谓词工厂


def _bps(a: "float | None") -> int:
    """达成率 → 万分位整数（所有区间比较的统一口径）。"""
    return round((a or 0) * 10000)


def _version_codes(chars: "tuple[str, ...]") -> frozenset[int]:
    """版本字 → 版本码集：「真」=初+真两码、「舞」=旧作全集（plates 同口径）。"""
    codes: set[int] = set()
    for ch in chars:
        if ch == "真":
            codes |= {Version.MAIMAI.value, Version.MAIMAI_PLUS.value}
        elif ch == "舞":
            codes |= {v.value for v in Version if v < Version.MAIMAI_DX}
        else:
            codes.add(plate_to_version[ch].value)
    return frozenset(codes)


def _version_cond(chars: "tuple[str, ...]", dai: bool) -> Cond:
    codes = _version_codes(chars)
    return Cond(
        CondType.VERSION,
        key="version:" + ",".join(sorted(map(str, codes))),
        label="".join(chars) + ("代" if dai else ""),
        chart=lambda s, d, _cur: d.version in codes,
        value=codes,
    )


def _rate_cond(spec: "str | RateType", *, exact: bool = False) -> Cond:
    """评级档条件（S-14）：≥ 档（默认）或 == 档（纯/仅）。"""
    target = spec if isinstance(spec, RateType) else _RATE_GE[spec]
    label = _RATE_LABEL[target] + ("纯" if exact else "")

    def record(s: ScoreExtend, _t: RateType = target, _eq: bool = exact) -> bool:
        if s.achievements is None:
            return False
        rate = RateType._from_achievement(s.achievements)
        return rate == _t if _eq else rate.value <= _t.value

    return Cond(
        CondType.RATE,
        key=f"rate:{'eq' if exact else 'ge'}:{target.name}",
        label=label,
        record=record,
        value=(target, exact),
    )


def _combo_cond(kind: str) -> Cond:
    """连击族条件（S-12 包含式）：理论值 ⊂ AP 族 ⊂ FCP 族 ⊂ FC 族。"""
    if kind == "fc_all":
        label, key = "FC", "fc_all"

        def record(s: ScoreExtend) -> bool:
            return s.fc is not None

    elif kind == "fcp_all":
        label, key = "FC+", "fcp_all"

        def record(s: ScoreExtend) -> bool:
            return s.fc is not None and s.fc.value <= FCType.FCP.value

    elif kind == "ap_all":
        label, key = "AP", "ap_all"

        def record(s: ScoreExtend) -> bool:
            return s.fc is not None and s.fc.value <= FCType.AP.value

    else:
        label, key = "理论值", "app"

        def record(s: ScoreExtend) -> bool:
            return s.fc == FCType.APP

    return Cond(CondType.COMBO, key=key, label=label, record=record)


def _sync_cond(kind: str) -> Cond:
    """同步族条件（S-13 包含式）：FSType 值序与 FCType 相反（越大越好）。

    ``fs``/``fsp`` 子型为既有 plan 词收编（FS 族 / FSP 族）。
    """
    if kind == "fs_all":
        label, key = "FS", "fs_all"

        def record(s: ScoreExtend) -> bool:
            return s.fs is not None

    elif kind == "fsd":
        label, key = "舞舞", "fsd"

        def record(s: ScoreExtend) -> bool:
            return s.fs is not None and s.fs.value >= FSType.FSD.value

    elif kind == "fsp_all":
        label, key = "FSP", "fsp_all"

        def record(s: ScoreExtend) -> bool:
            return s.fs is not None and s.fs.value >= FSType.FSP.value

    else:
        label, key = "舞舞+", "fsdp"

        def record(s: ScoreExtend) -> bool:
            return s.fs == FSType.FSDP

    return Cond(CondType.SYNC, key=key, label=label, record=record)


def _badge_cond(kind: str) -> Cond:
    """牛逼/越级（S-15，万分位整数比较）。"""
    if kind == "nb":
        label, key = "牛逼", "nb"

        def record(s: ScoreExtend) -> bool:
            return s.achievements is not None and _bps(s.achievements) >= 1008000

    else:
        label, key = "越级", "loser"

        def record(s: ScoreExtend) -> bool:
            return s.achievements is not None and _bps(s.achievements) < 950000

    return Cond(CondType.BADGE, key=key, label=label, record=record)


def _star_cond(n: int) -> Cond:
    """DX 星数（S-16，恰好 N 星；dx_score 缺失的记录不命中任何星档）。"""
    return Cond(
        CondType.STAR,
        key=f"star:{n}",
        label=f"{n}星",
        record=lambda s: s.dx_star == n,
        value=n,
    )


def _cun_distance(bps: int) -> int:
    """寸：距所属里程碑（100.0 / 100.5）的万分位差。"""
    return 1005000 - bps if bps >= 1004500 else 1000000 - bps


def _kill_overshoot(bps: int) -> int:
    """名刀：超出所属里程碑（100.0 / 100.5）的万分位量。"""
    return bps - 1005000 if bps >= 1005000 else bps - 1000000


def _cun_cond() -> Cond:
    """寸（S-17 定稿区间）：[99.9,100) ∪ [100.45,100.5)，按距目标线升序。"""
    return Cond(
        CondType.CUN,
        key="cun",
        label="寸",
        record=lambda s: (
            s.achievements is not None
            and (
                999000 <= _bps(s.achievements) < 1000000
                or 1004500 <= _bps(s.achievements) < 1005000
            )
        ),
        sort_key=lambda s: -_cun_distance(_bps(s.achievements)),
    )


def _kill_cond() -> Cond:
    """名刀（S-18 定稿区间）：[100,100.1) ∪ [100.5,100.55)，按超出量升序。"""
    return Cond(
        CondType.KILL,
        key="kill",
        label="名刀",
        record=lambda s: (
            s.achievements is not None
            and (
                1000000 <= _bps(s.achievements) < 1001000
                or 1005000 <= _bps(s.achievements) < 1005500
            )
        ),
        sort_key=lambda s: -_kill_overshoot(_bps(s.achievements)),
    )


def _ideal_of(s: ScoreExtend) -> ScoreExtend:
    """理想（S-25）：升一档重算 RA（SSSP 封顶=理论值 101/AP+）。

    返回 ``dataclasses.replace`` 副本——NET 成绩是窗口缓存共享对象，
    原地改会污染缓存。
    """
    if s.rate == RateType.SSSP:
        return replace(
            s,
            achievements=101.0,
            fc=FCType.APP,
            dx_rating=compute_rating(s.level_value, 101.0),
        )
    nxt = RateType(s.rate.value - 1)
    ach = _RATE_FLOOR[nxt]
    return replace(
        s, rate=nxt, achievements=ach, dx_rating=compute_rating(s.level_value, ach)
    )


def _ideal_cond() -> Cond:
    return Cond(CondType.IDEAL, key="ideal", label="理想", modifier=_ideal_of)


_OLD_RATE_COEF: "dict[RateType, float]" = {
    RateType.D: 0.0,
    RateType.C: 5.0,
    RateType.B: 6.0,
    RateType.BB: 7.0,
    RateType.BBB: 7.5,
    RateType.A: 8.5,
    RateType.AA: 9.5,
    RateType.AAA: 10.5,
    RateType.S: 12.5,
    RateType.SP: 12.7,
    RateType.SS: 13.0,
    RateType.SSP: 13.2,
    RateType.SSS: 13.5,
    RateType.SSSP: 14.0,
}
"""FiNALE 旧版 RA 系数表（KarenBot ``Rating.kt`` calcOld 同源；SSS+ 14.0 vs
现行 22.4）。b40 唯一动公式处（§3），maimai_py 无此表、本地硬编码。"""


def _old_ra(level_value: float, achievements: "float | None") -> int:
    """旧版单曲 RA：``floor(定数 × 档位系数 × min(100.5, 达成率)万倍 / 1e6)``。

    成绩为现达成率（历史成绩任何实现不可得，§9 S-2 固有限制）。
    """
    coef = _OLD_RATE_COEF[RateType._from_achievement(achievements or 0)]
    bps = min(1005000, _bps(achievements))
    return int(level_value * coef * bps / 1000000)


def _b40_modifier(s: ScoreExtend) -> ScoreExtend:
    """b40 旧系数重算（副本）：dx_rating 替换为 FiNALE 口径 RA。

    默认排序（dx_rating 降序）、头部合计与副行「定数 -> 单曲Ra」随之切换
    为旧口径。恒居 modifier 链尾。
    """
    return replace(s, dx_rating=_old_ra(s.level_value, s.achievements))


async def _era_level_modifier(boundary: int):
    """回到过去时点定数 modifier（§9 S-2 定稿）：定数取版本时点值
    （``State.resolve_chart_level`` carry-forward），RA 按现行系数表重算
    （系数表历史缺失按不变处理——2026-09-30 拍板）。历史无值（早于首变化
    点/无历史表）→ 保持现行值。"""
    state = await State.load()

    def mod(s: ScoreExtend) -> ScoreExtend:
        kind = (
            "utage"
            if s.type == SongType.UTAGE
            else "sd"
            if s.type == SongType.STANDARD
            else "dx"
        )
        hist = state.resolve_chart_level(s.id, kind, s.level_index.value, boundary)
        if hist is None:
            return s
        ra = (
            compute_rating(hist, s.achievements)
            if s.achievements is not None
            else s.dx_rating
        )
        return replace(s, level_value=hist, dx_rating=ra)

    return mod


def _fit_cond() -> Cond:
    """拟合定数（S-24）：modifier 类条件，不过滤、只重算。

    谓词/修改器不在本 Cond 上挂——拟合定数来自曲库 ``curve``，执行器按成绩
    键查 :func:`_build_fit_map` 映射后重算（无 curve 的谱面 fallback 实际
    定数 = 成绩原值不动）。n15 不参与判定（纯拟合 → 拆分，随谱面类条件
    在场时平铺）。
    """
    return Cond(CondType.FIT, key="fit", label="拟合")


def _build_fit_map(
    songs: "list[Song]",
) -> "dict[tuple[int, SongType, LevelIndex], float]":
    """拟合定数映射：谱面键 → 1 位舍入的 ``fit_level_value``（KarenBot 同款
    ``roundDecimalPlaces(1)``，对齐实际定数口径；显示与 RA 计算均用舍入值）。

    无 curve / 拟合值为 0 的谱面不入表——成绩侧查不到即 fallback 实际定数
    （= 原值不动）。JP 视图 curve 缺失时自然恒 fallback。
    """
    fit_map: "dict[tuple[int, SongType, LevelIndex], float]" = {}
    for song in songs:
        for diff in song.get_difficulties():
            if diff.type == SongType.UTAGE:
                continue  # 宴谱成绩本就不入成绩流（默认排除口径）
            curve = diff.curve
            if curve is not None and curve.fit_level_value:
                fit_map[(song.id, diff.type, diff.level_index)] = (
                    round(curve.fit_level_value * 10) / 10
                )
    return fit_map


def _fit_modifier_of(fit_map: "dict[tuple[int, SongType, LevelIndex], float]"):
    """拟合重算器：成绩副本替换 level_value 并按拟合定数重算 RA。

    副行「定数 -> 单曲Ra」随 level_value 替换自动显示拟合值，无需 sub_of。
    """

    def fit_mod(s: ScoreExtend) -> ScoreExtend:
        ds = fit_map.get((s.id, s.type, s.level_index))
        if ds is None or s.achievements is None:
            return s
        return replace(s, level_value=ds, dx_rating=compute_rating(ds, s.achievements))

    return fit_mod


_RATE_FLOOR: "dict[RateType, float]" = {
    RateType.SSSP: 100.5,
    RateType.SSS: 100.0,
    RateType.SSP: 99.5,
    RateType.SS: 99.0,
    RateType.SP: 98.0,
    RateType.S: 97.0,
    RateType.AAA: 94.0,
    RateType.AA: 90.0,
    RateType.A: 80.0,
    RateType.BBB: 75.0,
    RateType.BB: 70.0,
    RateType.B: 60.0,
    RateType.C: 50.0,
    RateType.D: 50.0,
}
"""评级档位下限达成率（对齐 ``RateType._from_achievement`` 阈值；理想升档用）。"""

_RATE_LABEL: "dict[RateType, str]" = {
    RateType.SSSP: "SSS+",
    RateType.SSS: "SSS",
    RateType.SSP: "SS+",
    RateType.SS: "SS",
    RateType.SP: "S+",
    RateType.S: "S",
    RateType.AAA: "AAA",
    RateType.AA: "AA",
    RateType.A: "A",
}

# 牌种字 → 判型条件（S-11 方案 B：牌条件 = 版本 Cond + 判型 Cond 的分解，
# 「紫将50」与 紫+将 自然同一）；「者」（覇者 ≥A）仅牌绑定内部谓词、落单丢弃
_KIND_CONDS: "dict[str, Cond]" = {
    "将": replace(_rate_cond("sss"), label="将"),
    "极": replace(_combo_cond("fc_all"), label="极"),
    "神": replace(_combo_cond("ap_all"), label="神"),
    "者": replace(_rate_cond(RateType.A), label="者"),
}


# ---------------------------------------------------------------- 装配（assembler）
# 全部邻接/上下文规则集中于此（§11.3）；未识别残片忽略（contains 语义）。


def _assemble(tokens: "list[Token]") -> "list[Cond] | ComboAmbiguity | None":
    conds: "list[Cond]" = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        if t.kind == "version":
            chars, dai = t.value
            if len(chars) == 1 and chars[0] in ("紫", "白") and not dai:
                # 裸紫/白（段长 1 且无「代」）：右邻牌种字 → 牌绑定（歧义豁免）；
                # 否则中止。「紫代/白谱」等显式组合在层 1 已消解，不受影响
                if nxt is not None and nxt.kind == "kind" and nxt.value in _KIND_CONDS:
                    conds.append(_version_cond(chars, False))
                    conds.append(_KIND_CONDS[nxt.value])
                    i += 2
                    continue
                return ComboAmbiguity(_AMBIGUITY_HINT.format(ch=chars[0]))
            conds.append(_version_cond(chars, dai))
        elif t.kind == "kind":
            if cond := _KIND_CONDS.get(t.value):
                conds.append(cond)  # 「者」落单丢弃（S-11 内部谓词不暴露）
        elif t.kind == "rate_mod":
            # 纯/仅：绑定紧邻档位词 → 精确档变体；落单残片忽略
            if nxt is not None and nxt.kind == "rate" and nxt.value in _RATE_GE:
                conds.append(_rate_cond(nxt.value, exact=True))
                i += 2
                continue
        elif t.kind == "rate":
            if t.value in _RATE_GE:
                conds.append(_rate_cond(t.value))
        elif t.kind == "combo":
            conds.append(_combo_cond(t.value))
        elif t.kind == "sync":
            conds.append(_sync_cond(t.value))
        elif t.kind == "badge":
            conds.append(_badge_cond(t.value))
        elif t.kind == "star":
            conds.append(_star_cond(t.value))
        elif t.kind == "cun":
            conds.append(_cun_cond())
        elif t.kind == "kill":
            conds.append(_kill_cond())
        elif t.kind == "ideal":
            conds.append(_ideal_cond())
        elif t.kind == "fit":
            conds.append(_fit_cond())
        elif t.kind == "designer":
            # S-10 谱师：命中归一名按「包含式」匹配谱面 note_designer
            # （覆盖合作谱「A×B」形态；短名已在注册侧过滤）
            name: str = t.value
            conds.append(
                Cond(
                    CondType.DESIGNER,
                    key=f"designer:{name}",
                    label=t.text,
                    chart=lambda s, d, _cur, _n=name: (
                        _n in normalize_text(d.note_designer or "")
                    ),
                    value=name,
                    single_chart=True,
                )
            )
        elif t.kind == "diff":
            li: LevelIndex = t.value
            conds.append(
                Cond(
                    CondType.DIFF,
                    key=f"diff:{li.name}",
                    label=t.text,
                    chart=lambda s, d, _cur, _li=li: d.level_index == _li,
                    record=lambda s, _li=li: s.level_index == _li,
                    value=li,
                    single_chart=True,
                )
            )
        elif t.kind == "level":
            conds.append(_level_cond(t.value))
        elif t.kind == "ds":
            conds.append(_ds_cond(t.value))
        elif t.kind == "genre":
            g: Genre = t.value
            conds.append(
                Cond(
                    CondType.GENRE,
                    key=f"genre:{g.name}",
                    label=t.text,
                    chart=lambda s, d, _cur, _g=g: s.genre == _g,
                    value=g,
                )
            )
        elif t.kind == "genre_sub":
            which: str = t.value
            conds.append(
                Cond(
                    CondType.GENRE_SUB,
                    key=f"genre_sub:{which}",
                    label="音击" if which == "ongeki" else "中二",
                    chart=lambda s, d, _cur, _w=which: _genre_sub_match(s, _w),
                    value=which,
                )
            )
        elif t.kind == "era_year":
            year_e: int = t.value
            if year_e in _YEAR_TO_CODE:
                conds.append(_era_year_cond(year_e, t.text))
            # 未收录年份（2000–2018 / 2027+）：残片忽略
        elif t.kind == "era":
            which: str = t.value
            conds.append(
                Cond(
                    CondType.ERA,
                    key=f"era:{which}",
                    label="dx" if which == "dx" else "旧框",
                    chart=(
                        (lambda s, d, _cur: d.version > 19900)
                        if which == "dx"
                        else (lambda s, d, _cur: d.version <= 19900)
                    ),
                    value=which,
                )
            )
        elif t.kind == "chart_type":
            st: SongType = t.value
            conds.append(
                Cond(
                    CondType.CHART_TYPE,
                    key=f"ctype:{st.name}",
                    label="dx谱" if st == SongType.DX else "标准",
                    chart=lambda s, d, _cur, _st=st: d.type == _st,
                    value=st,
                )
            )
        elif t.kind == "newness":
            which_n: str = t.value
            conds.append(
                Cond(
                    CondType.NEWNESS,
                    key=f"new:{which_n}",
                    label="新版本" if which_n == "new" else "旧版本",
                    chart=lambda s, d, cur, _w=which_n: (
                        d.version == cur if _w == "new" else d.version != cur
                    ),
                    value=which_n,
                )
            )
        elif t.kind == "utage":
            conds.append(
                Cond(
                    CondType.UTAGE,
                    key="utage",
                    label="宴谱",
                    chart=lambda s, d, _cur: d.type == SongType.UTAGE,
                )
            )
        i += 1
    # 同型同键去重（§9.0：同一 Cond 不重复计入），保持解析顺序
    seen: set[tuple[CondType, str]] = set()
    deduped: "list[Cond]" = []
    for c in conds:
        if (k := (c.ctype, c.key)) not in seen:
            seen.add(k)
            deduped.append(c)
    return deduped or None


_YEAR_TO_CODE: "dict[int, int]" = {
    2019: Version.MAIMAI_DX.value,
    2020: Version.MAIMAI_DX_PLUS.value,
    2021: Version.MAIMAI_DX_SPLASH.value,
    2022: Version.MAIMAI_DX_UNIVERSE.value,
    2023: Version.MAIMAI_DX_FESTIVAL.value,
    2024: Version.MAIMAI_DX_BUDDIES.value,
    2025: Version.MAIMAI_DX_PRISM.value,
    2026: Version.MAIMAI_DX_CIRCLE.value,
}
"""回到过去 年份 → 代基码（Version 枚举直查；PLUS 尾码不分，对齐笔记 §9 S-2
与 KarenBot nowVersion）。未收录年份（2000–2018 / 2027+）的 token 丢弃。"""


def _era_year_cond(year: int, label: str) -> Cond:
    """回到过去条件（S-2）：版本 ≤ 年份码 + boundary 覆写（value=码）。

    定数时点值与分界覆写在执行器侧由 :func:`_era_level_modifier` 与
    ``build_bests(latest_version_value=码)`` 承接。
    """
    code = _YEAR_TO_CODE[year]
    return Cond(
        CondType.ERA_YEAR,
        key=f"era_year:{code}",
        label=label,
        chart=lambda s, d, _cur, _c=code: d.version is not None and d.version <= _c,
        value=code,
    )


def _level_cond(level: str) -> Cond:
    """等级精确条件（S-8）。"""
    return Cond(
        CondType.LEVEL,
        key=f"level:{level}",
        label=f"{level}级",
        chart=lambda s, d, _cur, _lv=level: d.level == _lv,
        value=level,
        single_chart=True,
    )


def _ds_cond(v: float) -> Cond:
    """定数精确条件（S-9，一位小数整数比较防浮点尾差）。"""
    return Cond(
        CondType.DS,
        key=f"ds:{round(v * 10)}",
        label=f"{v:g}定数",
        chart=lambda s, d, _cur, _v=v: round(d.level_value * 10) == round(_v * 10),
        value=v,
        single_chart=True,
    )


def parse_combo(
    text: str, *, numeric_level: bool = False
) -> "list[Cond] | ComboAmbiguity | None":
    """条件串 → 条件表 / 歧义中止 / None（零条件，静默不回话）。

    ``numeric_level``：裸数字（含 13+ / 14.5 形态）是否解析为等级/定数条件
    ——S-8 拍板口径「『级/定数』必带**仅限数字尾缀 50/40**」，中文尾缀
    （分数列表等）排除纯数字闲聊、裸数字即可。默认 False（b50 语境保持
    「1350/650 静默」防护）。
    """
    text = text.strip()
    if not text:
        return None
    if _PURE_NUMBER.fullmatch(text) or (
        numeric_level and _PURE_NUMBER_LEVEL.fullmatch(text)
    ):
        if not numeric_level:
            return None
        if "." in text:
            # 「13.5」定数串（剥 +；一位小数域由 _ds_cond 语义保证）
            return [_ds_cond(float(text.rstrip("+")))]
        year = int(text.rstrip("+"))
        if year in _YEAR_TO_CODE:
            # 「2024进度」裸年份 = 回到过去（等级域 ≤15，4 位数无歧义）
            return [_era_year_cond(year, text)]
        if year > 15:
            return None  # 「1350」非等级域（1-15），静默防闲聊误触发
        return [_level_cond(text)]  # 「13+」的 + 是等级语义，原样保留
    return _assemble(tokenize(text, numeric_level=numeric_level))


# ---------------------------------------------------------------- 执行器


def _empty_message(conds: "list[Cond]", cur: int) -> str:
    """谱面集空文案：可静态判定的版本∩世代/新旧矛盾附点破提示（§9.4）。"""
    ver = next((c for c in conds if c.ctype is CondType.VERSION), None)
    if ver is None:
        return "没有符合条件的谱面"
    codes: "frozenset[int]" = ver.value
    dx_bound = Version.MAIMAI_DX.value
    all_old = all(code < dx_bound for code in codes)
    all_new = all(code >= dx_bound for code in codes)
    era = next((c for c in conds if c.ctype is CondType.ERA), None)
    if era is not None:
        if era.value == "dx" and all_old:
            return f"没有符合条件的谱面（{ver.label} 为旧作版本，与 dx 世代无交集）"
        if era.value == "old" and all_new:
            return f"没有符合条件的谱面（{ver.label} 为 DX 世代版本，与旧框无交集）"
    new = next((c for c in conds if c.ctype is CondType.NEWNESS), None)
    if new is not None:
        if new.value == "new" and all(code < cur for code in codes):
            return f"没有符合条件的谱面（{ver.label} 非当前版本，与「新版本」无交集）"
        if new.value == "old" and all(code >= cur for code in codes):
            return f"没有符合条件的谱面（{ver.label} 即当前版本，与「旧版本」无交集）"
    return "没有符合条件的谱面"


def _apply_modifiers(
    s: ScoreExtend, mods: "list[Callable[[ScoreExtend], ScoreExtend]]"
) -> ScoreExtend:
    """按声明序链式应用成绩变换（全部经 ``replace`` 副本，缓存防污染）。"""
    for mod in mods:
        s = mod(s)
    return s


def _chart_key(song: Song, diff: SongDifficulty) -> "tuple[int, SongType, LevelIndex]":
    """谱面键（与成绩 id 口径对齐）：SD/DX = 归一根 id；宴谱 = diff_id
    （水鱼 _deser_score 对 >100000 的 song_id 原样保留、本模块 NET 抓取不含
    宴谱），与完成表 ``chart_display_id`` 同语义。"""
    if diff.type == SongType.UTAGE:
        return (diff.diff_id, diff.type, diff.level_index)
    return (song.id, diff.type, diff.level_index)


async def _songs_of(binding) -> "list[Song]":
    """绑定源视图曲库（``binding=None`` = CN 视图；定数表等纯曲库输出用）。"""
    jp = binding is not None and score_service.view_of(binding.service) == "jp"
    return await (song_service.jp_all() if jp else song_service.get_all())


def _current_of(binding) -> int:
    """绑定源视图的现行版本码（CN 25500 / JP 27000 分界）。"""
    jp = binding is not None and score_service.view_of(binding.service) == "jp"
    return current_version_jp.value if jp else current_version.value


def _group_by_type(conds: "list[Cond]", attr: str) -> "dict[CondType, list[Cond]]":
    """条件按 CondType 分组（§9.0：同型 OR、跨型 AND——分组语义在执行器
    统一实现，本函数即其单源）。"""
    groups: "dict[CondType, list[Cond]]" = {}
    for c in conds:
        if getattr(c, attr) is not None:
            groups.setdefault(c.ctype, []).append(c)
    return groups


async def _build_chart_hit(conds: "list[Cond]", cur: int):
    """谱面判定闭包（同型 OR/跨型 AND + 宴谱口径 + 回到过去时点定数）。

    - 返回 None = 条件集无谱面类条件（全库通过）；
    - DS 条件在回到过去在场时对比**历史定数**（``State.resolve_chart_level``
      carry-forward；早于首变化点视为未实装 → 不命中，§9 S-2）。
    """
    groups = _group_by_type(conds, "chart")
    if not groups:
        return None
    has_utage = any(c.ctype is CondType.UTAGE for c in conds)
    era = next((c for c in conds if c.ctype is CondType.ERA_YEAR), None)
    hist_state = (
        await State.load() if era is not None and CondType.DS in groups else None
    )

    def chart_hit(song: Song, diff: SongDifficulty) -> bool:
        if (diff.type == SongType.UTAGE) != has_utage:
            return False
        for ctype, group in groups.items():
            if ctype is CondType.DS and hist_state is not None:
                kind = (
                    "utage"
                    if diff.type == SongType.UTAGE
                    else "sd"
                    if diff.type == SongType.STANDARD
                    else "dx"
                )
                hist = hist_state.resolve_chart_level(
                    song.id, kind, diff.level_index.value, era.value
                )
                if hist is None or not any(
                    round(hist * 10) == round(c.value * 10) for c in group
                ):
                    return False
            elif not any(c.chart(song, diff, cur) for c in group):
                return False
        return True

    return chart_hit


def _representative_of(
    per_song: "list[tuple[Song, list[SongDifficulty]]]",
) -> "list[tuple[Song, SongDifficulty]]":
    """§5 选谱启发式（每曲代表谱面）：一般条件下每曲只取最高难度，
    有 Re:MASTER 谱 → MASTER + Re:MASTER 两张。"""
    out: "list[tuple[Song, SongDifficulty]]" = []
    for song, diffs in per_song:
        if not diffs:
            continue
        remasters = [d for d in diffs if d.level_index == LevelIndex.ReMASTER]
        if remasters:
            masters = [d for d in diffs if d.level_index == LevelIndex.MASTER]
            if masters:
                out.append((song, max(masters, key=lambda d: d.level_value)))
            out.append((song, max(remasters, key=lambda d: d.level_value)))
        else:
            # 最高难度 = 难度色最高、同色取定数高者（199 类 SD/DX 同色双谱
            # 收定数高的那张）
            out.append(
                (song, max(diffs, key=lambda d: (d.level_index.value, d.level_value)))
            )
    return out


async def combo_chart_entries(
    conds: "list[Cond]", binding=None
) -> "list[tuple[Song, SongDifficulty]] | ComboEmpty":
    """条件 → 谱面集（§5 选谱启发式）：进度/完成表/定数表消费。

    - 谱面级精确条件（难度/等级/定数/谱师，``single_chart``）在场 → 命中
      谱面全部保留（完成表不收缩难度，§9.0）；
    - 否则每曲只取代表谱面（:func:`_representative_of`）；
    - 收缩后仍 >400 → 只留 MASTER；>200 → 只留定数 ≥14.0（KarenBot §5
      图长启发式）；
    - 谱面集空 → :class:`ComboEmpty`（可证明矛盾附点破提示）。
    """
    songs = await _songs_of(binding)
    cur = _current_of(binding)
    await ensure_ongeki_titles()  # 中二/音击谓词的集合前置加载（幂等）
    chart_hit = await _build_chart_hit(conds, cur)
    if any(c.single_chart for c in conds if c.chart is not None):
        entries = [
            (song, diff)
            for song in songs
            for diff in song.get_difficulties()
            if chart_hit is not None and chart_hit(song, diff)
        ]
    else:
        per_song = [
            (
                song,
                [
                    d
                    for d in song.get_difficulties()
                    if chart_hit is None or chart_hit(song, d)
                ],
            )
            for song in songs
        ]
        entries = _representative_of(per_song)
    if not entries:
        return ComboEmpty(_empty_message(conds, cur))
    if len(entries) > 400:
        entries = [e for e in entries if e[1].level_index == LevelIndex.MASTER]
    if len(entries) > 200:
        entries = [e for e in entries if e[1].level_value >= 14.0]
    return entries


async def combo_filtered_scores(
    conds: "list[Cond]",
    binding,
    notify_slow=None,
) -> "list[ScoreExtend] | ComboEmpty":
    """条件 → 成绩集（分数列表/b50 组装消费；键集全量过滤、无选谱收缩）。

    谱面类条件在绑定源视图曲库上产出谱面键集 ``(id, type, level_index)``
    （各源成绩 id 均为归一根 id、宴谱为 diff_id，与完成表同口径）；
    纯成绩类条件不过滤谱面。成绩集空 ≠ 错误——分数列表自行出空态文案，
    b50 组装路径照常渲染空槽卡（§9.4）。
    """
    songs = await _songs_of(binding)
    cur = _current_of(binding)
    await ensure_ongeki_titles()  # 中二/音击谓词的集合前置加载（幂等）
    chart_hit = await _build_chart_hit(conds, cur)

    keys: "set[tuple[int, SongType, LevelIndex]] | None" = None
    if chart_hit is not None:
        keys = {
            _chart_key(song, diff)
            for song in songs
            for diff in song.get_difficulties()
            if chart_hit(song, diff)
        }
        if not keys:
            return ComboEmpty(_empty_message(conds, cur))

    scores = (await score_service.get_scores_all(binding, notify_slow)).scores
    if keys is not None:
        scores = [s for s in scores if (s.id, s.type, s.level_index) in keys]
    else:
        scores = list(scores)
    scores = [
        s
        for s in scores
        if (s.type == SongType.UTAGE) == any(c.ctype is CondType.UTAGE for c in conds)
    ]
    record_groups = _group_by_type(conds, "record")
    return [
        s
        for s in scores
        if all(any(c.record(s) for c in group) for group in record_groups.values())
    ]


async def run_combo(
    conds: "list[Cond]",
    binding,
    *,
    output: OutputKind = OutputKind.B50,
    notify_slow: "Callable[[], Any] | None" = None,
) -> "ComboResult | ComboEmpty":
    """执行条件组合（§8.3）：成绩过滤（:func:`combo_filtered_scores`）→
    排序/修改 → 组装。谱面集空 → :class:`ComboEmpty`；成绩集空 → 照常返回
    空组装（渲染全空槽卡，对齐两上游）。

    ``output``：b50（35/15 现行系数，n15 按条件集构成）或 b40（§3 旧口径：
    FiNALE 系数重算 + 恒拆分 25/15，可与回到过去叠加：dx2022b40）。
    """
    filtered = await combo_filtered_scores(conds, binding, notify_slow)
    if isinstance(filtered, ComboEmpty):
        return filtered
    scores = filtered
    cur = _current_of(binding)
    era = next((c for c in conds if c.ctype is CondType.ERA_YEAR), None)

    # 排序覆盖：取最后声明者（§9.0）；modifier：声明序链式应用。链序定案：
    # 时点定数居首（历史值先落位），理想/拟合随声明序，b40 旧系数恒居链尾
    # （对最终 (达成率, 定数) 做旧口径重算）
    sort_key = next(
        (c.sort_key for c in reversed(conds) if c.sort_key is not None),
        lambda s: s.dx_rating or 0,
    )
    modifiers = [c.modifier for c in conds if c.modifier is not None]
    if era is not None:
        modifiers.insert(0, await _era_level_modifier(era.value))
    if any(c.ctype is CondType.FIT for c in conds):
        modifiers.append(_fit_modifier_of(_build_fit_map(await _songs_of(binding))))
    if output is OutputKind.B40:
        modifiers.append(_b40_modifier)
    if modifiers:
        scores = [_apply_modifiers(s, modifiers) for s in scores]

    # n15 判定（§8 + 特则）：b40 恒拆分（25/15 旧口径）；回到过去恒拆分
    # （查询目的即分界结构，覆盖其谱面类性质）；其余按条件集构成——含谱面类
    # → 平铺，纯成绩类 → 拆分
    if output is OutputKind.B40 or era is not None:
        flat = False
    else:
        flat = any(c.ctype in CHART_COND_TYPES for c in conds)
    if flat:
        bests = build_flat_bests(scores, key=sort_key)
    else:
        bests = build_bests(
            scores,
            key=sort_key,
            latest_version_value=era.value if era is not None else cur,
            old_cap=25 if output is OutputKind.B40 else 35,
        )
    return ComboResult(
        title="·".join(c.label for c in conds),
        bests=bests,
        flat=flat,
        total_ra=bests.rating,
        scores=bests.scores_b35 + bests.scores_b15,
    )
