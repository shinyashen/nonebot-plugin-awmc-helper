"""自实现计算：RA/评级、分数线容错、推分推荐。

RA 计算直接使用 maimai-py 的 ``ScoreCoefficient``（单一事实来源），
分数线与推分算法对齐原版 maimaiDX（core/utils/calc.py + handler.get_rise_score_list）。
"""

from maimai_py import Song, RateType, SongType, ScoreExtend, SongDifficulty
from maimai_py.utils import ScoreCoefficient

from ..constants import RATE_TO_ZH, UTAGE_ID_BASE

# 推分试算的达成率档位（原版 RISE_ACHIEVEMENT_LIST）
RISE_ACHIEVEMENTS = (99.0, 99.5, 100.0, 100.5)

SSSP_ACHIEVEMENT = 100.5
"""SSS+ 达成率阈值（推分忽略集与评级上界）。"""

SSSP_COEFFICIENT = ScoreCoefficient(SSSP_ACHIEVEMENT).c
"""SSSP 档 RA 系数（22.4，NB get_mai_what 的 RA→定数反推基准）。"""


def min_ds_of_ra(ra: float) -> float:
    """B50 末位 RA → 入线所需最低定数（SSSP 系数反推，调用方自行取整）。"""
    return ra / SSSP_COEFFICIENT


def compute_rating(ds: float, achievement: float) -> int:
    """达成率 → RA（maimai-py 系数表）。"""
    return int(ScoreCoefficient(achievement).ra(ds))


def rate_of(achievement: float) -> str:
    """达成率 → 评级名（SSS+ 等）。"""
    return RATE_TO_ZH[RateType._from_achievement(achievement)]


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


def rise_recommend(
    scores: list[ScoreExtend],
    songs: list[Song],
    *,
    level: str | None = None,
    target: int = 1,
    per_side: int = 5,
    latest_version_value: int | None = None,
) -> list[dict]:
    """推分推荐（对齐原版 get_rise_score_list 的双栏语义）。

    双栏按**版本**划分（用户确认口径，即 NB 旧版本/新版本谱面推荐）：
    - ``old`` 旧版本谱面推荐 = 当前版本以前的全部谱面（进入 b35 的一侧），
      SD/DX 均可；
    - ``new`` 新版本谱面推荐 = 当前版本谱面（进入 b15 的一侧），SD/DX 均可。

    每栏以该侧 B50 末位 RA 为入线基准（低于基准的新成绩无法入栏替换），
    返回按定数降序、每侧至多 ``per_side`` 条的推荐列表
    （song/diff/side/达成率/新 RA/提升/旧成绩）。

    - ``scores``：玩家当前 B50 成绩（ScoreExtend，含 dx_rating 与 version）；
    - ``songs``：候选曲库（通常为按等级或定数过滤后的子集）；
    - ``level``：指定等级时按等级选谱，否则按 B50 末位 RA 反推定数区间；
    - ``latest_version_value``：当前版本码；缺省取 maimai_py ``current_version``
      （硬编码会在新版本时代漏推当前版本曲）。
    """
    if latest_version_value is None:
        from maimai_py import current_version

        latest_version_value = current_version.value
    by_key: dict[tuple, ScoreExtend] = {
        (s.id, s.type, s.level_index): s for s in scores
    }
    ignored_ids = {s.id for s in scores if (s.achievements or 0) >= SSSP_ACHIEVEMENT}

    # 两侧入线基准 = 该侧 B50 末位（最低）RA：升序排列后取首位
    sides: dict[str, list[ScoreExtend]] = {
        "old": sorted(
            (s for s in scores if s.version < latest_version_value),
            key=lambda s: s.dx_rating or 0,
        ),
        "new": sorted(
            (s for s in scores if s.version >= latest_version_value),
            key=lambda s: s.dx_rating or 0,
        ),
    }

    results: list[dict] = []
    for song in songs:
        if song.id >= UTAGE_ID_BASE or song.id in ignored_ids:  # 宴谱不推分
            continue
        for diff in song.get_difficulties():
            if diff.type == SongType.UTAGE:
                continue
            side = "old" if diff.version < latest_version_value else "new"
            side_scores = sides[side]
            if not side_scores:
                continue
            base_ra = side_scores[0].dx_rating or 0
            if level is not None and diff.level != level:
                continue

            key = (song.id, diff.type, diff.level_index)
            old = by_key.get(key)
            old_ra = max(old.dx_rating or 0, base_ra) if old else 0

            best_gain: dict | None = None
            for ach in RISE_ACHIEVEMENTS:
                new_ra = compute_rating(diff.level_value, ach)
                if old is None:
                    if new_ra <= base_ra:
                        continue
                    gain = new_ra - base_ra
                else:
                    gain = new_ra - old_ra
                    if gain < target:
                        continue
                # 每谱面首个满足档位即最低要求（break 恒触发），best_gain 到此必为 None
                if best_gain is None:
                    best_gain = {
                        "song": song,
                        "diff": diff,
                        "side": side,
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

    # 每侧按定数降序取前 per_side 条
    old_side = sorted(
        (r for r in results if r["side"] == "old"),
        key=lambda r: r["diff"].level_value,
        reverse=True,
    )[:per_side]
    new_side = sorted(
        (r for r in results if r["side"] == "new"),
        key=lambda r: r["diff"].level_value,
        reverse=True,
    )[:per_side]
    return sorted(
        old_side + new_side, key=lambda r: r["diff"].level_value, reverse=True
    )
