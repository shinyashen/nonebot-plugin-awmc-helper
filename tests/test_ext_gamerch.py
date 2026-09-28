"""core/ext/gamerch：wiki 运行时补充源（解析 / 缺口定向 / fill 合并）。

页面样本为真实 gamerch 页（tests/data/snapshots/gamerch/，2026-09-29 取材）：
True Love Song（534105，STD 表含旧框行）、ラグトレイン（533989，DX 表 +
[協] buddy (左)/(右) 宴段）、BUDDiES 配信順リスト（796273，清单爬取）。
"""

import gzip
import json
from pathlib import Path

import pytest
from songdb_fixtures import (
    make_lxns,
    make_all_data,
    make_dschange,
    make_divingfish,
    make_otoge_live,
    make_otoge_deleted,
)

_SNAP = Path(__file__).parent / "data" / "snapshots" / "gamerch"


def _page_text(name: str) -> str:
    return gzip.open(_SNAP / f"{name}.html.gz", "rt", encoding="utf-8").read()


PAGE_TRUE_LOVE_SONG = _page_text("534105_TrueLoveSong")
PAGE_RAGTRAIN = _page_text("533989_ラグトレイン")
PAGE_BUDDIES_LIST = _page_text("796273_BUDDiESリスト")


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


def test_parse_page_tables():
    """真实 STD 表（True Love Song 534105）：无 Touch 列、旧框「定数 -」行保留/剔除。"""
    from nonebot_plugin_awmc_helper.core.ext import gamerch

    page = gamerch.parse_page(PAGE_TRUE_LOVE_SONG)
    assert page is not None
    assert page.title == "True Love Song"
    assert len(page.tables) == 1
    pt = page.tables[0]
    assert pt.touch is False
    # 原始解析保留旧框行（「定数 -」的 Lv2 移植谱），过滤在 _legend_free_rows 中进行
    assert [r[0] for r in pt.diff_rows] == ["2", "5", "7+", "10", "12"]
    free = gamerch._legend_free_rows(pt)
    assert [r[0] for r in free] == ["5", "7+", "10", "12"]
    # 真实 wiki 物量与 otoge-db 下架记录/maimaiinfo 完全一致（Tap, Hold, Slide, Break）
    assert [r[3:7] for r in free] == [
        ["63", "23", "8", "2"],
        ["85", "27", "6", "4"],
        ["110", "56", "9", "2"],
        ["263", "14", "19", "6"],
    ]


def test_parse_page_buddy_utage():
    """真实 buddy 宴段（ラグトレイン 533989）：[協] 标签行 + (左)/(右) 两行物量。"""
    from nonebot_plugin_awmc_helper.core.ext import gamerch

    page = gamerch.parse_page(PAGE_RAGTRAIN)
    assert page is not None
    assert page.title == "ラグトレイン"
    pt = gamerch._pick_table(page, "dx")
    assert pt is not None
    assert pt.touch is True  # DX 表带 Touch 列
    # 普通谱行：旧框 Lv2 行在此页带真实定数（2.0，FiNALE 移植值）→ 不剔除
    assert [r[0] for r in gamerch._legend_free_rows(pt)] == ["2", "6", "9+", "13"]
    assert gamerch._legend_free_rows(pt)[0][1] == "2.0"
    # [協]バディ宴段：标签行 + (右) 续行；総数/定数跨行共享
    assert len(pt.label_rows) == 2
    main = pt.label_rows[0]
    assert "協" in main["label"]
    assert "13?" in main["label"]
    assert main["spans"][2] is True  # 総数跨行共享
    # 右手物量（真实值：Tap 172 / Hold 63 / Slide 53 / Touch 102 / Break 216）
    assert main["right"] is not None
    assert main["right"][1:] == ["172", "63", "53", "102", "216"]
    # 左手物量在标签行内（総数 1255 之后）
    assert main["cells"][3:8] == ["183", "76", "53", "164", "173"]


@pytest.mark.asyncio
async def test_apply_fill_only_empty_charts(db, tmp_path, monkeypatch):
    """缺口谱面回填：只填全零物量的曲，非空谱面/其他曲不受影响。

    用真实 True Love Song 页回填真实曲 8：wiki 值与 maimaiinfo 本就一致，
    清零后回填应精确复原（跨源一致性回归）。
    """
    from nonebot_plugin_awmc_helper.core import songdb
    from nonebot_plugin_awmc_helper.core.ext import gamerch

    await songdb.rebuild(full_payloads())
    # 制造缺口：song 8 的 sd0 物量清零（模拟 maimaiinfo/otoge 均无数据）
    state = await songdb.State.load()
    c = state.charts[(8, "sd", 0)]
    c.notes_tap = c.notes_hold = c.notes_slide = c.notes_touch = c.notes_break = 0
    await state.save()

    async def fake_inventory(http, *, max_age):
        return {"truelovesong": 534105}

    async def fake_fetch(http, url, *, max_age):
        assert "534105" in url
        return PAGE_TRUE_LOVE_SONG

    monkeypatch.setattr(gamerch, "build_page_inventory", fake_inventory)
    monkeypatch.setattr(gamerch, "fetch_page_text", fake_fetch)
    # 隔离磁盘缓存（localstore 目录跨运行持久）
    monkeypatch.setattr(gamerch, "_cache_dir", lambda: tmp_path / "gamerch-cache")

    applied, changed = await gamerch.apply_fill()
    assert changed
    assert applied > 0
    state = await songdb.State.load()
    # sd0 被 wiki 值回填（底部锚定：4 行对位 bas/adv/exp/mas；touch 列缺席 → 0）
    assert (
        state.charts[(8, "sd", 0)].notes_tap,
        state.charts[(8, "sd", 0)].notes_hold,
        state.charts[(8, "sd", 0)].notes_slide,
        state.charts[(8, "sd", 0)].notes_touch,
        state.charts[(8, "sd", 0)].notes_break,
    ) == (63, 23, 8, 0, 2)
    # sd1 非缺口（有物量）不受影响
    assert state.charts[(8, "sd", 1)].notes_tap == 85  # 快照原值，未被 fill 改动

    # 再跑一次：无缺口 → 零抓取、无变化
    applied2, changed2 = await gamerch.apply_fill()
    assert applied2 == 0
    assert not changed2


@pytest.mark.asyncio
async def test_page_inventory_kv_cache(db, tmp_path, monkeypatch):
    """清单入库 kv：TTL 内重取不发网络。真实 BUDDiES 配信順リスト页（796273）。"""
    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.core.ext import gamerch

    calls = 0
    monkeypatch.setattr(gamerch, "_cache_dir", lambda: tmp_path / "gamerch-cache")

    class FakeHttp:
        async def get(self, url, **kwargs):
            nonlocal calls
            calls += 1

            class R:
                status_code = 200

                def raise_for_status(self):
                    pass

                text = PAGE_BUDDIES_LIST

            return R()

    inv = await gamerch.build_page_inventory(FakeHttp(), max_age=24)
    # 真实清单锚文本 → 页面编号（ラグトレイン 533989 / チルノ 534554 实测）
    assert inv["ラグトレイン"] == 533989
    assert inv["チルノのパーフェクトさんすう教室"] == 534554
    first_calls = calls
    assert first_calls >= 1
    # kv 已入库
    raw = await store.kv_get("gamerch_page_inventory")
    assert raw is not None
    assert "ラグトレイン" in raw
    # TTL 内重取：零网络
    monkeypatch.setattr(
        gamerch,
        "fetch_page_text",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应发网络请求")),
    )
    inv2 = await gamerch.build_page_inventory(FakeHttp(), max_age=24)
    assert inv2["ラグトレイン"] == 533989
    assert calls == first_calls
    _ = json  # 保持 json 导入（其他断言经由 kv 序列化覆盖）
