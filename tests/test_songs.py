"""core/songs：曲库服务测试（注入样例数据，不走网络）。"""

import pytest
from maimai_py import Genre, SongType  # maimai_py 不依赖 nonebot 初始化


@pytest.fixture
async def songs(tmp_path):
    """注入样例曲库 + 独立临时数据库（插件模块延迟导入：需在 nonebot 初始化后）。"""
    from mocks import seed_service

    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.core.songs import song_service

    store.set_db_file(tmp_path / "awmc.db")
    await store.init_db()
    data = await seed_service(song_service)
    yield data
    song_service._ready.clear()
    store.set_db_file(None)


@pytest.mark.asyncio
async def test_query_by_title_and_id(songs):
    from nonebot_plugin_awmc_helper.core.songs import song_service

    song = await song_service.by_id(231)
    assert song is not None
    assert song.title == "PENGUIN"
    assert await song_service.by_title("Preferences") is not None
    fuzzy = await song_service.by_title_fuzzy("PRE")
    assert {s.id for s in fuzzy} == {500}  # 大小写不敏感子串


@pytest.mark.asyncio
async def test_alias_lookup_incl_disabled_filter(songs):
    from nonebot_plugin_awmc_helper.core.songs import song_service

    got = await song_service.by_alias("普瑞")
    assert got is not None
    assert got[0].id == 500
    # disabled 曲目默认不出现在 get_all
    all_songs = await song_service.get_all()
    assert all(not s.disabled for s in all_songs)
    assert len(await song_service.get_all(include_disabled=True)) == len(songs)


@pytest.mark.asyncio
async def test_filters(songs):
    from nonebot_plugin_awmc_helper.core.songs import song_service

    # 定数查歌：13.0~14.0 命中 500 的 13+（13.7）与 231 的 13.2
    got = await song_service.by_level_value(13.0, 14.0)
    assert {s.id for s in got} == {231, 500}
    # 曲师（大小写不敏感）
    assert {s.id for s in await song_service.by_artist("UZZ")} == {500}
    # 谱师
    assert {s.id for s in await song_service.by_note_designer("サルミ")} == {231, 500}
    # BPM 范围
    assert {s.id for s in await song_service.by_bpm(150, 190)} == {500}
    # 分类（disabled 一律排除）
    assert {s.id for s in await song_service.by_genre(Genre.maimai)} == {231, 500}


@pytest.mark.asyncio
async def test_random(songs):
    from nonebot_plugin_awmc_helper.core.songs import song_service

    got = await song_service.random(song_type=SongType.DX, level="13+")
    assert got is not None
    assert got[0].id == 500
    # 排除宴会谱后按类型过滤
    got2 = await song_service.random(genre=Genre.宴会場, exclude_utage=True)
    assert got2 is None  # 901 只有宴会谱
    got3 = await song_service.random(genre=Genre.宴会場, exclude_utage=False)
    assert got3 is not None
    assert got3[0].id == 901


@pytest.mark.asyncio
async def test_snapshot_roundtrip(songs):
    """快照序列化 → 反序列化应无损还原查询所需字段。"""
    from nonebot_plugin_awmc_helper.core.songs import song_to_dict, song_from_dict

    for song in songs:
        restored = song_from_dict(song_to_dict(song))
        assert restored.id == song.id
        assert restored.title == song.title
        assert (restored.aliases or []) == (song.aliases or [])
        assert restored.genre == song.genre
        for d in song.get_difficulties(SongType.STANDARD) + song.get_difficulties(
            SongType.DX
        ):
            rd = restored.get_difficulty(d.type, d.level_index)
            assert rd is not None
            assert rd.level == d.level
            assert rd.level_value == d.level_value
            assert rd.tap_num == d.tap_num
        for u in song.get_difficulties(SongType.UTAGE):
            ru = restored.get_difficulty(SongType.UTAGE, u.diff_id)
            assert ru is not None
            assert getattr(ru, "kanji") == u.kanji


@pytest.mark.asyncio
async def test_local_alias_hot_reload(songs):
    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.core.songs import song_service

    await store.add_local_alias(902, "废曲别名", "u1")
    await song_service.reload_alias_index()
    got = await song_service.by_alias("废曲别名")
    assert got is not None
    assert got[0].id == 902
