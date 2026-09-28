"""MuNET current_jp 批次内新增歌曲补充：title-diff 候选 → 拉取 → fill 合并。"""

import json

import pytest
from songdb_fixtures import (
    make_lxns,
    make_all_data,
    make_dschange,
    make_divingfish,
    make_otoge_live,
    make_otoge_deleted,
)

BATCH_ENTRY_ID = 2059
BATCH_PAYLOAD = {
    "id": BATCH_ENTRY_ID,
    "name": "Test Batch Song",
    "artist": "iKz",
    "bpm": 155,
    "genre": 105,
    "addVersion": 27,
    "aliases": [{"alias": "批次测试曲"}],
    "charts": [
        {
            "difficulty": 3,
            "kind": 1,
            "designer": "譜面-人",
            "utageId": 0,
            "releaseTime": "2026-09-17T00:00:00+00:00",
            "tapCount": 158,
            "holdCount": 9,
            "slideCount": 22,
            "touchCount": 8,
            "breakCount": 5,
            "constants": [{"version": 27, "constant": 13.4}],
        }
    ],
}


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

    async def fake_filters():
        return {"genres": [101], "versions": [0, 27]}

    async def fake_search(query):
        if query == "Test Batch Song":
            return [dict(BATCH_PAYLOAD)]
        if query == "Pending Song":
            return [{**BATCH_PAYLOAD, "id": 2060, "name": "Pending Song"}]
        return []

    async def fake_by_id(music_id):
        if music_id == BATCH_ENTRY_ID:
            return dict(BATCH_PAYLOAD)
        if music_id == 2060:
            return {**BATCH_PAYLOAD, "id": 2060, "name": "Pending Song"}
        return None

    async def fake_pr():
        return [{"title": "Test Batch Song", "image_url": "batch-cover.png"}]

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
    async with store.session() as s:
        s.add(
            store.SongPending(
                source="otoge-db",
                key="title:Pending Song",
                payload=json.dumps({"title": "Pending Song"}),
            )
        )
        await s.commit()

    result = await munet.run_batch_supplement()
    assert result["status"] == "batch"
    assert result["entries"] == 2  # PR 预读 + song_pending 各解析出一首

    state = await songdb.State.load()
    row = state.songs.get(BATCH_ENTRY_ID)
    assert row is not None
    assert row.title == "Test Batch Song"
    group = state.groups.get((BATCH_ENTRY_ID, "dx"))
    assert group is not None
    assert group.version == 27000  # addVersion 27 → 日服码
    assert group.date == 20260917
    chart = state.chart(BATCH_ENTRY_ID, "dx", 3)
    assert chart.designer == "譜面-人"
    assert (chart.notes_tap, chart.notes_slide) == (158, 22)
    history = state.history_of(BATCH_ENTRY_ID, "dx", 3)
    assert history
    assert history[-1][1] == 13.4  # 当前定数快照语义
    # 封面来自 otoge PR 预读（MuNET 无封面字段）
    assert row.image_url == "batch-cover.png"
    assert 2060 in state.songs

    aliases = await store.load_song_aliases(["munet"])
    assert aliases[BATCH_ENTRY_ID] == ["批次测试曲"]


async def test_batch_supplement_is_idempotent(db, monkeypatched_munet):
    from nonebot_plugin_awmc_helper.core import store, songdb
    from nonebot_plugin_awmc_helper.core.ext import munet

    await songdb.rebuild(full_payloads())
    async with store.session() as s:
        s.add(
            store.SongPending(
                source="otoge-db",
                key="title:Pending Song",
                payload=json.dumps({"title": "Pending Song"}),
            )
        )
        await s.commit()
    first = await munet.run_batch_supplement()
    assert first["status"] == "batch"
    assert first["entries"] == 2
    # 首轮已建曲 → 二轮候选为空（title-diff 幂等）
    second = await munet.run_batch_supplement()
    assert second["entries"] == 0


async def test_batch_disabled(db, monkeypatched_munet, monkeypatch):
    from nonebot_plugin_awmc_helper.config import plugin_config
    from nonebot_plugin_awmc_helper.core.ext import munet

    monkeypatch.setattr(plugin_config, "awmc_munet_batch", False)
    assert (await munet.run_batch_supplement())["status"] == "disabled"
