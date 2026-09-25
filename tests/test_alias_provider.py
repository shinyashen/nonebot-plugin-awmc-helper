"""core/provider.AwmcAliasProvider：柚子+落雪+本地 三源别名合并与持久化（Q31）。"""

import httpx
import respx
import pytest

LXNS_ALIAS_URL = "https://maimai.lxns.net/api/v0/maimai/alias/list"
YUZU_ALIAS_URL = "https://www.yuzuchan.moe/api/maimaidx/maimaidxalias"


@pytest.fixture
async def db(tmp_path):
    from nonebot_plugin_awmc_helper.core import store

    store.set_db_file(tmp_path / "awmc.db")
    await store.init_db()
    yield store
    store.set_db_file(None)


@pytest.fixture
def maimai_client():
    from nonebot_plugin_awmc_helper.core.client import client

    return client


@pytest.fixture
def provider():
    from nonebot_plugin_awmc_helper.core.client import lxns_provider, yuzu_provider
    from nonebot_plugin_awmc_helper.core.provider import AwmcAliasProvider

    return AwmcAliasProvider(yuzu_provider, lxns_provider)


@pytest.fixture
def remote_mock():
    with respx.mock(assert_all_called=False) as mock:
        mock.get(LXNS_ALIAS_URL).mock(
            return_value=httpx.Response(
                200,
                json={"aliases": [{"song_id": 199, "aliases": ["チルノ", "9"]}]},
            )
        )
        mock.get(YUZU_ALIAS_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "content": [
                        {"SongID": 10199, "Alias": ["dxチルノ", "9"]},
                        {"SongID": 100018, "Alias": ["協チルノ"]},
                        # 后缀剥离（入库层）：dx/标准 后缀剥为基名；iidx 等英文词保护
                        {"SongID": 10363, "Alias": ["牛奶猫dx", "牛奶猫标准", "iidx"]},
                    ]
                },
            )
        )
        yield mock


@pytest.mark.asyncio
async def test_merge_fold_and_persist(db, provider, remote_mock, maimai_client):
    """三源合并：柚子谱面级 SongID 折叠根 id，落雪并集，快照入库。"""
    from sqlmodel import select

    from nonebot_plugin_awmc_helper.core import store

    # 数据侧去前后缀：dxチルノ 剥为 チルノ；宴 kanji 剥离需 DB 有该曲宴谱汉字
    merged = await provider.get_aliases(maimai_client)
    # 10199（DX 谱）折叠进根 id 199，与落雪条目并集
    assert sorted(merged[199]) == ["9", "チルノ"]
    assert merged[18] == ["協チルノ"]  # DB 无宴谱汉字记录时 kanji 前缀暂不剥
    # 后缀入库即剥：牛奶猫dx/牛奶猫标准 → 牛奶猫（去重后单条）；iidx 保留原样
    assert merged[363] == ["牛奶猫", "iidx"]
    assert provider._hash() != "empty"
    # 快照入库（按源）
    async with store.session() as session:
        all_rows = list((await session.exec(select(store.SongAlias))).all())
    by_source = {(r.source, r.song_id, r.alias) for r in all_rows}
    # 快照存原始形态（无损），去前后缀统一在合并层发生
    assert ("yuzu", 199, "dxチルノ") in by_source
    assert ("yuzu", 199, "9") in by_source
    assert ("lxns", 199, "チルノ") in by_source
    assert ("yuzu", 363, "牛奶猫dx") in by_source
    assert ("yuzu", 363, "牛奶猫标准") in by_source
    assert ("yuzu", 363, "iidx") in by_source


@pytest.mark.asyncio
async def test_offline_falls_back_to_snapshot(db, provider, remote_mock, maimai_client):
    """远端失败：回退上次入库快照（内容不变则指纹不变）。"""
    await provider.get_aliases(maimai_client)  # 首次成功并入库
    fp = provider._hash()
    remote_mock.get(LXNS_ALIAS_URL).mock(return_value=httpx.Response(500))
    remote_mock.get(YUZU_ALIAS_URL).mock(return_value=httpx.Response(500))
    merged = await provider.get_aliases(maimai_client)
    assert sorted(merged[199]) == ["9", "チルノ"]
    assert provider._hash() == fp


@pytest.mark.asyncio
async def test_hash_changes_on_remote_update(db, provider, remote_mock, maimai_client):
    """远端别名更新 → 内容哈希变化（驱动 maimai_py 自动重建缓存）。"""
    await provider.get_aliases(maimai_client)
    fp = provider._hash()
    remote_mock.get(LXNS_ALIAS_URL).mock(
        return_value=httpx.Response(
            200,
            json={"aliases": [{"song_id": 199, "aliases": ["チルノ", "9", "新别名"]}]},
        )
    )
    await provider.get_aliases(maimai_client)
    assert provider._hash() != fp


@pytest.mark.asyncio
async def test_local_alias_merged(db, provider, remote_mock, maimai_client):
    """local_alias 表并入合并视图（大小写不敏感去重）。"""
    from nonebot_plugin_awmc_helper.core import store

    await store.add_local_alias(199, "局部别名", "tester")
    await store.add_local_alias(
        199, "チルノ", "tester"
    )  # 与远端重复（大小写不敏感去重）
    merged = await provider.get_aliases(maimai_client)
    assert "局部别名" in merged[199]
    assert merged[199].count("チルノ") == 1
