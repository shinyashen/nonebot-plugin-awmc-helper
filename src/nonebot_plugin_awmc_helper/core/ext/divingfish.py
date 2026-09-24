"""水鱼直连：RA 排行榜（maimai-py 未提供）。"""

from dataclasses import dataclass

from . import fetch_json

RANKING_URL = "https://www.diving-fish.com/api/maimaidxprober/rating_ranking"
MUSIC_DATA_URL = "https://www.diving-fish.com/api/maimaidxprober/music_data"


async def fetch_music_data() -> list[dict]:
    """直连拉取水鱼曲库（国服对账源：version_cn/定数互证，song-db-design §7.2）。

    与落雪同为唯二国服源；不得走 MaimaiClient（理由同 lxns.fetch_song_list）。
    """
    return await fetch_json(MUSIC_DATA_URL, name="水鱼曲库")


@dataclass
class RankUser:
    username: str
    ra: int
    rating: int | None = None


async def rating_ranking() -> list[RankUser]:
    """全量 RA 排行（按 RA 从高到低）。"""
    data = await fetch_json(RANKING_URL, name="水鱼排行榜")
    users = [
        RankUser(username=u["username"], ra=int(u["ra"]), rating=u.get("rating"))
        for u in data
    ]
    users.sort(key=lambda x: x.ra, reverse=True)
    return users
