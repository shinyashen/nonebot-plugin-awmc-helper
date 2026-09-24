"""core/ext/gamerch：wiki 运行时补充源（解析 / 缺口定向 / fill 合并）。"""

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


# gamerch 2025+ 版式的最小页面：DX 表（含旧框「定数 -」行 + Touch 列）
PAGE_DX_STD = """
<html><body><div class="main">
<h1 class="content-head">Test Song DX</h1>
<table><thead>
<tr><th rowspan="2">Lv</th><th rowspan="2">定数</th><th rowspan="2">総数</th>
<th colspan="5">内訳</th></tr>
<tr><th>Tap</th><th>Hold</th><th>Slide</th><th>Touch</th><th>Break</th></tr>
</thead><tbody>
<tr><th>3</th><td>-</td><td>50</td><td>40</td><td>4</td><td>3</td><td>0</td><td>3</td></tr>
<tr><th>3</th><td>3.0</td><td>66</td><td>52</td><td>6</td><td>3</td><td>1</td><td>4</td></tr>
<tr><th>7</th><td>7.1</td><td>130</td><td>100</td><td>10</td><td>8</td><td>6</td><td>6</td></tr>
<tr><th>10</th><td>10.0</td><td>220</td><td>160</td><td>20</td><td>12</td><td>18</td><td>10</td></tr>
<tr><th>13</th><td>13.2</td><td>350</td><td>240</td><td>30</td><td>20</td><td>30</td><td>30</td></tr>
</tbody></table>
</div></body></html>
"""

# STD 表（无 Touch 列）+ 旧框「Lv2 定数-」多余行
PAGE_STD = """
<html><body><div class="main">
<h1 class="content-head">Test Song SD</h1>
<table><thead>
<tr><th rowspan="2">Lv</th><th rowspan="2">定数</th><th rowspan="2">総数</th>
<th colspan="4">内訳</th></tr>
<tr><th>Tap</th><th>Hold</th><th>Slide</th><th>Break</th></tr>
</thead><tbody>
<tr><th>2</th><td>-</td><td>50</td><td>44</td><td>2</td><td>2</td><td>2</td></tr>
<tr><th>4</th><td>4.0</td><td>63</td><td>55</td><td>4</td><td>2</td><td>2</td></tr>
<tr><th>6</th><td>6.0</td><td>120</td><td>96</td><td>8</td><td>10</td><td>6</td></tr>
<tr><th>9</th><td>9.0</td><td>200</td><td>150</td><td>20</td><td>20</td><td>10</td></tr>
<tr><th>11</th><td>11.5</td><td>300</td><td>220</td><td>30</td><td>40</td><td>10</td></tr>
</tbody></table>
</div></body></html>
"""


def test_parse_page_tables():
    from nonebot_plugin_awmc_helper.core.ext import gamerch

    page = gamerch.parse_page(PAGE_DX_STD)
    assert page is not None
    assert page.title == "Test Song DX"
    assert len(page.tables) == 1
    pt = page.tables[0]
    assert pt.touch is True
    # 原始解析保留旧框行（「定数 -」），过滤在 _legend_free_rows 中进行
    assert [r[0] for r in pt.diff_rows] == ["3", "3", "7", "10", "13"]
    assert [r[0] for r in gamerch._legend_free_rows(pt)] == ["3", "7", "10", "13"]

    page_std = gamerch.parse_page(PAGE_STD)
    pt = page_std.tables[0]
    assert pt.touch is False
    # 旧框「Lv2 定数-」行剔除后恰 4 行
    assert [r[0] for r in gamerch._legend_free_rows(pt)] == ["4", "6", "9", "11"]


def test_parse_page_buddy_utage():
    from nonebot_plugin_awmc_helper.core.ext import gamerch

    html = """
    <html><body><div class="main"><h1 class="content-head">Test Song</h1>
    <table><thead>
    <tr><th rowspan="2">Lv</th><th rowspan="2">台</th><th rowspan="2">総数</th>
    <th rowspan="2">宴2 14?</th><th colspan="5">内訳</th></tr>
    <tr><th>Tap</th><th>Hold</th><th>Slide</th><th>Touch</th><th>Break</th></tr>
    </thead><tbody>
    <tr><th rowspan="2">宴2 14?</th><td>(左)</td><td rowspan="2">1268</td>
    <td rowspan="2">800</td><td rowspan="2">40</td><td rowspan="2">60</td>
    <td rowspan="2">10</td><td rowspan="2">58</td></tr>
    <tr><td>(右)</td></tr>
    </tbody></table></div></body></html>
    """
    page = gamerch.parse_page(html)
    assert page is not None
    pt = page.tables[0]
    assert len(pt.label_rows) == 1
    row = pt.label_rows[0]
    assert "宴2" in row["label"]
    assert row["right"] == ["(右)"]
    assert row["spans"][2] is True  # 総数跨行共享


@pytest.mark.asyncio
async def test_apply_fill_only_empty_charts(db, tmp_path, monkeypatch):
    """缺口谱面回填：只填全零物量的曲，非空谱面/其他曲不受影响。"""
    from nonebot_plugin_awmc_helper.core import songdb
    from nonebot_plugin_awmc_helper.core.ext import gamerch

    await songdb.rebuild(full_payloads())
    # 制造缺口：song 8 的 sd0 物量清零（模拟 maimaiinfo/otoge 均无数据）
    state = await songdb.State.load()
    c = state.charts[(8, "sd", 0)]
    c.notes_tap = c.notes_hold = c.notes_slide = c.notes_touch = c.notes_break = 0
    await state.save()

    async def fake_inventory(http, *, max_age):
        return {"testsongsd": 555}

    async def fake_fetch(http, url, *, max_age):
        assert "555" in url
        return PAGE_STD

    monkeypatch.setattr(gamerch, "build_page_inventory", fake_inventory)
    monkeypatch.setattr(gamerch, "fetch_page_text", fake_fetch)
    # 隔离磁盘缓存（localstore 目录跨运行持久）
    monkeypatch.setattr(gamerch, "_cache_dir", lambda: tmp_path / "gamerch-cache")

    applied, changed = await gamerch.apply_fill()
    assert changed
    assert applied > 0
    state = await songdb.State.load()
    # sd0 被 wiki 值回填（底部锚定：4 行对位 bas/adv/exp/mas）
    assert (
        state.charts[(8, "sd", 0)].notes_tap,
        state.charts[(8, "sd", 0)].notes_hold,
        state.charts[(8, "sd", 0)].notes_slide,
        state.charts[(8, "sd", 0)].notes_touch,
        state.charts[(8, "sd", 0)].notes_break,
    ) == (55, 4, 2, 0, 2)
    # sd1 非缺口（有物量）不受影响
    assert state.charts[(8, "sd", 1)].notes_tap == 85  # 夹具原值，未被 fill 改动

    # 再跑一次：无缺口 → 零抓取、无变化
    applied2, changed2 = await gamerch.apply_fill()
    assert applied2 == 0
    assert not changed2


@pytest.mark.asyncio
async def test_page_inventory_kv_cache(db, tmp_path, monkeypatch):
    """清单入库 kv：TTL 内重取不发网络。"""
    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.core.ext import gamerch

    calls = 0
    monkeypatch.setattr(gamerch, "_cache_dir", lambda: tmp_path / "gamerch-cache")

    class FakeHttp:
        async def get(self, url, **kwargs):
            nonlocal calls
            calls += 1
            pid = url.rstrip("/").rsplit("/", 1)[-1]

            class R:
                status_code = 200

                def raise_for_status(self):
                    pass

                text = (
                    f'<html><body><div class="main"><h1 class="content-head">'
                    f"リスト {pid}</h1>"
                    f'<a href="/maimai/555">Test Song SD</a>'
                    f"</div></body></html>"
                )

            return R()

    inv = await gamerch.build_page_inventory(FakeHttp(), max_age=24)
    assert inv["testsongsd"] == 555
    first_calls = calls
    assert first_calls >= 1
    # kv 已入库
    raw = await store.kv_get("gamerch_page_inventory")
    assert "testsongsd" in raw
    # TTL 内重取：零网络
    monkeypatch.setattr(
        gamerch,
        "fetch_page_text",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应发网络请求")),
    )
    inv2 = await gamerch.build_page_inventory(FakeHttp(), max_age=24)
    assert inv2["testsongsd"] == 555
    assert calls == first_calls
    _ = json  # 保持 json 导入（其他断言经由 kv 序列化覆盖）
