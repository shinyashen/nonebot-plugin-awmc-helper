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
async def test_by_alias_chart_prefix_fallback(songs):
    """精确未命中时剥离谱面类型前缀重查（Q31：dx/标准/标/旧/sd/宴）。"""
    from nonebot_plugin_awmc_helper.core.songs import song_service

    # 带前缀命中（种子别名「普瑞」「企鹅舞」）
    got = await song_service.by_alias("dx普瑞")
    assert [s.id for s in got] == [500]
    got = await song_service.by_alias("标准企鹅舞")
    assert [s.id for s in got] == [231]
    # 叠层前缀只剥一层：「dx标普瑞」→「标普瑞」不在去前缀库中 → 不命中
    assert await song_service.by_alias("dx标普瑞") == []
    # by_alias_detail 暴露剥离信息（供别名查询提示语）
    detail_songs, strip_info = await song_service.by_alias_detail("dx普瑞")
    assert [s.id for s in detail_songs] == [500]
    assert strip_info == ("普瑞", "dx", "prefix")  # (剥离后别名, 命中词, 位置)
    _, exact_info = await song_service.by_alias_detail("普瑞")
    assert exact_info is None
    # 精确命中优先，不受剥离影响
    got = await song_service.by_alias("普瑞")
    assert [s.id for s in got] == [500]
    # 完全未命中：剥无可剥 → 空
    assert await song_service.by_alias("不存在的别名") == []
    assert await song_service.by_alias("dx") == []


@pytest.mark.asyncio
async def test_by_alias_chart_suffix_fallback(songs):
    """精确未命中时剥离谱面类型后缀重查（dx/标准，柚子库实测形态）。"""
    from nonebot_plugin_awmc_helper.core.songs import song_service

    # 「普瑞dx」库里没有（种子别名只有「普瑞」）→ 剥后缀「普瑞」命中
    got, info = await song_service.by_alias_detail("普瑞dx")
    assert [s.id for s in got] == [500]
    assert info == ("普瑞", "dx", "suffix")
    got, info = await song_service.by_alias_detail("企鹅舞标准")
    assert [s.id for s in got] == [231]
    assert info == ("企鹅舞", "标准", "suffix")
    # 「标」/汉字后缀实测不存在，不剥：查询原样未命中 → 空
    assert (await song_service.by_alias_detail("普瑞标"))[0] == []
    assert (await song_service.by_alias_detail("普瑞宴"))[0] == []
    # 剥完为空不剥
    assert (await song_service.by_alias_detail("标准"))[0] == []


@pytest.mark.asyncio
async def test_alias_title_duplicate_filtered(songs):
    """剥前后缀后与歌名相同的别名不进展示列表（归一化比对）。

    生产链路中 provider 已把「dx翼」剥成「翼」入库，inject 绕过 provider，
    故直接种入剥后形态模拟。
    """
    from mocks import make_song, seed_service

    from nonebot_plugin_awmc_helper.core.songs import song_service

    dup = make_song(456, "翼", aliases=["翼", "ＷＩＮＧ", "小鸟"])
    await seed_service(song_service, [*list(songs), dup])
    try:
        aliases = await song_service.aliases_of(456)
        assert aliases == ["ＷＩＮＧ", "小鸟"]  # 「翼」与歌名重复 → 不展示
        # 查询侧索引仍保留，剥后可正常命中本曲
        got, _ = await song_service.by_alias_detail("小鸟dx")
        assert [s.id for s in got] == [456]
    finally:
        # 绕过 fixture 直调 seed 会置位 _ready，必须清理（否则同 worker
        # 后续依赖「运行时未加载」的测试被污染，如 test_jp_songs_entrypoint）
        song_service._ready.clear()


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


@pytest.mark.asyncio
async def test_jp_title_fallback(songs, tmp_path):
    """国服标题未命中 → 日服视图标题子串匹配（Q32）。"""
    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.core.songs import song_service

    # 日服限定曲仅入 DB 规范表（CN 运行时视图无此曲）；日服视图只收有谱面组的曲
    async with store.session() as session:
        session.add(
            store.SongRow(
                id=2019,
                title="Cryogenic",
                artist="Camellia ft. Petra Gurin「In Falsus」",
                bpm="180",
                image_url="c4ec.png",
            )
        )
        session.add(
            store.SongSheetGroup(
                song_id=2019, kind="dx", version=27000, version_cn=None, date=20260702
            )
        )
        session.add(
            store.SongChart(
                song_id=2019, kind="dx", level_id=3, notes_tap=100, designer="TEST"
            )
        )
        session.add(
            store.SongChartLevel(
                song_id=2019, kind="dx", level_id=3, version=27000, level_value=13.5
            )
        )
        await session.commit()
    song_service._jp_view = {}
    song_service._jp_fingerprint = None
    try:
        got = await song_service.jp_by_title_fuzzy("cryo")
        assert [s.id for s in got] == [2019]
        assert got[0].title == "Cryogenic"
        # 国服视图确实没有这首
        assert await song_service.by_title_fuzzy("cryo") == []
    finally:
        song_service._jp_view = {}
        song_service._jp_fingerprint = None


@pytest.mark.asyncio
async def test_jp_attribute_fallbacks(songs):
    """定数/BPM/曲师/谱师四条属性查询的日服视图版本（Q32）。"""
    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.core.songs import song_service

    song_service._jp_view = {}
    song_service._jp_fingerprint = None
    try:
        # 日服限定曲仅入 DB 规范表（CN 运行时视图无此曲）；日服视图只收有谱面组的曲
        async with store.session() as session:
            session.add(
                store.SongRow(
                    id=2019,
                    title="Cryogenic",
                    artist="Camellia ft. Petra Gurin「In Falsus」",
                    bpm="180",
                    image_url="c4ec.png",
                )
            )
            session.add(
                store.SongSheetGroup(
                    song_id=2019,
                    kind="dx",
                    version=27000,
                    version_cn=None,
                    date=20260702,
                )
            )
            session.add(
                store.SongChart(
                    song_id=2019,
                    kind="dx",
                    level_id=3,
                    notes_tap=100,
                    designer="TEST",
                )
            )
            session.add(
                store.SongChartLevel(
                    song_id=2019,
                    kind="dx",
                    level_id=3,
                    version=27000,
                    level_value=13.5,
                )
            )
            await session.commit()
        lv = await song_service.jp_by_level_value(13.0, 14.0)
        assert [s.id for s in lv] == [2019]
        assert await song_service.jp_by_level_value(10.0, 11.0) == []
        bpm = await song_service.jp_by_bpm(170, 190)
        assert [s.id for s in bpm] == [2019]
        assert await song_service.jp_by_artist("Camellia") == []
        artist = "camellia ft. petra gurin「in falsus」"
        assert [s.id for s in await song_service.jp_by_artist(artist)] == [2019]
        designer = await song_service.jp_by_note_designer("test")
        assert [s.id for s in designer] == [2019]
    finally:
        song_service._jp_view = {}
        song_service._jp_fingerprint = None
