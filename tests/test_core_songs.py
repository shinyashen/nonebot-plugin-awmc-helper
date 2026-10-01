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

    song = await song_service.by_id(199)
    assert song is not None
    assert song.title == "チルノのパーフェクトさんすう教室"
    fuzzy = await song_service.by_title_fuzzy("true love")
    assert {s.id for s in fuzzy} == {8}  # 大小写不敏感子串


def test_exact_first_ordering():
    """标题精确命中置顶、其余 id 升序（上游 by_keywords 精确优先语义单源）。

    纯函数直测：真实快照裁剪后无「精确曲 id 大于子串曲 id」的天然标题对
    （id 升序会掩盖置顶效果），排序语义在此用构造曲区分；子串预过滤是
    调用方职责（by_title_fuzzy），此处先过滤再断言。"""
    from types import SimpleNamespace

    from nonebot_plugin_awmc_helper.core.songs import _exact_first

    songs = [
        SimpleNamespace(id=665, title="チルノのパーフェクトさんすう教室 ⑨周年"),
        SimpleNamespace(id=1355, title="ラグトレイン"),
        SimpleNamespace(id=199, title="チルノのパーフェクトさんすう教室"),
    ]
    # 子串预过滤（调用方职责）后进排序：「チルノの…教室」精确命中 199 置顶
    kw = "チルノのパーフェクトさんすう教室"
    hits = [s for s in songs if kw in s.title.lower()]
    assert [s.id for s in _exact_first(hits, kw)] == [199, 665]
    # 无精确命中：纯 id 升序
    hits = [s for s in songs if "ラグ" in s.title.lower()]
    assert [s.id for s in _exact_first(hits, "ラグ")] == [1355]


@pytest.mark.asyncio
async def test_alias_lookup_incl_disabled_filter(songs):
    from nonebot_plugin_awmc_helper.core.songs import song_service

    got = await song_service.by_alias("琪露诺")
    assert got is not None
    assert got[0].id == 199
    # disabled 曲目默认不出现在 get_all
    all_songs = await song_service.get_all()
    assert all(not s.disabled for s in all_songs)
    assert len(await song_service.get_all(include_disabled=True)) == len(songs)


@pytest.mark.asyncio
async def test_by_alias_chart_prefix_fallback(songs):
    """精确未命中时剥离谱面类型前缀重查（Q31：dx/标准/标/旧/sd/宴）。"""
    from nonebot_plugin_awmc_helper.core.songs import song_service

    # 带前缀命中（种子别名「琪露诺」）
    got = await song_service.by_alias("dx琪露诺")
    assert [s.id for s in got] == [199]
    got = await song_service.by_alias("标准琪露诺")
    assert [s.id for s in got] == [199]
    # 宴谱汉字前缀（199 蛸宴）：裸写汉字剥前缀命中（柚子库 [宴]cycles 形态）
    got, info = await song_service.by_alias_detail("蛸琪露诺")
    assert [s.id for s in got] == [199]
    assert info == ("琪露诺", "蛸", "prefix")
    # 叠层前缀只剥一层：「dx标琪露诺」→「标琪露诺」不在去前缀库中 → 不命中
    assert await song_service.by_alias("dx标琪露诺") == []
    # by_alias_detail 暴露剥离信息（供别名查询提示语）
    detail_songs, strip_info = await song_service.by_alias_detail("dx琪露诺")
    assert [s.id for s in detail_songs] == [199]
    assert strip_info == ("琪露诺", "dx", "prefix")  # (剥离后别名, 命中词, 位置)
    _, exact_info = await song_service.by_alias_detail("琪露诺")
    assert exact_info is None
    # 精确命中优先，不受剥离影响
    got = await song_service.by_alias("琪露诺")
    assert [s.id for s in got] == [199]
    # 完全未命中：剥无可剥 → 空
    assert await song_service.by_alias("不存在的别名") == []
    assert await song_service.by_alias("dx") == []


@pytest.mark.asyncio
async def test_by_alias_chart_suffix_fallback(songs):
    """精确未命中时剥离谱面类型后缀重查（dx/标准，柚子库实测形态）。"""
    from nonebot_plugin_awmc_helper.core.songs import song_service

    # 「琪露诺dx」库里没有（种子别名只有「琪露诺」）→ 剥后缀「琪露诺」命中
    got, info = await song_service.by_alias_detail("琪露诺dx")
    assert [s.id for s in got] == [199]
    assert info == ("琪露诺", "dx", "suffix")
    got, info = await song_service.by_alias_detail("琪露诺标准")
    assert [s.id for s in got] == [199]
    assert info == ("琪露诺", "标准", "suffix")
    # 「标」/汉字后缀实测不存在，不剥：查询原样未命中 → 空
    assert (await song_service.by_alias_detail("琪露诺标"))[0] == []
    assert (await song_service.by_alias_detail("琪露诺宴"))[0] == []
    # 剥完为空不剥
    assert (await song_service.by_alias_detail("标准"))[0] == []


@pytest.mark.asyncio
async def test_alias_title_duplicate_filtered(songs):
    """剥前后缀后与歌名相同的别名不进展示列表（归一化比对）。

    生产链路中 provider 已把「dx翼」剥成「翼」入库，inject 绕过 provider，
    故直接种入剥后形态模拟；「标题与别名同形」无法用真实曲构造，
    用（构造）占位曲（别名与歌名同形是本用例的被测形态）。
    """
    from mocks import make_song, seed_service

    from nonebot_plugin_awmc_helper.core.songs import song_service

    dup = make_song(456, "（构造）翼", aliases=["（构造）翼", "ＷＩＮＧ", "小鸟"])
    await seed_service(song_service, [*list(songs), dup])
    try:
        aliases = await song_service.aliases_of(456)
        assert aliases == ["ＷＩＮＧ", "小鸟"]  # 「（构造）翼」与歌名重复 → 不展示
        # 查询侧索引仍保留，剥后可正常命中本曲
        got, _ = await song_service.by_alias_detail("小鸟dx")
        assert [s.id for s in got] == [456]
    finally:
        # 绕过 fixture 直调 seed 会置位 _ready，必须清理（否则同 worker
        # 后续依赖「运行时未加载」的测试被污染，如 test_jp_songs_entrypoint）
        song_service._ready.clear()


@pytest.mark.asyncio
async def test_by_note_designer_alias_class(songs):
    """谱师查歌等价类包含式（designer-alias-notes §6.2，Q55 拍板的行为变化）。

    样例库谱师＝はっぴー（SD EX）/某S氏（SD MAS）/まぐランド（默认）；
    L1 图经 use_memo 注入（等价 onungbus 曲库快照三页解析结果的子集）。
    """
    from mocks import seed_service

    from nonebot_plugin_awmc_helper.core import designer
    from nonebot_plugin_awmc_helper.core.songs import song_service

    await seed_service(song_service, list(songs))
    try:
        designer.use_memo({"はっぴー": ["緑風 犬三郎", "原田ひろゆき"]})
        # 本名精确命中（exact 排前语义：全量本名曲集合）
        got = await song_service.by_note_designer("はっぴー")
        assert got
        assert all(
            any((d.note_designer or "") == "はっぴー" for d in s.get_difficulties())
            for s in got
        )
        # 查询别名（中文昵称）命中同一批
        got2 = await song_service.by_note_designer("哈皮")
        assert {s.id for s in got2} == {s.id for s in got}
        # 声明别名（L1 马甲）命中同一批——旧版精确匹配命不中的行为
        got3 = await song_service.by_note_designer("緑風 犬三郎")
        assert {s.id for s in got3} == {s.id for s in got}
        # 假名脚本折叠：片假名输入命中平假名本名
        got4 = await song_service.by_note_designer("ハッピー")
        assert {s.id for s in got4} == {s.id for s in got}
        # 单字查询别名（显式语境）：「狗」→ はっぴー
        got5 = await song_service.by_note_designer("狗")
        assert {s.id for s in got5} == {s.id for s in got}
        # 未知名 → 空结果不抛
        assert await song_service.by_note_designer("存在しない譜面師") == []
    finally:
        designer.use_memo({})  # 还原无 L1 图状态，防污染同 worker 后续用例
        song_service._ready.clear()


@pytest.mark.asyncio
async def test_filters(songs):
    from nonebot_plugin_awmc_helper.core.songs import song_service

    # 定数查歌：13.0~14.0 命中 199（SD 13.3 / DX 13.0）与 624 的 13.4
    got = await song_service.by_level_value(13.0, 14.0)
    assert {s.id for s in got} == {199, 624}
    # 宴谱定数同样参与窗口查询（199 蛸宴 12.7）
    assert {s.id for s in await song_service.by_level_value(12.6, 12.8)} == {199}
    # 曲师（大小写不敏感精确匹配）
    assert {s.id for s in await song_service.by_artist("ao")} == {624}
    # 谱师查询暂缺断言：真实数据低难度谱面无谱师（note_designer=None），
    # by_note_designer 直取 .lower() 会崩溃（产品侧待修，见测试报告），
    # 真实样例库全量含 None 谱师谱面，无法构造不触雷的查询
    # BPM 范围（8 的 150 在下界外）
    assert {s.id for s in await song_service.by_bpm(160, 190)} == {199}


@pytest.mark.asyncio
async def test_random(songs):
    from nonebot_plugin_awmc_helper.core.songs import song_service

    got = await song_service.random(song_type=SongType.DX, level="13")
    assert got is not None
    assert got[0].id == 199  # DX 13 仅 199（902 disabled 不入池）
    # 宴会谱排除语义：等级 12+ 仅 199 的蛸宴命中 → 排除后落空、放行后唯一
    got2 = await song_service.random(level="12+", exclude_utage=True)
    assert got2 is None
    got3 = await song_service.random(level="12+", exclude_utage=False)
    assert got3 is not None
    assert got3[0].id == 199
    # 东方Project：199 的普通谱在池，排除宴谱口径下仍非空
    got4 = await song_service.random(genre=Genre.東方Project, exclude_utage=True)
    assert got4 is not None
    assert got4[0].id == 199


@pytest.mark.asyncio
async def test_random_jp_pool_and_rng(songs, monkeypatch):
    """random(jp=True) 从日服视图抽取（NET 绑定用户的随机池，L-5）；
    rng 注入的随机源替代进程全局随机（fortune 同日同曲种子语义，P-3）。"""
    from mocks import make_diff, make_song
    from maimai_py import LevelIndex

    from nonebot_plugin_awmc_helper.core.songs import song_service

    # JP 视图独有曲（国服缺席）+ 与 CN 同根的日服对象（version 可辨）
    jp_only = make_song(
        1634,
        "[協]青春コンプレックス",
        diffs=[
            make_diff(
                type=SongType.DX,
                level_index=LevelIndex.MASTER,
                level="13",
                level_value=13.2,
            )
        ],
    )
    jp_199 = make_song(
        199,
        "チルノのパーフェクトさんすう教室",
        version=24000,
        diffs=[
            make_diff(
                type=SongType.DX,
                level_index=LevelIndex.MASTER,
                level="13",
                level_value=13.2,
            )
        ],
    )

    async def fake_jp_map():
        return {199: jp_199, 1634: jp_only}

    monkeypatch.setattr(song_service, "_jp_songs_map", fake_jp_map)

    # jp 池抽尽：CN 池 DX13 仅 199，JP 池两曲都命中（含国服缺席曲）
    picks = set()
    for _ in range(50):
        got = await song_service.random(song_type=SongType.DX, level="13", jp=True)
        assert got is not None
        picks.add(got[0].id)
    assert picks == {199, 1634}
    # 不带 jp 时 CN 池不变（JP-only 曲不可达）
    got = await song_service.random(song_type=SongType.DX, level="13")
    assert got is not None
    assert got[0].id == 199
    # rng 注入：种子随机可复现（同种子同结果，且与全局随机解耦）
    import random

    got_a = await song_service.random(
        song_type=SongType.DX, level="13", jp=True, rng=random.Random(7)
    )
    got_b = await song_service.random(
        song_type=SongType.DX, level="13", jp=True, rng=random.Random(7)
    )
    assert got_a is not None
    assert got_b is not None
    assert got_a[0].id == got_b[0].id


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
async def test_local_alias_hot_reload_normalized(songs):
    """热更路径键归一必须与全量重建一致（normalize_text，L-2）。

    含全角/繁体/和制汉字的本地别名若热更用 ``.lower()``，查询键（归一化）
    命不中，直到下次全量重建才恢复。
    """
    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.core.songs import song_service

    # 全角字母 + 繁体字：查询侧（by_alias 归一化）与热更键必须同形
    await store.add_local_alias(902, "Ｂａｋａ東方曲", "u1")
    await song_service.reload_alias_index()
    got = await song_service.by_alias("baka东方曲")
    assert got is not None
    assert got[0].id == 902

    # 和制汉字（zhconv 未覆盖，constants 补充映射）
    await store.add_local_alias(902, "蔵発両覚", "u1")
    await song_service.reload_alias_index()
    got2 = await song_service.by_alias("藏发两觉")
    assert got2 is not None
    assert got2[0].id == 902


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


@pytest.mark.asyncio
async def test_load_passes_curve_provider(db, monkeypatch):
    """load() 接线：曲库 / 别名 / 水鱼曲线三 provider 一并传入 client.songs。"""
    from mocks import sample_songs
    from maimai_py import DivingFishProvider

    from nonebot_plugin_awmc_helper.core import client as client_mod
    from nonebot_plugin_awmc_helper.core.songs import song_service

    captured = {}

    class _FakeSongs:
        async def get_all(self):
            return sample_songs()

    async def fake_songs(**kwargs):
        captured.update(kwargs)
        return _FakeSongs()

    monkeypatch.setattr(client_mod.client, "songs", fake_songs)
    assert await song_service.load() is True
    assert captured["provider"] is not None
    assert captured["alias_provider"] is not None
    assert type(captured["curve_provider"]) is DivingFishProvider


@pytest.mark.asyncio
async def test_load_curves_failure_degrades(db, monkeypatch):
    """曲线失败不拖垮曲库加载：本轮无曲线重载成功（下轮 load 才重试曲线）。"""
    from mocks import sample_songs

    from nonebot_plugin_awmc_helper.core import client as client_mod
    from nonebot_plugin_awmc_helper.core.songs import song_service

    class _FakeSongs:
        async def get_all(self):
            return sample_songs()

    calls: list[dict] = []

    async def fake_songs(**kwargs):
        calls.append(kwargs)
        if "curve_provider" in kwargs:  # 曲线与曲表同 gather：曲线炸＝整次装载炸
            raise RuntimeError("chart_stats down")
        return _FakeSongs()

    monkeypatch.setattr(client_mod.client, "songs", fake_songs)
    assert await song_service.load() is True
    assert len(calls) == 2
    assert "curve_provider" in calls[0]
    assert "curve_provider" not in calls[1]


@pytest.fixture
async def db(tmp_path):
    """独立临时数据库（load 快照写入用）。"""
    from nonebot_plugin_awmc_helper.core import store

    store.set_db_file(tmp_path / "awmc.db")
    await store.init_db()
    yield
    store.set_db_file(None)
