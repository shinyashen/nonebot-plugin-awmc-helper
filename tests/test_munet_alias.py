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
    provider = AwmcAliasProvider(None, None)  # 柚子/落雪拉取失败走空快照回退
    merged = await provider.get_aliases(client=None)
    assert merged[100] == ["TYW"]
    assert merged[1449] == ["拼图丝带"]
    assert provider._hash() != "empty"  # 内容驱动哈希
