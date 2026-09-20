"""水鱼直连：RA 排行榜（maimai-py 未提供）。"""

from dataclasses import dataclass

import httpx

from . import ExtError, ExtNetworkError, get_client

RANKING_URL = "https://www.diving-fish.com/api/maimaidxprober/rating_ranking"


@dataclass
class RankUser:
    username: str
    ra: int
    rating: int | None = None


async def rating_ranking() -> list[RankUser]:
    """全量 RA 排行（按 RA 从高到低）。"""
    try:
        resp = await get_client().get(RANKING_URL, timeout=60)
    except httpx.RequestError as e:
        raise ExtNetworkError("水鱼排行榜网络异常，请稍后再试") from e
    if resp.status_code != 200:
        raise ExtError(f"水鱼排行榜服务异常（HTTP {resp.status_code}）")
    data = resp.json()
    users = [
        RankUser(username=u["username"], ra=int(u["ra"]), rating=u.get("rating"))
        for u in data
    ]
    users.sort(key=lambda x: x.ra, reverse=True)
    return users
