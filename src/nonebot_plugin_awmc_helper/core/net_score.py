"""日服 NET 成绩组装：NET 记录 → maimai-py Score（JP 视图映射）→ B50。

不走 maimai-py 的 ``client.bests()``：其曲库映射与 b35/b15 划分都按国服视图
（CN 曲库 + ``current_version``），日服限定曲查不到、国服定数 ≠ 日服定数。
本模块用规范表 JP 视图（``core.songs`` 缓存）按「曲名 + 谱面类型 + 难度」
定位谱面，ra/评级/DX 星与 maimai-py ``MaimaiScores.configure`` 同口径，b35/b15
划分用 ``current_version_jp``（maimai_py 内置 = 日服现行版本）。

NET 记录页无谱面内部 id（曲名即唯一线索，dxrating 同样按标题匹配其曲库）；
数据滞后导致未匹配的记录逐条 log warning 后跳过，不阻塞 B50 组装。
抓取与组装分层：本模块不感知绑定语义与用户文案，冷却检查在 core.score 分派处。
"""

import time
from dataclasses import asdict, dataclass

from nonebot import logger
from maimai_py import (
    Song,
    Score,
    FCType,
    FSType,
    RateType,
    SongType,
    LevelIndex,
    PlayerBests,
    ScoreExtend,
    SongDifficulty,
    current_version_jp,
)
from maimai_py.utils import ScoreCoefficient

from .songs import song_service
from .ext.net import NetRecord, NetCredentials, MaimaiNetClient
from ..constants import normalize_text

DX_ID_OFFSET = 10000  # Score.id 的 DX 谱面偏移（maimai_py 约定，与水鱼/落雪返回一致）

_DIFFICULTY_TO_LEVEL_INDEX: dict[str, LevelIndex] = {
    "basic": LevelIndex.BASIC,
    "advanced": LevelIndex.ADVANCED,
    "expert": LevelIndex.EXPERT,
    "master": LevelIndex.MASTER,
    "remaster": LevelIndex.ReMASTER,
}

_FC_TO_ENUM = {"fc": FCType.FC, "fcp": FCType.FCP, "ap": FCType.AP, "app": FCType.APP}
_FS_TO_ENUM = {
    "sync": FSType.SYNC,
    "fs": FSType.FS,
    "fsp": FSType.FSP,
    "fsd": FSType.FSD,
    "fsdp": FSType.FSDP,
}

# DX 星阈值（maimai_py MaimaiScores._calcuate_dx_star 同源）
_DX_STAR_THRESHOLDS = (0.85, 0.90, 0.93, 0.95, 0.97)

# B50 排序键（dx_rating, dx_score, achievements 降序，maimai_py 同口径）


class NetCooldownBook:
    """per-user NET 查询冷却（进程内存；重启清零，无需持久化）。"""

    def __init__(self) -> None:
        self._last: dict[tuple[str, str], float] = {}

    def try_acquire(self, platform: str, user_id: str) -> int:
        """尝试占用一次查询窗口；返回 0 = 成功，>0 = 距下次可查秒数。

        抓取失败由调用方 :meth:`release` 释放（失败不占冷却）。
        """
        from ..config import plugin_config

        cooldown = max(0, plugin_config.awmc_net_cooldown_minutes) * 60
        if cooldown <= 0:  # 部署侧显式置 0 关闭冷却
            return 0
        now = time.monotonic()
        last = self._last.get((platform, user_id), 0.0)
        remain = int(last + cooldown - now)
        if remain <= 0:
            self._last[(platform, user_id)] = now
            return 0
        return remain

    def release(self, platform: str, user_id: str) -> None:
        """释放冷却占用（抓取失败时调用，成功后保持冷却生效）。"""
        self._last.pop((platform, user_id), None)


@dataclass
class _Chart:
    """NET 记录匹配结果：日服视图谱面及其所属曲。"""

    song: Song
    diff: SongDifficulty


class NetScoreService:
    """日服 NET 查分：抓取 + JP 视图映射 + B50 组装。"""

    def __init__(self) -> None:
        self.cooldown = NetCooldownBook()
        # per-user 抓取冷却（成功生效、失败释放，core.score 分派处检查）
        self._title_index: tuple[str | None, dict[str, list[Song]]] = (None, {})
        # 标题索引随曲库指纹缓存：(fingerprint, {归一化标题: [Song]})

    # -- 抓取 ---------------------------------------------------------------

    async def fetch_records(self, binding) -> list[NetRecord]:
        """按绑定凭据登录 NET 并抓全曲记录（调用方负责冷却检查与错误转文案）。"""
        creds = NetCredentials(
            sega_id=binding.net_sega_id or "", password=binding.net_password or ""
        )
        client = MaimaiNetClient()
        try:
            await client.login(creds)
            return await client.fetch_music_records()
        finally:
            await client.aclose()

    # -- B50 组装 -----------------------------------------------------------

    async def build_b50(self, records: list[NetRecord]) -> PlayerBests:
        """NET 记录 → 日服 B50（纯组装，不触发抓取，供测试与未来复用）。"""
        index = await self._title_index_map()
        unmatched: list[str] = []
        scores: list[ScoreExtend] = []
        for record in records:
            chart = self._resolve_chart(index, record)
            if chart is None:
                unmatched.append(f"{record.title} ({record.type}/{record.difficulty})")
                continue
            scores.append(self._to_score_extend(chart, record))
        if unmatched:
            logger.warning(
                f"net-score：{len(unmatched)} 条 NET 记录未匹配到日服视图"
                f"（数据滞后或官方改名）：{unmatched[:10]}"
                f"{'…' if len(unmatched) > 10 else ''}"
            )
        return self._bests_of(scores)

    # -- 内部 ---------------------------------------------------------------

    async def _title_index_map(self) -> dict[str, list[Song]]:
        """日服视图标题索引：归一化标题 → Song 列表（随曲库指纹缓存）。"""
        from . import songdb

        fp = songdb.CURRENT_FINGERPRINT
        if self._title_index[0] == fp:
            return self._title_index[1]
        songs = await song_service.jp_all()
        index: dict[str, list[Song]] = {}
        for song in songs:
            index.setdefault(normalize_text(song.title), []).append(song)
        self._title_index = (fp, index)
        return index

    def _resolve_chart(
        self, index: dict[str, list[Song]], record: NetRecord
    ) -> _Chart | None:
        """NET 记录 → 日服谱面；同名曲先按谱面类型过滤，唯一命中才有效。"""
        song_type = SongType.DX if record.type == "dx" else SongType.STANDARD
        level_index = _DIFFICULTY_TO_LEVEL_INDEX.get(record.difficulty)
        if level_index is None:
            return None
        hits = []
        for song in index.get(normalize_text(record.title), []):
            diff = song.get_difficulty(song_type, level_index)
            if diff is not None:
                hits.append(_Chart(song=song, diff=diff))
        return hits[0] if len(hits) == 1 else None

    def _to_score_extend(self, chart: _Chart, record: NetRecord) -> ScoreExtend:
        """日服谱面 + NET 记录 → ScoreExtend（字段约定对齐 maimai_py Score 族）。"""
        song, diff = chart.song, chart.diff
        # Song 本身无 type（SD/DX 折叠在根 id 下，谱面类型从 NET 记录取）
        score_type = SongType.DX if record.type == "dx" else SongType.STANDARD
        ra = int(ScoreCoefficient(record.achievement).ra(diff.level_value))
        level_dx_score = (
            diff.tap_num
            + diff.hold_num
            + diff.slide_num
            + diff.break_num
            + diff.touch_num
        ) * 3
        # Score.id：SD = 曲 id；DX = 曲 id + 10000（水鱼/落雪返回口径，
        # 渲染层按 id % 10000 取封面、DX 补偏移外链）
        score = Score(
            id=song.id + (DX_ID_OFFSET if score_type == SongType.DX else 0),
            level=diff.level,
            level_index=_DIFFICULTY_TO_LEVEL_INDEX[record.difficulty],
            achievements=record.achievement,
            fc=_FC_TO_ENUM.get(record.fc) if record.fc else None,
            fs=_FS_TO_ENUM.get(record.fs) if record.fs else None,
            dx_score=record.dx_score,
            dx_rating=ra,
            play_count=None,
            play_time=None,
            rate=RateType._from_achievement(record.achievement),
            type=score_type,
        )
        return ScoreExtend(
            **{
                **asdict(score),
                "title": song.title,
                "level_value": diff.level_value,
                "level_dx_score": level_dx_score,
                "dx_star": self._dx_star(record.dx_score, level_dx_score),
                "version": diff.version,
            }
        )

    @staticmethod
    def _dx_star(dx_score: int | None, level_dx_score: int) -> int | None:
        if not dx_score or not level_dx_score:
            return None
        ratio = dx_score / level_dx_score
        for i, threshold in enumerate(_DX_STAR_THRESHOLDS):
            if ratio < threshold:
                return i
        return 5

    @staticmethod
    def _bests_of(scores: list[ScoreExtend]) -> PlayerBests:
        """按 maimai_py MaimaiScores.configure 同口径组装 b35/b15 与总 rating。"""
        b35: list[ScoreExtend] = []
        b15: list[ScoreExtend] = []
        current = current_version_jp.value
        for score in scores:
            if score.type not in (SongType.STANDARD, SongType.DX):
                continue  # 宴谱不参与 rating（本模块抓取不含宴谱页，保险再滤一次）
            (b15 if score.version >= current else b35).append(score)

        def _key(s: ScoreExtend):
            return (s.dx_rating or 0, s.dx_score or 0, s.achievements or 0)

        b35.sort(key=_key, reverse=True)
        b15.sort(key=_key, reverse=True)
        b35, b15 = b35[:35], b15[:15]
        rating_b35 = int(sum(s.dx_rating or 0 for s in b35))
        rating_b15 = int(sum(s.dx_rating or 0 for s in b15))
        return PlayerBests(
            rating=rating_b35 + rating_b15,
            rating_b35=rating_b35,
            rating_b15=rating_b15,
            scores_b35=b35,
            scores_b15=b15,
        )


net_score_service = NetScoreService()
"""日服 NET 查分服务单例。"""
