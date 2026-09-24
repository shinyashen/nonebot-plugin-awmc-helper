"""otoge-db 直连：日服数据充实（zvuc/otoge-db，现役表 + 下架记录）。

- ``music-ex.json``：日服现役曲目（无任何 id，经 title join 使用，§2.5）；
- ``music-ex-deleted.json``：权威下架记录（带 deleted_date；「当前下架集」=
  下架记录 ∖ 现役列表，含单张期间限定宴谱）。
"""

from typing import Any

from . import fetch_json

RAW_BASE = "https://raw.githubusercontent.com/zvuc/otoge-db/main/maimai/data"


async def _fetch_json(name: str) -> Any:
    return await fetch_json(f"{RAW_BASE}/{name}", name=f"otoge-db {name}", timeout=120)


async def fetch_music_ex() -> list[dict]:
    """日服现役曲目表。"""
    return await _fetch_json("music-ex.json")


async def fetch_music_ex_deleted() -> list[dict]:
    """下架记录表。"""
    return await _fetch_json("music-ex-deleted.json")
