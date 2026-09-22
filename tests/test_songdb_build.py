"""core/songdb 合并管线：rebuild 入库、缺失/删除规则、pending、外部源。"""

import json

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
    """独立临时数据库。"""
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


@pytest.mark.asyncio
async def test_rebuild_full_union(db):
    """全量重建：并集入库、组级版本、日期规则、封面、宴字段、定数历史。"""
    from nonebot_plugin_awmc_helper.core import songdb

    result = await songdb.rebuild(full_payloads())
    state = await songdb.State.load()
    # 并集：maimaiinfo 8 曲（8/21/18/12/777/355/42/555）+ 国服独有 9002 = 9，
    # 其中 42（maimaiinfo 滞留 × otoge 已下架 × 国服无）当轮即被删除 → 8
    assert result["songs"] == 8
    assert 42 not in state.songs
    # from=未知 且国服源没有（12）：版本未知 ≠ 两侧皆无，记录保留、不被误删
    assert 12 in state.songs
    assert state.groups[(12, "sd")].version is None
    # 国服独有曲（日侧无）：version=None、version_cn=25000
    g = state.groups[(9002, "sd")]
    assert g.version is None
    assert g.version_cn == 25000
    # JP-only 曲（国服源完全没有）：version=22000、version_cn=None
    g = state.groups[(555, "dx")]
    assert g.version == 22000
    assert g.version_cn is None
    # 组级版本：SD 来自 from、DX 来自 PLUS；version_cn 取组内谱面值
    assert state.groups[(8, "sd")].version == 20000
    assert state.groups[(8, "sd")].version_cn == 20000
    assert state.groups[(21, "dx")].version == 20500
    assert state.groups[(21, "dx")].version_cn == 20000
    # 日期规则：sd=date_added；dx=release 优先；宴=release（复活日）
    assert state.groups[(8, "sd")].date == 20120711
    assert state.groups[(21, "dx")].date == 230914
    assert state.groups[(18, "utage")].date == 230622
    # 封面与宴字段（otoge-db 唯一来源）
    assert state.songs[8].image_url == "abc123.png"
    utage = state.charts[(18, "utage", 0)]
    assert utage.kanji == "宴"
    assert utage.comment == "パーティーだ！"
    assert not utage.is_buddy
    # buddy 宴左右物量
    buddy = state.charts[(355, "utage", 1)]
    assert buddy.is_buddy
    assert buddy.notes_left is not None
    assert buddy.notes_right is not None
    assert json.loads(buddy.notes_left) == [150, 20, 25, 0, 5]
    assert json.loads(buddy.notes_right) == [130, 25, 20, 0, 5]
    # 定数历史（变化点）与宴推导值
    assert state.history_of(8, "sd", 0) == [(20000, 4.0), (23000, 4.5)]
    assert state.history_of(18, "utage", 0) == [(24000, 12.0)]
    # 标准 JSON 与指纹已生成
    doc = await db.kv_get("songdb_json")
    assert doc["8"]["sheets"]["sd"]["version_cn"] == 20000
    # 01 文档线格式：level 自登场版本（此处 DX 初代）逐版本共 14 值，23000 起变 4.5
    assert doc["8"]["sheets"]["sd"]["contents"][0]["level"] == [4.0] * 6 + [4.5] * 8
    assert doc["8"]["sheets"]["sd"]["contents"][0]["notes"] == [63, 23, 8, 0, 2]
    # 宴为单元素标级浮点列表
    assert doc["18"]["sheets"]["utage"]["contents"][0]["level"] == [12.0]
    fp = songdb.CURRENT_FINGERPRINT
    assert fp is not None
    assert len(fp) == 32
    # 国服当前版本 = max(version_cn) = 25000（PRiSM）
    assert state.cn_current_version() == 25000


@pytest.mark.asyncio
async def test_cn_derivation_and_fallback(db):
    """国服定数推导：≤ 国服版本末值；同步上线曲走首值兜底（§5.3）。"""
    from nonebot_plugin_awmc_helper.core.songdb import State, rebuild, cn_level_value

    await rebuild(full_payloads())
    state = await State.load()
    # 日服 CiRCLE 变 12.5 未进国服（国服 20000）→ 推导取变更前 12.3，与落雪一致
    assert state.resolve_chart_level(21, "dx", 3) == 12.5  # JP 最新
    assert state.resolve_chart_level(21, "dx", 3, version=25000) == 12.3  # 国服视角
    assert cn_level_value(state.history_of(21, "dx", 3), 25000) == 12.3
    # carry-forward 边界：早于首行 → None（该版本尚无此谱面）
    assert state.resolve_chart_level(21, "dx", 3, version=20000) is None
    # 国服推导的首值兜底：区间无值取日服首值（同步上线的曲，§5.3 已验证 20/20）
    assert cn_level_value(state.history_of(21, "dx", 3), 20000) == 12.3


@pytest.mark.asyncio
async def test_cn_missing_and_restore(db):
    """CN 缺失/恢复：双源消失置 NULL 且历史保留；单源消失不动；回归直接回填。"""
    from nonebot_plugin_awmc_helper.core import songdb

    await songdb.rebuild(full_payloads())
    # 双源同时失去 9002 → 国服下架（version_cn 置 NULL），记录保留（日服无 → 整曲删除）
    await songdb.rebuild(
        full_payloads(
            lxns=make_lxns(drop_ids={9002}), divingfish=make_divingfish(drop_ids={9002})
        )
    )
    state = await songdb.State.load()
    assert 9002 not in state.songs  # JP 也无 → 两侧皆无才删
    # 单源消失（仅水鱼）不动作
    await songdb.rebuild(full_payloads(divingfish=make_divingfish(drop_ids={9002})))
    # 回归：国服重新上架 → version_cn 回填
    await songdb.rebuild(full_payloads())
    state = await songdb.State.load()
    assert state.groups[(9002, "sd")].version_cn == 25000


@pytest.mark.asyncio
async def test_jp_missing_and_cn_absence_delete(db):
    """缺失矩阵：一侧缺失保留、两侧信号皆无删除；JP 历史不受 CN 波动影响。"""
    from nonebot_plugin_awmc_helper.core import songdb

    await songdb.rebuild(full_payloads())
    # JP-only 曲（555）：国服源始终无 → version_cn=None，记录与 JP 定数历史保留
    state = await songdb.State.load()
    assert 555 in state.songs
    g = state.groups[(555, "dx")]
    assert g.version == 22000
    assert g.version_cn is None
    assert state.history_of(555, "dx", 0) == [(22000, 4.0)]
    # 国服在列曲（9002）双源同时消失：JP 也无 → 两侧信号皆无 → 整曲删除
    await songdb.rebuild(
        full_payloads(
            lxns=make_lxns(drop_ids={9002}), divingfish=make_divingfish(drop_ids={9002})
        )
    )
    state = await songdb.State.load()
    assert 9002 not in state.songs
    # JP 侧波动不影响其余曲（8 仍在且历史完好）
    assert state.history_of(8, "sd", 0) == [(20000, 4.0), (23000, 4.5)]


@pytest.mark.asyncio
async def test_source_failure_tolerated(db):
    """单源失败：该源列不动、不做缺省同步（落雪失败时 version_cn 保持）。"""
    from nonebot_plugin_awmc_helper.core import songdb

    await songdb.rebuild(full_payloads())
    await songdb.rebuild(full_payloads(lxns=None, divingfish=None))
    state = await songdb.State.load()
    assert state.groups[(8, "sd")].version_cn == 20000  # 未被误清
    assert 9002 in state.songs
    # maimaiinfo 失败：otoge 充实跳过，JP/CN 列保持
    await songdb.rebuild(full_payloads(maimaiinfo=None, dschange=None))
    state = await songdb.State.load()
    assert state.groups[(8, "sd")].version == 20000


@pytest.mark.asyncio
async def test_disabled_means_cn_absent(db):
    """落雪 disabled（删除/宴下架）= CN 缺失：version_cn 置 NULL；组粒度互不影响。"""
    from nonebot_plugin_awmc_helper.core import songdb

    await songdb.rebuild(full_payloads())
    # 8 被禁用 + 水鱼同步移除：sd 组 version_cn 置 NULL，曲保留（JP 在列）
    await songdb.rebuild(
        full_payloads(
            lxns=make_lxns(disable_ids={8}),
            divingfish=make_divingfish(drop_ids={8}),
        )
    )
    state = await songdb.State.load()
    assert 8 in state.songs
    g = state.groups[(8, "sd")]
    assert g.version == 20000
    assert g.version_cn is None
    # 其他曲不受影响
    assert state.groups[(21, "dx")].version_cn == 20000
    # 宴条目单独禁用：仅宴组置 NULL（组粒度）
    await songdb.rebuild(
        full_payloads(
            lxns=make_lxns(disable_ids={355}),
            divingfish=make_divingfish(drop_ids={355}),
        )
    )
    state = await songdb.State.load()
    assert state.groups[(355, "utage")].version_cn is None
    assert state.groups[(355, "utage")].version == 24500
    # 国服限定曲 9002 被禁用 + JP 也无 → 整曲删除
    await songdb.rebuild(
        full_payloads(
            lxns=make_lxns(disable_ids={9002}),
            divingfish=make_divingfish(drop_ids={9002}),
        )
    )
    state = await songdb.State.load()
    assert 9002 not in state.songs


@pytest.mark.asyncio
async def test_pending_and_flush(db):
    """otoge 独有的无 id 条目进 pending；id 到位后归并清理。"""
    from nonebot_plugin_awmc_helper.core import store, songdb

    await songdb.rebuild(full_payloads())
    from sqlmodel import select

    async with store._open_session() as session:
        pending = list((await session.exec(select(store.SongPending))).all())
    assert {p.key for p in pending} == {
        "title:[狂]Otoge Only Uta",
        "title:[宴]Rotated Out",
        "title:Link",  # 同名多义两体均无 id
    }
    assert all(p.reason == "missing_id" for p in pending)
    # id 到位：maimaiinfo 新增该曲 → 重建 + flush 归并
    payloads = full_payloads()
    payloads["maimaiinfo"]["100999"] = {
        "id": "100999",
        "title": "[狂]Otoge Only Uta",
        "type": "SD",
        "ds": [13.7],
        "level": ["13+"],
        "charts": [{"notes": [250, 45, 35, 5, 12], "charter": "K-A"}],
        "basic_info": {
            "title": "[狂]Otoge Only Uta",
            "artist": "A",
            "genre": "宴会場",
            "bpm": "150",
            "from": "maimai でらっくす BUDDiES PLUS",
        },
    }
    await songdb.rebuild(payloads)
    merged = await songdb.flush_pending()
    assert merged == 1  # 仅拿到 id 的「Otoge Only Uta」归并；其余仍无 id 保留
    state = await songdb.State.load()
    assert 999 in state.songs
    async with store._open_session() as session:
        remain = list((await session.exec(select(store.SongPending))).all())
    assert {p.key for p in remain} == {"title:[宴]Rotated Out", "title:Link"}


@pytest.mark.asyncio
async def test_external_sources_merge(db, tmp_path, monkeypatch):
    """外部补充源：仅日服侧、override/fill、version_cn 拒写、哈希变化才应用。"""
    from nonebot_plugin_awmc_helper.core import songdb

    await songdb.rebuild(full_payloads())
    extra = tmp_path / "extra.json"
    extra.write_text(
        json.dumps(
            {
                "8": {
                    "sheets": {
                        "sd": {
                            "version_cn": 99999,  # 国服内容：必须被忽略
                            "version": 21000,  # 自 21000 起，列表为 12 值（21000→最新）
                            "contents": [
                                {
                                    "level_id": 0,
                                    "designer": "EXTRA!",
                                    # 文档线格式：自登场版本 21000 起 12 值
                                    "level": [4.2] * 12,
                                },
                                {
                                    "level_id": 1,
                                    "designer": "FILLED",
                                    "notes": [1, 0, 0, 0, 0],
                                },
                            ],
                        }
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    # 阶段一：fill 模式 —— 空字段被填充、已有字段不动
    monkeypatch.setattr(
        "nonebot_plugin_awmc_helper.config.plugin_config.awmc_extra_song_sources",
        [f"{extra}::fill"],
    )
    summary = await songdb.apply_external_sources()
    assert summary["changed"]
    assert summary["applied"] > 0
    state = await songdb.State.load()
    assert state.charts[(8, "sd", 1)].designer == "FILLED"  # 原 designer 为空 → 填充
    assert state.charts[(8, "sd", 1)].notes_tap == 85  # 已有物量不被 fill 覆盖
    assert state.history_of(8, "sd", 1) == [(20000, 6.0)]  # 已有历史不被 fill 覆盖
    assert state.groups[(8, "sd")].version_cn == 20000  # version_cn 拒写（国服唯二源）
    assert state.groups[(8, "sd")].version == 20000  # fill：已有 version 不动
    # 阶段二：override 模式 —— 日服字段被覆盖、version_cn 仍被拒绝
    monkeypatch.setattr(
        "nonebot_plugin_awmc_helper.config.plugin_config.awmc_extra_song_sources",
        [f"{extra}::override"],
    )
    summary = await songdb.apply_external_sources()
    assert summary["changed"]
    state = await songdb.State.load()
    assert state.groups[(8, "sd")].version == 21000
    assert state.groups[(8, "sd")].version_cn == 20000
    assert state.charts[(8, "sd", 0)].designer == "EXTRA!"
    assert state.charts[(8, "sd", 1)].notes_tap == 1  # override 覆盖物量
    # 扁平列表按登场版本 21000 对位重建变化点（连续去重后单点）
    assert state.history_of(8, "sd", 0) == [(21000, 4.2)]
    # 哈希未变 → 不重复应用
    summary2 = await songdb.apply_external_sources()
    assert not summary2["changed"]


@pytest.mark.asyncio
async def test_external_source_invalid_not_blocking(db, tmp_path, monkeypatch):
    from nonebot_plugin_awmc_helper.core import songdb

    await songdb.rebuild(full_payloads())
    bad = tmp_path / "bad.json"
    bad.write_text("{invalid", encoding="utf-8")
    monkeypatch.setattr(
        "nonebot_plugin_awmc_helper.config.plugin_config.awmc_extra_song_sources",
        [str(bad)],
    )
    summary = await songdb.apply_external_sources()
    assert not summary["changed"]
    assert summary["applied"] == 0
    state = await songdb.State.load()
    assert state.groups[(8, "sd")].version == 20000  # 数据无恙


@pytest.mark.asyncio
async def test_external_source_current_level(db, tmp_path, monkeypatch):
    """单元素 level = 快照源「当前定数」语义（§7.5-C）：

    - 有历史且末值不同：锚定日服当前版本**追加**变化点，绝不清掉既有变化点；
    - 末值相同：幂等 no-op；
    - 无历史：退化为登场版本单行；
    - fill 模式：已有历史一律不动。
    """
    from nonebot_plugin_awmc_helper.core import songdb

    await songdb.rebuild(full_payloads())
    doc = tmp_path / "magical.json"
    doc.write_text(
        json.dumps(
            {
                # sd0 已有历史 [(20000, 4.0), (23000, 4.5)]，快照当前定数 4.8
                "8": {
                    "sheets": {
                        "sd": {
                            "contents": [{"level_id": 0, "level": [4.8]}],
                        }
                    }
                },
                # 无历史（from=未知、无 dschange）：登场版本在文档中给出
                "12": {
                    "sheets": {
                        "sd": {
                            "version": 23000,
                            "contents": [{"level_id": 0, "level": [5.2]}],
                        }
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "nonebot_plugin_awmc_helper.config.plugin_config.awmc_extra_song_sources",
        [str(doc)],
    )
    summary = await songdb.apply_external_sources()
    assert summary["changed"]
    state = await songdb.State.load()
    # 既有变化点保留，末尾按日服当前版本（夹具 max 组版本 = 26500 increments 曲）追加
    assert state.history_of(8, "sd", 0) == [(20000, 4.0), (23000, 4.5), (26500, 4.8)]
    # 无历史 → 登场版本单行（§6 退化口径）
    assert state.groups[(12, "sd")].version == 23000
    assert state.history_of(12, "sd", 0) == [(23000, 5.2)]
    # 幂等：同值重放（绕过哈希门直接合并）不再追加
    await songdb._merge_extra_docs(
        [("magical.json", "override", json.loads(doc.read_text(encoding="utf-8")))]
    )
    state = await songdb.State.load()
    assert state.history_of(8, "sd", 0) == [(20000, 4.0), (23000, 4.5), (26500, 4.8)]
    # fill 模式：已有历史不动
    fill_doc = tmp_path / "fill.json"
    fill_doc.write_text(
        json.dumps(
            {"8": {"sheets": {"sd": {"contents": [{"level_id": 0, "level": [9.9]}]}}}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "nonebot_plugin_awmc_helper.config.plugin_config.awmc_extra_song_sources",
        [f"{fill_doc}::fill"],
    )
    await songdb.apply_external_sources()
    state = await songdb.State.load()
    assert state.history_of(8, "sd", 0) == [(20000, 4.0), (23000, 4.5), (26500, 4.8)]


@pytest.mark.asyncio
async def test_external_source_creates_missing_song(db, tmp_path, monkeypatch):
    """骨架外新曲（文档自带 id）直接创建日侧行，并经 extra 在列信号免于误删。"""
    from nonebot_plugin_awmc_helper.core import songdb

    await songdb.rebuild(full_payloads())
    doc = tmp_path / "new.json"
    doc.write_text(
        json.dumps(
            {
                "6001": {
                    "title": "Magical New Song",
                    "artist": " Someone ",
                    "genre": "オニゲー",
                    "bpm": "200",
                    "sheets": {
                        "sd": {
                            "version": 27000,
                            "contents": [
                                {"level_id": 0, "level": [3.0]},
                                {
                                    "level_id": 3,
                                    "level": [13.5],
                                    "designer": "MAGI",
                                },
                            ],
                        },
                        "utage": {
                            "version": 27000,
                            "contents": [
                                {
                                    "level_id": 0,
                                    "level": [12.7],
                                    "kanji": "魔",
                                    "comment": "マジカル",
                                    "is_buddy": False,
                                }
                            ],
                        },
                    },
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "nonebot_plugin_awmc_helper.config.plugin_config.awmc_extra_song_sources",
        [str(doc)],
    )
    summary = await songdb.apply_external_sources()
    assert summary["changed"]
    state = await songdb.State.load()
    assert state.songs[6001].title == "Magical New Song"
    assert state.groups[(6001, "sd")].version == 27000
    assert state.groups[(6001, "sd")].version_cn is None  # 恒 NULL（日侧行）
    assert state.history_of(6001, "sd", 3) == [(27000, 13.5)]
    assert state.charts[(6001, "utage", 0)].kanji == "魔"
    assert state.charts[(6001, "utage", 0)].comment == "マジカル"
    doc_json = songdb.standard_json(state)
    assert doc_json["6001"]["title"] == "Magical New Song"
    # 下次全量重建：extra id 作为 JP 在列信号 → 曲与组版本保留
    await songdb.rebuild(full_payloads(), extra_jp_ids={6001})
    state = await songdb.State.load()
    assert 6001 in state.songs
    assert state.groups[(6001, "sd")].version == 27000
    assert state.history_of(6001, "sd", 3) == [(27000, 13.5)]
    # 无信号时（maimaiinfo/otoge 均不知晓）→ 两侧皆无 → 整曲删除
    await songdb.rebuild(full_payloads())
    state = await songdb.State.load()
    assert 6001 not in state.songs


def test_genre_alias_normalization():
    """国服口径分类名（落雪/水鱼）归一为 maimai_py Genre 值，未知名回落 maimai。"""
    from maimai_py import Genre

    from nonebot_plugin_awmc_helper.core.songdb import _genre_of

    assert _genre_of("其他游戏") == Genre.ゲームバラエティ
    assert _genre_of("流行&动漫") == Genre.POPSアニメ
    assert _genre_of("niconico & VOCALOID") == Genre.niconicoボーカロイド
    assert _genre_of("东方Project") == Genre.東方Project
    assert _genre_of("音击&中二节奏") == Genre.オンゲキCHUNITHM
    assert _genre_of("舞萌") == Genre.maimai
    assert _genre_of("未实装") == Genre.maimai


@pytest.mark.asyncio
async def test_external_merge_syncs_fingerprint(db, tmp_path, monkeypatch):
    """外部源直写主表后必须同步指纹，否则日服视图缓存失效键不变、读不到新曲。"""
    from nonebot_plugin_awmc_helper.core import store, songdb

    await songdb.rebuild(full_payloads())
    fp_before = songdb.CURRENT_FINGERPRINT
    extra = tmp_path / "new_song.json"
    extra.write_text(
        json.dumps(
            {
                "2041": {
                    "title": "指纹新曲",
                    "sheets": {
                        "dx": {
                            "version": 27000,
                            "contents": [{"level_id": 1, "level": [7.5]}],
                        }
                    },
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "nonebot_plugin_awmc_helper.config.plugin_config.awmc_extra_song_sources",
        [str(extra)],
    )
    summary = await songdb.apply_external_sources()
    assert summary["changed"]
    assert songdb.CURRENT_FINGERPRINT != fp_before
    # 全库标准 JSON 同步包含新曲
    doc = await store.kv_get("songdb_json")
    assert "指纹新曲" in json.dumps(doc, ensure_ascii=False)
