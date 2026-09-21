"""otoge-db 直连：日服数据充实（zvuc/otoge-db，maimai/data/ 现役表 + 下架记录）。

- ``music-ex.json``：日服现役曲目（无任何 id，经 title join 使用，song-db-design §2.5）；
- ``music-ex-deleted.json``：权威下架记录（带 deleted_date；「当前下架集」=
  下架记录 ∖ 现役列表，含单张期间限定宴谱）。
"""

import httpx
from typing import Any

from . import ExtError, ExtNetworkError, get_client

RAW_BASE = "https://raw.githubusercontent.com/zvuc/otoge-db/main/maimai/data"


async def _fetch_json(name: str) -> Any:
    try:
        resp = await get_client().get(f"{RAW_BASE}/{name}", timeout=120)
    except httpx.RequestError as e:
        raise ExtNetworkError(f"otoge-db {name} 网络异常") from e
    if resp.status_code != 200:
        raise ExtError(f"otoge-db {name} 拉取失败（HTTP {resp.status_code}）")
    return resp.json()


async def fetch_music_ex() -> list[dict]:
    """日服现役曲目表。"""
    return await _fetch_json("music-ex.json")


async def fetch_music_ex_deleted() -> list[dict]:
    """下架记录表。"""
    return await _fetch_json("music-ex-deleted.json")
