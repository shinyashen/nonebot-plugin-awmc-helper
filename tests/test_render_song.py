"""core/render/song 列表图测试：双谱曲条目展开与行内 per-type 自洽。

条目展开口径 2026-11-13 用户拍板。回归原型：bpm查歌 240 实测 411 初音ミクの
消失行卡画 DX 徽章/定数、版本 logo 却取曲级最小版本（PiNK）——规范表
``_song_version`` 取组版本 min 所致。取材真实数据（maimaiinfo all_data）：
411 SD@PiNK(16000) 6.0/9.5/11.8/13.9、DX@BUDDiES(24000) 6.0/8.0/12.0/14.5。
"""

from mocks import make_diff, make_song, make_utage, requires_assets
from maimai_py import Genre, SongType, LevelIndex


def _song411():
    """411 双谱曲原型：曲级 version = 组版本 min = 16000（与生产物化一致）。"""
    return make_song(
        411,
        "初音ミクの消失",
        genre=Genre.niconicoボーカロイド,
        bpm=240,
        version=16000,
        diffs=[
            make_diff(
                type=SongType.STANDARD,
                level_index=LevelIndex.BASIC,
                level="6",
                level_value=6.0,
                version=16000,
            ),
            make_diff(
                type=SongType.DX,
                level_index=LevelIndex.BASIC,
                level="6",
                level_value=6.0,
                version=24000,
            ),
        ],
    )


def test_list_entries_expand_dual():
    """双谱曲展开为 SD/DX 两行，SD 前 DX 后（与详卡 SD 优先排序一致）。"""
    from nonebot_plugin_awmc_helper.core.render.song import list_entries

    song = _song411()
    entries = list_entries([song])
    assert entries == [(song, SongType.STANDARD), (song, SongType.DX)]


def test_list_entries_shapes():
    """单类型曲单条目；纯宴曲 UTAGE 单条；宿主曲挂宴谱不展开宴条目。

    纯宴宿主无真实快照原型（宴谱均挂普通曲），按测试数据口径以构造补位。
    """
    from nonebot_plugin_awmc_helper.core.render.song import list_entries

    sd_only = make_song(8, "True Love Song", diffs=[make_diff(type=SongType.STANDARD)])
    # 1301 为真实 DX 专用曲（divingfish_new_song 快照）；默认谱面即 DX MASTER
    dx_only = make_song(1301, "華の集落、秋のお届け", diffs=[make_diff()])
    utage_only = make_song(
        363,
        "Milky Beat",
        genre=Genre.宴会場,
        diffs=[],
        utage=[make_utage(diff_id=100363, kanji="牛")],
    )
    host = make_song(
        199,
        "チルノのパーフェクトさんすう教室",
        utage=[make_utage(diff_id=100199, kanji="蛸")],
    )
    assert list_entries([sd_only]) == [(sd_only, SongType.STANDARD)]
    assert list_entries([dx_only]) == [(dx_only, SongType.DX)]
    assert list_entries([utage_only]) == [(utage_only, SongType.UTAGE)]
    # 宿主曲（199 普通+宴）：只出普通谱条目，宴谱经 6 位 id 召唤
    assert list_entries([host]) == [(host, SongType.STANDARD), (host, SongType.DX)]


def test_list_entry_id():
    """条目 id 按查分器 id 形状：SD=根 id、DX=根 id+10000；宴条目维持根 id。"""
    from nonebot_plugin_awmc_helper.core.render.song import list_entry_id

    song = _song411()
    assert list_entry_id(song, SongType.STANDARD) == 411
    assert list_entry_id(song, SongType.DX) == 10411


@requires_assets
def test_song_list_version_follows_entry(monkeypatch):
    """回归：列表行版本 logo 按条目类型组登场版本，不再取曲级最小版本。

    修复前 411 两行（若展开）都会贴 PiNK(16000)；修复后 SD 行 16000、
    DX 行 24000（BUDDiES）。
    """
    import nonebot_plugin_awmc_helper.core.render.song as song_render
    from nonebot_plugin_awmc_helper.core.render.song import (
        list_entries,
        song_list_bytes,
    )

    captured: list[int] = []
    monkeypatch.setattr(
        song_render,
        "paste_version_logo",
        lambda _im, version, _box, **_kw: captured.append(version),
    )
    assert song_list_bytes(list_entries([_song411()])).startswith(b"\x89PNG")
    assert captured == [16000, 24000]
