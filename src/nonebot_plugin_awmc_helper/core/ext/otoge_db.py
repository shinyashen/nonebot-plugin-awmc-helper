"""otoge-db 直连：日服数据充实（zvuc/otoge-db，现役表 + 下架记录）。

- ``music-ex.json``：日服现役曲目（无任何 id，经 title join 使用，§2.5）；
- ``music-ex-deleted.json``：权威下架记录（带 deleted_date；「当前下架集」=
  下架记录 ∖ 现役列表，含单张期间限定宴谱）。
"""

import asyncio

from . import fetch_github_raw

_fetch_json = fetch_github_raw(
    "https://raw.githubusercontent.com/zvuc/otoge-db/main/maimai/data",
    source="otoge-db",
)


async def fetch_music_ex() -> list[dict]:
    """日服现役曲目表。"""
    return await _fetch_json("music-ex.json")


async def fetch_music_ex_deleted() -> list[dict]:
    """下架记录表。"""
    return await _fetch_json("music-ex-deleted.json")


_fetch_ongeki = fetch_github_raw(
    "https://raw.githubusercontent.com/zvuc/otoge-db/main/ongeki/data",
    source="otoge-db",
)
"""音击曲库（同仓 ongeki 目录；中二/音击侧别判定数据源，combo 笔记 §12.4）。"""


async def fetch_ongeki_origin() -> "list[dict]":
    """音击原创栏条目（现役 + 下架一体抓取，供 norm_title 标题集）。

    两文件作为一个作业：任一失败整体抛错（songdb 侧保留旧集合）——下架
    记录（music-ex-deleted，含オンゲキ 5）单独缺档会造成「原创曲已从音击
    下架但 maimai 仍在」的误判缺口，不允部分成功。
    """
    music, deleted = await asyncio.gather(
        _fetch_ongeki("music.json"),
        _fetch_ongeki("music-ex-deleted.json"),
    )
    return [*music, *deleted]
