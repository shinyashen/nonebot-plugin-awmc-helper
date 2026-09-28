"""core/store：统一 SQLite 存取测试。"""

import asyncio
from pathlib import Path

import pytest
from sqlmodel import select


@pytest.fixture
async def tmp_db(tmp_path: Path):
    """每个用例独立的临时数据库文件。"""
    from nonebot_plugin_awmc_helper.core import store

    store.set_db_file(tmp_path / "awmc.db")
    await store.init_db()
    yield store
    store.set_db_file(None)


@pytest.mark.asyncio
async def test_binding_crud(tmp_db):
    store = tmp_db
    assert await store.get_binding("qq", "10001") is None

    binding = store.UserBinding(
        platform="qq", user_id="10001", service="divingfish", divingfish_username="test"
    )
    await store.save_binding(binding)
    got = await store.get_binding("qq", "10001")
    assert got is not None
    assert got.divingfish_username == "test"

    got.divingfish_username = "renamed"
    await store.save_binding(got)
    got2 = await store.get_binding("qq", "10001")
    assert got2 is not None
    assert got2.divingfish_username == "renamed"

    assert await store.delete_binding("qq", "10001")
    assert await store.get_binding("qq", "10001") is None


@pytest.mark.asyncio
async def test_group_switch_override(tmp_db):
    store = tmp_db
    assert await store.get_group_switch("g1", "guess") is None
    await store.set_group_switch("g1", "guess", False)
    assert await store.get_group_switch("g1", "guess") is False
    await store.set_group_switch("g1", "guess", True)
    assert await store.get_group_switch("g1", "guess") is True
    assert await store.get_group_switch("g2", "guess") is None


@pytest.mark.asyncio
async def test_local_alias_unique(tmp_db):
    store = tmp_db
    assert await store.add_local_alias(231, "企鹅", "u1")
    assert not await store.add_local_alias(231, "企鹅", "u2")  # 唯一约束
    assert await store.add_local_alias(500, "企鹅", "u1")  # 同别名不同曲允许
    aliases = await store.get_local_aliases()
    assert len(aliases) == 2


@pytest.mark.asyncio
async def test_kv_cache(tmp_db):
    store = tmp_db
    assert await store.kv_get("songs_snapshot") is None
    await store.kv_set("songs_snapshot", {"songs": [{"id": 1}], "aliases": {}})
    data = await store.kv_get("songs_snapshot")
    assert data == {"songs": [{"id": 1}], "aliases": {}}
    await store.kv_set("songs_snapshot", {"songs": []})
    assert await store.kv_get("songs_snapshot") == {"songs": []}


@pytest.mark.asyncio
async def test_arcade_tables(tmp_db):
    store = tmp_db
    arcade = store.Arcade(
        id=10000, name="游戏厅", address="某地", machines=4, is_custom=True
    )
    await store.save_arcade(arcade)
    assert len(await store.get_arcades_by_name("游戏")) == 1
    assert await store.get_arcades_by_name("不存在") == []

    assert await store.add_arcade_alias(10000, "Game")
    assert not await store.add_arcade_alias(10001, "Game")  # 别名全局唯一
    assert len(await store.get_arcades_by_name("game")) == 1  # 别名模糊匹配

    await store.subscribe("g1", 10000)
    assert await store.get_subscriptions("g1") == [10000]
    await store.unsubscribe("g1", 10000)
    assert await store.get_subscriptions("g1") == []

    await store.add_count_log(10000, 2, 6, "u1")  # 写入路径不抛错即可

    assert await store.delete_arcade(10000)
    assert await store.get_arcade(10000) is None


@pytest.mark.asyncio
async def test_update_arcade_count_atomic(tmp_db):
    """排卡/机台数原子更新：相对增量单语句 UPDATE + RETURNING，防并发丢更新。"""
    store = tmp_db
    await store.save_arcade(store.Arcade(id=1, name="测试厅", machines=4, person=5))

    # inc/dec/set 与 dec 的钳 0 下限
    assert await store.update_arcade_count(1, "person", "inc", 2, updated_by="u1") == 7
    assert await store.update_arcade_count(1, "person", "dec", 3) == 4
    assert await store.update_arcade_count(1, "person", "dec", 99) == 0
    assert await store.update_arcade_count(1, "machines", "set", 7, touch=False) == 7

    async with store.session() as db:
        row = (await db.exec(select(store.Arcade).where(store.Arcade.id == 1))).one()
    assert (row.person, row.machines) == (0, 7)
    assert row.updated_by == "u1"  # 只有带 updated_by 的调用记录操作人

    # 行不存在返回 None
    assert await store.update_arcade_count(999, "person", "inc", 1) is None

    # 并发语义：模拟旧「读-改-写」路径下两人同时 +1 只净加 1 的场景——
    # 原子增量下交错执行仍各计各的（同一事件循环内串行命中 DB，最终 +2）
    await store.update_arcade_count(1, "person", "set", 0)
    await asyncio.gather(
        store.update_arcade_count(1, "person", "inc", 1),
        store.update_arcade_count(1, "person", "inc", 1),
    )
    async with store.session() as db:
        row = (await db.exec(select(store.Arcade).where(store.Arcade.id == 1))).one()
    assert row.person == 2


@pytest.mark.asyncio
async def test_save_song_aliases_dedupes(tmp_db):
    """远端源内同曲完全重复的别名（柚子数据实测存在）入库前精确去重。"""
    store = tmp_db
    await store.save_song_aliases("yuzu", {8: ["糖糖", "糖糖", "真 Love"], 9: ["糖糖"]})
    merged = await store.load_song_aliases(["yuzu"])
    assert len(merged[8]) == 2  # 重复对被去掉
    assert set(merged[8]) == {"糖糖", "真 Love"}  # 存储层不保证顺序
    assert merged[9] == ["糖糖"]

    # 整源替换：第二次写入覆盖第一次
    await store.save_song_aliases("yuzu", {8: ["糖糖"]})
    assert await store.load_song_aliases(["yuzu"]) == {8: ["糖糖"]}


@pytest.mark.asyncio
async def test_binding_refresh_token(tmp_db):
    store = tmp_db
    binding = store.UserBinding(
        platform="qq",
        user_id="20001",
        service="lxns",
        lxns_token="at",
        lxns_refresh_token="rt",
    )
    await store.save_binding(binding)
    got = await store.get_binding("qq", "20001")
    assert got is not None
    assert got.lxns_token == "at"
    assert got.lxns_refresh_token == "rt"


@pytest.mark.asyncio
async def test_migrate_adds_refresh_token_column(tmp_path: Path):
    """旧版库（无 lxns_refresh_token 列）经 init_db 迁移补列后可读写该字段。"""
    import sqlite3

    from nonebot_plugin_awmc_helper.core import store

    legacy = tmp_path / "legacy.db"
    con = sqlite3.connect(legacy)
    con.execute(
        "CREATE TABLE user_binding ("
        "platform VARCHAR NOT NULL, user_id VARCHAR NOT NULL, service VARCHAR, "
        "divingfish_username VARCHAR, divingfish_import_token VARCHAR, "
        "lxns_friend_code INTEGER, lxns_token VARCHAR, theme VARCHAR, "
        "bound_at VARCHAR, PRIMARY KEY (platform, user_id))"
    )
    con.commit()
    con.close()

    store.set_db_file(legacy)
    try:
        await store.init_db()
        await store.save_binding(
            store.UserBinding(platform="qq", user_id="1", lxns_refresh_token="rt")
        )
        got = await store.get_binding("qq", "1")
        assert got is not None
        assert got.lxns_refresh_token == "rt"
    finally:
        store.set_db_file(None)
