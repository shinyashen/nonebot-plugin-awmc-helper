"""core/songdb 合并管线：rebuild 入库、缺失/删除规则、pending、外部源。

断言值全部取自 tests/data/snapshots/ 真实快照（2026-09-29 取材）的派生结果；
外部源合并机制类用例的临时文档为构造 fixture（测机制不测数据，已注明）。
"""

import json

import pytest
from songdb_fixtures import (
    make_lxns,
    make_all_data,
    make_dschange,
    make_divingfish,
    make_otoge_live,
    make_pending_item,
    make_otoge_deleted,
    make_pending_revealed,
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
    # 并集：快照 14 根曲 − 青春コンプレックス（maimaiinfo 滞留 × otoge 已下架
    # 20260807 × 国服已无）+ 构造国服限定 9002 = 14
    assert result["songs"] == 14
    assert result["removed"] == 1
    assert 1634 not in state.songs
    # from=未知 且国服源没有（12 レーザービーム，otoge 亦无）：版本未知 ≠ 两侧
    # 皆无，记录保留、不被误删（maimaiinfo 在列即 JP 在列信号）
    assert 12 in state.songs
    assert state.groups[(12, "sd")].version is None
    # 构造国服限定曲（日侧无）：version=None、version_cn=25500
    g = state.groups[(9002, "sd")]
    assert g.version is None
    assert g.version_cn == 25500
    # 日服限定组（199 DX＝チルノ CiRCLE 重制，国服源无）：version=26000、version_cn=None
    g = state.groups[(199, "dx")]
    assert g.version == 26000
    assert g.version_cn is None
    # increments 新曲组（10267 Ignite Infinity，CiRCLE PLUS 登场）：version=26500
    assert state.groups[(267, "dx")].version == 26500
    assert state.groups[(267, "dx")].version_cn is None
    # 组级版本：8 True Love Song SD 来自 from（maimai）；version_cn 取组内谱面值
    assert state.groups[(8, "sd")].version == 10000
    assert state.groups[(8, "sd")].version_cn == 10000
    # 落雪 CN 本地 id（BLACK ROSE 落雪 1001 ↔ 水鱼/日服 11001）正常合流
    assert state.songs[1001].title == "BLACK ROSE"
    assert state.groups[(1001, "dx")].version_cn == 20000
    # 日期规则（当前 otoge 现役 payload 已不含 date_added，只保留 release）：
    # sd/dx 无可得日期 → None；宴 = release（复活日）优先
    assert state.groups[(8, "sd")].date is None
    assert state.groups[(199, "utage")].date == 231225
    assert state.groups[(1355, "utage")].date == 230914
    # 封面（otoge-db 唯一来源）与宴字段
    assert state.songs[8].image_url == "c7cfd8a91e0436ac.png"
    utage = state.charts[(199, "utage", 0)]
    assert utage.kanji == "蛸"
    assert utage.comment == "パーフェクトホールド教室"
    assert not utage.is_buddy
    # buddy 宴左右物量（[協]ラグトレイン，otoge/all_data/lxns 三源同值）
    buddy = state.charts[(1355, "utage", 1)]
    assert buddy.is_buddy
    assert json.loads(buddy.notes_left) == [183, 76, 53, 164, 173]
    assert json.loads(buddy.notes_right) == [172, 63, 53, 102, 216]
    # 主物量不变式：buddy 行主列 ≡ 左右之和（dx 星按主物量算 max DX）
    assert (
        buddy.notes_tap,
        buddy.notes_hold,
        buddy.notes_slide,
        buddy.notes_touch,
        buddy.notes_break,
    ) == (355, 139, 106, 266, 389)
    # 定数历史（变化点）与宴推导值：8 BASIC 在 UNiVERSE PLUS 4.0→5.0；
    # 蛸宴标级 12+? 推导 12.7
    assert state.history_of(8, "sd", 0) == [(20000, 4.0), (22500, 5.0)]
    assert state.history_of(199, "utage", 0) == [(24000, 12.7)]
    # 标准 JSON 与指纹已生成
    doc = await db.kv_get("songdb_json")
    assert doc["8"]["sheets"]["sd"]["version_cn"] == 10000
    # 01 文档线格式：自登场版本（DX 初代）起 14 值，UNiVERSE PLUS 起变 5.0
    assert doc["8"]["sheets"]["sd"]["contents"][0]["level"] == [4.0] * 5 + [5.0] * 9
    assert doc["8"]["sheets"]["sd"]["contents"][0]["notes"] == [63, 23, 8, 0, 2]
    # 宴为单元素标级浮点列表
    assert doc["199"]["sheets"]["utage"]["contents"][0]["level"] == [12.7]
    fp = songdb.CURRENT_FINGERPRINT
    assert fp is not None
    assert len(fp) == 32
    # 国服当前版本 = max(version_cn) = 25500（PRiSM PLUS，快照时点）
    assert state.cn_current_version() == 25500


@pytest.mark.asyncio
async def test_cn_derivation_and_fallback(db):
    """国服定数推导：≤ 国服版本末值；同步上线曲走首值兜底（§5.3）。

    真实锚：239 System "Z" Re:MASTER 在 CiRCLE PLUS 由 14.0→14.2，国服
    （PRiSM PLUS）仍为 14.0——落雪实测与推导一致。
    """
    from nonebot_plugin_awmc_helper.core.songdb import State, rebuild, cn_level_value

    await rebuild(full_payloads())
    state = await State.load()
    assert state.history_of(239, "sd", 4) == [
        (21000, 13.9),
        (21500, 14.0),
        (26500, 14.2),
    ]
    assert state.resolve_chart_level(239, "sd", 4) == 14.2  # JP 最新
    assert state.resolve_chart_level(239, "sd", 4, version=25500) == 14.0  # 国服视角
    assert cn_level_value(state.history_of(239, "sd", 4), 25500) == 14.0
    # carry-forward 边界：早于首行 → None（该版本尚无此谱面）
    assert state.resolve_chart_level(239, "sd", 4, version=20000) is None
    # 国服推导的首值兜底：区间无值取日服首值（同步上线的曲，§5.3 已验证 20/20）
    assert cn_level_value(state.history_of(239, "sd", 4), 20000) == 13.9


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
    assert state.groups[(9002, "sd")].version_cn == 25500


@pytest.mark.asyncio
async def test_jp_missing_and_cn_absence_delete(db):
    """缺失矩阵：一侧缺失保留、两侧信号皆无删除；JP 历史不受 CN 波动影响。"""
    from nonebot_plugin_awmc_helper.core import songdb

    await songdb.rebuild(full_payloads())
    # 日服限定组（199 DX）：国服源始终无 → version_cn=None，记录与 JP 定数历史保留
    state = await songdb.State.load()
    g = state.groups[(199, "dx")]
    assert g.version == 26000
    assert g.version_cn is None
    assert state.history_of(199, "dx", 0) == [(26000, 3.0)]
    # 国服在列曲（9002）双源同时消失：JP 也无 → 两侧信号皆无 → 整曲删除
    await songdb.rebuild(
        full_payloads(
            lxns=make_lxns(drop_ids={9002}), divingfish=make_divingfish(drop_ids={9002})
        )
    )
    state = await songdb.State.load()
    assert 9002 not in state.songs
    # JP 侧波动不影响其余曲（8 仍在且历史完好）
    assert state.history_of(8, "sd", 0) == [(20000, 4.0), (22500, 5.0)]


@pytest.mark.asyncio
async def test_source_failure_tolerated(db):
    """单源失败：该源列不动、不做缺省同步（落雪失败时 version_cn 保持）。"""
    from nonebot_plugin_awmc_helper.core import songdb

    await songdb.rebuild(full_payloads())
    await songdb.rebuild(full_payloads(lxns=None, divingfish=None))
    state = await songdb.State.load()
    assert state.groups[(8, "sd")].version_cn == 10000  # 未被误清
    assert 9002 in state.songs
    # maimaiinfo 失败：otoge 充实跳过，JP/CN 列保持
    await songdb.rebuild(full_payloads(maimaiinfo=None, dschange=None))
    state = await songdb.State.load()
    assert state.groups[(8, "sd")].version == 10000


@pytest.mark.asyncio
async def test_disabled_means_cn_absent(db):
    """落雪 disabled（删除/宴下架）= CN 缺失：version_cn 置 NULL；组粒度互不影响。

    落雪对删除曲打标留在列表，宴条目（11xxxx 命名空间）独立于普通谱条目——
    按真实 payload 粒度（raw id）禁用。
    """
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
    assert g.version == 10000
    assert g.version_cn is None
    # 其他曲不受影响
    assert state.groups[(30, "sd")].version_cn == 10000
    # 宴条目单独禁用（[協]ラグトレイン 宴条目 111355）：仅宴组置 NULL，
    # 同曲 DX 普通谱组（ラグトレイン 1355 条目）version_cn 保持
    await songdb.rebuild(
        full_payloads(
            lxns=make_lxns(disable_ids={111355}),
            divingfish=make_divingfish(drop_ids={111355}),
        )
    )
    state = await songdb.State.load()
    assert state.groups[(1355, "utage")].version_cn is None
    assert state.groups[(1355, "utage")].version == 24000
    assert state.groups[(1355, "dx")].version_cn == 22007
    # 构造国服限定曲 9002 被禁用 + JP 也无 → 整曲删除
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
    """otoge 独有的无 id 条目进 pending；id 到位后归并清理。

    真实锚：MAGiCAL 新曲「物語はここから」已在 otoge 现役（version 27000）但
    maimaiinfo 快照未收录 → 唯一 pending；「id 到位」按真实演进模拟——maimaiinfo
    未来收录（此处以 MuNET 真实条目 id 2020 转换为 all_data 形态注入）。
    """
    from nonebot_plugin_awmc_helper.core import store, songdb

    await songdb.rebuild(full_payloads())
    from sqlmodel import select

    async with store.session() as session:
        pending = list((await session.exec(select(store.SongPending))).all())
    assert [(p.key, p.reason) for p in pending] == [
        ("title:物語はここから", "missing_id")
    ]
    # id 到位：maimaiinfo 收录（MuNET 真实值：DX id 10202 → 根 id 202，DX 四谱，
    # 定数 4.0/7.5/10.9/13.5）
    payloads = full_payloads()
    payloads["maimaiinfo"]["10202"] = {
        "id": "10202",
        "title": "物語はここから",
        "type": "DX",
        "ds": [4.0, 7.5, 10.9, 13.5],
        "level": ["4", "7", "10+", "13"],
        "charts": [
            {"notes": [192, 12, 4, 4, 4], "charter": "-"},
            {"notes": [262, 14, 7, 40, 4], "charter": "-"},
            {"notes": [348, 73, 39, 12, 25], "charter": "けんけん法師"},
            {"notes": [564, 65, 80, 32, 68], "charter": "Luxizhel"},
        ],
        "basic_info": {
            "title": "物語はここから",
            "artist": "OSTER project feat. Kanata.N",
            "genre": "舞萌",
            "bpm": "190",
            "from": "maimai でらっくす MAGiCAL",
        },
    }
    await songdb.rebuild(payloads)
    merged = await songdb.flush_pending()
    assert merged == 1
    state = await songdb.State.load()
    assert 202 in state.songs
    assert state.groups[(202, "dx")].version == 27000
    assert state.history_of(202, "dx", 3) == [(27000, 13.5)]
    async with store.session() as session:
        remain = list((await session.exec(select(store.SongPending))).all())
    assert remain == []


@pytest.mark.asyncio
async def test_external_sources_merge(db, tmp_path, monkeypatch):
    """外部补充源：仅日服侧、override/fill、version_cn 拒写、哈希变化才应用。

    被合并非真实数据（构造文档测机制）；被操作的真实行是 8 True Love Song
    （sd1 designer 为空「-」、sd0 已有 UNiVERSE PLUS 变更点）。
    """
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
    assert state.history_of(8, "sd", 1) == [
        (20000, 6.4),
        (23000, 7.2),
    ]  # 已有历史不被 fill 覆盖
    assert state.groups[(8, "sd")].version_cn == 10000  # version_cn 拒写（国服唯二源）
    assert state.groups[(8, "sd")].version == 10000  # fill：已有 version 不动
    # 阶段二：override 模式 —— 日服字段被覆盖、version_cn 仍被拒绝
    monkeypatch.setattr(
        "nonebot_plugin_awmc_helper.config.plugin_config.awmc_extra_song_sources",
        [f"{extra}::override"],
    )
    summary = await songdb.apply_external_sources()
    assert summary["changed"]
    state = await songdb.State.load()
    assert state.groups[(8, "sd")].version == 21000
    assert state.groups[(8, "sd")].version_cn == 10000
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
    assert state.groups[(8, "sd")].version == 10000  # 数据无恙


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
                # sd0 已有历史 [(20000,4.0),(22500,5.0)]，快照当前定数 4.8（构造）
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
    assert state.history_of(8, "sd", 0) == [
        (20000, 4.0),
        (22500, 5.0),
        (26500, 4.8),
    ]
    # 无历史 → 登场版本单行（§6 退化口径）
    assert state.groups[(12, "sd")].version == 23000
    assert state.history_of(12, "sd", 0) == [(23000, 5.2)]
    # 幂等：同值重放（绕过哈希门直接合并）不再追加
    await songdb._merge_extra_docs(
        [("magical.json", "override", json.loads(doc.read_text(encoding="utf-8")))]
    )
    state = await songdb.State.load()
    assert state.history_of(8, "sd", 0) == [
        (20000, 4.0),
        (22500, 5.0),
        (26500, 4.8),
    ]
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
    assert state.history_of(8, "sd", 0) == [
        (20000, 4.0),
        (22500, 5.0),
        (26500, 4.8),
    ]


@pytest.mark.asyncio
async def test_external_source_forced_reapply_after_rebuild(db, tmp_path, monkeypatch):
    """回归（2026-09-25）：重建以基础源覆写外部字段后，必须强制重应用外部源。

    apply_jp 无条件写回 maimaiinfo 物量/定数历史，外部源合并曾按文件哈希门控
    ——文件未变即跳过，机台校正活不过下一次每日重建。refresh_all 现以 force
    重放；无真实值变化时 changed=False（不触发无谓底图重建）。
    """
    from nonebot_plugin_awmc_helper.core import songdb

    await songdb.rebuild(full_payloads())
    doc = tmp_path / "magical.json"
    doc.write_text(
        json.dumps(
            {
                "8": {
                    "sheets": {
                        "sd": {
                            "contents": [
                                {
                                    "level_id": 0,
                                    "notes": [9, 9, 9, 9, 9],
                                    "level": [7.7],
                                }
                            ]
                        }
                    }
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
    summary = await songdb.apply_external_sources(force=True)
    assert summary["changed"]
    assert summary["applied"] > 0
    state = await songdb.State.load()
    assert (
        state.charts[(8, "sd", 0)].notes_tap,
        state.charts[(8, "sd", 0)].notes_touch,
    ) == (9, 9)

    # 模拟次日重建：maimaiinfo 旧物量/定数历史写回，机台校正被冲掉
    await songdb.rebuild(full_payloads())
    state = await songdb.State.load()
    assert state.charts[(8, "sd", 0)].notes_tap == 63
    assert state.history_of(8, "sd", 0) == [(20000, 4.0), (22500, 5.0)]

    # 文件未变，force 重放：校正恢复且 changed 反映真实变化
    summary = await songdb.apply_external_sources(force=True)
    assert summary["changed"]
    state = await songdb.State.load()
    assert (
        state.charts[(8, "sd", 0)].notes_tap,
        state.charts[(8, "sd", 0)].notes_touch,
    ) == (9, 9)
    assert state.history_of(8, "sd", 0) == [
        (20000, 4.0),
        (22500, 5.0),
        (26500, 7.7),
    ]

    # 幂等：再次 force 无真实变化 → changed=False（不触发底图重建）
    summary = await songdb.apply_external_sources(force=True)
    assert not summary["changed"]

    # 非 force：哈希未变仍跳过（上传路径语义保持）
    summary = await songdb.apply_external_sources()
    assert not summary["changed"]


@pytest.mark.asyncio
async def test_external_source_none_fields_do_not_wipe(db, tmp_path, monkeypatch):
    """回归（2026-09-25 封面全挂事故）：文档显式 None 的字段不得清空既有值。

    机台源无官方封面哈希名，条目曾带 image_url: None——override 合并把它写进
    既有行，导致全表封面被抹（此前每次上传后"封面消失又恢复"即此因）。
    """
    from nonebot_plugin_awmc_helper.core import songdb

    await songdb.rebuild(full_payloads())
    state = await songdb.State.load()
    state.song(8).image_url = "cover.png"
    await state.save()

    doc = tmp_path / "magical.json"
    doc.write_text(
        json.dumps(
            {
                "8": {
                    "title": "True Love Song",
                    "image_url": None,
                    "sheets": {},
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
    summary = await songdb.apply_external_sources(force=True)
    assert not summary["changed"]
    state = await songdb.State.load()
    assert state.songs[8].image_url == "cover.png"  # 未被 None 抹掉


@pytest.mark.asyncio
async def test_external_source_creates_missing_song(db, tmp_path, monkeypatch):
    """骨架外新曲（文档自带 id）直接创建日侧行，并经 extra 在列信号免于误删。

    真实演进锚：MAGiCAL 曲「物語はここから」先经机台快照（MuNET）建行——
    maimaiinfo 未收录，但 otoge 现役在列，title 在列信号即可保行（真实链路：
    MAGiCAL 曲不会因 maimaiinfo 滞后被误删）。
    """
    from nonebot_plugin_awmc_helper.core import songdb

    await songdb.rebuild(full_payloads())
    doc = tmp_path / "new.json"
    doc.write_text(
        json.dumps(
            {
                # MuNET 真实条目（id 2020）转换为标准 JSON 形态
                "2020": {
                    "title": "物語はここから",
                    "artist": "OSTER project feat. Kanata.N",
                    "genre": "maimai",
                    "bpm": "190",
                    "sheets": {
                        "dx": {
                            "version": 27000,
                            "contents": [
                                {"level_id": 0, "level": [4.0]},
                                {
                                    "level_id": 3,
                                    "level": [13.5],
                                    "designer": "Luxizhel",
                                },
                            ],
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
        [str(doc)],
    )
    summary = await songdb.apply_external_sources()
    assert summary["changed"]
    state = await songdb.State.load()
    assert state.songs[2020].title == "物語はここから"
    assert state.groups[(2020, "dx")].version == 27000
    assert state.groups[(2020, "dx")].version_cn is None  # 恒 NULL（日侧行）
    assert state.history_of(2020, "dx", 3) == [(27000, 13.5)]
    doc_json = songdb.standard_json(state)
    assert doc_json["2020"]["title"] == "物語はここから"
    # 无 extra 信号重建：otoge 现役 title 在列 → 曲与组版本保留（真实在列信号）
    await songdb.rebuild(full_payloads())
    state = await songdb.State.load()
    assert 2020 in state.songs
    assert state.groups[(2020, "dx")].version == 27000
    assert state.history_of(2020, "dx", 3) == [(27000, 13.5)]

    # 对照（构造条目，测「两侧皆无」路径）：otoge/maimaiinfo 均不知晓 → 整曲删除
    doc2 = tmp_path / "ghost.json"
    doc2.write_text(
        json.dumps(
            {
                "6001": {
                    "title": "（构造）幽灵样例曲",
                    "sheets": {
                        "dx": {
                            "version": 27000,
                            "contents": [{"level_id": 3, "level": [13.5]}],
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
        [str(doc2)],
    )
    summary = await songdb.apply_external_sources()
    assert summary["changed"]
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
                # 构造条目（测指纹机制，快照中无此 id）
                "2041": {
                    "title": "（构造）指纹新曲",
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
    assert "（构造）指纹新曲" in json.dumps(doc, ensure_ascii=False)


@pytest.mark.asyncio
async def test_otoge_fills_external_created_row(db, tmp_path, monkeypatch):
    """maimaiinfo 未收录、外部源已建行的曲：otoge title join 直接充实，不进暂存。

    真实链路：MAGiCAL 曲「物語はここから」先经机台快照建行（此时 otoge 载荷
    在场）→ otoge 现役条目（封面 7562b43964819ada.png / release 260917）title
    join 充实，pending 不再收纳；物量列按 otoge「后续补数」的演进形态断言
    （值取 MuNET 真实物量，字段名按 otoge 约定构造）。
    """
    from sqlmodel import select

    from nonebot_plugin_awmc_helper.core import store, songdb

    await songdb.rebuild(full_payloads())
    extra = tmp_path / "extra_first.json"
    extra.write_text(
        json.dumps(
            {
                # 键为根 id（标准 JSON 契约空间；MuNET musicId 2020 ↔ 根 202）
                "202": {
                    "title": "物語はここから",
                    "sheets": {
                        "dx": {
                            "version": 27000,
                            "contents": [{"level_id": 3, "level": [13.5]}],
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
    # 下一轮重建：otoge 载荷含同名条目 → 直接充实已有行；此处同时模拟 otoge
    # 为 MAGiCAL 新曲补数的真实演进（追加物量字段，值取 MuNET 实测）
    payloads = full_payloads()
    for item in payloads["otoge_db"]:
        if item["title"] == "物語はここから":
            item.update(
                {
                    "dx_lev_mas_notes_tap": "564",
                    "dx_lev_mas_notes_hold": "65",
                    "dx_lev_mas_notes_slide": "80",
                    "dx_lev_mas_notes_touch": "32",
                    "dx_lev_mas_notes_break": "68",
                }
            )
    await songdb.rebuild(payloads)
    # 首轮重建留下的 pending 行由周期 flush 清理（真实生产流程）
    merged = await songdb.flush_pending()
    assert merged == 1
    state = await songdb.State.load()
    assert state.songs[202].image_url == "7562b43964819ada.png"
    assert state.songs[202].genre == "maimai"
    assert state.groups[(202, "dx")].date == 260917  # dx = release 优先
    chart = state.charts[(202, "dx", 3)]
    assert (
        chart.notes_tap,
        chart.notes_hold,
        chart.notes_slide,
        chart.notes_touch,
        chart.notes_break,
    ) == (564, 65, 80, 32, 68)
    async with store.session() as session:
        pend = list((await session.exec(select(store.SongPending))).all())
    assert all("物語はここから" not in p.key for p in pend)


@pytest.mark.asyncio
async def test_external_merge_buddy_utage(db, tmp_path, monkeypatch):
    """外部源宴 buddy 谱：notes_left/notes_right 与 is_buddy 合并进谱面行。

    构造行（外部源测机制），左右物量取真实 [協]ラグトレイン 值。
    """
    from nonebot_plugin_awmc_helper.core import songdb

    await songdb.rebuild(full_payloads())
    extra = tmp_path / "extra_buddy.json"
    extra.write_text(
        json.dumps(
            {
                "1903": {
                    "title": "（构造）外部源 buddy 宴样例",
                    "sheets": {
                        "utage": {
                            "version": 27000,
                            "contents": [
                                {
                                    "level_id": 1,
                                    "kanji": "協",
                                    "is_buddy": True,
                                    "level": [13.0],
                                    "notes_left": [183, 76, 53, 164, 173],
                                    "notes_right": [172, 63, 53, 102, 216],
                                }
                            ],
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
    state = await songdb.State.load()
    chart = state.charts[(1903, "utage", 1)]
    assert chart.is_buddy
    assert chart.notes_left is not None
    assert json.loads(chart.notes_left) == [183, 76, 53, 164, 173]
    assert chart.notes_right is not None
    assert json.loads(chart.notes_right) == [172, 63, 53, 102, 216]
    # 主物量入库即存左右合计（dx 星按主物量算 max DX，留 0 会除零）
    assert (
        chart.notes_tap,
        chart.notes_hold,
        chart.notes_slide,
        chart.notes_touch,
        chart.notes_break,
    ) == (355, 139, 106, 266, 389)
    assert state.groups[(1903, "utage")].version == 27000


def test_pending_to_song_placeholder_fields():
    """物化为临时 Song：id=0、缺失字段按「0 即 -」约定、版本未知 logo 缺席。

    真实形态：MAGiCAL 曲「物語はここから」otoge 现役条目定数未揭 → gate 拒之
    门外；MuNET 揭晓形态（make_pending_revealed）物化后，谱师/物量缺的难度行
    仍按「0 即 -」展示。
    """
    from maimai_py import SongType

    from nonebot_plugin_awmc_helper.core.songdb import (
        pending_to_song,
        parse_pending_item,
    )

    # 定数未揭（真实现役形态）：gate 直接拒之门外
    assert parse_pending_item(make_pending_item()) is None

    bare = parse_pending_item(
        make_pending_revealed(artist=None, bpm=None, catcode="未知分类", version=None)
    )
    assert bare is not None
    assert bare.genre_display is None
    song = pending_to_song(bare)
    assert song.id == 0  # 临时 id，不参与任何 id 键路径
    assert song.bpm == 0  # 「0 即 -」：卡面画 -
    assert song.artist == "-"
    assert song.version == 0  # from_value(0) 为 None → 版本 logo 自然缺席
    assert all(d.version == 0 for d in song.difficulties.dx)
    # 真实标级串：otoge 现役 4/7/10+/13
    assert [d.level for d in song.difficulties.dx] == ["4", "7", "10+", "13"]
    # 谱师仅 exp/mas 有（MuNET 实测），bas/adv 画 -
    assert (
        next(d for d in song.difficulties.dx if d.level_index.value == 2).note_designer
        == "けんけん法師"
    )
    assert (
        next(d for d in song.difficulties.dx if d.level_index.value == 0).note_designer
        == "-"
    )
    assert all(
        d.type == SongType.DX and d.level_value > 0 for d in song.difficulties.dx
    )

    full = parse_pending_item(make_pending_revealed())
    assert full is not None
    assert full.genre_display == "舞萌"
    song = pending_to_song(full)
    # 真实现役条目未收录 bpm（MAGiCAL 新曲常态）→ 0 画 -
    assert song.bpm == 0
    assert song.artist == "OSTER project feat. Kanata.N"
    mas = next(d for d in song.difficulties.dx if d.level_index.value == 3)
    assert mas.level == "13"
    assert mas.level_value == 13.5  # MuNET 实测
    assert mas.note_designer == "Luxizhel"
    exp = next(d for d in song.difficulties.dx if d.level_index.value == 2)
    assert exp.note_designer == "けんけん法師"
    # 无谱师字段的两级画 -
    assert (
        next(d for d in song.difficulties.dx if d.level_index.value == 0).note_designer
        == "-"
    )
    assert all(
        d.type == SongType.DX and d.level_value > 0 for d in song.difficulties.dx
    )


def test_cover_key_stable_per_title():
    """封面缓存键按标题派生（pending 曲无 id，不能按 id 缓存）。"""
    from nonebot_plugin_awmc_helper.core.songdb import parse_pending_item

    a = parse_pending_item(make_pending_revealed())
    b = parse_pending_item(make_pending_revealed(title="別の新曲"))
    assert a is not None
    assert b is not None
    assert a.cover_key == a.cover_key
    assert a.cover_key != b.cover_key


@pytest.mark.asyncio
async def test_pending_search_filters(db):
    """搜索过滤：gate 排除无定数行、reason 限定 missing_id、标题归一子串与定数区间。

    真实两态：otoge 现役条目定数未揭（gate 拒之门外）；MuNET 定数揭晓后可查。
    """
    from nonebot_plugin_awmc_helper.core.songdb import pending_search, upsert_pending

    await upsert_pending(
        "otoge-db", "title:物語はここから", "missing_id", make_pending_item()
    )
    # 定数未揭（真实现役形态）：gate 不过，任何搜索都不出
    assert await pending_search() == []
    assert await pending_search(title="物語") == []

    # 定数揭晓（MuNET 实测值）：可查
    await upsert_pending(
        "otoge-db", "title:物語はここから", "missing_id", make_pending_revealed()
    )
    all_pending = await pending_search()
    assert [p.title for p in all_pending] == ["物語はここから"]

    hit = await pending_search(title="物語")  # 子串
    assert len(hit) == 1
    assert hit[0].key == "title:物語はここから"
    assert hit[0].source == "otoge-db"
    assert not await pending_search(title="ここからじゃない")  # 不含关键词

    in_range = await pending_search(ds_range=(13.0, 14.0))
    assert len(in_range) == 1
    assert not await pending_search(ds_range=(15.0, 16.0))

    # 非 missing_id 行不参与
    await upsert_pending(
        "otoge-db",
        "title:（构造）別理由",
        "other_reason",
        make_pending_revealed(title="（构造）別理由"),
    )
    assert [p.title for p in await pending_search()] == ["物語はここから"]


@pytest.mark.asyncio
async def test_song_service_pending_wrappers(db):
    """SongService 包装层透传（music_query 实际调用入口）。"""
    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.songdb import upsert_pending

    await upsert_pending(
        "otoge-db", "title:物語はここから", "missing_id", make_pending_revealed()
    )
    assert len(await song_service.pending_by_title_fuzzy("物語")) == 1
    assert len(await song_service.pending_by_level_value(13.0, 14.0)) == 1
    assert await song_service.pending_by_title_fuzzy("没有的歌") == []
