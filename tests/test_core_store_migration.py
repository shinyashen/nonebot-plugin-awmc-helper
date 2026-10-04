"""core/store 列自动迁移与生态引擎工厂测试。

覆盖：
- 旧库缺列经 init_db 自动补齐（简单列按模型定义生成 ADD COLUMN），
  旧行保留且新列取模型语义默认值（如 ``divingfish_oauth`` 旧行读 False）；
- 全新库建表路径不变（各表列集与模型完全一致）；
- 幂等：连续两次 init_db 不报错、不重复加列；
- 自动迁移不可为的列（NOT NULL + default_factory）走 warning 并跳过；
- ``create_plugin_engine`` / ``init_plugin_db`` 工厂直调（兄弟仓接法）。
"""

import sqlite3
from pathlib import Path

import pytest
from sqlmodel import SQLModel
from sqlalchemy import inspect as sa_inspect

# 旧版 user_binding（补 divingfish_oauth 等列前的真实形态，与
# test_core_store.py 的历史用例同源）：缺 5 个后加简单列
_LEGACY_USER_BINDING_DDL = (
    "CREATE TABLE user_binding ("
    "platform VARCHAR NOT NULL, user_id VARCHAR NOT NULL, service VARCHAR, "
    "divingfish_username VARCHAR, divingfish_import_token VARCHAR, "
    "lxns_friend_code INTEGER, lxns_token VARCHAR, theme VARCHAR, "
    "bound_at VARCHAR, PRIMARY KEY (platform, user_id))"
)

# 旧版 arcade（缺后加简单列 updated_by）
_LEGACY_ARCADE_DDL = (
    "CREATE TABLE arcade ("
    "id INTEGER NOT NULL, name VARCHAR NOT NULL, address VARCHAR NOT NULL, "
    "province VARCHAR NOT NULL, mall VARCHAR NOT NULL, machines INTEGER NOT NULL, "
    "person INTEGER NOT NULL, is_custom BOOLEAN NOT NULL, "
    "updated_at VARCHAR NOT NULL, PRIMARY KEY (id))"
)


def _pragma_columns(db_path: Path, table: str) -> set[str]:
    """读某表现有列名集合。"""
    con = sqlite3.connect(db_path)
    try:
        return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
    finally:
        con.close()


@pytest.mark.asyncio
async def test_auto_migration_backfills_legacy_columns(tmp_path: Path):
    """旧库缺简单列 → init_db 按模型定义自动补齐，旧行保留、默认值符合模型语义。"""
    from nonebot_plugin_awmc_helper.core import store

    legacy = tmp_path / "legacy.db"
    con = sqlite3.connect(legacy)
    con.execute(_LEGACY_USER_BINDING_DDL)
    con.execute(_LEGACY_ARCADE_DDL)
    con.execute(
        "INSERT INTO user_binding (platform, user_id, service, theme, bound_at) "
        "VALUES ('qq', '10001', 'divingfish', 'circle', '2026-01-01 00:00:00')"
    )
    con.execute(
        "INSERT INTO arcade (id, name, address, province, mall, machines, person, "
        "is_custom, updated_at) "
        "VALUES (1, '游戏厅', '某地', '某省', '某mall', 4, 2, 1, '2026-01-01 00:00:00')"
    )
    con.commit()
    con.close()

    store.set_db_file(legacy)
    try:
        await store.init_db()

        # 两表列集都与模型对齐（多表一次遍历）
        assert _pragma_columns(legacy, "user_binding") == set(
            store.UserBinding.__table__.columns.keys()
        )
        assert _pragma_columns(legacy, "arcade") == set(
            store.Arcade.__table__.columns.keys()
        )

        # 旧行保留；后加 bool 列旧行读 False 而非 NULL（与原手写
        # NOT NULL DEFAULT 0 的语义一致，`is True` 类判定不漏行）
        binding = await store.get_binding("qq", "10001")
        assert binding is not None
        assert (binding.service, binding.theme) == ("divingfish", "circle")
        assert binding.divingfish_oauth is False
        assert binding.lxns_refresh_token is None

        arcade = await store.get_arcade(1)
        assert arcade is not None
        assert (arcade.name, arcade.machines, arcade.person) == ("游戏厅", 4, 2)
        assert arcade.is_custom is True
        assert arcade.updated_by == ""  # 常量默认补齐

        # 后加列可正常写入读出
        binding.lxns_refresh_token = "rt"
        await store.save_binding(binding)
        got = await store.get_binding("qq", "10001")
        assert got is not None
        assert got.lxns_refresh_token == "rt"
    finally:
        store.set_db_file(None)


@pytest.mark.asyncio
async def test_fresh_db_schema_matches_models(tmp_path: Path):
    """全新库：建表路径不变，本仓全部表一次建齐、列集与模型一致。"""
    from nonebot_plugin_awmc_helper.core import store

    fresh = tmp_path / "fresh.db"
    store.set_db_file(fresh)
    try:
        await store.init_db()
        tables = store._own_tables()
        assert len(tables) >= 19
        for name, table in tables.items():
            assert _pragma_columns(fresh, name) == set(table.columns.keys()), (
                f"表 {name} 列集与模型不一致"
            )
    finally:
        store.set_db_file(None)


@pytest.mark.asyncio
async def test_init_db_idempotent(tmp_path: Path):
    """连续两次 init_db：不报错、不重复加列（列集不变）。"""
    from nonebot_plugin_awmc_helper.core import store

    db = tmp_path / "idem.db"
    store.set_db_file(db)
    try:
        await store.init_db()
        snapshot = {name: _pragma_columns(db, name) for name in store._own_tables()}
        await store.init_db()
        assert {
            name: _pragma_columns(db, name) for name in store._own_tables()
        } == snapshot
    finally:
        store.set_db_file(None)


@pytest.mark.asyncio
async def test_unmigratable_column_warns_and_skips(tmp_path: Path, monkeypatch):
    """NOT NULL + default_factory 列（kv_cache.updated_at）无法自动迁移：
    log.warning 提醒走特例通道，跳过该列且不中断建库。"""
    from nonebot_plugin_awmc_helper.core import store

    legacy = tmp_path / "warn.db"
    con = sqlite3.connect(legacy)
    con.execute(
        "CREATE TABLE kv_cache ("
        "key VARCHAR NOT NULL, payload VARCHAR NOT NULL, PRIMARY KEY (key))"
    )
    con.commit()
    con.close()

    warnings: list[str] = []

    class _Recorder:
        def warning(self, msg, *args):
            warnings.append(str(msg) % args if args else str(msg))

    monkeypatch.setattr(store, "logger", _Recorder())

    store.set_db_file(legacy)
    try:
        await store.init_db()  # 不因缺列抛错
        assert any("kv_cache.updated_at" in w for w in warnings), (
            f"未对不可迁移列告警：{warnings}"
        )
        # 缺列保持缺失（等特例通道登记），其余表正常建齐
        assert _pragma_columns(legacy, "kv_cache") == {"key", "payload"}
        names = {
            row[0]
            for row in sqlite3.connect(legacy).execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "user_binding" in names
    finally:
        store.set_db_file(None)


@pytest.mark.asyncio
async def test_create_plugin_engine_factory(tmp_path: Path):
    """引擎工厂直调（兄弟仓 arcade/score-updater 后续的接法）：
    拼路径建 aiosqlite 引擎 → init_plugin_db 幂等建表 → 文件落位、表存在。"""
    from nonebot_plugin_awmc_helper.core import store

    engine = store.create_plugin_engine(str(tmp_path), "factory.db")  # str 形参覆盖
    try:
        await store.init_plugin_db(engine, SQLModel.metadata)
        await store.init_plugin_db(engine, SQLModel.metadata)  # 幂等
        assert (tmp_path / "factory.db").exists()
        async with engine.connect() as conn:
            tables = await conn.run_sync(
                lambda sync_conn: set(sa_inspect(sync_conn).get_table_names())
            )
        assert {"user_binding", "arcade", "kv_cache"} <= tables
    finally:
        await engine.dispose()
