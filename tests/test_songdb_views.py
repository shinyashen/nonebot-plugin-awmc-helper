"""core/songdb 双视图与 core/provider：规范表 → maimai_py 对象（§5.5/§5.6）。"""

import pytest
from songdb_fixtures import (
    make_lxns,
    make_all_data,
    make_dschange,
    make_divingfish,
    make_otoge_live,
    make_otoge_deleted,
)


@pytest.fixture
async def db(tmp_path):
    from nonebot_plugin_awmc_helper.core import store

    store.set_db_file(tmp_path / "awmc.db")
    await store.init_db()
    yield store
    store.set_db_file(None)


def full_payloads(**overrides):
    payloads = {
        "maimaiinfo": make_all_data(),
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

    song = build_song(built, 18, "jp")
    assert song is not None
    assert song.title == "[宴]Test Party"
    assert song.version == 24000
    diff = song.get_difficulty(SongType.UTAGE, 100018)  # 按 6 位 diff_id 查找
    assert isinstance(diff, SongDifficultyUtage)
    assert diff.diff_id == 100018
    assert diff.kanji == "宴"
    assert diff.description == "パーティーだ！"
    assert diff.level_index == LevelIndex(0)  # 库约定：宴 level_index 恒为 0
    assert diff.level_value == 12.0
    assert diff.level == "12"  # 标级由定数纯函数推导
    assert diff.version == 24000
    # get_divingfish_id 三态（§5.5）
    assert song.get_divingfish_id(SongType.UTAGE, 100018) == 100018
    dx_song = build_song(built, 21, "jp")
    assert dx_song is not None
    assert dx_song.get_divingfish_id(SongType.DX, LevelIndex.MASTER) == 21 + 10000
    # buddy 宴
    buddy_song = build_song(built, 355, "jp")
    assert buddy_song is not None
    bdiff = buddy_song.get_difficulties(SongType.UTAGE)[0]
    assert isinstance(bdiff, SongDifficultyUtage)
    assert bdiff.is_buddy
    assert bdiff.buddy_notes is not None
    assert bdiff.buddy_notes.left_tap_num == 150
    assert bdiff.buddy_notes.right_tap_num == 130
    # JP 定数（含 CiRCLE 变更后的最新值）
    dx_master = dx_song.get_difficulty(SongType.DX, LevelIndex.MASTER)
    assert dx_master is not None
    assert dx_master.level_value == 12.5
    assert dx_master.level == "12"  # 标级由定数推导：x.5 → 无+


@pytest.mark.asyncio
async def test_cn_view_uses_cn_values(built):
    """CN 视图：定数取国服推导值（非日服最新）；JP-only 曲不可见、CN-only 曲可见。"""
    from maimai_py import SongType, LevelIndex

    from nonebot_plugin_awmc_helper.core.songdb import build_song

    song = build_song(built, 21, "cn")
    assert song is not None
    master = song.get_difficulty(SongType.DX, LevelIndex.MASTER)
    assert master is not None
    # 日服 CiRCLE 已变 12.5，国服应为变更前 12.3（§2.11 差异模式）
    assert master.level_value == 12.3
    assert master.version == 20000  # 国服批次码
    # 国服限定曲仅 CN 视图可见；JP-only 曲仅 JP 视图可见
    assert build_song(built, 9002, "cn") is not None
    assert build_song(built, 9002, "jp") is None
    assert build_song(built, 555, "jp") is not None
    assert build_song(built, 555, "cn") is None
    # JP 视图不含国服限定曲；整库物化按 scope 过滤
    from nonebot_plugin_awmc_helper.core.songdb import all_songs

    cn_ids = {s.id for s in all_songs(built, "cn")}
    jp_ids = {s.id for s in all_songs(built, "jp")}
    assert 9002 in cn_ids
    assert 9002 not in jp_ids
    assert 555 in jp_ids
    assert 555 not in cn_ids


@pytest.mark.asyncio
async def test_snapshot_roundtrip_matches_runtime_serializer(built):
    """视图对象与既有快照序列化（song_to_dict/song_from_dict）行为对齐（同一模型类）。"""
    from maimai_py.models import SongDifficultyUtage

    from nonebot_plugin_awmc_helper.core.songs import song_to_dict, song_from_dict
    from nonebot_plugin_awmc_helper.core.songdb import build_song

    song = build_song(built, 355, "jp")
    assert song is not None
    restored = song_from_dict(song_to_dict(song))
    assert restored.id == song.id
    assert restored.title == song.title
    orig = song.get_difficulties()[0]
    assert isinstance(orig, SongDifficultyUtage)
    back = restored.get_difficulties()[0]
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
    assert {s.id for s in jp_songs} >= {8, 21, 18, 355, 555}
    # 新歌入库 → 指纹变化
    payloads = full_payloads()
    payloads["maimaiinfo"]["100888"] = {
        "id": "100888",
        "title": "Fingerprint Song",
        "type": "DX",
        "ds": [10.0],
        "level": ["10"],
        "charts": [{"notes": [100, 20, 15, 8, 5], "charter": "F"}],
        "basic_info": {
            "title": "Fingerprint Song",
            "artist": "A",
            "genre": "舞萌",
            "bpm": "160",
            "from": "maimai でらっくす CiRCLE",
        },
    }
    await songdb.rebuild(payloads)
    assert provider._hash() != fp1
    # 指纹是库级（规范表整体）的，与 scope 无关（同一缓存命名空间不能双视图并存 §5.2）
    assert AwmcSongProvider(scope="cn")._hash() == provider._hash()


@pytest.mark.asyncio
async def test_jp_songs_entrypoint(db):
    """core.songs.jp_songs()：JP 视图入口，不依赖 CN 运行时就绪。"""
    from nonebot_plugin_awmc_helper.core import songdb
    from nonebot_plugin_awmc_helper.core.songs import jp_songs, song_service

    await songdb.rebuild(full_payloads())
    # 运行时未加载（_ready 未置位）也不影响 JP 视图
    assert not song_service.loaded
    songs = await jp_songs()
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
        assert song.title == "Test Song SD"
        assert await song_service.by_id(555) is None  # JP-only
        assert await song_service.by_id(9002) is not None  # CN-only
        dx_song = await song_service.by_id(21)
        assert dx_song is not None
        master = dx_song.get_difficulty(SongType.DX, LevelIndex.MASTER)
        assert master is not None
        assert master.level_value == 12.3  # 日服 12.5 未进国服（§5.3）

        # 规范表被清空（离线首启模拟）：空数据视为失败 → 快照降级恢复
        from sqlmodel import delete

        from nonebot_plugin_awmc_helper.core import store

        async with store._open_session() as session:
            for table in (
                store.SongChartLevel,
                store.SongChart,
                store.SongSheetGroup,
                store.SongRow,
            ):
                await session.execute(delete(table))
            await session.commit()
        assert await songdb.is_empty()
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

    from nonebot_plugin_awmc_helper.core.render.nb_chart import _major_diffs

    song = make_song(199, "dual")  # 默认种子：SD EXPERT + DX MASTER
    assert [d.type for d in _major_diffs(song)] == [SongType.DX]  # NB 默认
    prefer_sd = _major_diffs(song, SongType.STANDARD)
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
    assert [d.type for d in _major_diffs(sd_only)] == [SongType.STANDARD]
    assert [d.type for d in _major_diffs(sd_only, SongType.STANDARD)] == [
        SongType.STANDARD
    ]


def test_search_prefix_to_type_mapping():
    """前缀 → 卡片主类型映射：dx→DX，标准/标→SD，宴/kanji 不改变卡片。"""
    from maimai_py import SongType

    from nonebot_plugin_awmc_helper.plugins.music_query import _PREFIX_TO_TYPE

    assert _PREFIX_TO_TYPE["dx"] == SongType.DX
    assert _PREFIX_TO_TYPE["标准"] == SongType.STANDARD
    assert _PREFIX_TO_TYPE["标"] == SongType.STANDARD
    assert "宴" not in _PREFIX_TO_TYPE
