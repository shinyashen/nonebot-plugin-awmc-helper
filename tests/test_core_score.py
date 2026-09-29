"""core/score 查分服务测试：B50 组装、错误映射文案、落雪 401 续期链与阶梯重试。"""

from pathlib import Path

import respx
import pytest
from maimai_py.exceptions import InvalidPlayerIdentifierError

BASE_DF = "https://www.diving-fish.com/api/maimaidxprober"


@pytest.fixture
async def db(tmp_path: Path):
    from nonebot_plugin_awmc_helper.core import store

    store.set_db_file(tmp_path / "awmc.db")
    await store.init_db()
    yield
    store.set_db_file(None)


@pytest.fixture
async def songs(db):
    from mocks import seed_service

    from nonebot_plugin_awmc_helper.core.songs import song_service

    await seed_service(song_service)
    yield
    song_service._ready.clear()


@pytest.mark.asyncio
async def test_get_b50_divingfish(db, songs):
    """水鱼 B50：respx mock query/player，经真实 maimai-py 客户端。"""
    from nonebot_plugin_awmc_helper.core.score import score_service
    from nonebot_plugin_awmc_helper.core.binding import binding_service

    binding = await binding_service.ensure("qq", "10001")
    score = {
        "song_id": 10199,  # 199 的 DX 谱
        "level": "13",
        "level_index": 3,
        "achievements": 100.5,
        "fc": "ap",
        "fs": "fsd",
        "dxScore": 2000,
        "rate": "sssp",
        "ra": 300,
    }
    player_payload = {
        "username": "tester",
        "rating": 300,
        "nickname": "tester",
        "plate": "彩将",
        "additional_rating": 1,
        "charts": {"sd": [], "dx": [score]},
    }
    with respx.mock(assert_all_called=False) as m:
        m.post(f"{BASE_DF}/query/player").respond(json=player_payload)
        player = await score_service.get_player(binding)
        bests = await score_service.get_b50(binding)

    assert player is not None  # get_player 经数据源适配层，类型上可空
    assert player.name == "tester"
    assert bests.rating == 300
    assert len(bests.scores_b15) == 1  # 199 DX 谱版本 26000 ≥ 当前版本 25500 → b15


@pytest.mark.asyncio
async def test_error_mapping_privacy(db, songs):
    """水鱼 403 → 隐私提示文案。"""
    from nonebot_plugin_awmc_helper.core.score import UserScoreError, score_service
    from nonebot_plugin_awmc_helper.core.binding import binding_service

    binding = await binding_service.ensure("qq", "10001")
    with respx.mock(assert_all_called=False) as m:
        m.post(f"{BASE_DF}/query/player").respond(
            status_code=403, json={"message": "未授权"}
        )
        with pytest.raises(UserScoreError, match="未授权"):
            await score_service.get_player(binding)


@pytest.mark.asyncio
async def test_lxns_token_auto_refresh(db, songs, monkeypatch):
    """落雪 personal token 401 → refresh_token 自动续期落库 → 重试成功。

    对齐原版 maimaiDX 的 _on_unauthorized：迁移自 Hoshino 的绑定曾因
    缺刷新流程集体 401（表现为「没有找到这个玩家」），此用例守住修复。
    """
    import httpx
    import respx

    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.config import plugin_config
    from nonebot_plugin_awmc_helper.core.score import score_service
    from nonebot_plugin_awmc_helper.core.binding import binding_service

    monkeypatch.setattr(plugin_config, "awmc_lxns_client_id", "cid")
    monkeypatch.setattr(plugin_config, "awmc_lxns_client_secret", "sec")
    monkeypatch.setattr(plugin_config, "awmc_lxns_redirect_uri", "http://localhost/cb")

    binding = await binding_service.ensure("OneBot V11", "30003")
    await binding_service.bind_lxns(
        binding, token="old-token", friend_code=979740832727161
    )
    binding.lxns_refresh_token = (
        "rt-old"  # 直绑路径无 refresh_token，OAuth 迁移绑定才有
    )
    await store.save_binding(binding)

    score_payload = {
        "id": 10199,
        "level": "13",
        "level_index": 3,
        "achievements": 100.5,
        "fc": "ap",
        "fs": "fsd",
        "dx_score": 2000,
        "dx_rating": 300,
        "rate": "sssp",
        "type": "dx",
    }
    scores_url = "https://maimai.lxns.net/api/v0/user/maimai/player/scores"
    with respx.mock(assert_all_called=False) as m:
        m.get(scores_url).side_effect = [
            httpx.Response(
                401, json={"code": 401, "success": False, "message": "Unauthorized"}
            ),
            httpx.Response(
                200,
                json={"code": 0, "success": True, "data": [score_payload]},
            ),
        ]
        m.post("https://maimai.lxns.net/api/v0/oauth/token").respond(
            json={
                "code": 0,
                "success": True,
                "data": {
                    "access_token": "new-token",
                    "refresh_token": "rt-new",
                    "friend_code": 979740832727161,
                },
            }
        )
        result = await score_service.get_scores_all(binding)

    assert len(result.scores) == 1
    # 新 token 与可能轮换的 refresh_token 均已落库 + 内存对象同步
    assert binding.lxns_token == "new-token"
    assert binding.lxns_refresh_token == "rt-new"
    got = await binding_service.get("OneBot V11", "30003")
    assert got is not None
    assert got.lxns_token == "new-token"


@pytest.mark.asyncio
async def test_lxns_refresh_failure_falls_through(db, songs, monkeypatch):
    """refresh_token 也失效（如落雪侧轮换废弃）时不再重试，正常映射报错。"""
    import respx

    from nonebot_plugin_awmc_helper.config import plugin_config
    from nonebot_plugin_awmc_helper.core.score import UserScoreError, score_service
    from nonebot_plugin_awmc_helper.core.binding import binding_service

    monkeypatch.setattr(plugin_config, "awmc_lxns_client_id", "cid")
    monkeypatch.setattr(plugin_config, "awmc_lxns_client_secret", "sec")
    monkeypatch.setattr(plugin_config, "awmc_lxns_redirect_uri", "http://localhost/cb")

    binding = await binding_service.ensure("OneBot V11", "30003")
    await binding_service.bind_lxns(binding, token="old-token", friend_code=123456)
    binding.lxns_refresh_token = "rt-dead"
    from nonebot_plugin_awmc_helper.core import store

    await store.save_binding(binding)

    scores_url = "https://maimai.lxns.net/api/v0/user/maimai/player/scores"
    with respx.mock(assert_all_called=False) as m:
        m.get(scores_url).respond(
            401, json={"code": 401, "success": False, "message": "Unauthorized"}
        )
        m.post("https://maimai.lxns.net/api/v0/oauth/token").respond(
            400, json={"code": 400, "success": False, "message": "invalid grant"}
        )
        with pytest.raises(UserScoreError):
            await score_service.get_scores_all(binding)


@pytest.mark.asyncio
async def test_lxns_refresh_dead_oauth_error_body(db, songs, monkeypatch):
    """落雪 OAuth 风格错误体（invalid_grant，无 message 字段）→ 三态 "dead"。

    查询路径对 dead 给「重新绑定落雪」文案而非「没有找到玩家」——rt 30 天
    过期死局的准确定位（Q43 ②；2026-09-28 服务器实测回归）。"""
    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.config import plugin_config
    from nonebot_plugin_awmc_helper.core.score import UserScoreError, score_service
    from nonebot_plugin_awmc_helper.core.binding import binding_service

    monkeypatch.setattr(plugin_config, "awmc_lxns_client_id", "cid")
    monkeypatch.setattr(plugin_config, "awmc_lxns_client_secret", "sec")
    monkeypatch.setattr(plugin_config, "awmc_lxns_redirect_uri", "http://localhost/cb")

    binding = await binding_service.ensure("OneBot V11", "30008")
    await binding_service.bind_lxns(binding, token="expired-token", friend_code=123)
    binding.lxns_refresh_token = "rt-dead"
    await store.save_binding(binding)

    scores_url = "https://maimai.lxns.net/api/v0/user/maimai/player/scores"
    with respx.mock(assert_all_called=False) as m:
        m.get(scores_url).respond(
            401, json={"code": 401, "success": False, "message": "unauthorized"}
        )
        m.post("https://maimai.lxns.net/api/v0/oauth/token").respond(
            400,
            json={
                "error": "invalid_grant",
                "error_description": "refresh token expired",
            },
        )
        assert await binding_service.refresh_lxns(binding) == "dead"
        binding2 = await binding_service.get("OneBot V11", "30008")
        assert binding2 is not None
        with pytest.raises(UserScoreError, match="重新「绑定落雪」"):
            await score_service.get_scores_all(binding2)


@pytest.mark.asyncio
async def test_run_retry_ladder_after_refresh(db, songs, monkeypatch):
    """续期成功后的阶梯重试（Q43）：立即 → 5s → 20s 三级；仅 401 合流异常
    继续阶梯，其余异常立即映射；skip 按原错误处理；全败给非技术兜底文案。"""
    from nonebot_plugin_awmc_helper.core import sources as sources_module
    from nonebot_plugin_awmc_helper.core.score import UserScoreError
    from nonebot_plugin_awmc_helper.core.binding import binding_service

    binding = await binding_service.ensure("OneBot V11", "30009")

    sleeps: list[float] = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    # 阶梯重试的 sleep 在 core.sources（管线随适配层下沉）
    monkeypatch.setattr(sources_module.asyncio, "sleep", fake_sleep)

    async def make(status: str, n_fail: int):
        sleeps.clear()
        calls = {"n": 0}

        async def fake_refresh(b):
            return status

        async def factory():
            calls["n"] += 1
            if calls["n"] <= n_fail:
                raise InvalidPlayerIdentifierError("unauthorized")
            return "ok"

        monkeypatch.setattr(binding_service, "refresh_lxns", fake_refresh)
        return factory, calls

    async def make_other_error(n_fail: int):
        sleeps.clear()
        calls = {"n": 0}

        async def fake_refresh(b):
            return "refreshed"

        async def factory():
            calls["n"] += 1
            if calls["n"] == 1:
                raise InvalidPlayerIdentifierError("unauthorized")
            if calls["n"] <= 1 + n_fail:
                from maimai_py import PrivacyLimitationError

                raise PrivacyLimitationError("private")
            return "ok"

        monkeypatch.setattr(binding_service, "refresh_lxns", fake_refresh)
        return factory, calls

    # 立即重试成功（窗口 ≈ 0 的常态）
    factory, calls = await make("refreshed", 1)
    assert await sources_module._run(binding, factory) == "ok"
    assert calls["n"] == 2
    assert sleeps == []

    # 5s 后成功
    factory, calls = await make("refreshed", 2)
    assert await sources_module._run(binding, factory) == "ok"
    assert calls["n"] == 3
    assert sleeps == [5]

    # 10s 后成功；进入 10s 档时慢查询提示触发一次
    notices = []

    async def notify_slow():
        notices.append(1)

    factory, calls = await make("refreshed", 3)
    assert await sources_module._run(binding, factory, notify_slow) == "ok"
    assert calls["n"] == 4
    assert sleeps == [5, 10]
    assert len(notices) == 1

    # 全败：非技术兜底文案（不暴露令牌/续期细节）
    factory, calls = await make("refreshed", 99)
    with pytest.raises(UserScoreError, match="暂时无法访问"):
        await sources_module._run(binding, factory, notify_slow)
    assert calls["n"] == 4
    assert sleeps == [5, 10]
    assert len(notices) == 2

    # dead：重绑文案
    factory, calls = await make("dead", 99)
    with pytest.raises(UserScoreError, match="重新「绑定落雪」"):
        await sources_module._run(binding, factory)
    assert calls["n"] == 1
    assert sleeps == []

    # skip（无凭据/网络）：按原错误映射
    factory, calls = await make("skip", 99)
    with pytest.raises(UserScoreError, match="没有找到这个玩家"):
        await sources_module._run(binding, factory)
    assert calls["n"] == 1
    assert sleeps == []

    # 续期后遇到非 401 异常：立即映射，不进阶梯
    factory, calls = await make_other_error(1)
    with pytest.raises(UserScoreError, match="未授权第三方查询"):
        await sources_module._run(binding, factory)
    assert calls["n"] == 2
    assert sleeps == []


@pytest.mark.asyncio
async def test_b50_net_error_mapped_to_user_message(db, songs, monkeypatch):
    """NET 抓取层 NetError 经映射表转 UserScoreError 专项文案（L-1）。

    net_score_service.get_scores 抓取失败原样透传 NetError；score 层若只捕
    NetScoreError，专项文案（「SEGA ID 或密码错误」「定期维护中」等）永远
    落不到用户面前。
    """
    from maimai_py import SongType

    from nonebot_plugin_awmc_helper.core.score import UserScoreError, score_service
    from nonebot_plugin_awmc_helper.core.binding import (
        SERVICE_NET,
        binding_service,
    )
    from nonebot_plugin_awmc_helper.core.ext.net import NET_ERROR_MESSAGES, NetError
    from nonebot_plugin_awmc_helper.core.net_score import net_score_service

    binding = await binding_service.ensure("qq", "10002")
    await binding_service.bind_net(binding, sega_id="sid", password="pw")
    assert binding.service == SERVICE_NET

    # NET 适配器委托 net_score_service 单例：patch 单例方法即全链路生效
    async def raise_invalid_credentials(_binding):
        raise NetError("invalid_credentials")

    monkeypatch.setattr(net_score_service, "get_b50", raise_invalid_credentials)
    with pytest.raises(UserScoreError, match="SEGA ID 或密码错误"):
        await score_service.get_b50(binding)
    # 文案与映射表一致（未知 code 回落 str(e)）
    assert NET_ERROR_MESSAGES["invalid_credentials"] == "SEGA ID 或密码错误，请重新绑定"

    async def raise_maintenance(_binding):
        raise NetError("maintenance")

    monkeypatch.setattr(net_score_service, "get_b50", raise_maintenance)
    with pytest.raises(UserScoreError, match="定期维护中"):
        await score_service.get_b50(binding)

    # minfo NET 路径同样映射（门面 get_minfo 按 service 路由到 NET 适配器）
    async def raise_minfo(_binding, _song):
        raise NetError("maintenance")

    monkeypatch.setattr(net_score_service, "get_minfo_scores", raise_minfo)
    from mocks import make_diff, make_song

    song = make_song(199, "测试曲", diffs=[make_diff(type=SongType.STANDARD)])
    with pytest.raises(UserScoreError, match="定期维护中"):
        await score_service.get_minfo(song, binding)
