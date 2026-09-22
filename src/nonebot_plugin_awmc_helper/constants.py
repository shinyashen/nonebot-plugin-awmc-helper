"""游戏常量：分类中文映射、等级颜色、版本显示名等。

maimai-py 的 `Genre` 枚举值是日文分类，库未公开导出中文映射，
本模块自带「中文/日文 → Genre」双向映射（对齐原版 maimaiDX 的 CATEGORY 语义）。
"""

import re

from maimai_py import Genre, Version, RateType, LevelIndex

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

# 中文（及日文原名）→ 枚举，用于「随个流行」等分类过滤
ZH_TO_GENRE: dict[str, Genre] = {zh: genre for genre, zh in GENRE_TO_ZH.items()} | {
    # 常见日文/别名入口
    "POPSアニメ": Genre.POPSアニメ,
    "niconicoボーカロイド": Genre.niconicoボーカロイド,
    "東方Project": Genre.東方Project,
    "ゲームバラエティ": Genre.ゲームバラエティ,
    "オンゲキCHUNITHM": Genre.オンゲキCHUNITHM,
    "宴会場": Genre.宴会場,
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
# 难度（LevelIndex）→ 颜色/名称
# ---------------------------------------------------------------------------
LEVEL_INDEX_COLOR: dict[LevelIndex, str] = {
    LevelIndex.BASIC: "绿",
    LevelIndex.ADVANCED: "黄",
    LevelIndex.EXPERT: "红",
    LevelIndex.MASTER: "紫",
    LevelIndex.ReMASTER: "白",
}
COLOR_TO_LEVEL_INDEX: dict[str, LevelIndex] = {
    v: k for k, v in LEVEL_INDEX_COLOR.items()
}

LEVEL_INDEX_ZH: dict[LevelIndex, str] = {
    LevelIndex.BASIC: "基础",
    LevelIndex.ADVANCED: "高级",
    LevelIndex.EXPERT: "专家",
    LevelIndex.MASTER: "大师",
    LevelIndex.ReMASTER: "宗师",
}

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
# 版本：枚举 → 中文显示名（用于查歌结果与版本过滤）
# ---------------------------------------------------------------------------
VERSION_TO_ZH: dict[Version, str] = {
    Version.MAIMAI: "maimai",
    Version.MAIMAI_PLUS: "maimai PLUS",
    Version.MAIMAI_GREEN: "maimai GreeN",
    Version.MAIMAI_GREEN_PLUS: "maimai GreeN PLUS",
    Version.MAIMAI_ORANGE: "maimai ORANGE",
    Version.MAIMAI_ORANGE_PLUS: "maimai ORANGE PLUS",
    Version.MAIMAI_PINK: "maimai PiNK",
    Version.MAIMAI_PINK_PLUS: "maimai PiNK PLUS",
    Version.MAIMAI_MURASAKI: "maimai MURASAKi",
    Version.MAIMAI_MURASAKI_PLUS: "maimai MURASAKi PLUS",
    Version.MAIMAI_MILK: "maimai MiLK",
    Version.MAIMAI_MILK_PLUS: "maimai MiLK PLUS",
    Version.MAIMAI_FINALE: "maimai FiNALE",
    Version.MAIMAI_DX: "舞萌DX",
    Version.MAIMAI_DX_PLUS: "舞萌DX PLUS",
    Version.MAIMAI_DX_SPLASH: "舞萌DX SPLASH",
    Version.MAIMAI_DX_SPLASH_PLUS: "舞萌DX SPLASH PLUS",
    Version.MAIMAI_DX_UNIVERSE: "舞萌DX UNiVERSE",
    Version.MAIMAI_DX_UNIVERSE_PLUS: "舞萌DX UNiVERSE PLUS",
    Version.MAIMAI_DX_FESTIVAL: "舞萌DX FESTiVAL",
    Version.MAIMAI_DX_FESTIVAL_PLUS: "舞萌DX FESTiVAL PLUS",
    Version.MAIMAI_DX_BUDDIES: "舞萌DX BUDDiES",
    Version.MAIMAI_DX_BUDDIES_PLUS: "舞萌DX BUDDiES PLUS",
    Version.MAIMAI_DX_PRISM: "舞萌DX PRiSM",
    Version.MAIMAI_DX_PRISM_PLUS: "舞萌DX PRiSM PLUS",
    Version.MAIMAI_DX_CIRCLE: "舞萌DX CiRCLE",
    Version.MAIMAI_DX_CIRCLE_PLUS: "舞萌DX CiRCLE PLUS",
    Version.MAIMAI_DX_FUTURE: "舞萌DX FUTURE",
}

# 牌子版本单字 → 中文显示名（牌种判定交给 maimai-py）
PLATE_VERSION_ZH: dict[str, str] = {
    "初": "maimai",
    "真": "maimai PLUS",
    "超": "maimai GreeN",
    "檄": "maimai GreeN PLUS",
    "橙": "maimai ORANGE",
    "晓": "maimai ORANGE PLUS",
    "桃": "maimai PiNK",
    "樱": "maimai PiNK PLUS",
    "紫": "maimai MURASAKi",
    "堇": "maimai MURASAKi PLUS",
    "白": "maimai MiLK",
    "雪": "maimai MiLK PLUS",
    "辉": "maimai FiNALE",
    "熊": "舞萌DX",
    "华": "舞萌DX PLUS",
    "爽": "舞萌DX SPLASH",
    "煌": "舞萌DX SPLASH PLUS",
    "星": "舞萌DX UNiVERSE",
    "宙": "舞萌DX UNiVERSE PLUS",
    "祭": "舞萌DX FESTiVAL",
    "祝": "舞萌DX FESTiVAL PLUS",
    "双": "舞萌DX BUDDiES",
    "宴": "舞萌DX BUDDiES PLUS",
    "镜": "舞萌DX PRiSM",
    "彩": "舞萌DX PRiSM PLUS",
    "丸": "舞萌DX CiRCLE",
    "舞": "旧作全集（舞）",
    "霸": "旧作全集（霸）",
}

# 牌种达成条件说明（牌子条件 指令用）
PLATE_KIND_ZH: dict[str, str] = {
    "者": "达成率 ≥ A（80%）",
    "将": "达成率 ≥ SSS（97%）",
    "极": "全曲目 Full Combo",
    "神": "全曲目 All Perfect",
    "舞舞": "全曲目 Full Sync DX（FSD）",
}

# 完成表预渲染枚举（SUPERUSER 指令与国服更新自动触发共用，song-db-design §7.3）
PLATE_CHARS = "舞霸真超檄橙晓桃樱紫堇白雪辉熊华爽煌星宙祭祝双宴镜彩丸"
PLATE_KINDS = ("将", "者", "极", "神", "舞舞")

# ---------------------------------------------------------------------------
# 版本名 → 版本码（歌曲库日侧骨架用；穷举自 maimai_py Version 枚举，勿凭记忆增删）
# ---------------------------------------------------------------------------

# 数据源版本名 → 版本码。两套命名并存（穷举自真实数据，勿凭记忆增删）：
# - all_data `from`：旧框英文名 + DX 时代日文名（"maimai でらっくす*"，同 maimai_py）；
# - dschange 版本名 / __increments__.version：DX 时代英文名（"maimai DX*"）
SOURCE_NAME_TO_VERSION: dict[str, int] = {
    # —— dschange 系（maimaiinfo/static/dschange.json 实测命名）——
    "maimai DX": 20000,
    "maimai DX PLUS": 20500,
    "maimai DX Splash": 21000,
    "maimai DX Splash PLUS": 21500,
    "maimai DX UNiVERSE": 22000,
    "maimai DX UNiVERSE PLUS": 22500,
    "maimai DX FESTiVAL": 23000,
    "maimai DX FESTiVAL PLUS": 23500,
    "maimai DX BUDDiES": 24000,
    "maimai DX BUDDiES PLUS": 24500,
    "maimai DX PRiSM": 25000,
    "maimai DX PRiSM PLUS": 25500,
    "maimai DX CiRCLE": 26000,
    "maimai DX CiRCLE PLUS": 26500,
    # —— all_data `from` 系（divingfish_to_version 缺 PLUS 各版，按枚举补全）——
    "maimai": 10000,
    "maimai PLUS": 11000,
    "maimai GreeN": 12000,
    "maimai GreeN PLUS": 13000,
    "maimai ORANGE": 14000,
    "maimai ORANGE PLUS": 15000,
    "maimai PiNK": 16000,
    "maimai PiNK PLUS": 17000,
    "maimai MURASAKi": 18000,
    "maimai MURASAKi PLUS": 18500,
    "maimai MiLK": 19000,
    "maimai MiLK PLUS": 19500,
    "maimai FiNALE": 19900,
    "maimai でらっくす": 20000,
    "maimai でらっくす PLUS": 20500,
    "maimai でらっくす Splash": 21000,
    "maimai でらっくす Splash PLUS": 21500,
    "maimai でらっくす UNiVERSE": 22000,
    "maimai でらっくす UNiVERSE PLUS": 22500,
    "maimai でらっくす FESTiVAL": 23000,
    "maimai でらっくす FESTiVAL PLUS": 23500,
    "maimai でらっくす BUDDiES": 24000,
    "maimai でらっくす BUDDiES PLUS": 24500,
    "maimai でらっくす PRiSM": 25000,
    "maimai でらっくす PRiSM PLUS": 25500,
    "maimai でらっくす CiRCLE": 26000,
    "maimai でらっくす CiRCLE PLUS": 26500,
}

# 版本码 → 显示名兜底（maimai_py 枚举之外的已知新版本；数据源 otoge-db/DXRating 已收录。
# 库跟进新版本枚举后，此处条目自然失效，version_name() 会优先命中枚举）
EXTRA_VERSION_NAMES: dict[int, str] = {
    # 2026-09-17 日服上线；maimai_py 1.5.2 尚未收录（song-db-design §7.4）
    27000: "MAGiCAL",
}

# DX 时代版本轴（穷举自 Version 枚举 MAIMAI_DX..MAIMAI_DX_CIRCLE_PLUS，勿凭记忆增删）：
# 01 文档标准 JSON 的 sd/dx `level` 扁平列表即按此轴逐版本对齐
# （「不包含旧框版本的定数，均从 dx 初代版本开始统计」）
DX_VERSION_CODES: list[int] = [
    20000,  # maimai でらっくす
    20500,  # maimai でらっくす PLUS
    21000,  # maimai でらっくす Splash
    21500,  # maimai でらっくす Splash PLUS
    22000,  # maimai でらっくす UNiVERSE
    22500,  # maimai でらっくす UNiVERSE PLUS
    23000,  # maimai でらっくす FESTiVAL
    23500,  # maimai でらっくす FESTiVAL PLUS
    24000,  # maimai でらっくす BUDDiES
    24500,  # maimai でらっくす BUDDiES PLUS
    25000,  # maimai でらっくす PRiSM
    25500,  # maimai でらっくす PRiSM PLUS
    26000,  # maimai でらっくす CiRCLE
    26500,  # maimai でらっくす CiRCLE PLUS
]


# 谱面类型别名前缀：社区对同根id双谱（标准/DX/宴）的惯用区分写法
# （dx圣诞、标准39、标星光、旧谱、宴Oshama…）。用于查询侧剥离兜底，
# 最长优先匹配；数据侧别名保留原样（含前缀）不去除
# 谱面类型别名前缀（作者口径，穷举）：dx / 标准 / 标 / 宴 / {宴谱汉字}。
# 汉字前缀随曲而异（撫/協/蔵…），由调用方从规范表 song_chart.kanji / 运行时
# 宴谱对象取该曲的汉字后经 extra_prefixes 传入；匹配经 normalize_text 归一，
# 简体输入（抚/协/藏）同样命中
_CHART_PREFIX_RE = re.compile(r"^(dx|标准|标|宴)[\s·・.。:：_-]*", re.IGNORECASE)

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
) -> "tuple[str, str] | None":
    """剥离别名开头**一层**谱面类型前缀，返回 (剥离后别名, 命中的前缀)。

    - 静态前缀：dx / 标准 / 标 / 宴（作者口径穷举；sd/旧 等不会出现）；
    - ``extra_prefixes``：该曲宴谱的汉字（单字），按归一化比对（简体输入兼容）；
    - 只剥一层：叠层前缀（「dx标39」）剥完的「标39」不在去前缀别名库中，
      自然不命中（作者口径）；无前缀可剥（或剥完为空）返回 None。
    """
    text = alias.strip()
    match = _CHART_PREFIX_RE.match(text)
    if match:
        stripped = text[match.end() :].strip()
        return (stripped, match.group(1)) if stripped else None
    extra = extra_prefixes or set()
    normalized_extra = {normalize_text(p): p for p in extra if p}
    first = normalize_text(text[:1])
    if text and first in normalized_extra:
        stripped = text[1:].strip()
        return (stripped, normalized_extra[first]) if stripped else None
    return None


def version_name(version: int) -> str:
    """版本码 → 显示名：① maimai_py 枚举精确成员 → ② 数据源版本表 → ③ 原码字符串。

    禁止用 ``Version.from_value`` 判未知码（其语义为「≤ 值的最近枚举」，27000 会被
    错误钳成 CiRCLE PLUS，song-db-design §7.4）。
    """
    try:
        ver = Version(version)
    except ValueError:
        return EXTRA_VERSION_NAMES.get(version, str(version))
    return VERSION_TO_ZH.get(ver, ver.name)


def level_from_value(level_value: float) -> str:
    """定数 → 标级串（otoge-db 全量验证：x.0–x.5 → 无+，x.6–x.9 → +，0 冲突）。"""
    base = int(level_value)
    return f"{base}+" if round(level_value * 10) % 10 >= 6 else f"{base}"


# ---------------------------------------------------------------------------
# NB 版绘图移植用的映射表（core/render/nb_chart.py、best50.py 使用）
# ---------------------------------------------------------------------------

# 达成率系数表阈值（与 maimai-py ScoreCoefficient 一致，升序）
ACHIEVEMENT_LIST = [50, 60, 70, 75, 80, 90, 94, 97, 98, 99, 99.5, 100, 100.5]

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

# RateType 枚举名 → UI_TTR_Rank_*.png 文件名后缀
RATE_FILE = {
    "SSSP": "SSSp",
    "SSS": "SSS",
    "SSP": "SSp",
    "SS": "SS",
    "SP": "Sp",
    "S": "S",
    "AAA": "AAA",
    "AA": "AA",
    "A": "A",
    "BBB": "BBB",
    "BB": "BB",
    "B": "B",
    "C": "C",
    "D": "D",
}

# FCType/FSType 枚举名 → UI_MSS_MBase_Icon_*.png 文件名后缀
COMBO_FILE = {"FC": "FC", "FCP": "FCp", "AP": "AP", "APP": "APp"}
SYNC_FILE = {"FS": "FS", "FSP": "FSp", "FSD": "FSD", "FSDP": "FSDp"}

# 日服 DX 世代版本码 → 日服 logo 文件名（static/mai/pic/jp/ 下）。
# 现有素材库的 DX 代 logo 为国服特有版本，日服视图渲染时改用本表；
# 旧框（<20000）中日 logo 相同，走 VERSION_IMAGE 通用路径。
# MAGiCAL(27000) 等超出 maimai_py 枚举的版本同样在此映射（song-db-design §7.4）
JP_VERSION_IMAGE: dict[int, str] = {
    20000: "DX",
    20500: "DX PLUS",
    21000: "Splash",
    21500: "Splash PLUS",
    22000: "UNiVERSE",
    22500: "UNiVERSE PLUS",
    23000: "FESTiVAL",
    23500: "FESTiVAL PLUS",
    24000: "BUDDiES",
    24500: "BUDDiES PLUS",
    25000: "PRiSM",
    25500: "PRiSM PLUS",
    26000: "CiRCLE",
    26500: "CiRCLE PLUS",
    27000: "MAGiCAL",
}

# Version 枚举 → 版本图文件名（pic/ 下，键与 maimai-py divingfish_to_version 一致）
try:  # maimai-py 未公开导出该映射时的兜底
    from maimai_py.enums import divingfish_to_version as _DF_TO_VERSION

    VERSION_IMAGE = {v: k for k, v in _DF_TO_VERSION.items()}
except ImportError:  # pragma: no cover
    VERSION_IMAGE = {}


def genre_zh(genre: Genre) -> str:
    """Genre 枚举 → 中文分类名。"""
    return GENRE_TO_ZH.get(genre, genre.value)


def version_zh(version: int) -> str:
    """曲谱版本整数 → 中文显示名（未知值原样返回）。"""
    ver = Version.from_value(version)
    return VERSION_TO_ZH.get(ver, str(version)) if ver is not None else str(version)
