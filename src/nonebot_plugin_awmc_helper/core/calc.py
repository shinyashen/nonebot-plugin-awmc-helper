"""自实现计算：RA/评级、分数线容错、推分推荐、best50 变体组装。

RA 计算直接使用 maimai-py 的 ``ScoreCoefficient``（单一事实来源），
分数线与推分算法对齐原版 maimaiDX（core/utils/calc.py + handler.get_rise_score_list）。
"""

from typing import Any
from collections.abc import Callable

from maimai_py import (
    Song,
    RateType,
    SongType,
    PlayerBests,
    ScoreExtend,
    SongDifficulty,
)
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


def sssp_ignore_ids(scores: list[ScoreExtend]) -> set[int]:
    """成绩中已达到 SSS+ 的曲目 id 集（推分排除集，各消费方与
    :func:`rise_recommend` 内部共用同一口径）。"""
    return {s.id for s in scores if (s.achievements or 0) >= SSSP_ACHIEVEMENT}


def rise_candidates(
    songs: list[Song],
    *,
    ds_range: tuple[float, float] | None = None,
    level: str | None = None,
    song_type: SongType | None = None,
    scores: list[ScoreExtend] | None = None,
) -> list[Song]:
    """推分候选集构建（随机推分 / 推分推荐两消费方共用，纯过滤不做 IO）。

    - ``ds_range``：定数闭区间（按 B50 末位 RA 反推的 [ds, ds+1] 形态）；
    - ``level``：指定标级时按标级过滤（「我要在<等级>上加N分」）；
    - ``song_type``：谱面类型过滤（随机推分的 DX/SD 分侧）；
    - ``scores``：给出时按 :func:`sssp_ignore_ids` 排除已 SSS+ 的曲目。

    ⚠️ 两消费方的「B50 末位 RA」口径**刻意不同**（随机推分取该谱面类型一侧
    的 b35/b15 末位，推分推荐取 b35+b15 全体 min，NB 两处原型即如此、各有
    领域理由）——本函数只统一候选集构建，ds 区间由调用方各自计算传入。
    """
    ignore_ids = sssp_ignore_ids(scores) if scores else set()
    candidates: list[Song] = []
    for song in songs:
        if song.id in ignore_ids:
            continue
        if any(
            (song_type is None or d.type == song_type)
            and (level is None or d.level == level)
            and (ds_range is None or ds_range[0] <= d.level_value <= ds_range[1])
            for d in song.get_difficulties()
        ):
            candidates.append(song)
    return candidates


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
    ignored_ids = sssp_ignore_ids(scores)

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


def build_flat_bests(
    scores: list[ScoreExtend],
    *,
    key: Callable[[ScoreExtend], Any],
    n: int = 50,
) -> PlayerBests:
    """flat 组装（条件50 平铺形态，karenbot-combo-notes §8）：整体按 ``key``
    降序取前 ``n`` 条，全部置于 b35 侧、b15 侧置空（渲染层 flat 版式消费）。

    rating 三字段 = 所列成绩 RA 之和（与 :func:`build_bests` 同口径，不是
    玩家 rating）；不拆新旧、不足 ``n`` 条照实返回（留白不回退）。
    """
    ordered = sorted(scores, key=key, reverse=True)[:n]
    total = int(sum(s.dx_rating or 0 for s in ordered))
    return PlayerBests(
        rating=total,
        rating_b35=total,
        rating_b15=0,
        scores_b35=ordered,
        scores_b15=[],
    )


def build_bests(
    scores: list[ScoreExtend],
    *,
    key: Callable[[ScoreExtend], Any],
    latest_version_value: int | None = None,
    old_cap: int = 35,
    new_cap: int = 15,
) -> PlayerBests:
    """公共 best50 组装：按版本拆旧 ``old_cap`` / 新 ``new_cap``（默认 35/15，
    b40 传 25/15），两侧各按 ``key`` 降序取满。

    maimai_py 的 ``MaimaiScores.configure`` 固定按 RA 排序且不可注入排序键，
    b50 变体（ap50、日服 NET、第三方 pc50）经本函数得到同构 PlayerBests。

    - ``key``：单侧排序键（降序；标量或元组皆可，平手次序由键的后续位决定）；
    - ``latest_version_value``：「新版本侧」下界版本码，缺省取 maimai_py
      ``current_version``（日服 NET 链路传 ``current_version_jp``）；
    - rating 三字段为**所列成绩的 RA 之和**（b50 变体的模板占位口径，
      与 ap50 现状一致），不是玩家 rating。

    调用方自行完成过滤/去重/曲级聚合——本函数只做拆分、排序、截断、求和。
    """
    if latest_version_value is None:
        from maimai_py import current_version

        latest_version_value = current_version.value
    old: list[ScoreExtend] = []
    new: list[ScoreExtend] = []
    for score in scores:
        (new if (score.version or 0) >= latest_version_value else old).append(score)
    old.sort(key=key, reverse=True)
    new.sort(key=key, reverse=True)
    old, new = old[:old_cap], new[:new_cap]
    ra_old = int(sum(s.dx_rating or 0 for s in old))
    ra_new = int(sum(s.dx_rating or 0 for s in new))
    return PlayerBests(
        rating=ra_old + ra_new,
        rating_b35=ra_old,
        rating_b15=ra_new,
        scores_b35=old,
        scores_b15=new,
    )
