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
    assert group.version == 27000  # addVersion 27 → 日服码
    assert group.date is None  # 真实 MuNET 条目无 releaseTime，日期留给 otoge
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

    # 下一轮重建：otoge title join 充实日期（真实 release 260917）
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
