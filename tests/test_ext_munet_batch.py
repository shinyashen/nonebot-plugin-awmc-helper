"""MuNET current_jp 批次内新增歌曲补充：title-diff 候选 → 拉取 → fill 合并。

批次条目取 MuNET 真实条目（物語はここから，id 2020，2026-09-29 GetById 快照，
见 songdb_fixtures.make_munet_entry）；封面走 otoge PR 预读的真实哈希名。
"""

import pytest
from sqlmodel import select
from songdb_fixtures import (
    make_lxns,
    make_all_data,
    make_dschange,
    make_divingfish,
    make_otoge_live,
    make_munet_entry,
    make_otoge_deleted,
)

BATCH_ENTRY_ID = 2020  # MuNET 真实 musicId（物語はここから）

# 真实 otoge 现役条目的封面哈希（MuNET 无封面字段，PR 预读提供）
BATCH_COVER = "7562b43964819ada.png"


@pytest.fixture
async def db(tmp_path):
    from nonebot_plugin_awmc_helper.core import store

    store.set_db_file(tmp_path / "awmc.db")
    await store.init_db()
    yield store
    store.set_db_file(None)


@pytest.fixture
def monkeypatched_munet(monkeypatch):
    """批次外部依赖全部替换：开关 + BrowseFilters/Search/GetById/otoge 源/PR 预读。"""
    from nonebot_plugin_awmc_helper.config import plugin_config
    from nonebot_plugin_awmc_helper.core.ext import munet, otoge_db, otoge_pr

    monkeypatch.setattr(plugin_config, "awmc_munet_batch", True)
    monkeypatch.setattr(munet, "_MIN_INTERVAL", 0)

    entry = make_munet_entry()
    real_search = entry["search"]["musicData"][0]
    real_by_id = entry["get_by_id"]

    async def fake_filters():
        return entry["browse_filters"]

    async def fake_search(query):
        if query == "物語はここから":
            return [dict(real_search)]
        return []

    async def fake_by_id(music_id):
        if music_id == BATCH_ENTRY_ID:
            return dict(real_by_id)
        return None

    async def fake_pr():
        return [{"title": "物語はここから", "image_url": BATCH_COVER}]

    async def fake_main():
        return make_otoge_live()

    monkeypatch.setattr(munet, "fetch_browse_filters", fake_filters)
    monkeypatch.setattr(munet, "search_music", fake_search)
    monkeypatch.setattr(munet, "fetch_music_by_id", fake_by_id)
    monkeypatch.setattr(otoge_pr, "load_open_pr_entries", fake_pr)
    monkeypatch.setattr(otoge_db, "fetch_music_ex", fake_main)


def full_payloads():
    return {
        "maimaiinfo": make_all_data(),
        "dschange": make_dschange(),
        "otoge_db": make_otoge_live(),
        "otoge_deleted": make_otoge_deleted(),
        "lxns": make_lxns(),
        "divingfish": make_divingfish(),
    }


async def test_batch_supplement_creates_songs_and_aliases(db, monkeypatched_munet):
    from nonebot_plugin_awmc_helper.core import store, songdb
    from nonebot_plugin_awmc_helper.core.ext import munet

    await songdb.rebuild(full_payloads())
    # 重建后「物語はここから」已在 song_pending（otoge 无 id 真实新曲）——
    # 批次候选来自 PR 预读 ∪ otoge 现役 ∪ pending 三路（同一真实曲）

    result = await munet.run_batch_supplement()
    assert result["status"] == "batch"
    # 候选 3 个：物語はここから（PR/otoge/pending 三路合一）+ 宴前缀标题
    # [協]ラグトレイン、[蛸]チルノ…（otoge 宴条目标题与规范表基曲标题不同构，
    # 各自成候选——真实 title-diff 语义，搜索不命中即跳过）
    assert result["candidates"] == 3
    assert result["entries"] == 1

    state = await songdb.State.load()
    row = state.songs.get(BATCH_ENTRY_ID)
    assert row is not None
    assert row.title == "物語はここから"
    group = state.groups.get((BATCH_ENTRY_ID, "dx"))
    assert group is not None
    assert group.version == 27000  # addVersion 27 → 日服码（otoge 事实同值）
    assert group.date == 260917  # otoge 现役表 release 事实随建曲直接写入
    chart = state.chart(BATCH_ENTRY_ID, "dx", 3)
    assert chart.designer == "Luxizhel"  # MuNET 实测谱师
    assert (chart.notes_tap, chart.notes_slide, chart.notes_break) == (564, 80, 68)
    history = state.history_of(BATCH_ENTRY_ID, "dx", 3)
    assert history == [(27000, 13.5)]  # constants 末值「当前定数」快照语义
    # 封面来自 otoge PR 预读（MuNET 无封面字段）：真实哈希名
    assert row.image_url == BATCH_COVER

    # 别名收割（MuNET 真实别名）
    aliases = await store.load_song_aliases(["munet"])
    assert aliases[BATCH_ENTRY_ID] == ["物语", "舞萌皇帝"]

    # 下一轮重建：otoge title join 与已写入值一致（日期建曲时已按事实落定）
    await songdb.rebuild(full_payloads())
    state = await songdb.State.load()
    assert state.groups[(BATCH_ENTRY_ID, "dx")].date == 260917


async def test_batch_supplement_is_idempotent(db, monkeypatched_munet):
    from nonebot_plugin_awmc_helper.core import songdb
    from nonebot_plugin_awmc_helper.core.ext import munet

    await songdb.rebuild(full_payloads())
    first = await munet.run_batch_supplement()
    assert first["status"] == "batch"
    assert first["entries"] == 1
    # 首轮已建曲 → 物語はここから 退出候选；宴前缀标题仍为候选但搜索不命中
    second = await munet.run_batch_supplement()
    assert second["candidates"] == 2
    assert second["entries"] == 0


async def test_batch_disabled(db, monkeypatched_munet, monkeypatch):
    from nonebot_plugin_awmc_helper.config import plugin_config
    from nonebot_plugin_awmc_helper.core.ext import munet

    monkeypatch.setattr(plugin_config, "awmc_munet_batch", False)
    assert (await munet.run_batch_supplement())["status"] == "disabled"


@pytest.mark.asyncio
async def test_pending_titles_skip_over_attempts_threshold(db, monkeypatched_munet):
    """pending 候选降频（X-3）：attempts 达阈值的标题不再进批次候选，
    未达阈值的照常参与；字段计数语义见 store.SongPending.attempts。"""
    from nonebot_plugin_awmc_helper.core import store, songdb
    from nonebot_plugin_awmc_helper.core.ext import munet

    await songdb.upsert_pending(
        "otoge-db", "title:超限曲", "missing_id", {"title": "超限曲"}
    )
    await songdb.upsert_pending(
        "otoge-db", "title:新鲜曲", "missing_id", {"title": "新鲜曲"}
    )
    # 人为把「超限曲」计数抬到阈值
    async with store.session() as session_:
        row = (
            await session_.exec(
                select(store.SongPending).where(store.SongPending.key == "title:超限曲")
            )
        ).one()
        row.attempts = munet._PENDING_MAX_ATTEMPTS
        session_.add(row)
        await session_.commit()

    titles = await munet._pending_titles()
    assert "新鲜曲" in titles
    assert "超限曲" not in titles


async def test_munet_ids_guard_against_otoge_rollback(db, monkeypatched_munet):
    """otoge 侧回滚（三曲从现役表消失）后重建，MuNET 已入库曲不被误删。"""
    from nonebot_plugin_awmc_helper.core import songdb
    from nonebot_plugin_awmc_helper.core.ext import munet

    await songdb.rebuild(full_payloads())
    await munet.run_batch_supplement()

    # 模拟 otoge 回滚：现役表只剩旧曲（三曲从 PR/main 双双消失）
    rollback = {
        "maimaiinfo": make_all_data(),
        "dschange": make_dschange(),
        "otoge_db": make_otoge_live(),  # otoge main 本来就无三曲
        "otoge_deleted": make_otoge_deleted(),
        "lxns": make_lxns(),
        "divingfish": make_divingfish(),
    }
    # 机台/extra 无三曲（服务器实况：magical.json 是旧快照）
    await songdb.rebuild(rollback, extra_jp_ids=await songdb._munet_known_ids())
    state = await songdb.State.load()
    assert state.songs.get(BATCH_ENTRY_ID) is not None  # 不被回滚误删


async def test_batch_corrects_poisoned_version(db, monkeypatched_munet, monkeypatch):
    """既有曲版本校正（2026-10-03 OV3RCLOCK 实况）：批次建曲写入国际服批次码
    后，otoge 权威值（PR 预读）出现时经 override 通道纠正（fill/基础源都只填
    空，不校正会永久滞留）。"""
    from nonebot_plugin_awmc_helper.core import songdb
    from nonebot_plugin_awmc_helper.core.ext import munet, otoge_pr

    await songdb.rebuild(full_payloads())
    await munet.run_batch_supplement()
    # 模拟国际服先行曲误写：组版本退化为国际服码（addVersion 26 → CiRCLE PLUS）
    state = await songdb.State.load()
    state.groups[(BATCH_ENTRY_ID, "dx")].version = 26500
    await state.save()

    async def fake_pr():
        # 真实 PR #1213 口径：MAGiCAL 期中 27002 + release 261002
        return [
            {
                "title": "物語はここから",
                "image_url": BATCH_COVER,
                "version": "27002",
                "release": "261002",
            }
        ]

    monkeypatch.setattr(otoge_pr, "load_open_pr_entries", fake_pr)
    result = await munet.run_batch_supplement()
    assert result["corrected"] == [BATCH_ENTRY_ID]
    state = await songdb.State.load()
    group = state.groups[(BATCH_ENTRY_ID, "dx")]
    assert group.version == 27002
    assert group.date == 261002

    # 稳态：值已一致，下一轮不再产生校正
    second = await munet.run_batch_supplement()
    assert "corrected" not in second


async def test_batch_intl_first_song_uses_otoge_fact(
    db, monkeypatched_munet, monkeypatch
):
    """国际服先行曲建曲（OV3RCLOCK 形状）：optJapan 空的条目不写 MuNET 版本码
    （26→26500），otoge PR 事实在场时按日服权威值建曲（day-0 正确版本）。"""
    from nonebot_plugin_awmc_helper.core import songdb
    from nonebot_plugin_awmc_helper.core.ext import munet, otoge_pr

    intl_entry = {
        "id": 2024,
        "name": "OV3RCLOCK",
        "artist": "Alicemetix",
        "bpm": 118,
        "genre": 105,
        "addVersion": 26,
        "version": 26506,
        "aliases": [],
        "charts": [
            {
                "difficulty": 3,
                "kind": 1,
                "designer": "",
                "utageId": 0,
                "optJapan": "",
                "optInternational": "A031",
                "playableArea": 2,
                "releaseTime": None,
                "tapCount": 685,
                "holdCount": 40,
                "slideCount": 91,
                "touchCount": 24,
                "breakCount": 87,
                "constants": [{"version": 26, "constant": 14.8}],
            }
        ],
    }

    async def fake_search(query):
        if query == "OV3RCLOCK":
            return [dict(intl_entry)]
        return []

    async def fake_by_id(music_id):
        return dict(intl_entry) if music_id == 2024 else None

    async def fake_pr():
        return [
            {
                "title": "OV3RCLOCK",
                "image_url": "360427dd3d2c96ca.png",
                "version": "27002",
                "release": "261002",
            }
        ]

    monkeypatch.setattr(munet, "search_music", fake_search)
    monkeypatch.setattr(munet, "fetch_music_by_id", fake_by_id)
    monkeypatch.setattr(otoge_pr, "load_open_pr_entries", fake_pr)

    await songdb.rebuild(full_payloads())
    result = await munet.run_batch_supplement()
    assert result["entries"] == 1
    state = await songdb.State.load()
    row = state.songs.get(2024)
    assert row is not None
    assert row.title == "OV3RCLOCK"
    group = state.groups[(2024, "dx")]
    assert group.version == 27002  # otoge 事实，而非 MuNET 国际服码 26500
    assert group.date == 261002


@pytest.mark.asyncio
async def test_deletion_sweep_drops_absent_ids(db, monkeypatch):
    """MuNET 删除复核（2026-10-05）：GetById 真缺席（404/空壳）剔出
    munet_batch_ids 保护集并留档缺席裁决；请求失败与在列曲保留、在列
    只验一次（present 缓存）。"""
    from nonebot_plugin_awmc_helper.core.ext import munet

    await db.kv_set("munet_batch_ids", [2020, 2055, 2056])

    async def fake_by_id(music_id: int):
        if music_id == 2055:
            return None  # 删除曲：MuNET 已无索引（确定性缺席）
        if music_id == 2056:
            raise munet.ExtError("MuNET WAF 拦截")  # 不确定态，保留
        return {"name": "x"}

    monkeypatch.setattr(munet, "_MIN_INTERVAL", 0)
    monkeypatch.setattr(munet, "fetch_music_by_id", fake_by_id)

    assert await munet.run_deletion_sweep() == [2055]
    assert await db.kv_get("munet_batch_ids") == [2020, 2056]
    assert await db.kv_get("munet_absent_ids") == [2055]
    assert await db.kv_get("munet_present_ids") == [2020]

    # 在列缓存：2020 不再复核（2055 复核由候选集驱动，无候选时不再请求）
    calls = []

    async def counting_by_id(music_id: int):
        calls.append(music_id)
        return {"name": "x"}

    monkeypatch.setattr(munet, "fetch_music_by_id", counting_by_id)
    assert await munet.run_deletion_sweep() == []
    assert 2020 not in calls

    # 空留痕 / 全量失败：无缺席、留痕不动
    await db.kv_set("munet_batch_ids", [])

    async def fail_by_id(music_id: int):
        raise munet.ExtNetworkError("down")

    monkeypatch.setattr(munet, "fetch_music_by_id", fail_by_id)
    assert await munet.run_deletion_sweep() == []
    await db.kv_set("munet_batch_ids", [2055])
    assert await munet.run_deletion_sweep() == []
    assert await db.kv_get("munet_batch_ids") == [2055]


def _deleted_song_entry() -> dict:
    """（构造）12055 上线前删除曲原型：复用真实 DX 条目（10199）改键改名。

    真实原型 = ループザルーム/12055（maimaiinfo 机台全集收录、otoge 现役/
    下架与 MuNET 皆无），快照无此原型，按口径以（构造）补位。
    """
    import copy

    entry = copy.deepcopy(make_all_data()["10199"])
    entry["id"] = "12055"
    entry["basic_info"]["title"] = "（构造）删除曲原型"
    return entry


@pytest.mark.asyncio
async def test_munet_absent_filters_maimaiinfo_resurrection(db):
    """缺席信号（2026-10-05）：rebuild 跳过 maimaiinfo 重建行并整曲剔除，
    复活防护——maimaiinfo 持续收录的删除曲不再随重建回潮；缺席压过外部
    补充源在列信号（机台快照残留）。"""
    from nonebot_plugin_awmc_helper.core import songdb

    payloads = full_payloads()
    payloads["maimaiinfo"]["12055"] = _deleted_song_entry()

    # 无缺席信号：maimaiinfo 在列 → 建行保留（回滚保护依赖此现状语义）
    await songdb.rebuild(payloads)
    assert 2055 in (await songdb.State.load()).songs

    # 缺席信号：过滤重建行 + 整曲删除
    await songdb.rebuild(payloads, excluded_ids={2055})
    assert 2055 not in (await songdb.State.load()).songs

    # 复活防护：再次重建（maimaiinfo 仍收录）不复现
    await songdb.rebuild(payloads, excluded_ids={2055})
    assert 2055 not in (await songdb.State.load()).songs

    # 缺席压过「机台快照在列」（extra_jp_ids 单独存在时保留）
    await songdb.rebuild(payloads, extra_jp_ids={2055})
    assert 2055 in (await songdb.State.load()).songs
    await songdb.rebuild(payloads, extra_jp_ids={2055}, excluded_ids={2055})
    assert 2055 not in (await songdb.State.load()).songs


@pytest.mark.asyncio
async def test_excluded_song_ids_union(db, monkeypatch):
    """剔除集（2026-10-05）：MuNET 缺席裁决 kv ∪ 部署屏蔽名单（显示 id
    归一根 id）。"""
    from nonebot_plugin_awmc_helper.core import songdb
    from nonebot_plugin_awmc_helper.config import plugin_config

    await db.kv_set("munet_absent_ids", [845])
    monkeypatch.setattr(plugin_config, "awmc_song_denylist", [12055, 2056])

    assert await songdb._excluded_song_ids() == {845, 2055, 2056}
