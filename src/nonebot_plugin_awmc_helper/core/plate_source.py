"""柚子逐牌曲单缓存与国服牌子过筛（Q60 定案：只作查询时过筛，不进曲库管线）。

数据 = 柚子 ``maimaidx/music/get_plate``（Hoshino 完成表曲单同源），逐牌
白名单语义——同时承载「日服已删・国服在列」曲的排除（真代 ハッピーシンセ
サイザ 等 23 首）与「舞ReMASTER」ReM 槽名单（DX 时代追加的旧曲 ReM 不计
入舞/霸）。判定纯函数在 :mod:`core.plates`（:func:`plate_entry_allowed`），
本模块只管拉取/缓存/降级与库判牌对象包装：

- 缓存落 awmc.db kv_cache（规则 4 运行时数据统一 SQLite）；进程内 TTL
  （24h）外返回旧值并后台刷新（single-flight），冷启动读 DB 快照、无快照
  inline 拉取；
- 失败链全降级：任何拉取失败 → ``cn_plate_ids`` 返回 None = 不过筛（维持
  既有口径），不向查询路径上抛，负缓存 5 分钟防抖；
- 霸无专键 → 复用舞单（Hoshino ``VERSION_MAP["霸"] → "舞"`` 同款）。
"""

import time
import asyncio
import hashlib

from nonebot import logger
from maimai_py import LevelIndex, PlateObject

from . import store
from .plates import PLATE_FULL_SET_VERSIONS, norm_plate
from .ext.yuzu import get_plate_list
from ..constants import DX_ID_OFFSET

_KV_KEY = "yuzu_plate_list"
"""kv_cache 键：``{"fetched_at": 秒, "content": 柚子原始 content}``。"""

_REMASTER_KEY = "舞ReMASTER"
"""柚子键名（舞/霸 ReM 槽集基准）；其余键经 :func:`_normalize` 归一。"""

_TTL = 86400.0
"""进程内新鲜窗口（秒）：牌单变化频率为月级、柚子维护滞后天级，24h 足够。"""

_FAIL_RETRY = 300.0
"""拉取失败负缓存窗口（秒）：失败后短时间内不重复 inline 拉取。"""

_lock = asyncio.Lock()
_cache: dict | None = None
"""``{"ids": {版本字: frozenset}, "remaster": frozenset, "fetched_at": 秒}``。"""

_fail_ts = 0.0
_warn_ts = 0.0
_refresh_task: asyncio.Task | None = None


def _normalize(content: dict[str, list[int]]) -> dict:
    """柚子原始 content → 归一索引：组合牌两个版本字同单（熊&华 → 熊/华）、
    繁体归一（暁→晓，:func:`norm_plate`）；舞ReMASTER 单列；霸复用舞单。"""
    ids: dict[str, frozenset[int]] = {}
    for key, raw in content.items():
        if key == _REMASTER_KEY:
            continue
        # 组合牌两个版本字同牌同单（熊&华 → 熊/华 同单，两字同在
        # plate_to_version 且同版本码）；繁体归一（暁→晓）
        for ch in {norm_plate(x) for x in key.split("&")}:
            ids.setdefault(ch, frozenset(raw))
    if "舞" in ids:
        ids.setdefault("霸", ids["舞"])
    return {"ids": ids, "remaster": frozenset(content.get(_REMASTER_KEY, ()))}


def _warn_once(message: str) -> None:
    global _warn_ts
    if time.time() - _warn_ts >= _FAIL_RETRY:
        _warn_ts = time.time()
        logger.warning(message)


async def _fetch() -> dict:
    content = await get_plate_list()
    data = _normalize(content)
    data["fetched_at"] = time.time()
    try:
        await store.kv_set(
            _KV_KEY, {"fetched_at": data["fetched_at"], "content": content}
        )
    except Exception:
        # 持久化失败不否定内存缓存：本次进程内仍可用，仅失去断网降级
        logger.warning("柚子牌单写缓存失败（不影响本次使用）")
    return data


def _refresh_soon() -> None:
    """过期数据的后台刷新（single-flight）：成功换新，失败沿用旧值。"""
    global _refresh_task
    if _refresh_task is not None and not _refresh_task.done():
        return

    async def _run() -> None:
        global _cache
        try:
            async with _lock:
                if _cache is not None and time.time() - _cache["fetched_at"] < _TTL:
                    return  # 别的触发方已刷新
                _cache = await _fetch()
        except Exception:
            logger.debug("柚子牌单后台刷新失败，沿用旧数据")

    _refresh_task = asyncio.create_task(_run())


async def _load() -> dict | None:
    """缓存链：内存新鲜直用；过期返回旧值 + 后台刷新；冷启动读 DB 快照
    （过期同样转后台刷新）；无快照 inline 拉取。全败返回 None。"""
    global _cache, _fail_ts
    if _cache is not None:
        if time.time() - _cache["fetched_at"] < _TTL:
            return _cache
        _refresh_soon()
        return _cache
    if time.time() - _fail_ts < _FAIL_RETRY:
        return None
    async with _lock:
        if _cache is not None:
            return _cache
        try:
            raw = await store.kv_get(_KV_KEY)
        except Exception:
            raw = None
        if raw and raw.get("content"):
            _cache = _normalize(raw["content"])
            _cache["fetched_at"] = float(raw.get("fetched_at") or 0)
            if time.time() - _cache["fetched_at"] >= _TTL:
                _refresh_soon()
            return _cache
        try:
            _cache = await _fetch()
            return _cache
        except Exception:
            _fail_ts = time.time()
            _warn_once("柚子牌单不可用，国服牌子暂不过筛（维持既有口径）")
            return None


async def cn_plate_ids(version: str) -> frozenset[int] | None:
    """CN 版本字 → 该牌柚子曲单（全量 id 空间，比对经
    :func:`core.plates.plate_entry_allowed`）。None = 不过筛。"""
    data = await _load()
    if data is None:
        return None
    return data["ids"].get(norm_plate(version))


async def cn_remaster_ids() -> frozenset[int] | None:
    """舞ReMASTER 名单（舞/霸 ReM 槽集基准，全量 id 空间的 SD 根 id）。
    None = 不可用。"""
    data = await _load()
    return data["remaster"] if data is not None else None


def plate_fingerprint(ids: frozenset[int] | None) -> str:
    """牌单内容指纹（完成表底图失效检测）：None 态定值参与比对，牌单
    可用性翻转同样触发底图重渲。"""
    if ids is None:
        return "off"
    joined = ",".join(map(str, sorted(ids)))
    return hashlib.sha1(joined.encode()).hexdigest()[:16]


class _FilteredPlates:
    """库 ``MaimaiPlates`` 的柚子牌单过滤视图（duck-type，仿 LocalPlates）。

    库对象按版本区间匹配曲集（含日服已删曲），消费侧只取
    ``get_cleared``/``get_remained``，在此白名单过滤；舞/霸另按
    舞ReMASTER 名单把名单外曲的 Re:MASTER 槽剔除（完成表 ReM 收口同口径）。
    """

    def __init__(
        self,
        inner,
        *,
        ids: frozenset[int] | None,
        remaster_ids: frozenset[int] | None,
        full_set: bool,
    ) -> None:
        self._inner = inner
        self._ids = ids
        self._remaster_ids = remaster_ids
        self._full_set = full_set

    def _apply(self, objs: list[PlateObject]) -> list[PlateObject]:
        out: list[PlateObject] = []
        for obj in objs:
            # 柚子牌单按**条目**记曲（SD 条目 = 根 id、DX 条目 = 根 + 10000），
            # 同曲双组跨牌归属后任一条目在单内即在牌（库对象按根 id 给曲）
            if self._ids is not None and not (
                obj.song.id in self._ids or obj.song.id + DX_ID_OFFSET in self._ids
            ):
                continue
            levels = obj.levels
            if (
                self._full_set
                and self._remaster_ids is not None
                and LevelIndex.ReMASTER in levels
                and obj.song.id not in self._remaster_ids
            ):
                levels = levels - {LevelIndex.ReMASTER}
                if not levels:
                    continue
                obj = PlateObject(song=obj.song, levels=levels, scores=obj.scores)
            out.append(obj)
        return out

    async def get_cleared(self) -> list[PlateObject]:
        return self._apply(await self._inner.get_cleared())

    async def get_remained(self) -> list[PlateObject]:
        return self._apply(await self._inner.get_remained())


async def filter_cn_plates(plate: str, plates):
    """CN 判牌对象过筛（sources CN 源 ``get_plates`` 出口）：牌单不可用时
    原样返回。``plate`` 为「版本字+牌种」串（版本字取首字符）。"""
    version = norm_plate(plate[0])
    ids = await cn_plate_ids(version)
    full_set = version in PLATE_FULL_SET_VERSIONS
    remaster_ids = await cn_remaster_ids() if full_set else None
    if ids is None and remaster_ids is None:
        return plates
    return _FilteredPlates(
        plates,
        ids=ids,
        remaster_ids=remaster_ids,
        full_set=full_set,
    )


def _cache_clear() -> None:
    """进程内缓存整体清空（测试用）：含负缓存与后台刷新任务状态。"""
    global _cache, _fail_ts, _warn_ts, _refresh_task
    _cache = None
    _fail_ts = 0.0
    _warn_ts = 0.0
    if _refresh_task is not None and not _refresh_task.done():
        _refresh_task.cancel()
    _refresh_task = None
