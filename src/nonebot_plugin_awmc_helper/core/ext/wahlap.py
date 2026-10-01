"""华立直连：机厅 location 数据（maimai-py 未覆盖）。"""

from dataclasses import dataclass

from . import ExtError, ext_request

WAHLAP_LOCATION_URL = "https://wc.wahlap.net/maidx/rest/location"


@dataclass
class WahlapArcade:
    id: int
    name: str
    address: str
    province: str
    mall: str
    machine_count: int


async def fetch_locations() -> list[WahlapArcade]:
    """拉取华立官方机厅列表（id/店名/地址/省份/商场/机台数）。"""
    resp = await ext_request(
        "GET",
        WAHLAP_LOCATION_URL,
        name="华立机厅数据",
        network_message="华立机厅数据网络异常，请稍后再试",
        timeout=30,
    )
    if resp.status_code != 200:
        raise ExtError(f"华立机厅接口异常（HTTP {resp.status_code}）")
    try:
        data = resp.json()
    except ValueError as e:
        raise ExtError("华立机厅接口返回了无效数据") from e
    return [
        WahlapArcade(
            id=int(raw["id"]),
            name=raw.get("arcadeName", ""),
            address=raw.get("address", ""),
            province=raw.get("province", ""),
            mall=raw.get("mall", ""),
            machine_count=int(raw.get("machineCount", 0)),
        )
        for raw in data
    ]
