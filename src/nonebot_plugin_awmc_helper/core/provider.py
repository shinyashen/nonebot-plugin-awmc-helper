"""自定义 maimai_py provider：规范表/别名 → 库对象（song-db-design §5.6）。

参考 maimai.py 自带的 ``LocalProvider``（``_hash`` 由数据内容计算）与
``docs/concepts/caches.md``「覆写 provider 即替换缓存源」：

- ``_hash()`` 返回规范表内容指纹（``songdb.CURRENT_FINGERPRINT``，rebuild 末尾刷新），
  数据变更后哈希自动变化，``client.songs()`` 自行重建缓存，**无需手动删键**；
- ``get_songs()`` 按 scope（cn/jp）从规范表物化 ``Song`` 列表（§5.5 转化层）；
- CN 运行时视图已由本 provider 按规范表构造（``scope="cn"``，core.songs 加载
  即走它）；``scope="jp"`` 仅测试使用——日服视图由 core.songs 直接读规范表
  （``_jp_songs_map``），不经 maimai_py 运行时缓存。
"""

import json
import time
import asyncio
import hashlib
from typing import Any

from nonebot import logger
from maimai_py.models import Song
from maimai_py.providers.base import ISongProvider, IAliasProvider
from maimai_py.providers.lxns import LXNSProvider
from maimai_py.providers.yuzu import YuzuProvider

from . import store, songdb
from .songdb import Scope
from ..constants import DX_ID_OFFSET, normalize_text, strip_chart_prefix


class ListSongProvider(ISongProvider):
    """内存曲目列表 provider（测试注入与快照降级共用，内容哈希驱动缓存）。"""

    def __init__(self, songs: list[Song], digest: str) -> None:
        self._songs = songs
        self._digest = digest

    async def get_songs(self, client) -> list[Song]:
        return self._songs

    def _hash(self) -> str:
        return self._digest


def songs_list_digest(songs: list[Song]) -> str:
    """曲目列表内容哈希（注入/快照 provider 的缓存失效键；序列化含别名）。"""
    from .songs import song_to_dict

    payload = json.dumps(
        [song_to_dict(s) for s in songs], ensure_ascii=False, sort_keys=True
    )
    return hashlib.md5(payload.encode()).hexdigest()


class AwmcSongProvider(ISongProvider):
    """以规范表为数据源的曲库 provider（scope=cn/jp 双视图共用一个实现）。"""

    def __init__(self, scope: Scope = "cn") -> None:
        self.scope: Scope = scope

    async def get_songs(self, client) -> list[Song]:
        state = await songdb.State.load()
        return songdb.all_songs(state, self.scope)

    def _hash(self) -> str:
        return songdb.CURRENT_FINGERPRINT or "empty"


class AwmcAliasProvider(IAliasProvider):
    """柚子 + 落雪 + MuNET + 本地 四源别名合并 provider（song-db-design §5.6）。

    - 对齐原版 maimaidx 的别名合并口径（core/merge/alias.py：柚子+落雪）；
    - 柚子的 SongID 是谱面级（sd 541 / dx 793 / 宴 66 条），官方 provider 内部
      已 ``%10000`` 折叠到根 id——同根 id 的标准/DX/宴谱别名在此天然合并；
    - 叠加落雪别名库（``api/v0/maimai/alias/list``）、MuNET 全量走查快照
      （走查由每日任务离线完成，此处只读库内快照，零额外请求）与
      ``local_alias`` 本地别名；
    - ``_hash`` 基于上次拉取的别名内容：柚子/落雪官方 provider 均为常量哈希，
      远端别名更新从不触发缓存重建，此处修复为内容驱动。
    """

    def __init__(self, yuzu: YuzuProvider, lxns: LXNSProvider) -> None:
        self._yuzu = yuzu
        self._lxns = lxns
        self._fingerprint: str | None = None
        self.last_merged: dict[int, list[str]] = {}
        """最近一次合并的完整别名库（含仅日服曲目条目），日服 fallback 搜索用。"""

    async def get_aliases(self, client) -> dict[int, list[str]]:
        """三源合并，返回**去前后缀**别名库（维护口径，Q31）。

        每条别名经 ``strip_chart_prefix`` 剥离谱面类型前缀（dx/标准/标/宴 +
        该曲宴谱汉字裸写与 [汉字] 括号形式）与后缀（dx/标准，dx 前置 ASCII
        字母的 iidx 等英文词不剥），经 ``normalize_text`` 简繁归一后入库并入
        视图；快照保留原始形态（无损，可重放）；跨源归一化去重，保序。
        """
        merged: dict[int, list[str]] = {}
        seen: dict[int, set[str]] = {}
        utage_kanji = await store.get_utage_kanji()

        def _add(song_id: int, aliases: list[str]) -> None:
            bucket = seen.setdefault(song_id, set())
            target = merged.setdefault(song_id, [])
            extra = utage_kanji.get(song_id, set())
            for alias in aliases:
                stripped = strip_chart_prefix(alias, extra_prefixes=extra)
                text = stripped[0] if stripped else alias
                low = normalize_text(text)
                if low and low not in bucket:
                    bucket.add(low)
                    target.append(text)

        # 远端源单源容错：拉取成功即整源写库（song_alias 表）；失败回退库内快照。
        # 两源并发拉取，合并仍按 yuzu→lxns 顺序（保序去重口径不变）；
        # munet 源数据本就来自库内快照（走查离线写入），回写=每次曲库加载
        # DELETE+全量重插 1700+ 行的纯写放大，标 persist=False 跳过落库

        async def _pull(name, fetch):
            started = time.monotonic()
            try:
                pairs = await fetch(client)
                logger.info(
                    f"曲库加载：别名源 {name} 拉取成功 "
                    f"{sum(len(v) for v in pairs)} 条"
                    f"（{time.monotonic() - started:.1f}s）"
                )
                return pairs
            except Exception as e:
                logger.warning(
                    f"别名源 {name} 拉取失败，回退上次快照"
                    f"（{time.monotonic() - started:.1f}s：{e}）"
                )
                return None

        sources: tuple[tuple[str, Any, bool], ...] = (
            ("yuzu", self._fetch_yuzu, True),
            ("lxns", self._fetch_lxns, True),
            ("munet", self._fetch_munet, False),
        )
        pulled = await asyncio.gather(
            *(_pull(name, fetch) for name, fetch, _persist in sources)
        )
        for (_name, _fetch, persist), pairs in zip(sources, pulled):
            if pairs is None:
                pairs = list((await store.load_song_aliases([_name])).items())
            elif persist:
                await store.save_song_aliases(_name, dict(pairs))
            for sid, aliases in pairs:
                _add(sid, aliases)
        for la in await store.get_local_aliases():
            _add(la.song_id, [la.alias])
        logger.info(f"曲库加载：别名合并完成，覆盖 {len(merged)} 曲")
        raw = json.dumps(
            {str(k): sorted(v) for k, v in merged.items()},
            ensure_ascii=False,
            sort_keys=True,
        )
        self._fingerprint = hashlib.md5(raw.encode()).hexdigest()
        self.last_merged = merged
        return merged

    async def _fetch_yuzu(self, client) -> list[tuple[int, list[str]]]:
        return list((await self._yuzu.get_aliases(client)).items())

    async def _fetch_lxns(self, client) -> list[tuple[int, list[str]]]:
        raw = await self._lxns.get_aliases(client)
        return [(int(sid) % DX_ID_OFFSET, aliases) for sid, aliases in raw.items()]

    async def _fetch_munet(self, client) -> list[tuple[int, list[str]]]:
        """MuNET 别名快照（库内读，永不失败）：全量走查离线写入，见
        :func:`core.ext.munet.refresh_aliases_full`。仅参与合并不回写库
        （get_aliases 的 persist 位），避免每次曲库加载整源重写。"""
        return list((await store.load_song_aliases(["munet"])).items())

    def _hash(self) -> str:
        return self._fingerprint or "empty"
