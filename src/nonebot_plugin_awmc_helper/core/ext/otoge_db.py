"""otoge-db 直连：日服数据充实（zvuc/otoge-db，现役表 + 下架记录）。

- ``music-ex.json``：日服现役曲目（无任何 id，经 title join 使用，§2.5）；
- ``music-ex-deleted.json``：权威下架记录（带 deleted_date；「当前下架集」=
  下架记录 ∖ 现役列表，含单张期间限定宴谱）。
"""

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
