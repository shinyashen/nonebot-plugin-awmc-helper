"""MuNET 别名全量走查 + store 助手 + provider 第四源合并。"""

import pytest


@pytest.fixture
async def db(tmp_path):
    from nonebot_plugin_awmc_helper.core import store

    store.set_db_file(tmp_path / "awmc.db")
    await store.init_db()
    yield store
    store.set_db_file(None)


async def _seed(db):
    """规范表三形状：SD+DX 双组（100）/ 仅 DX（1449）/ 仅宴（60）。"""
    async with db.session() as s:
        s.add(db.SongRow(id=100, title="Tell Your World", artist="livetune"))
        s.add(db.SongRow(id=1449, title="パズルリボン", artist="すりぃ"))
        s.add(db.SongRow(id=60, title="宴のみ", artist="x"))
        for sid, kind in ((100, "sd"), (100, "dx"), (1449, "dx"), (60, "utage")):
            s.add(db.SongSheetGroup(song_id=sid, kind=kind))
        await s.commit()


def _entry(music_id: int, name: str, aliases: list[str]) -> dict:
    return {
        "id": music_id,
        "name": name,
        "artist": "x",
        "addVersion": 13,
        "aliases": [{"alias": a} for a in aliases],
        "charts": [
            {
                "difficulty": 0,
                "kind": 0,
                "utageId": 0,
                "tapCount": 1,
                "holdCount": 0,
                "slideCount": 0,
                "touchCount": 0,
                "breakCount": 0,
            }
        ],
    }


async def test_list_alias_walk_targets(db):
    await _seed(db)
    from nonebot_plugin_awmc_helper.core import store

    assert await store.list_alias_walk_targets() == [60, 100, 1449, 10100]


async def test_walk_full_and_root_folding(db, monkeypatch):
    from nonebot_plugin_awmc_helper.config import plugin_config

    monkeypatch.setattr(plugin_config, "awmc_munet_alias_days", 7)
    await _seed(db)
    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.core.ext import munet

    payloads = {
        60: _entry(60, "宴のみ", ["Party Time"]),
        100: _entry(100, "Tell Your World", ["TYW"]),
        1449: _entry(1449, "パズルリボン", ["拼图丝带"]),
        10100: _entry(10100, "Tell Your World", ["TYW DX"]),
    }

    async def fake_fetch(music_id):
        return payloads.get(music_id)

    monkeypatch.setattr(munet, "_MIN_INTERVAL", 0)
    monkeypatch.setattr(munet, "fetch_music_by_id", fake_fetch)
    result = await munet.refresh_aliases_full()
    assert result["status"] == "done"
    snapshot = await store.load_song_aliases(["munet"])
    # DX 组条目（10100）别名折叠到根 id 100
    assert set(snapshot[100]) == {"TYW", "TYW DX"}
    assert snapshot[1449] == ["拼图丝带"]
    state = await store.kv_get("munet_alias_walk")
    assert state is not None  # 走查完成必落状态（basedpyright 收窄）
    assert state["cursor"] is None
    assert state["finished_at"]


async def test_walk_resume_from_cursor(db, monkeypatch):
    from nonebot_plugin_awmc_helper.config import plugin_config

    monkeypatch.setattr(plugin_config, "awmc_munet_alias_days", 7)
    await _seed(db)
    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.core.ext import munet

    payloads = {
        60: _entry(60, "宴のみ", ["Party Time"]),
        100: _entry(100, "Tell Your World", ["TYW"]),
        1449: _entry(1449, "パズルリボン", ["拼图丝带"]),
        10100: _entry(10100, "Tell Your World", ["TYW DX"]),
    }

    async def fake_fetch(music_id):
        return payloads.get(music_id)

    monkeypatch.setattr(munet, "_MIN_INTERVAL", 0)
    monkeypatch.setattr(munet, "fetch_music_by_id", fake_fetch)
    partial = await munet.refresh_aliases_full(budget_seconds=0)
    assert partial["status"] == "partial"
    assert partial["cursor"] == 0  # 预算 0：一条未拉即断
    done = await munet.refresh_aliases_full()
    assert done["status"] == "done"
    snapshot = await store.load_song_aliases(["munet"])
    assert snapshot[60] == ["Party Time"]


async def test_walk_disabled(db, monkeypatch):
    from nonebot_plugin_awmc_helper.config import plugin_config
    from nonebot_plugin_awmc_helper.core.ext import munet

    monkeypatch.setattr(plugin_config, "awmc_munet_alias_days", 0)
    assert await munet.refresh_aliases_full() == {"status": "disabled"}


async def test_walk_fresh_within_interval(db, monkeypatch):
    from nonebot_plugin_awmc_helper.config import plugin_config

    monkeypatch.setattr(plugin_config, "awmc_munet_alias_days", 7)
    await _seed(db)
    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.core.ext import munet

    calls = {"n": 0}

    async def counting_fetch(music_id):
        calls["n"] += 1
        return None

    monkeypatch.setattr(munet, "_MIN_INTERVAL", 0)
    monkeypatch.setattr(munet, "fetch_music_by_id", counting_fetch)
    first = await munet.refresh_aliases_full()
    assert first["status"] == "done"
    second = await munet.refresh_aliases_full()
    assert second["status"] == "fresh"
    assert calls["n"] == len(await store.list_alias_walk_targets())


async def test_provider_merges_munet_snapshot(db):
    await _seed(db)
    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.core.provider import AwmcAliasProvider

    await store.save_song_aliases("munet", {100: ["TYW"], 1449: ["拼图丝带"]})
    provider = AwmcAliasProvider(None, None)  # type: ignore[arg-type]  # 柚子/落雪拉取失败走空快照回退
    merged = await provider.get_aliases(client=None)  # type: ignore[arg-type]
    assert merged[100] == ["TYW"]
    assert merged[1449] == ["拼图丝带"]
    assert provider._hash() != "empty"  # 内容驱动哈希


class _FakeClock:
    """受控时钟：monotonic/time 同源，随抓取推进（模拟预算耗尽）。"""

    def __init__(self):
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def time(self) -> float:
        return self.now


@pytest.mark.asyncio
async def test_walk_multi_night_preserves_earlier_nights(db, monkeypatch):
    """多晚走查：预算耗尽时本轮成果先增量落库（L-15），断点续走完成后
    不裁剪此前各晚成果；目标集外陈旧行仍在完成时清理。"""
    from nonebot_plugin_awmc_helper.config import plugin_config

    monkeypatch.setattr(plugin_config, "awmc_munet_alias_days", 7)
    await _seed(db)
    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.core.ext import munet

    payloads = {
        60: _entry(60, "宴のみ", ["Party Time"]),
        100: _entry(100, "Tell Your World", ["TYW"]),
        1449: _entry(1449, "パズルリボン", ["拼图丝带"]),
        10100: _entry(10100, "Tell Your World", ["TYW DX"]),
    }
    clock = _FakeClock()

    async def fake_fetch(music_id):
        clock.now += 10.0  # 每条消耗 10s 预算
        return payloads.get(music_id)

    monkeypatch.setattr(munet, "_MIN_INTERVAL", 0)
    monkeypatch.setattr(munet, "time", clock)
    monkeypatch.setattr(munet, "fetch_music_by_id", fake_fetch)

    # 第一晚：预算 15s → 走完 60、100 两条即断，成果已落库
    partial = await munet.refresh_aliases_full(budget_seconds=15)
    assert partial["status"] == "partial"
    assert partial["cursor"] == 2
    snap = await store.load_song_aliases(["munet"])
    assert snap[60] == ["Party Time"]
    assert snap[100] == ["TYW"]

    # 第二晚：从断点走完（force 跳过间隔检查）
    done = await munet.refresh_aliases_full(budget_seconds=3600, force=True)
    assert done["status"] == "done"
    snap2 = await store.load_song_aliases(["munet"])
    # 前晚成果不被完成路径裁剪；DX 组（10100）折叠根 id 100
    assert snap2[60] == ["Party Time"]
    assert snap2[100] == ["TYW", "TYW DX"]
    assert snap2[1449] == ["拼图丝带"]

    # 目标集外陈旧行清理：手工种一行不在本轮目标根集的别名
    async with store.session() as s:
        s.add(store.SongAlias(source="munet", song_id=999999, alias="已消失曲别名"))
        await s.commit()
    await munet.refresh_aliases_full(budget_seconds=3600, force=True)
    snap3 = await store.load_song_aliases(["munet"])
    assert 999999 not in snap3
    assert snap3[100] == ["TYW", "TYW DX"]  # 目标集内数据不受清理影响
