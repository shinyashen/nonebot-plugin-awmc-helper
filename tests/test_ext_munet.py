"""core/ext/munet：门户公开 API 拉取层与转换（respx mock + 纯函数单测）。"""

import httpx
import respx
import pytest


@pytest.fixture(autouse=True)
def munet(monkeypatch):
    """被测模块（懒导入：collection 时 nonebot 未初始化）+ 关闭请求间隔。"""
    from nonebot_plugin_awmc_helper.core.ext import munet as mod

    monkeypatch.setattr(mod, "_MIN_INTERVAL", 0)
    return mod


def _entry(**overrides):
    """Tell Your World 形状的 SD 组条目（id=100）。"""
    entry = {
        "id": 100,
        "name": "Tell Your World",
        "artist": "livetune",
        "bpm": 150,
        "genre": 102,
        "addVersion": 1,
        "hasStandard": True,
        "hasDeluxe": False,
        "isLocked": False,
        "aliases": [{"alias": "TYW"}, {"alias": "告诉你的世界"}],
        "charts": [
            {
                "difficulty": 0,
                "kind": 0,
                "designer": "-",
                "utageId": 0,
                "releaseTime": "2012-09-13T00:00:00+00:00",
                "tapCount": 113,
                "holdCount": 13,
                "slideCount": 8,
                "touchCount": 0,
                "breakCount": 4,
                "constants": [
                    {"version": 23, "constant": 6.0},
                    {"version": 27, "constant": 6.0},
                ],
            }
        ],
    }
    entry.update(overrides)
    return entry


@respx.mock
async def test_get_by_id_200_and_origin_header(munet):
    route = respx.get(url__startswith="https://apidashboard3.mumur.net:42081/").mock(
        return_value=httpx.Response(200, json=_entry())
    )
    entry = await munet.fetch_music_by_id(100)
    assert entry is not None
    assert entry["name"] == "Tell Your World"
    request = route.calls.last.request
    assert request.headers["Origin"] == munet._PORTAL_ORIGIN
    assert request.headers["accept"] == "application/json"


@respx.mock
async def test_get_by_id_404_returns_none(munet):
    respx.get(url__regex=r"https://apidashboard3[^/]*mumur\.net.*/GetById/1$").mock(
        return_value=httpx.Response(404, text="")
    )
    assert await munet.fetch_music_by_id(1) is None


@respx.mock
async def test_418_falls_back_to_cf_host(munet):
    respx.get(url__startswith="https://apidashboard3.mumur.net:42081/").mock(
        return_value=httpx.Response(418, text="")
    )
    route_cf = respx.get(url__startswith="https://apidashboard3-cf.mumur.net/").mock(
        return_value=httpx.Response(200, json=_entry())
    )
    entry = await munet.fetch_music_by_id(100)
    assert entry is not None
    assert route_cf.called


@respx.mock
async def test_search_min_length_skips_request(munet):
    route = respx.post(
        url__startswith="https://apidashboard3.mumur.net:42081/api/v3/mai2/Mai2Music/Search"
    ).mock(return_value=httpx.Response(200, json={"musicData": []}))
    assert await munet.search_music("a") == []
    assert not route.called
    await munet.search_music("ビビデバ")
    assert route.called


def test_add_version_to_code(munet):
    assert munet.add_version_to_code(13) == 20000
    assert munet.add_version_to_code(27) == 27000
    assert munet.add_version_to_code(12) is None  # 老框范围外


def test_entry_to_doc_sd(munet):
    base, doc = munet.entry_to_doc(_entry())
    assert base == 100
    assert doc["title"] == "Tell Your World"
    assert doc["genre"] == "niconico & VOCALOID"  # 102
    assert doc["bpm"] == "150"
    sheet = doc["sheets"]["sd"]
    assert sheet["contents"][0]["level"] == [6.0]  # constants 末值（快照语义）
    assert sheet["date"] == 20120913
    assert "notes" in sheet["contents"][0]


def test_entry_to_doc_dx_only_uses_base_id(munet):
    entry = _entry(id=1449, hasStandard=False, hasDeluxe=True)
    entry["charts"] = [{**_entry()["charts"][0], "kind": 1}]
    base, doc = munet.entry_to_doc(entry)
    assert base == 1449  # DX-only 曲 MuNET 直接用曲 id
    assert "dx" in doc["sheets"]


def test_entry_to_doc_dx_group_offset(munet):
    entry = _entry(id=11580)
    entry["charts"] = [{**_entry()["charts"][0], "kind": 1}]
    base, doc = munet.entry_to_doc(entry)
    assert base == 1580  # SD+DX 双组曲的 DX 组 = 曲 id + 10000
    assert "dx" in doc["sheets"]


def test_entry_to_doc_buddy_utage_excluded(munet):
    entry = _entry()
    entry["charts"] = entry["charts"] + [
        {
            "difficulty": 0,
            "kind": 0,
            "designer": "-",
            "utageId": 11,
            "tapCount": 0,
            "holdCount": 0,
            "slideCount": 0,
            "touchCount": 0,
            "breakCount": 0,
            "constants": [{"version": 24, "constant": 13.0}],
        }
    ]
    _base, doc = munet.entry_to_doc(entry)
    # 宴谱内容不转换（utageId 无法映射规范表 level_id），SD 内容保留
    assert len(doc["sheets"]["sd"]["contents"]) == 1


def test_entry_to_doc_standalone_utage(munet):
    entry = _entry(id=2500, genre=107, addVersion=13)
    entry["charts"] = [
        {
            "difficulty": 0,
            "kind": 0,
            "designer": "-",
            "utageId": 10,
            "tapCount": 10,
            "holdCount": 2,
            "slideCount": 1,
            "touchCount": 0,
            "breakCount": 1,
        }
    ]
    base, doc = munet.entry_to_doc(entry)
    assert base == 2500
    assert doc["genre"] == "宴会場"  # 107（推断档）
    assert doc["sheets"] == {"utage": {"version": 20000}}


def test_harvest_aliases_folds_root_id(munet):
    aliases = munet.harvest_aliases([_entry(id=10100)])
    assert aliases == {100: ["TYW", "告诉你的世界"]}
