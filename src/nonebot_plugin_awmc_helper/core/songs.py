"""曲库服务：预热/定时刷新/查询代理/随机/别名索引/快照降级。

- 全部查询接口都是异步可等待的（``ensure_loaded`` 之后再读缓存）；
- 别名索引由本服务自维护（柚子别名 + 本地 DB 别名热合并），
  不依赖 maimai-py 内部 aliases 缓存，断网降级时依然可用；
- 每次刷新成功后把曲库+别名快照例行写入 ``kv_cache``（store），
  启动拉取失败时降级为上次快照，不阻塞 bot 启动。
"""

import asyncio
from enum import Enum
from typing import Any
from dataclasses import asdict, fields

from nonebot import logger, get_driver
from maimai_py import Song, Genre, SongType, LevelIndex, MaimaiSongs, SongDifficulty
from nonebot_plugin_apscheduler import scheduler

from . import store
from .client import (
    client,
    lxns_provider,
    yuzu_provider,
    refresh_songs_cache,
)
from ..config import plugin_config

SNAPSHOT_KEY = "songs_snapshot"

# 曲库数据源组合：曲库走落雪（含物量），别名走柚子
_SONG_PROVIDER = lxns_provider
_ALIAS_PROVIDER = yuzu_provider


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
    from maimai_py.models import SongDifficulties

    song.difficulties = SongDifficulties(
        standard=[_diff_from_dict(x) for x in diffs["standard"]],
        dx=[_diff_from_dict(x) for x in diffs["dx"]],
        utage=[_diff_from_dict(x) for x in diffs["utage"]],
    )
    return song


class SongService:
    """曲库服务单例（见模块级 ``song_service``）。"""

    def __init__(self) -> None:
        self._ready = asyncio.Event()
        # 别名索引：alias(小写) → 曲目 id 集合（柚子别名 + 本地别名，同名可对应多曲）
        self._alias_index: dict[str, set[int]] = {}
        # 标题精确索引（小写）→ song_id
        self._title_index: dict[str, int] = {}

    # -- 生命周期 ----------------------------------------------------------

    async def ensure_loaded(self) -> MaimaiSongs:
        """等待曲库就绪并返回 MaimaiSongs 包装（查询均先调用本方法）。"""
        await self._ready.wait()
        return await client.songs()

    @property
    def loaded(self) -> bool:
        return self._ready.is_set()

    async def load(self) -> bool:
        """拉取曲库（含别名）。成功后例行写快照；失败降级快照。"""
        try:
            songs = await client.songs(
                provider=_SONG_PROVIDER, alias_provider=_ALIAS_PROVIDER
            )
        except Exception:
            logger.exception("曲库拉取失败")
            if not self._ready.is_set():
                if await self._load_snapshot():
                    logger.warning("已降级使用上次曲库快照（仅支持查询类指令）")
                else:
                    logger.error("曲库不可用且无快照，查询类指令将不可用")
            return False
        await self._apply(songs)
        await self._write_snapshot(songs)
        logger.info(f"曲库加载完成，共 {len(await songs.get_all())} 首")
        return True

    async def refresh(self) -> bool:
        """定时/手动刷新：删 provider 哈希键后以同组 provider 重拉。"""
        await refresh_songs_cache()
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
        await self._apply_to_cache(songs)

    async def _apply_to_cache(self, all_songs: list[Song]) -> None:
        index: dict[str, set[int]] = {}
        titles: dict[str, int] = {}
        for song in all_songs:
            titles[song.title.lower()] = song.id
            for alias in song.aliases or []:
                index.setdefault(alias.lower(), set()).add(song.id)
        for la in await store.get_local_aliases():
            index.setdefault(la.alias.lower(), set()).add(la.song_id)
        self._alias_index = index
        self._title_index = titles
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
        """按别名查曲（柚子 + 本地），返回命中的全部曲目（同一别名可对应多曲）。"""
        await self.ensure_loaded()
        result: list[Song] = []
        for song_id in sorted(self._alias_index.get(alias.lower(), set())):
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
        """随机谱面：按类型/分类/等级过滤后 random.choice（maimai-py 无随机接口）。"""
        import random as _random

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

    async def aliases_of(self, song_id: int) -> list[str] | None:
        """某曲目的全部别名（柚子 + 本地）；曲目不存在返回 None。"""
        song = await self.by_id(song_id)
        if song is None:
            return None
        aliases = list(song.aliases or [])
        for la in await store.get_local_aliases():
            if la.song_id == song_id and la.alias not in aliases:
                aliases.append(la.alias)
        # 标题本身不计入别名
        return [a for a in aliases if a.lower() != song.title.lower()]


song_service = SongService()
"""曲库服务单例，全部子插件共享。"""


@get_driver().on_startup
async def _startup() -> None:
    await store.init_db()
    if not plugin_config.awmc_startup_tasks:
        logger.debug("awmc_startup_tasks=false，跳过曲库预热（测试环境）")
        return
    # 不阻塞启动：后台拉取，失败自动降级快照（模块持有强引用防任务被回收）
    global _load_task
    _load_task = asyncio.get_running_loop().create_task(song_service.load())


_load_task: asyncio.Task | None = None


async def _daily_refresh() -> None:
    await song_service.refresh()


scheduler.add_job(_daily_refresh, "cron", hour=4, minute=0)
"""每日 4 点全量刷新曲库并写快照。"""
