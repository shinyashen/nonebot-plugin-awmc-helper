"""自实现计算：RA/评级、分数线容错、推分推荐、DX 星。

RA 计算直接使用 maimai-py 的 ``ScoreCoefficient``（单一事实来源），
分数线与推分算法对齐原版 maimaiDX（core/utils/calc.py + handler.get_rise_score_list）。
"""

from maimai_py import (
    Song,
    FCType,
    RateType,
    SongType,
    LevelIndex,
    ScoreExtend,
    SongDifficulty,
)
from maimai_py.utils import ScoreCoefficient

from ..constants import RATE_TO_ZH

# 推分试算的达成率档位（原版 RISE_ACHIEVEMENT_LIST）
RISE_ACHIEVEMENTS = (99.0, 99.5, 100.0, 100.5)


def compute_rating(ds: float, achievement: float) -> int:
    """达成率 → RA（maimai-py 系数表）。"""
    return int(ScoreCoefficient(achievement).ra(ds))


def rate_of(achievement: float) -> str:
    """达成率 → 评级名（SSS+ 等）。"""
    return RATE_TO_ZH[RateType._from_achievement(achievement)]


def min_ra_of(scores: list[ScoreExtend]) -> int:
    """B50 中最低 RA（入线门槛）。"""
    return int(min((s.dx_rating or 0 for s in scores), default=0))


def score_line(diff: SongDifficulty, line: float) -> dict[str, float] | None:
    """分数线容错计算（对齐原版公式）。

    返回 TAP+GREAT 等价容错数与 BREAK 50 落等价；参数非法返回 None。
    """
    reduce = 101 - line
    if reduce <= 0 or reduce >= 101:
        return None
    total = (
        diff.tap_num * 500
        + diff.hold_num * 1000
        + diff.slide_num * 1500
        + diff.touch_num * 500
        + diff.break_num * 2500
    )
    if diff.break_num == 0 or total == 0:
        return None
    break_bonus = 0.01 / diff.break_num
    break_50_reduce = total * break_bonus / 4
    return {
        "total": total,
        "tap_great": total * reduce / 10000,  # 允许的 TAP+GREAT 等价数
        "per_tap_pct": 10000 / total,  # 每个 TAP+GREAT 损失的百分比
        "break_50_tap": break_50_reduce / 100,  # BREAK 50 落等价 TAP 数
        "break_50_pct": break_50_reduce / total * 100,
        "breaks": diff.break_num,
    }


def dx_star_ratio(dx_score: int, level_dx_score: int) -> int:
    """DX 百分比 → 星数（0-5，阈值与 maimai-py 一致）。"""
    if level_dx_score <= 0:
        return 0
    pct = dx_score / level_dx_score * 100
    if pct <= 85:
        return 0
    if pct <= 90:
        return 1
    if pct <= 93:
        return 2
    if pct <= 95:
        return 3
    if pct <= 97:
        return 4
    return 5


def rise_recommend(
    scores: list[ScoreExtend],
    songs: list[Song],
    *,
    level: str | None = None,
    target: int = 1,
    max_count: int = 15,
    latest_version_value: int | None = None,
) -> list[dict]:
    """推分推荐（对齐原版 get_rise_score_list 语义）。

    - ``scores``：玩家当前 B50 成绩（ScoreExtend，含 dx_rating）；
    - ``songs``：候选曲库（通常为按等级或定数过滤后的子集）；
    - ``level``：指定等级时按等级选谱，否则按 B50 末位 RA 反推定数区间；
    - ``latest_version_value``：当前版本码；缺省取 maimai_py ``current_version``
      （硬编码 25000 会在新版本时代漏推当前版本 DX 曲）；
    - 版本语义对齐原版双栏（旧版本谱面推荐 / 新版本谱面推荐）：
      DX 谱只推当前版本（b15 侧），SD 谱只推旧版本（b35 侧）——当前版本
      SD 曲属于新版本侧，绝不可进入「旧版本」栏；
    - 返回按定数降序的推荐列表（song/diff/达成率/新 RA/提升）。
    """
    if latest_version_value is None:
        from maimai_py import current_version

        latest_version_value = current_version.value
    by_key: dict[tuple, ScoreExtend] = {
        (s.id, s.type, s.level_index): s for s in scores
    }
    ignored_ids = {s.id for s in scores if (s.achievements or 0) >= 100.5}

    sd_side = sorted(
        (s for s in scores if s.type == SongType.STANDARD),
        key=lambda s: s.dx_rating or 0,
    )
    dx_side = sorted(
        (s for s in scores if s.type == SongType.DX), key=lambda s: s.dx_rating or 0
    )
    lowest = {
        "sd": sd_side[-1] if sd_side else None,
        "dx": dx_side[-1] if dx_side else None,
    }

    results: list[dict] = []
    for song in songs:
        if song.id >= 100000 or song.id in ignored_ids:  # 宴谱不推分
            continue
        for diff in song.get_difficulties():
            if diff.type == SongType.UTAGE:
                continue
            side = "sd" if diff.type == SongType.STANDARD else "dx"
            base = lowest[side]
            if base is None:
                continue
            if level is not None and diff.level != level:
                continue
            # 双栏版本语义：DX 只推当前版本，SD 只推旧版本
            if diff.type == SongType.DX:
                if diff.version < latest_version_value:
                    continue
            elif diff.version >= latest_version_value:
                continue

            key = (song.id, diff.type, diff.level_index)
            old = by_key.get(key)
            base_ra = base.dx_rating or 0
            old_ra = max(old.dx_rating or 0, base_ra) if old else 0

            best_gain: dict | None = None
            for ach in RISE_ACHIEVEMENTS:
                new_ra = compute_rating(diff.level_value, ach)
                base_ra = base.dx_rating or 0
                if old is None:
                    if new_ra <= base_ra:
                        continue
                    gain = new_ra - base_ra
                else:
                    gain = new_ra - old_ra
                    if gain < target:
                        continue
                if best_gain is None or gain > best_gain["gain"]:
                    best_gain = {
                        "song": song,
                        "diff": diff,
                        "achievements": ach,
                        "rate": rate_of(ach),
                        "new_ra": new_ra,
                        "gain": gain,
                        # 旧成绩（推分行卡显示；未游玩为 0，对齐 NB RiseResult 默认值）
                        "old_achievements": (
                            float(old.achievements or 0) if old else 0.0
                        ),
                        "old_ra": int(old.dx_rating or 0) if old else 0,
                    }
                break  # 首个满足的档位即最低要求（原版语义）
            if best_gain is not None:
                results.append(best_gain)

    results.sort(key=lambda r: r["diff"].level_value, reverse=True)
    return results[:max_count]


def level_index_name(level_index: LevelIndex) -> str:
    """难度枚举 → 英文名（表格列头用）。"""
    return level_index.name


def fc_abbr(fc: FCType | None) -> str:
    return fc.name if fc else "-"
