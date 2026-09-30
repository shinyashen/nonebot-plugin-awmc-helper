"""core/ext/otoge_db 音击抓取 + songdb 侧别标题集存储测试（中二/音击区分）。

覆盖：ongeki 两文件一体抓取（music.json + music-ex-deleted.json，任一失败
整体抛错 → 保留旧集合）、kv_cache 单行替换 + 进程缓存更新、惰性加载。
真实锚（2026-09-30 调查）：现役オンゲキ栏 449、下架 5（Titania 等）→ 归一
约 421 键；测试用曲名取真实条目。
"""

import respx
import pytest

ONGEKI_BASE = "https://raw.githubusercontent.com/zvuc/otoge-db/main/ongeki/data"

MUSIC = [
    {"title": "STARTLINER", "category": "オンゲキ"},
    {"title": "Perfect Shining!!", "category": "オンゲキ"},
    {"title": "チュウマイ", "category": "チュウマイ"},  # 联动栏不收
]
DELETED = [{"title": "Titania", "category": "オンゲキ"}]


@pytest.fixture
async def db(tmp_path):
    from nonebot_plugin_awmc_helper.core import store

    store.set_db_file(tmp_path / "awmc.db")
    await store.init_db()
    yield
    store.set_db_file(None)


@pytest.fixture(autouse=True)
def _reset_ongeki_cache():
    from nonebot_plugin_awmc_helper.core import songdb

    songdb._ongeki_origin_titles = None
    yield
    songdb._ongeki_origin_titles = None


@respx.mock
@pytest.mark.asyncio
async def test_fetch_ongeki_origin_merges_both_files():
    """两文件一体抓取：现役 + 下架合并返回（调用方统一过滤原创栏）。"""
    from nonebot_plugin_awmc_helper.core.ext import otoge_db

    with respx.mock(assert_all_called=False) as m:
        m.get(f"{ONGEKI_BASE}/music.json").respond(json=MUSIC)
        m.get(f"{ONGEKI_BASE}/music-ex-deleted.json").respond(json=DELETED)
        entries = await otoge_db.fetch_ongeki_origin()
    assert len(entries) == len(MUSIC) + len(DELETED) == 4


@respx.mock
@pytest.mark.asyncio
async def test_fetch_ongeki_origin_all_or_nothing():
    """下架文件缺失 → 整体抛错（不允部分成功，防下架档缺口）。"""
    from nonebot_plugin_awmc_helper.core.ext import otoge_db

    with respx.mock(assert_all_called=False) as m:
        m.get(f"{ONGEKI_BASE}/music.json").respond(json=MUSIC)
        m.get(f"{ONGEKI_BASE}/music-ex-deleted.json").respond(status_code=404)
        from nonebot_plugin_awmc_helper.core.ext import ExtError

        with pytest.raises(ExtError):
            await otoge_db.fetch_ongeki_origin()


@pytest.mark.asyncio
async def test_store_ongeki_titles(db):
    """入库：过滤オンゲキ栏 → norm_title 集 → kv_cache 替换 + 进程缓存更新。"""
    from nonebot_plugin_awmc_helper.core import store, songdb

    ok = await songdb.store_ongeki_titles([*MUSIC, *DELETED])
    assert ok
    assert songdb.norm_title("チュウマイ") not in songdb.ongeki_titles()
    assert songdb.norm_title("STARTLINER") in songdb.ongeki_titles()
    assert songdb.norm_title("Titania") in songdb.ongeki_titles()  # 下架并入
    # kv 持久化 + 惰性加载回读一致
    stored = await store.kv_get("ongeki_origin_titles")
    assert songdb.norm_title("Perfect Shining!!") in set(stored)


@pytest.mark.asyncio
async def test_store_ongeki_titles_none_keeps_old(db):
    """一体抓取失败（payload=None）→ 保留旧集合（断网降级现成语义）。"""
    from nonebot_plugin_awmc_helper.core import songdb

    assert await songdb.store_ongeki_titles([*MUSIC, *DELETED])
    old = songdb.ongeki_titles()
    assert await songdb.store_ongeki_titles(None) is False
    assert songdb.ongeki_titles() == old
    assert songdb.norm_title("STARTLINER") in songdb.ongeki_titles()


@pytest.mark.asyncio
async def test_ensure_ongeki_titles_lazy_load(db):
    """冷启动惰性加载：kv 值 → 进程 frozenset；幂等。"""
    from nonebot_plugin_awmc_helper.core import store, songdb

    await store.kv_set("ongeki_origin_titles", ["startliner", "titania"])
    await songdb.ensure_ongeki_titles()
    assert songdb.ongeki_titles() == frozenset({"startliner", "titania"})
    await songdb.ensure_ongeki_titles()  # 幂等不重读
    assert songdb.ongeki_titles() == frozenset({"startliner", "titania"})
