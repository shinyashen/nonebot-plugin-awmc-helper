"""core/songs 国服轮询与触发链路：双源判定、幂等、触发动作、通知开关。"""

import httpx
import respx
import pytest
from songdb_fixtures import make_lxns, make_divingfish

LXNS_BASE = "https://maimai.lxns.net"
DF_URL = "https://www.diving-fish.com/api/maimaidxprober/music_data"


@pytest.fixture
def cn_mock():
    with respx.mock(assert_all_called=False) as mock:
        mock.get(
            f"{LXNS_BASE}/api/v0/maimai/song/list", params={"notes": "false"}
        ).mock(return_value=httpx.Response(200, json=make_lxns()))
        mock.get(DF_URL).mock(return_value=httpx.Response(200, json=make_divingfish()))
        yield mock


@pytest.fixture
async def db(tmp_path):
    from nonebot_plugin_awmc_helper.core import store

    store.set_db_file(tmp_path / "awmc.db")
    await store.init_db()
    yield store
    store.set_db_file(None)


@pytest.fixture
def no_templates(monkeypatch):
    """测试环境禁用底图兜底/自动预渲染（避免真实 PIL 渲染）。"""
    from nonebot_plugin_awmc_helper.core import songs as songs_mod
    from nonebot_plugin_awmc_helper.config import plugin_config

    calls = []
    monkeypatch.setattr(plugin_config, "awmc_auto_templates", False)

    async def fake_ensure():
        calls.append("ensure")

    monkeypatch.setattr(songs_mod, "_ensure_templates", fake_ensure)
    return calls


def _update_mock(cn_mock, *, with_new: bool = False, drop: set[int] | None = None):
    cn_mock.get(f"{LXNS_BASE}/api/v0/maimai/song/list", params={"notes": "false"}).mock(
        return_value=httpx.Response(
            200, json=make_lxns(with_new=with_new, drop_ids=drop)
        )
    )
    cn_mock.get(DF_URL).mock(
        return_value=httpx.Response(
            200, json=make_divingfish(with_new=with_new, drop_ids=drop)
        )
    )


@pytest.mark.asyncio
async def test_poll_baseline_and_idempotent(db, cn_mock, no_templates, monkeypatch):
    """首启仅建基线不触发；无变化不触发；kv 状态持久（重启不重复触发）。"""
    from nonebot_plugin_awmc_helper.core import songs as songs_mod
    from nonebot_plugin_awmc_helper.core import store

    triggered = []
    monkeypatch.setattr(songs_mod, "_on_cn_update", lambda *a: triggered.append(a))

    await songs_mod._hourly_cn_poll()  # 首启：基线
    state = await store.kv_get(songs_mod.CN_POLL_STATE_KEY)
    assert state is not None
    assert state["known"]
    assert triggered == []
    assert no_templates == ["ensure"]  # 首启兜底预渲染检查

    await songs_mod._hourly_cn_poll()  # 无变化
    assert triggered == []

    _update_mock(cn_mock)  # 与基线相同的载荷
    await songs_mod._hourly_cn_poll()
    assert triggered == []


@pytest.mark.asyncio
async def test_poll_triggers_on_both_sources(db, cn_mock, no_templates, monkeypatch):
    """双源新增交集 → 触发，携带新增 id 与标题。"""
    from nonebot_plugin_awmc_helper.core import songs as songs_mod

    triggered = []

    async def fake_on_update(a, r, t):
        triggered.append((a, r, t))

    monkeypatch.setattr(songs_mod, "_on_cn_update", fake_on_update)

    await songs_mod._hourly_cn_poll()  # 基线
    _update_mock(cn_mock, with_new=True)
    await songs_mod._hourly_cn_poll()  # ハム太郎 双源新增（真实宴轮换新曲）
    assert len(triggered) == 1
    added, removed, titles = triggered[0]
    assert added == {1301}
    assert removed == set()
    assert titles[1301] == "華の集落、秋のお届け"

    # 下一轮不再重复触发（kv 已更新）
    await songs_mod._hourly_cn_poll()
    assert len(triggered) == 1


@pytest.mark.asyncio
async def test_poll_single_source_new_not_triggered(
    db, cn_mock, no_templates, monkeypatch
):
    from nonebot_plugin_awmc_helper.core import songs as songs_mod

    triggered = []
    monkeypatch.setattr(songs_mod, "_on_cn_update", lambda *a: triggered.append(a))

    await songs_mod._hourly_cn_poll()
    # 仅落雪新增（水鱼无）
    cn_mock.get(f"{LXNS_BASE}/api/v0/maimai/song/list", params={"notes": "false"}).mock(
        return_value=httpx.Response(200, json=make_lxns(with_new=True))
    )
    await songs_mod._hourly_cn_poll()
    assert triggered == []


@pytest.mark.asyncio
async def test_poll_network_failure_skipped(db, cn_mock, no_templates, monkeypatch):
    """单源网络失败：跳过本轮检测、不误清基线。"""
    from nonebot_plugin_awmc_helper.core import songs as songs_mod
    from nonebot_plugin_awmc_helper.core import store

    triggered = []
    monkeypatch.setattr(songs_mod, "_on_cn_update", lambda *a: triggered.append(a))

    await songs_mod._hourly_cn_poll()
    kv_before = await store.kv_get(songs_mod.CN_POLL_STATE_KEY)
    assert kv_before is not None
    before = kv_before["known"]
    cn_mock.get(f"{LXNS_BASE}/api/v0/maimai/song/list", params={"notes": "false"}).mock(
        return_value=httpx.Response(500)
    )
    await songs_mod._hourly_cn_poll()
    assert triggered == []
    kv_after = await store.kv_get(songs_mod.CN_POLL_STATE_KEY)
    assert kv_after is not None
    assert kv_after["known"] == before  # 基线未动


@pytest.mark.asyncio
async def test_cn_update_actions_and_notify(db, monkeypatch):
    """触发动作串行：回填→刷运行时→预渲染；预渲染失败不阻断；通知可关。"""
    from nonebot_plugin_awmc_helper.core import songs as songs_mod
    from nonebot_plugin_awmc_helper.core import songdb
    from nonebot_plugin_awmc_helper.config import plugin_config
    from nonebot_plugin_awmc_helper.core.songs import song_service

    calls = {"refresh_all": [], "runtime": 0, "templates": 0, "notify": []}

    async def fake_refresh_all(**kw):
        calls["refresh_all"].append(kw)
        return {"songs": 10, "warnings": ["w1"]}

    async def fake_runtime_refresh():
        calls["runtime"] += 1
        return True

    async def fake_prerender():
        calls["templates"] += 1
        return "ok"

    async def fake_notify(text):
        calls["notify"].append(text)

    monkeypatch.setattr(songdb, "refresh_all", fake_refresh_all)
    monkeypatch.setattr(song_service, "refresh", fake_runtime_refresh)
    monkeypatch.setattr(songs_mod, "prerender_templates", fake_prerender)
    from nonebot_plugin_awmc_helper.core import utils as core_utils

    monkeypatch.setattr(core_utils, "notify_superusers", fake_notify)
    monkeypatch.setattr(plugin_config, "awmc_update_notify", True)

    await songs_mod._on_cn_update({1301}, set(), {1301: "華の集落、秋のお届け"})
    assert calls["refresh_all"] == [{"include_cn": True, "include_jp": False}]
    assert calls["runtime"] == 1
    assert calls["templates"] == 1
    assert len(calls["notify"]) == 1
    assert "華の集落、秋のお届け" in calls["notify"][0]

    # 预渲染失败不阻断（通知仍发出，注明保留旧底图）
    calls["notify"].clear()

    async def boom():
        raise RuntimeError("render down")

    monkeypatch.setattr(songs_mod, "prerender_templates", boom)
    await songs_mod._on_cn_update(set(), {9002}, {9002: "（构造）国服限定样例"})
    assert calls["runtime"] == 2
    assert len(calls["notify"]) == 1
    assert "（构造）国服限定样例" in calls["notify"][0]

    # 通知开关关闭
    monkeypatch.setattr(plugin_config, "awmc_update_notify", False)
    calls["notify"].clear()
    await songs_mod._on_cn_update({1301}, set(), {1301: "華の集落、秋のお届け"})
    assert calls["notify"] == []


@pytest.mark.asyncio
async def test_daily_songdb_pipeline(db, monkeypatch):
    """每日全量：JP+CN 全量重建、外部源变化触发预渲染、异常不抛出。"""
    from nonebot_plugin_awmc_helper.core import songs as songs_mod
    from nonebot_plugin_awmc_helper.core import songdb
    from nonebot_plugin_awmc_helper.config import plugin_config
    from nonebot_plugin_awmc_helper.core.songs import song_service

    # boom 分支的 runtime 刷新必须 fake：真实 load() 会对本用例的半空 DB 走一次
    # maimai_py 缓存写入（ids=[] + provider 指纹哈希），污染同 worker 后续用相同
    # fixture（指纹相同）的用例——client.songs() 命中缓存跳过重建读到空 ids；
    # 且别名拉取未 mock，是一次真实网络请求
    async def fake_runtime_refresh():
        return True

    monkeypatch.setattr(song_service, "refresh", fake_runtime_refresh)

    calls = {"refresh": [], "templates": 0}

    async def fake_refresh_all(**kw):
        calls["refresh"].append(kw)
        # 模拟 refresh_all 的 extra 应用结果：配置了外部源且内容有变化时触发底图重建
        changed = bool(plugin_config.awmc_extra_song_sources)
        return {
            "songs": 10,
            "groups": 10,
            "charts": 20,
            "level_points": 30,
            "removed": 0,
            "warnings": [],
            "cn_current_version": 25500,
            "extra": {
                "sources": len(plugin_config.awmc_extra_song_sources),
                "applied": 1 if changed else 0,
                "changed": changed,
            },
        }

    async def fake_prerender():
        calls["templates"] += 1
        return "ok"

    async def fake_ensure():
        pass

    monkeypatch.setattr(songdb, "refresh_all", fake_refresh_all)
    monkeypatch.setattr(songs_mod, "prerender_templates", fake_prerender)
    monkeypatch.setattr(songs_mod, "_ensure_templates", fake_ensure)
    monkeypatch.setattr(plugin_config, "awmc_extra_song_sources", [])
    await songs_mod._daily_songdb()
    assert calls["refresh"] == [{"include_cn": True, "include_jp": True}]
    assert calls["templates"] == 0

    # 外部源变化 → 重建底图
    import json
    from pathlib import Path

    extra = Path(db.db_file()).parent / "extra.json"
    extra.write_text(json.dumps({"8": {"sheets": {}}}), encoding="utf-8")
    monkeypatch.setattr(plugin_config, "awmc_extra_song_sources", [str(extra)])
    await songs_mod._daily_songdb()
    assert calls["templates"] == 1

    # 全量失败不抛出（下一轮自愈）
    async def boom(**kw):
        raise RuntimeError("network down")

    monkeypatch.setattr(songdb, "refresh_all", boom)
    await songs_mod._daily_songdb()  # 不抛


@pytest.mark.asyncio
async def test_poll_utage_rotation_not_triggered(
    db, cn_mock, no_templates, monkeypatch
):
    """宴轮换不构成更新事件：检测键排除宴（底图不含宴谱，轮换频繁）。

    用真实双源宴轮换新曲 [回]ハム太郎とっとこうた（lxns 111113 / 水鱼同 id）。
    """
    from nonebot_plugin_awmc_helper.core import songs as songs_mod

    triggered = []

    async def fake_on_update(a, r, t):
        triggered.append((a, r, t))

    monkeypatch.setattr(songs_mod, "_on_cn_update", fake_on_update)

    await songs_mod._hourly_cn_poll()  # 基线
    # 双源同时新增一张宴谱（轮换上架）
    cn_mock.get(f"{LXNS_BASE}/api/v0/maimai/song/list", params={"notes": "false"}).mock(
        return_value=httpx.Response(200, json=make_lxns(with_utage_new=True))
    )
    cn_mock.get(DF_URL).mock(
        return_value=httpx.Response(200, json=make_divingfish(with_utage_new=True))
    )
    await songs_mod._hourly_cn_poll()
    assert triggered == []
