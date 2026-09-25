"""游戏常量：分类中文映射、等级颜色、版本显示名等。

maimai-py 的 `Genre` 枚举值是日文分类，库未公开导出中文映射，
本模块自带「中文/日文 → Genre」双向映射（对齐原版 maimaiDX 的 CATEGORY 语义）。
"""

import re

from maimai_py import (
    Genre,
    Version,
    RateType,
    SongType,
    LevelIndex,
    current_version,
    plate_to_version,
)
from maimai_py.enums import (
    name_to_genre as _LIB_NAME_TO_GENRE,
)
from maimai_py.enums import (
    divingfish_to_version as _DF_TO_VERSION,
)
from maimai_py.utils.coefficient import SCORE_COEFFICIENT_TABLE

# ---------------------------------------------------------------------------
# 分类（Genre）：枚举 → 中文显示名
# ---------------------------------------------------------------------------
GENRE_TO_ZH: dict[Genre, str] = {
    Genre.POPSアニメ: "流行&动漫",
    Genre.niconicoボーカロイド: "niconico & VOCALOID",
    Genre.東方Project: "东方Project",
    Genre.ゲームバラエティ: "其他游戏",
    Genre.maimai: "舞萌",
    Genre.オンゲキCHUNITHM: "音击&中二节奏",
    Genre.宴会場: "宴会场",
}

# 中文（及日文原名）→ 枚举，用于「随个流行」等分类过滤。
# 官方名（中/日）直接取库内 name_to_genre，本地只补常见别名入口
ZH_TO_GENRE: dict[str, Genre] = dict(_LIB_NAME_TO_GENRE) | {
    "流行": Genre.POPSアニメ,
    "动漫": Genre.POPSアニメ,
    "niconico": Genre.niconicoボーカロイド,
    "vocaloid": Genre.niconicoボーカロイド,
    "东方": Genre.東方Project,
    "游戏": Genre.ゲームバラエティ,
    "音击": Genre.オンゲキCHUNITHM,
    "宴会": Genre.宴会場,
    "宴": Genre.宴会場,
}

# ---------------------------------------------------------------------------
# 难度（LevelIndex）元表：中文名/颜色字/英文小写/显示名 四表同轴（枚举序）合一
# ---------------------------------------------------------------------------
_LEVEL_INDEX_META: dict[LevelIndex, tuple[str, str, str, str]] = {
    LevelIndex.BASIC: ("基础", "绿", "basic", "Basic"),
    LevelIndex.ADVANCED: ("高级", "黄", "advanced", "Advanced"),
    LevelIndex.EXPERT: ("专家", "红", "expert", "Expert"),
    LevelIndex.MASTER: ("大师", "紫", "master", "Master"),
    LevelIndex.ReMASTER: ("宗师", "白", "remaster", "Re:Master"),
}
LEVEL_INDEX_ZH: dict[LevelIndex, str] = {k: v[0] for k, v in _LEVEL_INDEX_META.items()}
COLOR_TO_LEVEL_INDEX: dict[str, LevelIndex] = {
    v[1]: k for k, v in _LEVEL_INDEX_META.items()
}
# 英文小写名 = 素材名后缀（border_*/b50_score_*/rise_score_* 等），渲染模块共用
LEVEL_INDEX_EN: tuple[str, ...] = tuple(v[2] for v in _LEVEL_INDEX_META.values())
# 显示名 = 统计卡/进度总览表头
DIFF_DISPLAY_NAMES: tuple[str, ...] = tuple(v[3] for v in _LEVEL_INDEX_META.values())

# 达成率评级（RateType）→ 显示名，值越小评级越高
RATE_TO_ZH: dict[RateType, str] = {
    RateType.SSSP: "SSS+",
    RateType.SSS: "SSS",
    RateType.SSP: "SS+",
    RateType.SS: "SS",
    RateType.SP: "S+",
    RateType.S: "S",
    RateType.AAA: "AAA",
    RateType.AA: "AA",
    RateType.A: "A",
    RateType.BBB: "BBB",
    RateType.BB: "BB",
    RateType.B: "B",
    RateType.C: "C",
    RateType.D: "D",
}

# ---------------------------------------------------------------------------
# 版本名（DX 世代官方尾名为单一事实源：CN 显示名 / 日服 logo 文件名 / 数据源
# 版本名均由此派生；旧框官方系列名复用库表 divingfish_to_version）
# ---------------------------------------------------------------------------

# DX 世代枚举成员 → 官方尾名（CN 显示口径）。品牌名「舞萌DX」本身即初代版本名。
# 尾名取官方 logo 主体词（如 MAGiCAL 全称 maimai DX MAGiCAL，显示名与其他世代
# 同带前缀）。FUTURE 为占位枚举、无实际版本，不入本表、不设显示名。
# 素材包 pic/jp/ 的 logo 文件名与本表一致。
_DX_VERSION_NAMES: dict[Version, str] = {
    Version.MAIMAI_DX: "DX",
    Version.MAIMAI_DX_PLUS: "PLUS",
    Version.MAIMAI_DX_SPLASH: "SPLASH",
    Version.MAIMAI_DX_SPLASH_PLUS: "SPLASH PLUS",
    Version.MAIMAI_DX_UNIVERSE: "UNiVERSE",
    Version.MAIMAI_DX_UNIVERSE_PLUS: "UNiVERSE PLUS",
    Version.MAIMAI_DX_FESTIVAL: "FESTiVAL",
    Version.MAIMAI_DX_FESTIVAL_PLUS: "FESTiVAL PLUS",
    Version.MAIMAI_DX_BUDDIES: "BUDDiES",
    Version.MAIMAI_DX_BUDDIES_PLUS: "BUDDiES PLUS",
    Version.MAIMAI_DX_PRISM: "PRiSM",
    Version.MAIMAI_DX_PRISM_PLUS: "PRiSM PLUS",
    Version.MAIMAI_DX_CIRCLE: "CiRCLE",
    Version.MAIMAI_DX_CIRCLE_PLUS: "CiRCLE PLUS",
    Version.MAIMAI_DX_MAGICAL: "MAGiCAL",
}

# 枚举 → 中文显示名（查歌结果/猜歌/版本过滤用）。旧框官方系列名 = 库表键；
# DX 世代按「舞萌DX + 尾名」全量派生（MAGiCAL 同样带前缀，官方 logo 全称
# maimai DX MAGiCAL）。唯一特例：MiLK PLUS（库表键缺 maimai 前缀），显式覆盖。
# FUTURE 为占位枚举、无实际版本，不设显示名（version_zh 回落原码）
VERSION_TO_ZH: dict[Version, str] = {
    **{ver: name for name, ver in _DF_TO_VERSION.items() if ver < Version.MAIMAI_DX},
    **{
        ver: ("舞萌DX" if name == "DX" else f"舞萌DX {name}")
        for ver, name in _DX_VERSION_NAMES.items()
    },
    Version.MAIMAI_MILK_PLUS: "maimai MiLK PLUS",
}

# 牌种达成条件说明（牌子条件 指令用）
PLATE_KIND_ZH: dict[str, str] = {
    "者": "达成率 ≥ A（80%）",
    "将": "达成率 ≥ SSS（97%）",
    "极": "全曲目 Full Combo",
    "神": "全曲目 All Perfect",
    "舞舞": "全曲目 Full Sync DX（FSD）",
}

# 完成表预渲染枚举（SUPERUSER 指令与国服更新自动触发共用，song-db-design §7.3）。
# 牌子需求只算国服：舞/霸为旧作全集特牌（库表无映射，置首），其余牌字由
# maimai_py ``plate_to_version`` 键序（=发售序）按「版本 ≤ 国服当前版本
# ``current_version``」派生——库推进 current_version 后新代牌字自动纳入，
# 未实装代（如 CiRCLE 的丸/回）不进；「初」不在牌单（M4 定表以来即无，国服牌单自真起）。
PLATE_CHARS = "舞霸" + "".join(
    ch for ch, ver in plate_to_version.items() if ver <= current_version and ch != "初"
)
PLATE_KINDS = ("将", "者", "极", "神", "舞舞")

# ---------------------------------------------------------------------------
# 版本名 → Version（歌曲库日侧骨架用；只收录库表 divingfish_to_version 之外的
# 名字，重叠条目由 core/songdb._source_version 兜底查库表）
# ---------------------------------------------------------------------------

# 数据源两域命名 = 「前缀 + 日式尾名」（穷举自 dschange.json / all_data `from`
# 实测；初代无尾名），与官方尾名仅 Splash 一词大小写不同。MAGiCAL 段为按
# 命名规律的预收（数据源尚未出现）；FUTURE 为占位枚举，不生成任何名字。
_JP_SUFFIX: dict[Version, str] = {
    ver: name.replace("SPLASH", "Splash") for ver, name in _DX_VERSION_NAMES.items()
}
SOURCE_NAME_TO_VERSION: dict[str, Version] = {
    **{
        name: ver
        for ver, suffix in _JP_SUFFIX.items()
        for prefix in ("maimai DX", "maimai でらっくす")
        if (name := f"{prefix} {suffix}" if suffix != "DX" else prefix)
        not in _DF_TO_VERSION
    },
    # 旧框 MiLK PLUS 库表键缺 maimai 前缀，以全名补收
    "maimai MiLK PLUS": Version.MAIMAI_MILK_PLUS,
}

# DX 时代版本轴 = Version 枚举 MAIMAI_DX..MAIMAI_DX_CIRCLE_PLUS 的值切片：
# 01 文档标准 JSON 的 sd/dx `level` 扁平列表即按此轴逐版本对齐（导出固定 14 列，
# 「不包含旧框版本的定数，均从 dx 初代版本开始统计」；MAGiCAL/FUTURE 不在轴上，
# 导入侧由 songdb._points_from_flat 以枚举已知码 +500 递推扩展）
DX_VERSION_CODES: list[int] = [
    v.value for v in Version if Version.MAIMAI_DX <= v <= Version.MAIMAI_DX_CIRCLE_PLUS
]


# 谱面类型前缀 → 卡片主类型（键与 strip_chart_prefix 的静态前缀同源；
# 宴/汉字前缀无普通谱偏好，由调用方单独分支；命中词大小写归一后查表）
CHART_TYPE_BY_PREFIX: dict[str, SongType] = {
    "dx": SongType.DX,
    "标准": SongType.STANDARD,
    "标": SongType.STANDARD,
}

# 渲染主题（素材包 pic/<theme>/ 子目录名；bind 指令 0/1 映射）
THEMES = ("prism_plus", "circle")
DEFAULT_THEME = "prism_plus"

# 谱面类型别名前缀：社区对同根id双谱（标准/DX/宴）的惯用区分写法
# （dx圣诞、标准39、标星光、旧谱、宴Oshama…）。用于查询侧剥离兜底，
# 最长优先匹配；数据侧别名保留原样（含前缀）不去除
# 谱面类型别名前缀（作者口径，穷举）：dx / 标准 / 标 / 宴 / {宴谱汉字}。
# 汉字前缀随曲而异（撫/協/蔵…），由调用方从规范表 song_chart.kanji / 运行时
# 宴谱对象取该曲的汉字后经 extra_prefixes 传入；匹配经 normalize_text 归一，
# 简体输入（抚/协/藏）同样命中
_CHART_TYPE = r"dx|标准|标|宴"
_CHART_PREFIX_RE = re.compile(rf"^({_CHART_TYPE})[\s·・.。:：_-]*", re.IGNORECASE)
# [汉字] 括号前缀（柚子别名库实测 67 条：[協]love you / [宴]cycles / [蔵]in chaos）；
# 静态字的括号形式（[dx]）实测不存在，不支持。半/全角括号均收
_CHART_BRACKET_RE = re.compile(r"^[\[［]\s*([^\]］\s])\s*[\]］]")
# 后缀（柚子库 2026-09-23 实测：dx 8 条 / 标准 1 条；「标」与汉字后缀均不存在——
# 汉字词尾（土星/宵崎奏/夜宴…）是词语本身，绝不可当谱面后缀剥）。
# dx 前置 ASCII 字母时不剥（iidx 等英文词保护）
_CHART_SUFFIX_RE = re.compile(r"[\s·・.。:：_-]*(标准|(?<![a-zA-Z])dx)$", re.IGNORECASE)

# zhconv 未覆盖的和制汉字补充映射（2026-09-22 国服宴谱 kanji 全量实测：
# 蔵/発/両/覚 归一后不变，用户输入 藏/发/两/觉 无法命中）
_T2S_SUPPLEMENT = str.maketrans({"蔵": "藏", "発": "发", "両": "两", "覚": "觉"})

_t2s_cache: dict[str, str] = {}


def normalize_text(text: str) -> str:
    """别名匹配归一：小写 + NFKC（全角→半角）+ 简体化（和制汉字简体输入兼容）。"""
    import unicodedata

    from zhconv import convert

    lowered = unicodedata.normalize("NFKC", text).lower()
    if lowered not in _t2s_cache:
        _t2s_cache[lowered] = convert(lowered, "zh-cn").translate(_T2S_SUPPLEMENT)
    return _t2s_cache[lowered]


def strip_chart_prefix(
    alias: str, extra_prefixes: "set[str] | frozenset[str] | None" = None
) -> "tuple[str, str, str] | None":
    """剥离别名**一层**谱面类型前缀/后缀，返回 (剥离后别名, 命中词, 命中位置)。

    入库（合并层去前后缀）与查询（未命中兜底重查）共用本规则。

    - 静态前缀：dx / 标准 / 标 / 宴（作者口径穷举；sd/旧 等不会出现）；
    - 汉字前缀：该曲宴谱的汉字（单字），裸写与 [汉字] 括号形式均可（柚子别名库
      实测存在 [協]love you / [宴]cycles 形态），按归一化比对（简体输入兼容）；
    - 后缀：dx / 标准（2026-09 柚子库实测存在，「标」与汉字后缀不存在——词尾字
      如 土星/宵崎奏/夜宴 是词语本身，绝不可当谱面后缀剥）；dx 前置 ASCII 字母
      时不剥（iidx 等英文别名保护，库内形态原样保留）；
    - 只剥一层：叠层前缀（「dx标39」）剥完的「标39」不在去前缀别名库中，
      自然不命中（作者口径）；前缀命中即返回、不再剥后缀；
      无可剥（或剥完为空）返回 None。
    """
    text = alias.strip()
    match = _CHART_PREFIX_RE.match(text)
    if match:
        stripped = text[match.end() :].strip()
        return (stripped, match.group(1), "prefix") if stripped else None
    extra = extra_prefixes or set()
    normalized_extra = {normalize_text(p): p for p in extra if p}
    bracket = _CHART_BRACKET_RE.match(text)
    if bracket:
        key = normalize_text(bracket.group(1))
        if key in normalized_extra or key == normalize_text("宴"):
            stripped = text[bracket.end() :].strip()
            hit = normalized_extra.get(key, "宴")
            return (stripped, hit, "prefix") if stripped else None
    first = normalize_text(text[:1])
    if text and first in normalized_extra:
        stripped = text[1:].strip()
        return (stripped, normalized_extra[first], "prefix") if stripped else None
    match = _CHART_SUFFIX_RE.search(text)
    if match:
        stripped = text[: match.start()].strip()
        return (stripped, match.group(1), "suffix") if stripped else None
    return None


def level_from_value(level_value: float) -> str:
    """定数 → 标级串（otoge-db 全量验证：x.0–x.5 → 无+，x.6–x.9 → +，0 冲突）。"""
    base = int(level_value)
    return f"{base}+" if round(level_value * 10) % 10 >= 6 else f"{base}"


# ---------------------------------------------------------------------------
# NB 版绘图移植用的映射表（core/render/nb_chart.py、best50.py 使用）
# ---------------------------------------------------------------------------

# 达成率系数表阈值（升序）：自 maimai_py SCORE_COEFFICIENT_TABLE 推导，
# 剔除 <50 细分段与 79.9999 等区间哨兵行（round 抹平浮点尾差）
ACHIEVEMENT_LIST = [
    t
    for t in (row[0] for row in SCORE_COEFFICIENT_TABLE)
    if t >= 50 and round(t * 10000) % 10 != 9
]

# 全部谱面等级（lv1-15，定数表底图生成用）
LEVEL_LIST = [
    "1",
    "2",
    "3",
    "4",
    "5",
    "6",
    "7",
    "7+",
    "8",
    "8+",
    "9",
    "9+",
    "10",
    "10+",
    "11",
    "11+",
    "12",
    "12+",
    "13",
    "13+",
    "14",
    "14+",
    "15",
]

# FCType/FSType 枚举名小写 → UI_MSS_MBase_Icon_*.png 文件名后缀
# （键统一用 ``fc.name.lower()`` 口径，全部渲染模块共用这一份）
COMBO_FILE = {"fc": "FC", "fcp": "FCp", "ap": "AP", "app": "APp"}
SYNC_FILE = {"sync": "Sync", "fs": "FS", "fsp": "FSp", "fsd": "FSD", "fsdp": "FSDp"}

# 数据源 → 署名显示名（查分器站点品牌名，各卡面共用）
SERVICE_DISPLAY = {"divingfish": "Diving-Fish", "lxns": "Lxns-Network"}

# RateType 枚举名 → UI_TTR_Rank_*.png 文件名后缀（由 RATE_TO_ZH 派生：
# 素材名把显示名的 + 写作小写 p，如 "SS+" → "SSp"）
RATE_FILE = {m.name: zh.replace("+", "p") for m, zh in RATE_TO_ZH.items()}

# Version 枚举 → 日服 logo 文件名（static/mai/pic/jp/，文件名 = _DX_VERSION_NAMES
# 官方尾名）。旧框（<MAIMAI_DX）中日 logo 相同，不在本表、走 VERSION_IMAGE 通用路径。
JP_VERSION_IMAGE: dict[Version, str] = dict(_DX_VERSION_NAMES)

# Version 枚举 → 版本图文件名（pic/ 下，键与 maimai-py divingfish_to_version 一致）
VERSION_IMAGE = {v: k for k, v in _DF_TO_VERSION.items()}


def version_zh(version: int) -> str:
    """曲谱版本整数 → 中文显示名（未知值原样返回）。"""
    ver = Version.from_value(version)
    return VERSION_TO_ZH.get(ver, str(version)) if ver is not None else str(version)


def display_song_id(song) -> int:
    """展示用曲目 id：DX 专用曲官方 id = 根 id + 10000（机台内部 id 规则），
    其余（含 SD 谱面/兼容谱曲）即根 id。"""
    if song.difficulties.standard:
        return song.id
    if song.difficulties.dx:
        return song.id + 10000
    return song.id


def chart_display_id(song, diff) -> int:
    """谱面级展示 id（查分器 id 形状，NB per-type 条目语义）。

    SD = 根 id；DX = 根 id + 10000（机台内部 id 规则，如 835 → 10835），
    该规则单一来源为 maimai_py ``Song.get_divingfish_id``；
    宴 = 6 位机台内部 diff_id（直接读谱面对象，不要求已挂到 song）。
    卡片代表**具体谱面**时用本函数；曲级展示（搜索列表等）用
    :func:`display_song_id`。
    """
    from maimai_py.models import SongDifficultyUtage

    if isinstance(diff, SongDifficultyUtage):
        return diff.diff_id
    return song.get_divingfish_id(diff.type, diff.level_index)
