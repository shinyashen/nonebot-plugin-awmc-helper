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
    current_version,
)
from maimai_py.utils import ScoreCoefficient

from ..constants import RATE_TO_ZH

# 推分试算的达成率档位（原版 RISE_ACHIEVEMENT_LIST）
RISE_ACHIEVEMENTS = (99.0, 99.5, 100.0, 100.5)

SSSP_ACHIEVEMENT = 100.5
"""SSS+ 达成率阈值（推分忽略集与评级上界）。"""

THEORETICAL_ACHIEVEMENT = 101.0
"""理论值达成率（理论值计算与 RA 上限用；AP+ 即 100% 基础 + 1% BREAK 额外分）。"""

SSSP_COEFFICIENT = ScoreCoefficient(SSSP_ACHIEVEMENT).c
"""SSSP 档 RA 系数（22.4，NB get_mai_what 的 RA→定数反推基准）。"""

BREAK_JUDGES: tuple[tuple[str, int, int], ...] = (
    # BREAK 判定档位（专栏口径）：(显示名, 基础分, 额外分)；CP 2500+100 为满分
    # 基准不列。前两档为 Perfect 快慢（P-1/P-2），显示名用玩家通俗称法
    # 「50落/100落」（用户拍板的映射，与 G-3 的历史俗称无关，勿望文生义）。
    ("50落", 2500, 75),
    ("100落", 2500, 50),
    ("G-1", 2000, 40),
    ("G-2", 1500, 40),
    ("G-3", 1250, 40),
    ("GOOD", 1000, 30),
    ("MISS", 0, 0),
)

# 普通音符各判定的等效 GREAT TAP 数（恒定，不随谱面变化）：满分/基础分
# TAP·TOUCH 500、HOLD 1000、SLIDE 1500；判定得分率 GREAT 80% / GOOD 50%
NOTE_JUDGES: tuple[tuple[str, tuple[float, float, float]], ...] = (
    ("TAP·TOUCH", (1.0, 2.5, 5.0)),
    ("HOLD", (2.0, 5.0, 10.0)),
    ("SLIDE", (3.0, 7.5, 15.0)),
)

# 音符基础分（专栏计分口径，出处见 :func:`score_line` docstring）：
# TAP·TOUCH 500、HOLD 1000、SLIDE 1500、BREAK 2500
_NOTE_BASE_SCORE: dict[str, int] = {
    "tap": 500,
    "hold": 1000,
    "slide": 1500,
    "touch": 500,
    "break": 2500,
}
# 达成率百分点 → 万分位整数的换算基数（基础分/预算均按万分位折算）
_ACHIEVEMENT_BPS = 10_000


def basic_score_total(diff: SongDifficulty) -> int:
    """谱面基础满分（专栏计分口径）：TAP·TOUCH 500、HOLD 1000、SLIDE 1500、
    BREAK 2500（:func:`score_line` 与 combo 寸/锁血的谱面容错事实共用）。"""
    return (
        diff.tap_num * _NOTE_BASE_SCORE["tap"]
        + diff.hold_num * _NOTE_BASE_SCORE["hold"]
        + diff.slide_num * _NOTE_BASE_SCORE["slide"]
        + diff.touch_num * _NOTE_BASE_SCORE["touch"]
        + diff.break_num * _NOTE_BASE_SCORE["break"]
    )


def chart_loss_facts(diff: SongDifficulty) -> "tuple[float, float] | None":
    """谱面容错事实（combo 寸/锁血的谱面相关判定用），单位**万分位**。

    推导（专栏计分口径：达成率 = 基础分/基础满分×100% + 额外分/额外满分×1%）：

    - **每 1 个等效 GREAT TAP**（1 个 TAP 从 CP 掉 GREAT，损 100 基础分）：
      100/基础满分 × 100 个百分点 = 10⁴/满分 pct，万分位再 ×10⁴ →
      ``1e8/基础满分``。1e8 = 损失基础分 100 × 百分比基数 100 × 万分位基数
      10000；与 :func:`score_line` 预算口径自洽（预算 × 每损失 = 上限-线）。
    - **每 1 个 100落**（BREAK P-2：基础 2500 满额、额外仅 50/100，即损
      50 额外分）：额外通道全谱共值 1 个百分点（额外满分 = BREAK 数×100），
      损失 = 50/(100×BREAK数) × 1 = ``0.5/BREAK数`` pct = ``5000/BREAK数``
      万分位。0.5 = 50（损的额外分）/ 100（每 BREAK 的额外满分）；÷BREAK数
      = 摊到全谱额外通道。与 ``score_line`` break_rows 的「100落」等效数 ×
      每 GREAT TAP 损失同式（测试断言两者乘积自洽）。

    buddy 宴谱损失 ×2（份数，见 :func:`_scales_of`）。实践中宴谱成绩不入
    成绩流（寸/锁血谓词不会收到宴谱），此分支仅为口径完备。基础满分为 0
    或无 BREAK（100落 概念不成立）→ None（规则不适用）。
    """
    total = basic_score_total(diff)
    if total <= 0 or diff.break_num == 0:
        return None
    scales = _scales_of(achievement_cap(diff))
    return (
        _ACHIEVEMENT_BPS * _ACHIEVEMENT_BPS * scales / total,
        5000 * scales / diff.break_num,
    )


def min_ds_of_ra(ra: float) -> float:
    """B50 末位 RA → 入线所需最低定数（SSSP 系数反推，调用方自行取整）。"""
    return ra / SSSP_COEFFICIENT


def compute_rating(ds: float, achievement: float) -> int:
    """达成率 → RA（maimai-py 系数表）。"""
    return int(ScoreCoefficient(achievement).ra(ds))


def rate_of(achievement: float) -> str:
    """达成率 → 评级名（SSS+ 等）。"""
    return RATE_TO_ZH[RateType._from_achievement(achievement)]


def level_value_match(a: float, b: float) -> bool:
    """定数等值匹配（十分位 round 口径，S-9 防 14.5 浮点表示误差）。
    权威口径：core.combo 定数条件与传分插件本地成绩过滤共用。"""
    return round(a * 10) == round(b * 10)


def achievement_cap(diff: SongDifficulty) -> int:
    """谱面达成率上限（百分点）：buddy 宴谱 202（200 基础 + 2 额外，左右
    机台合计），其余 101（100 基础 + 1 额外）。"""
    if diff.type == SongType.UTAGE and bool(getattr(diff, "is_buddy", False)):
        return 202
    return 101


def rate_type_of(diff: SongDifficulty, line: float) -> "RateType":
    """谱面线对应的评级枚举：buddy 宴谱评级阈值 ×2（SSS+=201/SSS=200，
    依此类推），即按线的一半查普通档位；其余谱面按线直查。"""
    if diff.type == SongType.UTAGE and bool(getattr(diff, "is_buddy", False)):
        return RateType._from_achievement(line / 2)
    return RateType._from_achievement(line)


def _scales_of(cap: int) -> int:
    """达成率上限折合的 101 口径份数（普通/非 buddy 宴 = 1，buddy = 2）。

    buddy 宴谱 = 左右两机台**各打一张独立的** 101（100 基础 + 1 额外）谱面，
    显示达成率为两份之和（≤202）——已考证（2026-10-05，用户口径 + 上游
    provider + 快照实测）：maimai.py 水鱼 provider ``is_buddy = len(charts)==2``
    且顶层物量 = charts[0]+charts[1]；落雪 buddy 曲 notes 为 left/right 两组
    （[協]ラグトレイン 左 (183,76,53,164,173) / 右 (172,63,53,102,216)，两谱
    独立且不对称，左基础满分占 48.2%）；本项目规范表主物量列 = 左右之和、
    左右明细另存 BuddyNotes。

    由此按**合计物量**折算单判定损失：每判定损失 ×份数、预算 ÷份数；
    break_rows 的 per_bonus 因额外通道总值与每 GREAT 损失同乘份数而两相消，
    与普通谱同式（不变）。

    ⚠️ 左右谱面实际物量不对称，此处按「满分左右均摊」对称近似：损失值与
    左右真实损失的算术平均差 <0.2%（AM-GM：4·B_L·B_R/(B_L+B_R)² ≥ 0.985）。
    """
    return round(cap / THEORETICAL_ACHIEVEMENT)


def score_line(diff: SongDifficulty, line: float) -> dict[str, Any] | None:
    """分数线容错计算（2026-10-02 按专栏口径重写，替代原版复刻公式）。

    计分规则依据《maimai判定全解 第三部分 详细计分规则》（bilibili
    cv695015525113135112）：达成率 = 基础分/基础满分×100% + 额外分/额外满分×1%
    （额外分仅 BREAK 有，满分 = break 数×100）。

    「等效 GREAT TAP」= 1 个 TAP 从 Critical Perfect 掉到 GREAT 的损失
    （100 基础分）。总预算 = 基础满分×(上限-线)/(10000×份数)，普通谱与
    原版「允许的 TAP GREAT 数」同值（口径兼容）；基础分损失 Δb → Δb/100，
    额外分损失 Δx → Δx×基础满分/(额外满分×10000)，两通道严格可加。上限见
    :func:`achievement_cap`（buddy 宴谱 202，物量字段即左右机台合计值）；
    份数（buddy = 2）的含义与对称近似误差见 :func:`_scales_of`。

    BREAK 判定档位（CP 基础 2500+额外 100 为满分基准，不列）：
    P-1/P-2 的显示名用玩家通俗称法「50落/100落」（用户拍板，不望文生义）。

    返回 dict：``total_basic`` / ``total_bonus`` / ``budget``（等效 GREAT
    TAP 预算）/ ``per_great``（每等效 GREAT TAP 的达成率损失，百分点，
    恒满足 budget×per_great = 上限-线）/ ``breaks`` / ``cap`` / ``buddy`` /
    ``break_rows``（(档名, 等效数) 列表）。``line`` 非法（超出 (0, cap]）
    或谱面无 BREAK / 基础分为 0 返回 None。
    """
    cap = achievement_cap(diff)
    reduce_pct = cap - line
    if reduce_pct <= 0 or reduce_pct >= cap:
        return None
    total = basic_score_total(diff)
    if diff.break_num == 0 or total == 0:
        return None
    scales = _scales_of(cap)
    bonus_total = diff.break_num * 100
    # 每 1 额外分损失的等效 GREAT TAP 数（1% 通道权重折算；buddy 份数两相
    # 消——额外通道总值与每 GREAT 损失同乘份数——故与普通谱同式）
    per_bonus = total / (bonus_total * _ACHIEVEMENT_BPS)
    break_rows = [
        (
            name,
            (_NOTE_BASE_SCORE["break"] - basic) / 100 + (100 - extra) * per_bonus,
        )
        for name, basic, extra in BREAK_JUDGES
    ]
    return {
        "total_basic": total,
        "total_bonus": bonus_total,
        "budget": reduce_pct * total / (_ACHIEVEMENT_BPS * scales),
        "per_great": _ACHIEVEMENT_BPS * scales / total,
        "breaks": diff.break_num,
        "cap": cap,
        "buddy": cap == 202,
        "break_rows": break_rows,
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
        if song.id in ignored_ids:
            continue
        for diff in song.get_difficulties():
            if (
                diff.type == SongType.UTAGE
            ):  # 宴谱不推分（Song.id 恒为根 id，无 id 域判断）
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
