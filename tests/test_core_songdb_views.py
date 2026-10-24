"""core/songdb 双视图与 core/provider：规范表 → maimai_py 对象（§5.5/§5.6）。

断言值全部取自 tests/data/snapshots/ 真实快照（2026-09-29 取材）的派生结果；
日服限定样本 = 物語はここから（12020，真实骨架条目照抄上游全量 all_data，
otoge 现役在列、CN 无——closed-world 定案后テリトリーバトル已随 otoge 缺席
判删，不再作样本）；「整列表均为日服限定」的两首为构造条目补位（已注明；
真实日限曲 2020 的定数 13.5 与 CN 可见曲 131 的 ReM 重合，定数区间造不出
纯日限窗口，故全日限场景用构造曲）。
"""

import pytest
from mocks import requires_assets
from songdb_fixtures import (
    make_lxns,
    make_all_data,
    make_dschange,
    make_divingfish,
    make_otoge_live,
    make_otoge_deleted,
)

# 真实骨架条目（上游全量 all_data 实键 12020 照抄，测试共享夹具的 maimaiinfo
# 裁剪未含它）：日服限定主样本——otoge 现役在列（closed-world 存活）而国服无。
MAGICAL_SKELETON = {
    "12020": {
        "type": "DX",
        "ds": [4.0, 7.5, 10.9, 13.5],
        "level": ["4", "7", "10+", "13"],
        "charts": [{"notes": [192, 12, 4, 4, 4], "charter": "-"}],
        "basic_info": {
            "title": "物語はここから",
            "artist": "OSTER project feat. Kanata.N",
            "genre": "舞萌",
            "bpm": "190",
            "from": "maimai でらっくす MAGiCAL",
        },
    }
}

# 构造条目（快照无原型）：第二个日服限定曲，「整列表均为日服限定」的多曲
# 列表需构造补位。
JP_ONLY_EXTRA = {
    "10555": {
        "id": "10555",
        "title": "（构造）日服限定样例",
        "type": "DX",
        # MASTER 8.6：与 30 sd1 的日服口径（8.6，国服 7.9）同窗——CN 定数空窗
        "ds": [4.0, 7.2, 10.2, 8.6],
        "level": ["4", "7", "10", "8+"],
        "charts": [
            {"notes": [60, 12, 8, 4, 3], "charter": "-"},
            {"notes": [110, 25, 18, 10, 6], "charter": "-"},
            {"notes": [190, 40, 30, 18, 10], "charter": "-"},
            {"notes": [300, 55, 48, 28, 16], "charter": "构造"},
        ],
        "basic_info": {
            "title": "（构造）日服限定样例",
            "artist": "构造条目",
            "genre": "舞萌",
            "bpm": "150",
            "from": "maimai でらっくす UNiVERSE",
        },
    }
}

# 构造条目：全日限定数区间场景的第二首（与 10555 同定数 13.6，CN 视图空窗）
JP_ONLY_EXTRA2 = {
    "10666": {
        "id": "10666",
        "title": "（构造）日服限定样例2",
        "type": "DX",
        "ds": [4.0, 7.2, 10.2, 13.6],
        "level": ["4", "7", "10", "13+"],
        "charts": [
            {"notes": [60, 12, 8, 4, 3], "charter": "-"},
            {"notes": [110, 25, 18, 10, 6], "charter": "-"},
            {"notes": [190, 40, 30, 18, 10], "charter": "-"},
            {"notes": [300, 55, 48, 28, 16], "charter": "构造"},
        ],
        "basic_info": {
            "title": "（构造）日服限定样例2",
            "artist": "构造条目",
            "genre": "舞萌",
            "bpm": "150",
            "from": "maimai でらっくす UNiVERSE",
        },
    }
}


@pytest.fixture
async def db(tmp_path):
    from nonebot_plugin_awmc_helper.core import store

    store.set_db_file(tmp_path / "awmc.db")
    await store.init_db()
    yield store
    store.set_db_file(None)


def full_payloads(**overrides):
    payloads = {
        "maimaiinfo": {**make_all_data(), **MAGICAL_SKELETON},
        "dschange": make_dschange(),
        "otoge_db": make_otoge_live(),
        "otoge_deleted": make_otoge_deleted(),
        "lxns": make_lxns(),
        "divingfish": make_divingfish(),
    }
    payloads.update(overrides)
    return payloads


@pytest.fixture
async def built(db):
    from nonebot_plugin_awmc_helper.core import songdb

    await songdb.rebuild(full_payloads())
    return await songdb.State.load()


@pytest.mark.asyncio
async def test_jp_view_object_shape(built):
    """JP 视图：Song/谱面字段、宴 6 位 diff_id、buddy、标级由定数推导。"""
    from maimai_py import SongType, LevelIndex
    from maimai_py.models import SongDifficultyUtage

    from nonebot_plugin_awmc_helper.core.songdb import build_song

    # チルノ：SD（GreeN）+ DX（CiRCLE 重制）+ 蛸宴三组并存；卡片版本取 SD 组
    song = build_song(built, 199, "jp")
    assert song is not None
    assert song.title == "チルノのパーフェクトさんすう教室"
    assert song.version == 12000
    diff = song.get_difficulty(SongType.UTAGE, 100199)  # 按 6 位 diff_id 查找
    assert isinstance(diff, SongDifficultyUtage)
    assert diff.diff_id == 100199
    assert diff.kanji == "蛸"
    assert diff.description == "パーフェクトホールド教室"
    assert diff.level_index == LevelIndex(0)  # 库约定：宴 level_index 恒为 0
    assert diff.level_value == 12.7
    assert diff.level == "12+"  # 标级由定数纯函数推导（x.7 → 有 +）
    assert diff.version == 24000
    # get_divingfish_id 三态（§5.5）
    assert song.get_divingfish_id(SongType.UTAGE, 100199) == 100199
    # 双谱曲（ネコ日和。）：DX 组 id = 根 id + 10000（真实 30/10030）
    dx_song = build_song(built, 30, "jp")
    assert dx_song is not None
    assert dx_song.get_divingfish_id(SongType.DX, LevelIndex.MASTER) == 10030
    # buddy 宴（真实 [協]ラグトレイン）
    buddy_song = build_song(built, 1355, "jp")
    assert buddy_song is not None
    assert buddy_song.version == 22000  # 无 SD 组 → 取 DX 组版本
    bdiff = buddy_song.get_difficulties(SongType.UTAGE)[0]
    assert isinstance(bdiff, SongDifficultyUtage)
    assert bdiff.is_buddy
    assert bdiff.buddy_notes is not None
    assert bdiff.buddy_notes.left_tap_num == 183
    assert bdiff.buddy_notes.right_tap_num == 172
    # JP 定数最新值（真实 30 DX MASTER：PRiSM PLUS 登场 13.7）
    dx_master = dx_song.get_difficulty(SongType.DX, LevelIndex.MASTER)
    assert dx_master is not None
    assert dx_master.level_value == 13.7
    assert dx_master.level == "13+"  # 标级由定数推导：x.7 → 有 +


@pytest.mark.asyncio
async def test_cn_view_uses_cn_values(built):
    """CN 视图：定数取国服推导值（非日服最新）；JP-only 曲不可见、CN-only 曲可见。

    真实锚：System "Z" Re:MASTER 日服 CiRCLE PLUS 已变 14.2，国服仍 14.0。
    """
    from maimai_py import SongType, LevelIndex

    from nonebot_plugin_awmc_helper.core.songdb import build_song

    song = build_song(built, 239, "cn")
    assert song is not None
    master = song.get_difficulty(SongType.STANDARD, LevelIndex(4))  # Re:MASTER
    assert master is not None
    assert master.level_value == 14.0
    assert master.version == 12005  # 国服批次码（落雪实测）
    jp_song = build_song(built, 239, "jp")
    assert jp_song is not None
    jp_master = jp_song.get_difficulty(SongType.STANDARD, LevelIndex(4))
    assert jp_master is not None
    assert jp_master.level_value == 14.2  # 日服最新
    # 谱面登场版本逐谱面化（2026-10-11）：ReM 为组内后补（组 GreeN 13000），
    # 日服视图按定数历史首点取真实追加版本 21000（Splash）——不再以组级落进
    # 舞/霸；组登场谱面维持组级；CN 视图维持组级 version_cn（上断言 12005）
    assert jp_master.version == 21000
    jp_sd_master = jp_song.get_difficulty(SongType.STANDARD, LevelIndex.MASTER)
    assert jp_sd_master is not None
    assert jp_sd_master.version == 13000
    # 国服限定曲仅 CN 视图可见；JP-only 曲仅 JP 视图可见（物語はここから）
    assert build_song(built, 9002, "cn") is not None
    assert build_song(built, 9002, "jp") is None
    assert build_song(built, 2020, "jp") is not None
    assert build_song(built, 2020, "cn") is None
    # 整库物化按 scope 过滤；from=未知曲（12）版本不可知，两视图均不可见
    from nonebot_plugin_awmc_helper.core.songdb import all_songs

    cn_ids = {s.id for s in all_songs(built, "cn")}
    jp_ids = {s.id for s in all_songs(built, "jp")}
    assert 9002 in cn_ids
    assert 9002 not in jp_ids
    assert 2020 in jp_ids
    assert 2020 not in cn_ids
    assert 12 not in cn_ids
    assert 12 not in jp_ids


@pytest.mark.asyncio
async def test_snapshot_roundtrip_matches_runtime_serializer(built):
    """视图对象与既有快照序列化（song_to_dict/song_from_dict）行为对齐（同一模型类）。"""
    from maimai_py import SongType
    from maimai_py.models import SongDifficultyUtage

    from nonebot_plugin_awmc_helper.core.songs import song_to_dict, song_from_dict
    from nonebot_plugin_awmc_helper.core.songdb import build_song

    song = build_song(built, 1355, "jp")
    assert song is not None
    restored = song_from_dict(song_to_dict(song))
    assert restored.id == song.id
    assert restored.title == song.title
    orig = song.get_difficulties(SongType.UTAGE)[0]
    assert isinstance(orig, SongDifficultyUtage)
    back = restored.get_difficulties(SongType.UTAGE)[0]
    assert isinstance(back, SongDifficultyUtage)  # 宴谱重建为 SongDifficultyUtage
    assert back.diff_id == orig.diff_id
    assert back.is_buddy == orig.is_buddy
    assert back.level_value == orig.level_value
    assert back.kanji == orig.kanji
    assert back.description == orig.description
    # 快照仅降级查询用：buddy 物量与曲线不回填（既有约定）
    assert back.buddy_notes is None
    assert back.curve is None


@pytest.mark.asyncio
async def test_awmc_provider_hash_and_get_songs(db, monkeypatch):
    """AwmcSongProvider：数据变更 → 指纹变化（库自动重建缓存的依据）。"""
    from nonebot_plugin_awmc_helper.core import songdb
    from nonebot_plugin_awmc_helper.core.provider import AwmcSongProvider

    monkeypatch.setattr(songdb, "CURRENT_FINGERPRINT", None)  # 全局复位，避免用例间串扰
    provider = AwmcSongProvider(scope="jp")
    assert provider._hash() == "empty"  # 未构建时占位
    await songdb.rebuild(full_payloads())
    fp1 = provider._hash()
    assert fp1 != "empty"
    jp_songs = await provider.get_songs(None)  # type: ignore[arg-type]
    assert {s.id for s in jp_songs} >= {8, 30, 199, 1355, 2020}
    # 新歌入库 → 指纹变化（构造条目，测指纹机制）
    payloads = full_payloads()
    payloads["maimaiinfo"]["100888"] = {
        "id": "100888",
        "title": "（构造）指纹歌",
        "type": "DX",
        "ds": [10.0],
        "level": ["10"],
        "charts": [{"notes": [100, 20, 15, 8, 5], "charter": "F"}],
        "basic_info": {
            "title": "（构造）指纹歌",
            "artist": "构造条目",
            "genre": "舞萌",
            "bpm": "160",
            "from": "maimai でらっくす CiRCLE",
        },
    }
    # closed-world：otoge 现役集无此标题会被判删，指纹不变——现役条目一并构造
    payloads["otoge_db"].append(
        {"title": "（构造）指纹歌", "catcode": "maimai", "version": "26000"}
    )
    await songdb.rebuild(payloads)
    assert provider._hash() != fp1
    # 指纹是库级（规范表整体）的，与 scope 无关（同一缓存命名空间不能双视图并存 §5.2）
    assert AwmcSongProvider(scope="cn")._hash() == provider._hash()


@pytest.mark.asyncio
async def test_jp_songs_entrypoint(db):
    """song_service.jp_all()：JP 视图入口，不依赖 CN 运行时就绪。"""
    from nonebot_plugin_awmc_helper.core import songdb
    from nonebot_plugin_awmc_helper.core.songs import song_service

    # 前置条件：运行时未加载（xdist 同 worker 先跑的测试可能置位过 _ready）
    song_service._ready.clear()
    await songdb.rebuild(full_payloads())
    # 运行时未加载（_ready 未置位）也不影响 JP 视图
    assert not song_service._ready.is_set()
    songs = await song_service.jp_all()
    assert 8 in {s.id for s in songs}
    assert all(s.difficulties is not None for s in songs)


@pytest.mark.asyncio
async def test_cn_runtime_switched_to_songdb(db, monkeypatch):
    """§5.4 切换：运行时 CN 视图由规范表构造；空数据按失败走快照降级。"""
    from maimai_py import SongType, LevelIndex

    from nonebot_plugin_awmc_helper.core import songdb
    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.client import yuzu_provider

    async def fake_aliases(client):
        return {}

    monkeypatch.setattr(yuzu_provider, "get_aliases", fake_aliases)
    from nonebot_plugin_awmc_helper.core.client import lxns_provider

    monkeypatch.setattr(lxns_provider, "get_aliases", fake_aliases)
    try:
        # 空表判定与冷启动路径
        assert await songdb.is_empty()
        await songdb.rebuild(full_payloads())
        assert not await songdb.is_empty()

        # 运行时由规范表构造：CN-only 可见、JP-only 不可见、定数为国服推导值
        assert await song_service.load()
        song = await song_service.by_id(8)
        assert song is not None
        assert song.title == "True Love Song"
        assert await song_service.by_id(2020) is None  # JP-only
        assert await song_service.by_id(9002) is not None  # CN-only
        sys_z = await song_service.by_id(239)
        assert sys_z is not None
        remaster = sys_z.get_difficulty(SongType.STANDARD, LevelIndex(4))
        assert remaster is not None
        assert remaster.level_value == 14.0  # 日服 14.2 未进国服（§5.3）

        # 规范表被清空（离线首启模拟）：空数据视为失败 → 快照降级恢复
        from sqlmodel import delete

        from nonebot_plugin_awmc_helper.core import store

        async with store.session() as session:
            for table in (
                store.SongChartLevel,
                store.SongChart,
                store.SongSheetGroup,
                store.SongRow,
            ):
                await session.exec(delete(table))
            await session.commit()
        assert await songdb.is_empty()
        # 直写 DELETE 不经 rebuild/外部源合并路径，不会同步指纹；生产写库路径
        # （_refresh_standard_json）必同步，这里补齐同款语义强制下次 load 重建
        # 缓存——否则同指纹命中缓存旧 ids，加载成败取决于同 worker 先前用例
        # 留下的缓存状态，断言天然不确定
        monkeypatch.setattr(songdb, "CURRENT_FINGERPRINT", "cleared-for-refetch")
        # 已就绪状态下加载失败：保留旧运行时（不降级、也不清空）
        assert not await song_service.load()
        assert await song_service.by_id(8) is not None  # 旧运行时仍在
        # 未就绪（如重启后首载）：快照降级恢复（load 返回 False 表示非全新加载）
        song_service._ready.clear()
        assert not await song_service.load()
        assert await song_service.by_id(8) is not None  # 快照内容
    finally:
        song_service._ready.clear()


def test_major_diffs_prefer_type():
    """双谱歌曲卡片主类型：默认 DX 优先；带「标准/标」前缀搜索时显示 SD。"""
    from mocks import make_diff, make_song
    from maimai_py import SongType, LevelIndex

    from nonebot_plugin_awmc_helper.core.render.nb_chart import major_diffs

    song = make_song(199, "dual")  # 默认种子：SD EXPERT + DX MASTER
    assert [d.type for d in major_diffs(song)] == [SongType.DX]  # NB 默认
    prefer_sd = major_diffs(song, SongType.STANDARD)
    assert [d.type for d in prefer_sd] == [SongType.STANDARD]
    # 仅 SD 的歌曲：无偏好与偏好 SD 均显示 SD
    sd_only = make_song(
        100,
        "sdonly",
        diffs=[
            make_diff(
                type=SongType.STANDARD,
                level_index=LevelIndex.EXPERT,
                level="9",
                level_value=9.5,
            )
        ],
    )
    assert [d.type for d in major_diffs(sd_only)] == [SongType.STANDARD]
    assert [d.type for d in major_diffs(sd_only, SongType.STANDARD)] == [
        SongType.STANDARD
    ]


def test_search_prefix_to_type_mapping():
    """前缀 → 卡片主类型映射：dx→DX，标准/标→SD，宴/kanji 不改变卡片。"""
    from maimai_py import SongType

    from nonebot_plugin_awmc_helper.constants import (
        CHART_TYPE_BY_PREFIX as _PREFIX_TO_TYPE,
    )

    assert _PREFIX_TO_TYPE["dx"] == SongType.DX
    assert _PREFIX_TO_TYPE["标准"] == SongType.STANDARD
    assert _PREFIX_TO_TYPE["标"] == SongType.STANDARD
    assert "宴" not in _PREFIX_TO_TYPE


@pytest.mark.asyncio
async def test_jp_fallback_search(db, monkeypatch):
    """日服 fallback（Q32）：国服视图未命中 → 日服视图按别名/id 命中。"""
    from nonebot_plugin_awmc_helper.core import store, songdb
    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.client import lxns_provider, yuzu_provider

    async def fake_aliases(client):
        return {}

    monkeypatch.setattr(yuzu_provider, "get_aliases", fake_aliases)
    monkeypatch.setattr(lxns_provider, "get_aliases", fake_aliases)
    await songdb.rebuild(full_payloads())
    await store.add_local_alias(2020, "日限", "tester")
    try:
        assert await song_service.load()
        # 国服视图查不到 JP-only 曲
        assert not await song_service.by_alias("日限")
        assert await song_service.by_id(2020) is None
        # 日服 fallback：别名命中；谱面前缀剥离同样生效
        jp_songs, _ = await song_service.jp_by_alias_detail("日限")
        assert [s.id for s in jp_songs] == [2020]
        jp_pref, _ = await song_service.jp_by_alias_detail("dx日限")
        assert [s.id for s in jp_pref] == [2020]
        # id fallback
        jp_song = await song_service.jp_by_id(2020)
        assert jp_song is not None
        assert jp_song.version == 27000  # MAGiCAL（真实 from）
    finally:
        song_service._ready.clear()


@requires_assets
@pytest.mark.asyncio
async def test_jp_fallback_handler(db, monkeypatch, app):
    """搜歌日服 fallback 端到端：日服 logo 卡 + 「此歌曲为日服限定」标注。"""
    from maimai_py import SongType
    from test_music_query import _assert_image_reply

    from nonebot_plugin_awmc_helper.core import store, songdb
    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.client import lxns_provider, yuzu_provider
    from nonebot_plugin_awmc_helper.core.chart_card import chart_card_bytes

    async def fake_aliases(client):
        return {}

    monkeypatch.setattr(yuzu_provider, "get_aliases", fake_aliases)
    monkeypatch.setattr(lxns_provider, "get_aliases", fake_aliases)
    await songdb.rebuild(full_payloads())
    await store.add_local_alias(2020, "日限", "tester")
    try:
        await song_service.load()
        song = await song_service.jp_by_id(2020)
        assert song is not None
        await _assert_image_reply(
            app,
            "search_alias_song",
            "日限是什么歌",
            lambda: chart_card_bytes(song, None, SongType.DX, True),
            suffix="您要找的是不是这首？",
            prefix="此歌曲为日服限定",
        )
    finally:
        song_service._ready.clear()


async def _rebuild_load(monkeypatch, *, jp_extra: bool = False) -> None:
    """重建样例曲库并加载运行时（日服 fallback 列表系列测试共用）。"""
    from nonebot_plugin_awmc_helper.core import songdb
    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.client import lxns_provider, yuzu_provider

    async def fake_aliases(client):
        return {}

    monkeypatch.setattr(yuzu_provider, "get_aliases", fake_aliases)
    monkeypatch.setattr(lxns_provider, "get_aliases", fake_aliases)
    payloads = full_payloads()
    if jp_extra:
        payloads["maimaiinfo"].update(JP_ONLY_EXTRA)
        payloads["maimaiinfo"].update(JP_ONLY_EXTRA2)
        # closed-world：构造日限曲同样需要 otoge 现役条目才能存活
        payloads["otoge_db"] = [
            *payloads["otoge_db"],
            {"title": "（构造）日服限定样例", "catcode": "maimai", "version": "22000"},
            {"title": "（构造）日服限定样例2", "catcode": "maimai", "version": "22000"},
        ]
    await songdb.rebuild(payloads)
    assert await song_service.load()


@pytest.mark.asyncio
async def test_jp_title_exact_match_pinned(db, monkeypatch):
    """完整曲名查歌的集成防回归（上游 by_keywords 精确优先语义，JP 视图）：
    精确命中与子串命中同返、次序不炸。⚠️ 本快照里精确曲（199）恰为最小
    id，置顶与 id 序同形、无区分度——排序语义的区分性断言在
    test_core_songs.test_exact_first_ordering（构造曲直测纯函数）。"""
    from nonebot_plugin_awmc_helper.core.songs import song_service

    await _rebuild_load(monkeypatch)
    try:
        result = await song_service.jp_by_title_fuzzy(
            "チルノのパーフェクトさんすう教室"
        )
        assert [s.id for s in result][:2] == [199, 665]  # 精确(199) 先于子串(665)
        assert 665 in {s.id for s in result}
    finally:
        song_service._ready.clear()


@pytest.mark.asyncio
async def test_jp_fallback_mixed_list(db, monkeypatch, app):
    """日服 fallback 混合列表：CN 可见曲 + 日服限定曲同列 → 只给日限曲逐条
    标注，列表级说明用「包含」而非「此歌曲为」。

    真实锚：ネコ日和。 sd1（30）日服 8.6、国服 7.9——定数 [8.58, 8.62] 在
    国服视图为空窗，日服视图命中 30（CN 可见）+ 构造日限曲 10555（同 8.6）。
    """
    from test_music_query import _assert_reply

    from nonebot_plugin_awmc_helper.core.songs import song_service

    await _rebuild_load(monkeypatch, jp_extra=True)
    try:
        assert not await song_service.by_level_value(8.58, 8.62)
        assert [s.id for s in await song_service.jp_by_level_value(8.58, 8.62)] == [
            30,
            555,
        ]
        await _assert_reply(
            app,
            "search",
            "定数查歌 8.58 8.62",
            "「30」 ネコ日和。"
            "\n「10555」 （构造）日服限定样例（日服限定）"
            "\n列表中包含日服限定歌曲",
        )
    finally:
        song_service._ready.clear()


@pytest.mark.asyncio
async def test_jp_fallback_all_jp_list(db, monkeypatch, app):
    """日服 fallback 整列表均为日服限定：逐条标注 + 列表级说明用「均为」。

    两条构造日限曲（10555/10666，定数 13.6；真实日限曲 2020 的 13.5 与
    CN 可见的 131 ReM 重合，见模块头注）。
    """
    from test_music_query import _assert_reply

    from nonebot_plugin_awmc_helper.core.songs import song_service

    await _rebuild_load(monkeypatch, jp_extra=True)
    try:
        # 标题子串「构造」：国服别名未命中（9002 仅 CN 视图可见、标题不进别名），
        # 日服标题兜底命中两条构造日限曲
        assert [s.id for s in await song_service.jp_by_title_fuzzy("构造")] == [
            555,
            666,
        ]
        await _assert_reply(
            app,
            "search_alias_song",
            "构造是什么歌",
            "找到2个谱面："
            "\n10555：（构造）日服限定样例（日服限定）"
            "\n10666：（构造）日服限定样例2（日服限定）"
            "\n※ 请使用「id xxxxx」查询指定谱面"
            "\n列表中曲目均为日服限定歌曲",
        )
    finally:
        song_service._ready.clear()


@requires_assets
@pytest.mark.asyncio
async def test_jp_fallback_all_cn_delegates(db, monkeypatch, app):
    """日服 fallback 整列表国服都有（日服定数口径变更命中）：按普通结果渲染。

    真实锚：国服定数 [14.1, 14.3] 为空，日服口径命中 System "Z" Re:MASTER
    （CiRCLE PLUS 变 14.2，国服 14.0）→ 回取国服对象按普通卡渲染。
    """
    from test_music_query import _assert_image_reply

    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.binding import binding_service
    from nonebot_plugin_awmc_helper.core.chart_card import chart_card_bytes

    # 断开自动绑定（QQ 号直查水鱼）的 B50 嵌入，渲染不依赖外部成绩
    monkeypatch.setattr(binding_service, "identifier_or_none", lambda b: None)
    await _rebuild_load(monkeypatch)
    try:
        assert not await song_service.by_level_value(14.1, 14.3)
        song = await song_service.by_id(239)
        assert song is not None
        await _assert_image_reply(
            app,
            "search",
            "定数查歌 14.1 14.3",
            lambda: chart_card_bytes(song, None),
        )
    finally:
        song_service._ready.clear()


@pytest.mark.asyncio
async def test_jp_plate_remaster_addition_scoping(built):
    """组内后补 ReM（System "Z" ReM@21000）的牌子归属（2026-10-11 定案）：
    追加版本出旧作区间 → 舞/霸不再要求该 ReM（组级压平局限消除）；
    非舞/霸牌一律不要求 ReM → 煌（Splash）虽有该 ReM 条目但整曲不进牌。"""
    from maimai_py import LevelIndex

    from nonebot_plugin_awmc_helper.core.plates import build_local_plates
    from nonebot_plugin_awmc_helper.core.songdb import all_songs

    songs = all_songs(built, "jp")

    wu = build_local_plates("舞", "将", songs, [])
    obj = next((p for p in await wu.get_remained() if p.song.id == 239), None) or next(
        (p for p in await wu.get_cleared() if p.song.id == 239), None
    )
    assert obj is not None
    assert LevelIndex.ReMASTER not in obj.levels  # 只要求组登场谱面（13000 段）

    kai = build_local_plates("煌", "将", songs, [])
    assert all(p.song.id != 239 for p in await kai.get_remained())
    assert all(p.song.id != 239 for p in await kai.get_cleared())
