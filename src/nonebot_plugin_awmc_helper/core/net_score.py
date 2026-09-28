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
from maimai_py.maimai import MaimaiScores

from . import store
from .songs import song_service
from .ext.net import NetPlayer, NetRecord, NetCredentials, MaimaiNetClient
from ..constants import normalize_text

DX_ID_OFFSET = 10000  # Score.id 的 DX 谱面偏移（maimai_py 约定，与水鱼/落雪返回一致）

_DIFFICULTY_TO_LEVEL_INDEX: dict[str, LevelIndex] = {
    "basic": LevelIndex.BASIC,
    "advanced": LevelIndex.ADVANCED,
    "expert": LevelIndex.EXPERT,
    "master": LevelIndex.MASTER,
    "remaster": LevelIndex.ReMASTER,
}


# NET 记录的 fc/fs 字符串（fc/fcp/ap/app、sync/fs/fsp/fsd/fsdp）本就是
# maimai_py 枚举名的小写形式，直接按名转枚举（与上游 providers/lxns.py 同口径）
def _fc_of(record: NetRecord) -> FCType | None:
    if not record.fc:
        return None
    try:
        return FCType[record.fc.upper()]
    except KeyError:
        return None


def _fs_of(record: NetRecord) -> FSType | None:
    if not record.fs:
        return None
    try:
        return FSType[record.fs.upper()]
    except KeyError:
        return None


# 抓取失败后的短退避（秒）：窗口缓存由成功抓取填充，失败不占窗口，
# 但也不允许立刻重试轰炸官方（凭据错误连续重试是最典型场景）
FETCH_FAIL_BACKOFF_SECONDS = 60


class NetScoreError(Exception):
    """NET 查分业务错误（message 面向用户；score 层统一转 UserScoreError）。"""


@dataclass
class _Chart:
    """NET 记录匹配结果：日服视图谱面及其所属曲。"""

    song: Song
    diff: SongDifficulty


class NetScoreService:
    """日服 NET 查分：抓取 + JP 视图映射 + 组装 + 窗口缓存。

    交互模型（2026-09-26 重设计）：一次抓取拿到全量成绩后，在冷却窗口内
    b50 / minfo 等指令共享同一份组装结果（0 请求秒回）——即冷却语义是
    「两次**抓取**的最小间隔」而非「两次查询的间隔」。进程内存态，重启清零。
    """

    def __init__(self) -> None:
        self._window_cache: dict[
            tuple[str, str], tuple[float, list[ScoreExtend], NetPlayer | None]
        ] = {}
        # (platform, user_id) → (fetched_at, 组装后的全量成绩, 登录时抓到的首页身份)
        self._fail_until: dict[tuple[str, str], float] = {}
        # 抓取失败短退避，窗口内重试防轰炸
        self._title_index: tuple[str | None, dict[str, list[Song]]] = (None, {})
        # 标题索引随曲库指纹缓存：(fingerprint, {归一化标题: [Song]})

    def _window(self) -> int:
        from ..config import plugin_config

        return max(0, plugin_config.awmc_net_cooldown_minutes) * 60

    @staticmethod
    def _key(binding) -> tuple[str, str]:
        return (binding.platform, binding.user_id)

    def needs_fetch(self, binding) -> bool:
        """窗口内是否需要真实抓取（handler 据此先发「正在抓取」提示）。"""
        window = self._window()
        if window <= 0:
            return True
        entry = self._window_cache.get(self._key(binding))
        return entry is None or time.monotonic() - entry[0] >= window

    # -- 抓取与组装 -----------------------------------------------------------

    async def fetch_records(self, binding) -> tuple[list[NetRecord], NetPlayer | None]:
        """按绑定凭据登录 NET 并抓全曲记录 + 首页身份（错误透传 ext 层语义）。

        身份来自登录流的最后一跳 home/ 页（client.player），与成绩同一会话
        零额外请求；页面改版导致身份块缺失时为 None，不阻塞成绩组装。
        """
        creds = NetCredentials(
            sega_id=binding.net_sega_id or "", password=binding.net_password or ""
        )
        client = MaimaiNetClient()
        try:
            await client.login(creds)
            return await client.fetch_music_records(), client.player
        finally:
            await client.aclose()

    def player_of(self, binding) -> NetPlayer | None:
        """窗口内登录时抓到的首页身份（未抓取过/窗口清空返回 None）。"""
        entry = self._window_cache.get(self._key(binding))
        return entry[2] if entry is not None else None

    async def _merge_nameplate(self, binding, player: NetPlayer | None) -> None:
        """装备名牌 URL 持久缓存：抓到自定义即更新，抓不到回填上次结果。

        NET 收藏品区间歇性 302（实测成功率低），不持久化的话名牌会在
        「真实/缺省」间抖动；kv_cache 键按绑定隔离，玩家换名牌后随下一次
        抓取成功自动更新。确认装备「デフォルト」框（is_default 态）时反向
        写空串，防止更早缓存的自定义名牌在默认框时代复活。尽力而为：
        缓存层异常不影响查询主链路。
        """
        if player is None:
            return
        try:
            key = f"net_nameplate:{binding.platform}:{binding.user_id}"
            if player.nameplate_url:
                if await store.kv_get(key) != player.nameplate_url:
                    await store.kv_set(key, player.nameplate_url)
            elif player.nameplate_is_default:
                if await store.kv_get(key):
                    await store.kv_set(key, "")
            else:
                cached = await store.kv_get(key)
                if isinstance(cached, str) and cached:
                    player.nameplate_url = cached
        except Exception as e:
            logger.debug(f"net-score：名牌缓存读写失败（忽略）：{e!r}")

    async def get_scores(self, binding) -> tuple[list[ScoreExtend], bool]:
        """窗口内全量成绩（缓存优先）；返回 (scores, from_cache)。

        缓存未命中时真实抓取：失败进入短退避（NetError 等异常原样透传），
        成功写窗口缓存并清除退避标记。
        """
        key = self._key(binding)
        window = self._window()
        entry = self._window_cache.get(key)
        if window > 0 and entry is not None and time.monotonic() - entry[0] < window:
            return entry[1], True
        until = self._fail_until.get(key)
        if until is not None and time.monotonic() < until:
            remain = int(until - time.monotonic()) + 1
            raise NetScoreError(f"日服 NET 刚刚查询失败，请约 {remain} 秒后再重试")
        try:
            records, player = await self.fetch_records(binding)
        except Exception:
            self._fail_until[key] = time.monotonic() + FETCH_FAIL_BACKOFF_SECONDS
            raise
        await self._merge_nameplate(binding, player)
        scores = await self.assemble(records)
        if window > 0:
            self._window_cache[key] = (time.monotonic(), scores, player)
        else:
            self._window_cache.pop(key, None)
        return scores, False

    async def assemble(self, records: list[NetRecord]) -> list[ScoreExtend]:
        """NET 记录 → 组装成绩（JP 视图匹配 + ra/rate/DX 星；未匹配 log warning）。"""
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
        return scores

    # -- 指令接口（core.score 分派；窗口缓存内 0 请求） ------------------------

    async def get_b50(self, binding) -> PlayerBests:
        """日服 B50（b35 + b15 与总 rating）。"""
        scores, _ = await self.get_scores(binding)
        return self._bests_of(scores)

    async def get_minfo_scores(self, binding, song: Song) -> list[ScoreExtend] | None:
        """该曲全部谱面成绩（未游玩返回 None）；随窗口缓存复用。"""
        scores, _ = await self.get_scores(binding)
        hit = [s for s in scores if s.id % DX_ID_OFFSET == song.id]
        return hit or None

    async def build_b50(self, records: list[NetRecord]) -> PlayerBests:
        """NET 记录 → 日服 B50（纯组装，不触发抓取，供测试与未来复用）。"""
        return self._bests_of(await self.assemble(records))

    # -- 内部 ---------------------------------------------------------------

    async def _title_index_map(self) -> dict[str, list[Song]]:
        """日服视图标题索引：归一化标题 → Song 列表（随曲库指纹缓存）。

        命中判定必须带「索引非空」：``CURRENT_FINGERPRINT`` 仅在规范表重建
        管线中赋值，重启后到下次重建前为 None——与初始空缓存的键相同，
        无非空保护会把空索引误判为命中（服务器实测 2026-09-26：35 条全部
        未匹配即此因；对齐 songs._jp_songs_map 的 ``and 非空`` 模式）。
        """
        from . import songdb

        fp = songdb.CURRENT_FINGERPRINT
        if self._title_index[1] and self._title_index[0] == fp:
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
            fc=_fc_of(record),
            fs=_fs_of(record),
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
        """DX 星：阈值表单源 maimai_py（``MaimaiScores._calcuate_dx_star``）。

        本地仅保留「dx_score 缺失返回 None」的空值回退（上游
        ``_get_extended`` 在调用侧也是同款 ``if score.dx_score else None``）。
        """
        if not dx_score or not level_dx_score:
            return None
        return MaimaiScores._calcuate_dx_star(dx_score, level_dx_score)

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
