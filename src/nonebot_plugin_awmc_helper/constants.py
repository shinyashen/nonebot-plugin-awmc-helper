"""游戏常量：分类中文映射、等级颜色、版本显示名等。

maimai-py 的 `Genre` 枚举值是日文分类，库未公开导出中文映射，
本模块自带「中文/日文 → Genre」双向映射（对齐原版 maimaiDX 的 CATEGORY 语义）。
"""

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


# ---------------------------------------------------------------------------
# NB 版绘图移植用的映射表（core/render/nb_chart.py、best50.py 使用）
# ---------------------------------------------------------------------------

# 达成率系数表阈值（与 maimai-py ScoreCoefficient 一致，升序）
ACHIEVEMENT_LIST = [50, 60, 70, 75, 80, 90, 94, 97, 98, 99, 99.5, 100, 100.5]

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
