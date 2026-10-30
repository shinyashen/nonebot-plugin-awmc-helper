"""core/plate_source：柚子牌单缓存链与国服判牌过筛（Q60）。"""

import time
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from maimai_py import PlateObject
from mocks import seed_plate_cache


@pytest.fixture
async def db(tmp_path):
    """独立 SQLite（kv_cache 用）。"""
    from nonebot_plugin_awmc_helper.core import store

    store.set_db_file(tmp_path / "awmc.db")
    await store.init_db()
    yield
    store.set_db_file(None)


@pytest.fixture
def snapshot() -> dict:
    from mocks import load_yuzu_plate

    return load_yuzu_plate()


@pytest.fixture(autouse=True)
def _fail_closed_disabled(monkeypatch):
    """默认 fail-closed：disabled 集不可用（None）→ 放行面关闭。既有白名单
    语义用例零改动（make_song 默认 version=26000 恒为「当期新曲」形态，
    无此夹具会走放行路径）；放行用例自行覆写为具体集合。"""
    from nonebot_plugin_awmc_helper.core import plate_source

    async def _none():
        return None

    monkeypatch.setattr(plate_source, "cn_disabled_ids", _none)


def test_normalize_keys(snapshot):
    """键归一：繁体→简体、组合牌两字同单、霸复用舞单、舞ReMASTER 单列。"""
    from nonebot_plugin_awmc_helper.core import plate_source

    data = plate_source._normalize(snapshot)
    ids = data["ids"]
    assert set(ids) >= {
        "真",
        "晓",
        "樱",
        "堇",
        "辉",
        "熊",
        "华",
        "爽",
        "煌",
        "星",
        "宙",
        "祭",
        "祝",
        "双",
        "宴",
        "镜",
        "彩",
        "舞",
        "霸",
    }
    assert ids["熊"] == ids["华"]  # 组合牌两字同单
    assert ids["宙"] == ids["星"]
    assert "舞ReMASTER" not in ids
    assert ids["霸"] == ids["舞"]  # Hoshino VERSION_MAP["霸"] → "舞" 同款
    assert data["remaster"] == frozenset(snapshot["舞ReMASTER"])


@pytest.mark.asyncio
async def test_cn_plate_ids_per_plate_granularity(snapshot):
    """真实快照锚点：排除名单是**逐牌**策展而非全局——ジングルベル(70) 被真牌
    排除、舞牌仍计入；ハッピーシンセサイザ(44) 两牌均排除；DX 39(10146) 不在
    华（熊&华）单；True Love Song(8) 柚子尚未跟进日服删除、真牌仍计入。"""
    from nonebot_plugin_awmc_helper.core.plate_source import cn_plate_ids

    seed_plate_cache(snapshot)
    zhen = await cn_plate_ids("真")
    wu = await cn_plate_ids("舞")
    hua = await cn_plate_ids("华")
    assert 70 not in zhen
    assert 70 in wu
    assert 44 not in zhen
    assert 44 not in wu
    assert 10146 not in hua
    assert 11001 in hua  # BLACK ROSE：全量 id 空间
    assert 8 in zhen
    # 繁体键与霸复用同链路
    assert await cn_plate_ids("暁") == await cn_plate_ids("晓")
    assert await cn_plate_ids("霸") == wu


@pytest.mark.asyncio
async def test_remaster_list_scope(snapshot):
    """舞ReMASTER 名单与舞单的关系：名单 ⊆ 舞单，且 DX 时代追加 ReM 的曲
    （如 FLOWER 389、Link 131）不在名单（ReM 槽收口的依据）。"""
    from nonebot_plugin_awmc_helper.core.plate_source import (
        cn_plate_ids,
        cn_remaster_ids,
    )

    seed_plate_cache(snapshot)
    rem = await cn_remaster_ids()
    wu = await cn_plate_ids("舞")
    assert rem <= wu
    # Link(131)/FLOWER(389)：舞单计入但不在 ReM 名单（DX 时代追加 ReM 的
    # 收口依据）；ヒバナ(792) 则连舞单都不在（删曲排除）
    for sid in (131, 389):
        assert sid in wu
        assert sid not in rem
    assert 792 not in wu


@pytest.mark.asyncio
async def test_unavailable_degrades_to_none(db, monkeypatch):
    """无缓存 + 无快照 + 拉取失败：返回 None（不过筛）且负缓存限频重试。"""
    from nonebot_plugin_awmc_helper.core import plate_source

    calls = {"n": 0}

    async def _counting_fail():
        calls["n"] += 1
        raise plate_source.ExtError("down")

    monkeypatch.setattr(plate_source, "_fetch", _counting_fail)
    assert await plate_source.cn_plate_ids("真") is None
    assert await plate_source.cn_plate_ids("真") is None
    assert calls["n"] == 1  # 负缓存窗口内不重试


@pytest.mark.asyncio
async def test_kv_snapshot_fallback(db, snapshot):
    """冷启动读 DB 快照（断网降级）：拉取失败仍可用上次牌单。"""
    from nonebot_plugin_awmc_helper.core import store, plate_source

    await store.kv_set(
        plate_source._KV_KEY, {"fetched_at": time.time(), "content": snapshot}
    )
    # conftest 已把 _fetch 打成必失败：能出数即来自 kv
    ids = await plate_source.cn_plate_ids("真")
    assert ids == frozenset(snapshot["真"])
    rem = await plate_source.cn_remaster_ids()
    assert rem == frozenset(snapshot["舞ReMASTER"])


@pytest.mark.asyncio
async def test_stale_while_revalidate(db, snapshot, monkeypatch):
    """过期缓存：先答旧值，后台刷新成功换新、失败沿用。"""
    from nonebot_plugin_awmc_helper.core import plate_source

    stale = dict(plate_source._normalize({"真": [8]}))
    stale["fetched_at"] = 0.0
    plate_source._cache_clear()
    plate_source._cache = stale

    await plate_source.cn_plate_ids("真")  # 触发后台刷新（conftest _fetch 必败）
    task = plate_source._refresh_task
    assert task is not None
    await task
    assert plate_source._cache is stale  # 失败沿用旧值

    # 刷新成功路径：换 _fetch 为注入快照
    async def _ok():
        return {
            **plate_source._normalize(snapshot),
            "fetched_at": time.time(),
        }

    monkeypatch.setattr(plate_source, "_fetch", _ok)
    await plate_source.cn_plate_ids("真")
    task = plate_source._refresh_task
    await task
    assert plate_source._cache is not stale
    assert (await plate_source.cn_plate_ids("真")) == frozenset(snapshot["真"])


def test_fingerprint():
    from nonebot_plugin_awmc_helper.core.plate_source import admission_fingerprint

    assert admission_fingerprint(None, bypass=()) == "off"
    a = admission_fingerprint(frozenset({3, 1, 2}), bypass=())
    assert a == admission_fingerprint(frozenset({1, 2, 3}), bypass=())  # 序无关
    assert a != admission_fingerprint(frozenset({1, 2}), bypass=())
    assert a != admission_fingerprint(None, bypass=())  # 可用性翻转必换指纹
    # 放行实绩并入：新曲被柚子收录 / 被落雪 disabled 都会换指纹 → 重渲
    assert a != admission_fingerprint(frozenset({1, 2, 3}), bypass=(2025,))
    assert admission_fingerprint(
        frozenset({1, 2, 3}), bypass=(2, 1)
    ) == admission_fingerprint(frozenset({1, 2, 3}), bypass=(1, 2))


class _FakePlates:
    def __init__(self, cleared, remained):
        self._cleared, self._remained = cleared, remained

    async def get_cleared(self):
        return self._cleared

    async def get_remained(self):
        return self._remained


def _plate_obj(song_id: int, levels: set, *, version: int = 26000) -> "PlateObject":
    from mocks import make_song
    from maimai_py import PlateObject

    return PlateObject(
        song=make_song(song_id, f"曲{song_id}", version=version),
        levels=levels,
        scores=[],
    )


@pytest.mark.asyncio
async def test_filter_cn_plates_whitelist_and_remaster(snapshot):
    """判牌过滤视图：白名单滤曲 + 舞/霸按名单剔除 ReM 槽 + 全量 id 换算。"""
    from maimai_py import LevelIndex

    from nonebot_plugin_awmc_helper.core.plate_source import filter_cn_plates

    seed_plate_cache({"真": [8, 44], "舞": [44, 70], "舞ReMASTER": [44]})

    # 真（SD 主类型）：70 白名单外滤除
    inner = _FakePlates(
        [_plate_obj(8, {LevelIndex.MASTER})],
        [_plate_obj(44, {LevelIndex.MASTER}), _plate_obj(70, {LevelIndex.EXPERT})],
    )
    out = await filter_cn_plates("真将", inner)
    assert [p.song.id for p in await out.get_cleared()] == [8]
    assert [p.song.id for p in await out.get_remained()] == [44]

    # 舞（旧作全集 + ReM 名单）：名单外曲的 ReM 槽剔除、仅剩 ReM 的整曲剔除
    inner = _FakePlates(
        [_plate_obj(44, {LevelIndex.MASTER, LevelIndex.ReMASTER})],
        [_plate_obj(70, {LevelIndex.MASTER, LevelIndex.ReMASTER})],
    )
    out = await filter_cn_plates("舞将", inner)
    cleared = await out.get_cleared()
    remained = await out.get_remained()
    assert [p.song.id for p in cleared] == [44]
    assert LevelIndex.ReMASTER in cleared[0].levels
    assert [p.song.id for p in remained] == [70]
    assert LevelIndex.ReMASTER not in remained[0].levels

    # 全名单外整曲剔除（levels 剔空）
    inner = _FakePlates([], [_plate_obj(70, {LevelIndex.ReMASTER})])
    out = await filter_cn_plates("舞将", inner)
    assert await out.get_remained() == []


@pytest.mark.asyncio
async def test_filter_cn_plates_dx_full_id(snapshot):
    """DX 世代牌的全量 id 换算：根 id + DX_ID_OFFSET 参与白名单比对。"""
    from maimai_py import LevelIndex

    from nonebot_plugin_awmc_helper.core.plate_source import filter_cn_plates

    seed_plate_cache({"华": [10811, 812]})
    inner = _FakePlates(
        [_plate_obj(811, {LevelIndex.MASTER})],  # DX 曲 full id = 10811
        [_plate_obj(813, {LevelIndex.MASTER})],  # full id 10813 白名单外
    )
    out = await filter_cn_plates("华将", inner)
    assert [p.song.id for p in await out.get_cleared()] == [811]
    assert await out.get_remained() == []


@pytest.mark.asyncio
async def test_cn_disabled_ids_parses_song_doc(db, monkeypatch):
    """disabled 集解析落雪曲库 doc：``fetch_song_list`` 返回带 ``songs`` 键的
    dict（非 list）——迭代 dict 键会让 `.get` 炸掉、fail-closed 恒不放行
    （2026-10-30 生产实测回归）。"""
    from nonebot_plugin_awmc_helper.core import plate_source
    from nonebot_plugin_awmc_helper.core.ext import lxns

    async def fake_list(notes=True):
        return {
            "songs": [
                {"id": 12025, "disabled": False},
                {"id": 11001, "disabled": True},  # DX 曲 raw id → 根 1001
                {"id": 8, "disabled": True},  # SD 根 id
            ]
        }

    monkeypatch.setattr(lxns, "fetch_song_list", fake_list)
    # autouse 夹具替换了 cn_disabled_ids 本身（fail-closed 默认态），此处直测
    # 解析内层
    assert await plate_source._disabled_fetch() == frozenset({1001, 8})
    assert plate_source._disabled_cache["ids"] == frozenset({1001, 8})


@pytest.mark.asyncio
async def test_filter_cn_plates_newcomer_bypass(db, monkeypatch):
    """当期新曲受控放行（2026-10-30）：白名单缺席 × 落雪删除检查三态。

    彩将形态（魔法仕掛けのファンタジア 12025）：柚子单未及收录的国服新曲，
    落雪未 disabled → 放行入表；disabled → 剔除（当版本删除曲防混入）；
    disabled 集不可用 → fail-closed 剔除。旧世代曲不适用放行面。
    """
    from maimai_py import LevelIndex

    from nonebot_plugin_awmc_helper.core import plate_source

    seed_plate_cache({"彩": [2044]})  # 柚子单未含 12025

    # 放行：未 disabled
    async def _empty():
        return frozenset()

    monkeypatch.setattr(plate_source, "cn_disabled_ids", _empty)
    inner = _FakePlates(
        [],
        [
            # 库对象按根 id 给曲（CN 视图空间）：12025 显示 id 的根 = 2025
            _plate_obj(2025, {LevelIndex.MASTER}, version=25505),
            _plate_obj(2044, {LevelIndex.MASTER}, version=25500),
        ],
    )
    out = await plate_source.filter_cn_plates("彩将", inner)
    assert sorted(p.song.id for p in await out.get_remained()) == [2025, 2044]

    # 删除检查命中：disabled 根 id 2025 → 12025 出表
    async def _deleted():
        return frozenset({2025})

    monkeypatch.setattr(plate_source, "cn_disabled_ids", _deleted)
    out = await plate_source.filter_cn_plates("彩将", inner)
    assert [p.song.id for p in await out.get_remained()] == [2044]

    # fail-closed（autouse 默认 None）：不放行
    out = await plate_source.filter_cn_plates("彩将", inner)
    assert [p.song.id for p in await out.get_remained()] == [2044]

    # 旧世代曲（version ≤ current_version）不适用放行面：白名单外恒剔除
    # （重置进程内牌单缓存：前序 seed 的彩单仍驻留；华单需重新 seed）
    plate_source._cache_clear()
    seed_plate_cache({"华": [10811, 812]})

    async def _empty2():
        return frozenset()

    monkeypatch.setattr(plate_source, "cn_disabled_ids", _empty2)
    inner_old = _FakePlates([], [_plate_obj(813, {LevelIndex.MASTER}, version=24000)])
    out = await plate_source.filter_cn_plates("华将", inner_old)
    assert await out.get_remained() == []


@pytest.mark.asyncio
async def test_filter_cn_plates_unavailable_passthrough(db):
    """牌单不可用（None）原样返回库对象，不包过滤视图。"""
    from nonebot_plugin_awmc_helper.core.plate_source import filter_cn_plates

    inner = _FakePlates([], [])
    assert await filter_cn_plates("真将", inner) is inner
