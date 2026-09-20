"""core/store：统一 SQLite 存取测试。"""

from pathlib import Path

import pytest


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
    assert await store.remove_local_alias(231, "企鹅")
    assert not await store.remove_local_alias(231, "企鹅")


@pytest.mark.asyncio
async def test_kv_cache(tmp_db):
    store = tmp_db
    assert await store.kv_get("songs_snapshot") is None
    await store.kv_set("songs_snapshot", {"songs": [{"id": 1}], "aliases": {}})
    data = await store.kv_get("songs_snapshot")
    assert data == {"songs": [{"id": 1}], "aliases": {}}
    assert await store.kv_updated_at("songs_snapshot") is not None
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

    await store.add_count_log(10000, 2, 6, "u1")
    logs = await store.get_count_logs(10000)
    assert len(logs) == 1
    assert logs[0].delta == 2
    assert logs[0].machines == 6

    assert await store.delete_arcade(10000)
    assert await store.get_arcade(10000) is None
