"""曲库服务：预热/定时刷新/查询代理/随机/别名索引/快照降级。

- 全部查询接口都是异步可等待的（``ensure_loaded`` 之后再读缓存）；
- 别名索引由本服务自维护（柚子别名 + 本地 DB 别名热合并），
  不依赖 maimai-py 内部 aliases 缓存，断网降级时依然可用；
- 每次刷新成功后把曲库+别名快照例行写入 ``kv_cache``（store），
  启动拉取失败时降级为上次快照，不阻塞 bot 启动。
"""

import time
import random as _random
import asyncio
from enum import Enum
from typing import Any
from datetime import datetime
from dataclasses import asdict, fields

from nonebot import logger, get_driver
from maimai_py import Song, Genre, SongType, LevelIndex, MaimaiSongs, SongDifficulty
from maimai_py.models import SongDifficultyUtage
from nonebot_plugin_apscheduler import scheduler

from . import store, songdb
from .client import client, lxns_provider, yuzu_provider, divingfish_provider
from ..config import plugin_config
from .provider import AwmcSongProvider, AwmcAliasProvider
from ..constants import normalize_text, strip_chart_prefix

SNAPSHOT_KEY = "songs_snapshot"
CN_POLL_STATE_KEY = "cn_poll_state"

# 曲库数据源组合（2026-09-22 数据入口统一，设计稿 §5.4）：
# 曲库由规范表构造（CN 列；国服定数按 §5.3 推导、version_cn 为空的组不可见，
# 等价于原落雪 disabled 过滤）；别名走柚子；配置了水鱼开发者 token 时附带
# 曲线数据。规范表重建后 provider 指纹变化 → maimai_py 自动重建缓存。
_SONG_PROVIDER = AwmcSongProvider(scope="cn")
_ALIAS_PROVIDER = AwmcAliasProvider(yuzu_provider, lxns_provider)
_CURVE_PROVIDER = (
    divingfish_provider if plugin_config.awmc_divingfish_developer_token else None
)


def _json_default(obj: Any) -> Any:
    if isinstance(obj, Enum):
        return obj.value
    raise TypeError(f"未知的可序列化类型：{type(obj)!r}")


def _convert_curve_dict(curve: dict[str, Any] | None) -> None:
    """就地转换 asdict 出的 CurveObject：sample 字典的键是 RateType/FCType 枚举。"""
    if curve is None:
        return
    curve["rate_sample_size"] = {
        k.value: v for k, v in curve["rate_sample_size"].items()
    }
    curve["fc_sample_size"] = {k.value: v for k, v in curve["fc_sample_size"].items()}


def song_to_dict(song: Song) -> dict[str, Any]:
    """Song → 可 JSON 序列化的 dict（枚举转值）。"""
    data = asdict(song)
    data["genre"] = song.genre.value
    for diff in data["difficulties"]["standard"] + data["difficulties"]["dx"]:
        diff["type"] = diff["type"].value
        diff["level_index"] = diff["level_index"].value
        _convert_curve_dict(diff["curve"])
    data["difficulties"]["utage"] = []
    for u in song.get_difficulties(SongType.UTAGE):
        ud = asdict(u)
        ud["type"] = u.type.value
        ud["level_index"] = u.level_index.value
        _convert_curve_dict(ud["curve"])
        data["difficulties"]["utage"].append(ud)
    data["aliases"] = song.aliases or []
    return data


def _diff_from_dict(d: dict[str, Any]) -> SongDifficulty:
    from maimai_py.models import SongDifficultyUtage

    cls = SongDifficultyUtage if d["type"] == SongType.UTAGE.value else SongDifficulty
    kwargs = {
        f.name: d[f.name] for f in fields(cls) if f.name in d and f.name != "curve"
    }
    kwargs["type"] = SongType(d["type"])
    kwargs["level_index"] = LevelIndex(d["level_index"])
    kwargs["curve"] = None  # 快照仅用于查询降级，曲线数据不回填
    # 双人谱物量不参与快照回填（仅降级查询用）
    if "buddy_notes" in kwargs:
        kwargs["buddy_notes"] = None
    return cls(**kwargs)


def song_from_dict(d: dict[str, Any]) -> Song:
    """dict → Song（快照反序列化；宴会谱重建为 SongDifficultyUtage）。"""
    diffs = d["difficulties"]
    song = Song(
        id=d["id"],
        title=d["title"],
        artist=d["artist"],
        genre=Genre(d["genre"]),
        bpm=d["bpm"],
        map=d.get("map"),
        version=d["version"],
        rights=d.get("rights"),
        aliases=d.get("aliases") or [],
        disabled=d.get("disabled", False),
        difficulties=None,  # type: ignore[arg-type]
    )
    from maimai_py.models import SongDifficulties, SongDifficultyUtage

    song.difficulties = SongDifficulties(
        standard=[_diff_from_dict(x) for x in diffs["standard"]],
        dx=[_diff_from_dict(x) for x in diffs["dx"]],
        utage=[
            x
            for x in (_diff_from_dict(y) for y in diffs["utage"])
            if isinstance(x, SongDifficultyUtage)
        ],  # type: ignore[arg-type]
    )
    return song


def prefer_type_from_raw_id(raw_id: int) -> SongType | None:
    """查分器 id 形状 → 卡片主类型：≤4 位 SD、5 位 DX；6 位宴不指定偏好。

    数字查询（minfo/id 指令等）按用户输入的 id 形状推断其指向的谱面类型；
    供 music_query / score_query 共用（原 music_query._prefer_from_raw_id 下沉）。
    """
    if raw_id > 99999:
        return None
    return SongType.DX if raw_id > 9999 else SongType.STANDARD


class SongService:
    """曲库服务单例（见模块级 ``song_service``）。"""

    def __init__(self) -> None:
        self._ready = asyncio.Event()
        # 别名索引：归一化别名 → 曲目 id 集合（三源合并，同名可对应多曲）
        self._alias_index: dict[str, set[int]] = {}
        # 标题精确索引（小写）→ song_id
        self._title_index: dict[str, int] = {}
        # 宴谱汉字集（前缀剥离用）：运行时全部宴谱的 kanji 及其简体形态
        self._utage_kanji: set[str] = set()
        # 日服视图缓存（国服查不到时的 fallback，Q32）：根 id → 日服 Song
        self._alias_provider = _ALIAS_PROVIDER
        self._jp_view: dict[int, Song] = {}
        self._jp_fingerprint: str | None = None

    # -- 生命周期 ----------------------------------------------------------

    async def ensure_loaded(self) -> MaimaiSongs:
        """等待曲库就绪并返回 MaimaiSongs 包装（查询均先调用本方法）。"""
        await self._ready.wait()
        return await client.songs()

    @property
    def loaded(self) -> bool:
        return self._ready.is_set()

    async def load(self) -> bool:
        """加载曲库（规范表构造 + 别名）。成功后例行写快照；失败降级快照。"""
        started = time.monotonic()
        try:
            songs = await client.songs(
                provider=_SONG_PROVIDER, alias_provider=_ALIAS_PROVIDER
            )
            if not await songs.get_all():
                # 规范表未初始化（离线首启）等场景：空数据按失败处理走降级
                raise RuntimeError("曲库数据源返回为空")
        except Exception:
            logger.exception(f"曲库拉取失败（{time.monotonic() - started:.1f}s）")
            if not self._ready.is_set():
                if await self._load_snapshot():
                    logger.warning("已降级使用上次曲库快照（仅支持查询类指令）")
                else:
                    logger.error("曲库不可用且无快照，查询类指令将不可用")
            return False
        await self._apply(songs)
        await self._write_snapshot(songs)
        # 日服视图与国服视图口径并列展示（CN 视图不含仅日服曲目）
        jp_count = len(await self._jp_songs_map())
        logger.info(
            f"曲库加载完成：国服 {len(await songs.get_all())} 首，"
            f"日服 {jp_count} 首（耗时 {time.monotonic() - started:.1f}s）"
        )
        return True

    async def refresh(self) -> bool:
        """刷新曲库：规范表指纹较上次加载有变化时 maimai_py 自动重建缓存。"""
        return await self.load()

    async def _apply(self, songs: MaimaiSongs) -> None:
        """刷新别名/标题索引并置就绪。"""
        await self._apply_to_cache(await songs.get_all())

    async def reload_alias_index(self) -> None:
        """本地别名变更后热更新（只重读本地部分，避免重拉曲库）。"""
        for la in await store.get_local_aliases():
            self._alias_index.setdefault(la.alias.lower(), set()).add(la.song_id)

    async def inject(self, songs: list[Song]) -> None:
        """直接注入曲目数据并置就绪（测试与本地快照恢复共用；不触发网络）。"""
        cache = client._cache
        await cache.set("provider", "inject", ttl=client._cache_ttl, namespace="songs")
        await cache.set("ids", [s.id for s in songs], namespace="songs")
        await cache.multi_set(iter((s.id, s) for s in songs), namespace="songs")
        await cache.multi_set(iter((s.title, s.id) for s in songs), namespace="tracks")
        await self._seed_versions(songs)
        await self._apply_to_cache(songs)

    async def _seed_versions(self, all_songs: list[Song]) -> None:
        """种子化 versions 缓存（B35/B15 拆分与牌子进度依赖）。"""
        versions = {
            f"{s.id} {d.type} {d.level_index}": d.version
            for s in all_songs
            for d in s.get_difficulties()
        }
        await client._cache.set("versions", versions, namespace="songs")

    async def _apply_to_cache(self, all_songs: list[Song]) -> None:
        index: dict[str, set[int]] = {}
        titles: dict[str, int] = {}
        utage_kanji: set[str] = set()
        for song in all_songs:
            titles[song.title.lower()] = song.id
            for alias in song.aliases or []:
                index.setdefault(normalize_text(alias), set()).add(song.id)
            for diff in song.get_difficulties(SongType.UTAGE):
                if isinstance(diff, SongDifficultyUtage) and diff.kanji:
                    utage_kanji.add(diff.kanji)
                    utage_kanji.add(normalize_text(diff.kanji))  # 简体形态
        for la in await store.get_local_aliases():
            index.setdefault(normalize_text(la.alias), set()).add(la.song_id)
        self._alias_index = index
        self._title_index = titles
        self._utage_kanji = utage_kanji
        self._ready.set()

    # -- 快照 --------------------------------------------------------------

    async def _write_snapshot(self, songs: MaimaiSongs) -> None:
        try:
            all_songs = await songs.get_all()
            aliases = {s.id: (s.aliases or []) for s in all_songs}
            await store.kv_set(
                SNAPSHOT_KEY,
                {
                    "songs": [song_to_dict(s) for s in all_songs],
                    "aliases": aliases,
                },
            )
        except Exception:
            logger.exception("曲库快照写入失败（不影响运行）")

    async def _load_snapshot(self) -> bool:
        try:
            snap = await store.kv_get(SNAPSHOT_KEY)
            if not snap:
                return False
            all_songs = [song_from_dict(d) for d in snap["songs"]]
            cache = client._cache
            await cache.set(
                "provider", "snapshot", ttl=client._cache_ttl, namespace="songs"
            )
            await cache.set("ids", [s.id for s in all_songs], namespace="songs")
            await cache.multi_set(iter((s.id, s) for s in all_songs), namespace="songs")
            await cache.multi_set(
                iter((s.title, s.id) for s in all_songs), namespace="tracks"
            )
            await self._apply_to_cache(all_songs)
            return True
        except Exception:
            logger.exception("曲库快照恢复失败")
            return False

    # -- 查询代理 ----------------------------------------------------------

    async def get_all(self, include_disabled: bool = False) -> list[Song]:
        songs = await (await self.ensure_loaded()).get_all()
        return [s for s in songs if include_disabled or not s.disabled]

    async def by_id(self, song_id: int) -> Song | None:
        return await (await self.ensure_loaded()).by_id(song_id)

    async def by_title(self, title: str) -> Song | None:
        return await (await self.ensure_loaded()).by_title(title)

    async def by_title_fuzzy(self, title: str) -> list[Song]:
        """标题子串匹配（大小写不敏感），按 id 升序。"""
        kw = title.lower()
        return sorted(
            (s for s in await self.get_all() if kw in s.title.lower()),
            key=lambda s: s.id,
        )

    async def by_alias(self, alias: str) -> list[Song]:
        """按别名查曲（柚子 + 落雪 + 本地合并视图）。"""
        songs, _ = await self.by_alias_detail(alias)
        return songs

    async def _jp_songs_map(self) -> dict[int, Song]:
        """日服视图缓存：根 id → 日服 Song（规范表指纹失效时重建）。"""
        fp = songdb.CURRENT_FINGERPRINT
        if self._jp_view and self._jp_fingerprint == fp:
            return self._jp_view
        state = await songdb.State.load()
        self._jp_view = {s.id: s for s in songdb.all_songs(state, "jp")}
        self._jp_fingerprint = fp
        return self._jp_view

    async def jp_by_title_fuzzy(self, title: str) -> list[Song]:
        """日服视图标题子串匹配（国服查歌 fallback，大小写不敏感，按 id 升序）。"""
        jp = await self._jp_songs_map()
        kw = title.lower()
        return sorted(
            (s for s in jp.values() if kw in s.title.lower()),
            key=lambda s: s.id,
        )

    async def jp_by_artist(self, artist: str) -> list[Song]:
        """日服视图曲师查歌（大小写不敏感精确匹配）。"""
        kw = artist.lower()
        return [
            s for s in (await self._jp_songs_map()).values() if s.artist.lower() == kw
        ]

    async def jp_by_bpm(self, minimum: float, maximum: float) -> list[Song]:
        """日服视图 BPM 查歌（闭区间）。"""
        return [
            s
            for s in (await self._jp_songs_map()).values()
            if s.bpm is not None and minimum <= float(s.bpm) <= maximum
        ]

    async def jp_by_level_value(self, min_ds: float, max_ds: float) -> list[Song]:
        """日服视图定数查歌（日服定数口径，任意谱面落在 [min, max] 闭区间）。"""
        return [
            s
            for s in (await self._jp_songs_map()).values()
            if any(min_ds <= d.level_value <= max_ds for d in s.get_difficulties())
        ]

    async def jp_by_note_designer(self, designer: str) -> list[Song]:
        """日服视图谱师查歌（任意谱面谱师名匹配，大小写不敏感）。"""
        kw = designer.lower()
        return [
            s
            for s in (await self._jp_songs_map()).values()
            if any(d.note_designer.lower() == kw for d in s.get_difficulties())
        ]

    async def jp_by_alias_detail(
        self, alias: str
    ) -> tuple[list[Song], tuple[str, str, str] | None]:
        """日服视图按别名查曲（国服查不到时的 fallback，Q32）。

        别名库用 provider 的完整合并视图（含仅日服条目），匹配语义与国服侧
        一致（归一化 + 单层谱面前后缀剥离，宴谱汉字取日服视图自身）。
        """
        await self.ensure_loaded()
        jp = await self._jp_songs_map()
        if not jp:
            return [], None
        lib = getattr(self._alias_provider, "last_merged", None)
        if not lib:  # 尚未拉取成功：回退 song_alias 快照
            lib = await store.load_song_aliases(["yuzu", "lxns"])
        index: dict[str, set[int]] = {}
        for sid, aliases in lib.items():
            for a in aliases:
                index.setdefault(normalize_text(a), set()).add(sid)
        jp_kanji: set[str] = set()
        for song in jp.values():
            for diff in song.get_difficulties(SongType.UTAGE):
                if isinstance(diff, SongDifficultyUtage) and diff.kanji:
                    jp_kanji.add(diff.kanji)
                    jp_kanji.add(normalize_text(diff.kanji))
        key = normalize_text(alias)
        ids = index.get(key, set())
        strip_info = None
        if not ids:
            stripped = strip_chart_prefix(alias, extra_prefixes=jp_kanji)
            if stripped:
                ids = index.get(normalize_text(stripped[0]), set())
                strip_info = stripped
        return [jp[i] for i in sorted(ids) if i in jp], strip_info

    async def jp_by_id(self, song_id: int) -> Song | None:
        """日服视图按根 id 取曲（id 搜索的 fallback）。"""
        jp = await self._jp_songs_map()
        return jp.get(song_id % 10000)

    async def by_alias_detail(
        self, alias: str
    ) -> tuple[list[Song], tuple[str, str, str] | None]:
        """按别名查曲并返回剥离信息。

        返回 ``(曲目, (剥离后别名, 命中词, 前缀|"suffix") | None)``。精确未命中时
        剥离**一层**谱面类型前缀/后缀（前缀 dx/标准/标/宴/该曲宴谱汉字含
        [汉字] 括号形式，后缀 dx/标准，简繁归一）重查——别名库已去前缀按根 id
        合并，带前后缀的社区惯用写法（dx圣诞 / 标39 / 协love you / 牛奶猫dx）
        由此兜底命中（Q31）。
        """
        await self.ensure_loaded()
        key = normalize_text(alias)
        ids = self._alias_index.get(key, set())
        if not ids:
            stripped = strip_chart_prefix(alias, extra_prefixes=self._utage_kanji)
            if stripped:
                ids = self._alias_index.get(normalize_text(stripped[0]), set())
                if ids:
                    return await self._songs_of(ids), stripped
        return await self._songs_of(ids), None

    async def _songs_of(self, ids: set[int]) -> list[Song]:
        result: list[Song] = []
        for song_id in sorted(ids):
            if song := await self.by_id(song_id):
                result.append(song)
        return result

    async def by_keywords(self, keywords: str) -> list[Song]:
        result = await (await self.ensure_loaded()).by_keywords(keywords)
        return [s for s in result if not s.disabled]

    async def by_artist(self, artist: str) -> list[Song]:
        """曲师查歌（库实现区分大小写，这里归一为大小写不敏感）。"""
        kw = artist.lower()
        return [s for s in await self.get_all() if s.artist.lower() == kw]

    async def by_bpm(self, minimum: float, maximum: float) -> list[Song]:
        result = await (await self.ensure_loaded()).by_bpm(int(minimum), int(maximum))
        return [s for s in result if not s.disabled]

    async def by_genre(self, genre: Genre) -> list[Song]:
        result = await (await self.ensure_loaded()).by_genre(genre)
        return [s for s in result if not s.disabled]

    async def by_versions(self, version: Any) -> list[Song]:
        result = await (await self.ensure_loaded()).by_versions(version)
        return [s for s in result if not s.disabled]

    async def by_level_value(self, min_ds: float, max_ds: float) -> list[Song]:
        """定数查歌：任意谱面定数落在 [min, max] 闭区间。"""
        return [
            s
            for s in await self.get_all()
            if any(min_ds <= d.level_value <= max_ds for d in s.get_difficulties())
        ]

    async def by_note_designer(self, designer: str) -> list[Song]:
        """谱师查歌（任意谱面谱师名匹配，大小写不敏感）。"""
        kw = designer.lower()
        return [
            s
            for s in await self.get_all()
            if any(d.note_designer.lower() == kw for d in s.get_difficulties())
        ]

    async def random(
        self,
        *,
        song_type: SongType | None = None,
        genre: Genre | None = None,
        level: str | None = None,
        level_index: LevelIndex | None = None,
        exclude_utage: bool = True,
    ) -> tuple[Song, SongDifficulty] | None:
        """随机谱面：按类型/分类/等级过滤后随机选择（maimai-py 无随机接口）。"""
        candidates: list[tuple[Song, SongDifficulty]] = []
        for song in await self.get_all():
            if genre is not None and song.genre != genre:
                continue
            for diff in song.get_difficulties():
                if diff.type == SongType.UTAGE:
                    if exclude_utage:
                        continue
                elif song_type is not None and diff.type != song_type:
                    continue
                if level is not None and diff.level != level:
                    continue
                if level_index is not None and diff.level_index != level_index:
                    continue
                candidates.append((song, diff))
        return _random.choice(candidates) if candidates else None

    def alias_ids_of(self, alias: str) -> set[int]:
        """同步读别名索引（已就绪前提下）。"""
        return self._alias_index.get(alias.lower(), set())

    @staticmethod
    def available_ids(song: Song) -> list[int]:
        """曲目的全部**可用** id（升序），按谱面组实际存在与否决定：

        - SD id（=根 id）：仅有标准谱组时纳入（只有 DX 谱的曲不含根 id）；
        - DX id（根 id+10000）：仅有 DX 谱组时纳入；
        - 宴谱机台 id：逐谱纳入（``SongDifficultyUtage.diff_id``，
          100000 + level_id * 10000 + 根 id）。
        """
        ids: list[int] = []
        if song.get_difficulties(SongType.STANDARD):
            ids.append(song.id)
        if song.get_difficulties(SongType.DX):
            ids.append(song.id + 10000)
        ids.extend(
            sorted(
                d.diff_id
                for d in song.get_difficulties(SongType.UTAGE)
                if isinstance(d, SongDifficultyUtage)
            )
        )
        return ids or [song.id]  # 无任何谱面的异常数据兜底，避免展示空 ID

    async def aliases_of(self, song_id: int) -> list[str] | None:
        """某曲目的全部别名（柚子 + 本地）；曲目不存在返回 None。

        标题本身不计入别名——剥前后缀后与歌名相同的形态同样与歌名重复
        （如「标准39」剥出的「39」），按归一化比对去除（大小写/全角/简繁）。
        """
        song = await self.by_id(song_id)
        if song is None:
            return None
        aliases = list(song.aliases or [])
        for la in await store.get_local_aliases():
            if la.song_id == song_id and la.alias not in aliases:
                aliases.append(la.alias)
        title_key = normalize_text(song.title)
        return [a for a in aliases if normalize_text(a) != title_key]


song_service = SongService()
"""曲库服务单例，全部子插件共享。"""


@get_driver().on_startup
async def _startup() -> None:
    await store.init_db()
    if not plugin_config.awmc_startup_tasks:
        logger.debug("awmc_startup_tasks=false，跳过曲库预热（测试环境）")
        return
    # 不阻塞启动：后台执行（模块持有强引用防任务被回收）
    global _load_task
    _load_task = asyncio.get_running_loop().create_task(_startup_load())


async def _startup_load() -> None:
    """启动链：冷启动（规范表为空先全量重建）→ 运行时加载（失败降级快照）。"""
    try:
        if await songdb.is_empty():
            logger.info("规范表为空，执行歌曲库冷启动全量重建（需要几分钟）……")
            await songdb.refresh_all(include_cn=True, include_jp=True)
    except Exception:
        logger.exception("歌曲库冷启动重建失败（继续尝试加载运行时）")
    await song_service.load()


_load_task: asyncio.Task | None = None


# ---------------------------------------------------------------------------
# 歌曲库调度：国服小时轮询 / 每日全量 / 自动预渲染 / SUPERUSER 通知（song-db-design §7）
# ---------------------------------------------------------------------------


async def jp_songs() -> list[Song]:
    """JP 视图：规范表日侧字段 → maimai_py 对象（§5.2，后续日服功能的数据入口）。

    与 CN 视图互不干扰（不写 ``songs`` 缓存命名空间）；规范表为空返回 []。
    """
    state = await songdb.State.load()
    return songdb.all_songs(state, "jp")


async def _notify_superusers(text: str) -> None:
    """跨适配器向全部 SUPERUSER 主动私聊推送（OB11 优先；失败记 debug 不影响流程）。"""
    from nonebot_plugin_alconna.uniseg import Target, UniMessage, SupportAdapter

    for user_id in get_driver().config.superusers:
        try:
            await UniMessage.text(text).send(
                target=Target.user(user_id, adapter=SupportAdapter.onebot11)
            )
        except Exception as e:  # 平台不支持/未连接等一律跳过
            logger.debug(f"更新通知发送失败（superuser={user_id}）：{e}")


async def _prerender_templates() -> str:
    """预渲染全部底图（core 实现，自动触发与 SUPERUSER 指令共用），返回结果描述。"""
    from .render import table_template

    rating_total, rating_failed = await table_template.refresh_all_rating_tables(
        song_service
    )
    plate_total, plate_failed = await table_template.refresh_all_plate_tables(
        song_service
    )
    return (
        f"定数表 {rating_total} 谱面次（失败 {len(rating_failed)}）"
        f"、完成表 {plate_total} 谱面次（失败 {len(plate_failed)}）"
    )


async def _ensure_templates() -> None:
    """底图缺失时的兜底预渲染（首启基线 / 每日兜底，§7.2）。"""
    from .render import table_template

    if not plugin_config.awmc_auto_templates:
        return
    rating_dir, plate_dir = (
        table_template.rating_table_dir(),
        table_template.plate_table_dir(),
    )

    def _empty(path) -> bool:
        return not path.exists() or not any(path.iterdir())

    if _empty(rating_dir) or _empty(plate_dir):
        try:
            await _prerender_templates()
        except Exception:
            logger.exception("底图兜底预渲染失败（保留现状，不阻断）")


def _poll_keys(items: list[dict]) -> set[songdb.DetectKey]:
    """轮询检测键：(song_id, kind)。

    - 排除宴：宴轮换频繁，且定数表/完成表底图均不含宴谱，轮换不构成更新事件；
    - 排除禁用条目：落雪对删除/下架曲打 disabled 留在列表，禁用即「在列→消失」。
    """
    keys: set[songdb.DetectKey] = set()
    for s in items:
        if s.get("disabled"):
            continue
        raw_id, base = int(s["id"]), int(s["id"]) % 10000
        if raw_id > 99999:
            continue
        diffs = s.get("difficulties", {})
        if diffs.get("standard"):
            keys.add((base, "sd"))
        if diffs.get("dx"):
            keys.add((base, "dx"))
    return keys


async def _hourly_cn_poll() -> None:
    """国服源小时轮询（§7.2）：ext 直连轻拉双源 → 双源交集判定更新。"""
    from .ext import lxns as ext_lxns
    from .ext import divingfish as ext_df

    try:
        light = await ext_lxns.fetch_song_list(notes=False)
        df = await ext_df.fetch_music_data()
    except Exception as e:
        logger.warning(f"国服轮询拉取失败，跳过本次检测：{e}")
        return
    lx_keys = _poll_keys(light.get("songs", []))
    df_keys: set[songdb.DetectKey] = set()
    for d in df:  # 水鱼 id 形状即类型：≤4 位 sd、5 位 dx（宴不触发）
        i = int(d["id"])
        if i <= 9999:
            df_keys.add((i, "sd"))
        elif i <= 99999:
            df_keys.add((i % 10000, "dx"))
    titles = {int(s["id"]) % 10000: s.get("title", "") for s in light.get("songs", [])}
    known = {
        (int(sid), kind)
        for sid, kind in (await store.kv_get(CN_POLL_STATE_KEY) or {}).get("known", [])
    }
    detected = songdb.detect_cn_update(known, lx_keys, df_keys)
    await store.kv_set(
        CN_POLL_STATE_KEY,
        {
            "known": sorted([sid, kind] for sid, kind in lx_keys | df_keys),
            "last_poll": datetime.now().isoformat(timespec="seconds"),
        },
    )
    if detected is None:
        await _ensure_templates()  # 首启仅建基线；底图缺失时顺带预渲染一次
        return
    added, removed = detected
    logger.info(f"检测到国服曲库更新：新增 {sorted(added)}，下架 {sorted(removed)}")
    await _on_cn_update({sid for sid, _ in added}, {sid for sid, _ in removed}, titles)


async def _on_cn_update(
    added: set[int], removed: set[int], titles: dict[int, str]
) -> None:
    """更新确认后的串行动作（§7.3）：回填规范表 → 刷运行时 → 预渲染 → 通知。"""
    try:
        result = await songdb.refresh_all(include_cn=True, include_jp=False)
    except Exception:
        logger.exception("规范表国服回填失败（继续后续动作）")
        result = {}
    for warning in result.get("warnings", [])[:20]:
        logger.warning(f"songdb: {warning}")
    if not await song_service.refresh():
        logger.error("国服更新后运行时曲库刷新失败（保留旧运行时）")
    template_msg = "已跳过（awmc_auto_templates=false）"
    if plugin_config.awmc_auto_templates:
        try:
            template_msg = await _prerender_templates()
        except Exception:
            logger.exception("国服更新自动预渲染失败（保留旧底图，不阻断曲库刷新）")
            template_msg = "失败（保留旧底图）"
    if plugin_config.awmc_update_notify:
        new_names = "、".join(f"「{titles.get(i, i)}」" for i in sorted(added)) or "无"
        gone_names = (
            "、".join(f"「{titles.get(i, i)}」" for i in sorted(removed)) or "无"
        )
        await _notify_superusers(
            f"检测到国服曲库更新：新增 {len(added)} 首（{new_names}），"
            f"下架 {len(removed)} 首（{gone_names}）；底图重建：{template_msg}"
        )


async def full_refresh() -> dict:
    """歌曲库全量管线（每日任务与 SUPERUSER 手动刷新共用）：四源重建 →
    外部源底图 → 兜底 → 运行时刷新。返回重建统计；失败返回 {}（已记日志）。"""
    extra: dict = {}
    result: dict = {}
    try:
        result = await songdb.refresh_all(include_cn=True, include_jp=True)
        logger.info(
            f"歌曲库全量刷新完成：{result['songs']} 曲 / {result['groups']} 组 / "
            f"{result['charts']} 谱面 / {result['level_points']} 定数变化点，"
            f"删除 {result['removed']} 曲，国服当前版本 {result['cn_current_version']}"
        )
        for warning in result.get("warnings", [])[:20]:
            logger.warning(f"songdb: {warning}")
        extra = result.get("extra") or {}
    except Exception:
        logger.exception("歌曲库全量刷新失败（不影响曲库运行时）")
    if extra.get("changed"):
        # 外部源已在 refresh_all 内应用（含新曲创建），此处只负责底图重建
        logger.info(f"外部补充源有变化，重建底图（{extra}）")
        try:
            await _prerender_templates()
        except Exception:
            logger.exception("外部补充源触发的预渲染失败（保留旧底图，不阻塞）")
    await _ensure_templates()
    # 规范表已可能变化：指纹较上次加载不同时 maimai_py 自动重建运行时缓存
    await song_service.refresh()
    return result


async def _daily_songdb() -> None:
    """每日 4 点歌曲库全量（§7.1 ⑥⑦⑧）：日侧源 + CN 回填 + 外部源 + 底图兜底。"""
    await full_refresh()


scheduler.add_job(_daily_songdb, "cron", hour=4, minute=5)
"""每日 4:05 执行歌曲库全量管线（四源重建 → 运行时刷新 → 外部源 → 底图兜底）。"""

if plugin_config.awmc_cn_poll_minutes > 0:
    scheduler.add_job(
        _hourly_cn_poll,
        "interval",
        minutes=plugin_config.awmc_cn_poll_minutes,
        id="awmc_cn_poll",
    )
    """国服源小时轮询（awmc_cn_poll_minutes=0 时禁用）。"""
