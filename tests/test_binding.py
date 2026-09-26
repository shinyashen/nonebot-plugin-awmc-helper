"""core/binding + core/score：绑定与查分服务测试。"""

from pathlib import Path

import respx
import pytest
from mocks import requires_assets
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
async def test_binding_ensure_and_identifier(db):
    from maimai_py import PlayerIdentifier

    from nonebot_plugin_awmc_helper.core.binding import (
        SERVICE_LXNS,
        SERVICE_DIVINGFISH,
        BindingError,
        binding_service,
    )

    binding = await binding_service.ensure("qq", "10001")
    assert binding.service == SERVICE_DIVINGFISH  # 部署默认
    # 空 QQ 绑定 → qq 公开查询凭据可用
    ident = binding_service.identifier(binding)
    assert ident.qq == 10001

    # 非 QQ 平台且无凭据 → 不可查
    binding2 = await binding_service.ensure("discord", "20002")
    with pytest.raises(BindingError):
        binding_service.identifier(binding2)

    # 水鱼用户名绑定
    await binding_service.bind_divingfish_username(binding, "tester")
    got = await binding_service.get("qq", "10001")
    assert got is not None
    assert got.divingfish_username == "tester"

    # 落雪切换：无凭据拒绝
    from nonebot_plugin_awmc_helper.core.binding import BindingError as BE

    with pytest.raises(BE):
        await binding_service.set_service(binding, SERVICE_LXNS)
    await binding_service.bind_lxns(binding, token="jwt-token", friend_code=123456)
    await binding_service.set_service(binding, SERVICE_LXNS)
    got = await binding_service.get("qq", "10001")
    assert got is not None
    assert got.service == SERVICE_LXNS
    ident = binding_service.identifier(got)
    assert isinstance(ident, PlayerIdentifier)
    assert ident.credentials == "jwt-token"
    assert ident.friend_code == 123456

    # 主题与解绑
    await binding_service.set_theme(got, "circle")
    got_after = await binding_service.get("qq", "10001")
    assert got_after is not None
    assert got_after.theme == "circle"
    assert await binding_service.unbind("qq", "10001")
    assert await binding_service.get("qq", "10001") is None


@pytest.mark.asyncio
async def test_identifier_qq_semantics(db):
    """OneBot v11 运行时键为适配器标识 "OneBot V11"（uninfo 不填 platform，
    adapter 名即平台语义，session_keys 统一产出）：user_id 应照常按 QQ 号装配
    水鱼凭据，与 Hoshino 迁移数据（历史键 "qq"）同等对待；真正的非 QQ 平台
    仍不可查。"""
    from nonebot_plugin_uninfo import User, Scene, Session, SceneType

    from nonebot_plugin_awmc_helper.core.binding import (
        QQ_PLATFORMS,
        BindingError,
        session_keys,
        binding_service,
    )

    assert QQ_PLATFORMS == {"qq", "OneBot V11"}
    session = Session(
        self_id="test",
        adapter="OneBot V11",
        scope="qq_client",
        scene=Scene(id="30003", type=SceneType.PRIVATE),
        user=User(id="30003"),
        member=None,
        operator=None,  # OneBot v11 实际不填 platform
    )
    platform, user_id = session_keys(session)
    assert (platform, user_id) == ("OneBot V11", "30003")

    binding = await binding_service.ensure(platform, user_id)
    ident = binding_service.identifier(binding)
    assert ident.qq == 30003

    other = await binding_service.ensure("telegram", "40004")
    with pytest.raises(BindingError):
        binding_service.identifier(other)


@pytest.mark.asyncio
async def test_pending_bindings():
    from nonebot_plugin_awmc_helper.core.binding import pending_bindings

    pending_bindings.start("qq", "1", "lxns", ttl=60)
    assert pending_bindings.is_active("qq", "1", "lxns")
    assert not pending_bindings.is_active("qq", "2")

    # 过期
    pending_bindings.start("qq", "1", "lxns", ttl=-1)
    assert not pending_bindings.is_active("qq", "1")


@pytest.mark.asyncio
async def test_get_b50_divingfish(db, songs):
    """水鱼 B50：respx mock query/player，经真实 maimai-py 客户端。"""
    from nonebot_plugin_awmc_helper.core.score import score_service
    from nonebot_plugin_awmc_helper.core.binding import binding_service

    binding = await binding_service.ensure("qq", "10001")
    score = {
        "song_id": 10231,  # 231 的 DX 谱
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

    assert player.name == "tester"
    assert bests.rating == 300
    assert len(bests.scores_b35) == 1  # 版本 25000 < 当前版本 → b35
    s = bests.scores_b35[0]
    assert s.title == "PENGUIN"
    assert s.dx_rating == 300


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


@requires_assets
@pytest.mark.asyncio
async def test_b50_render_smoke(db, songs):
    """B50 与成绩列表绘图冒烟。"""
    import dataclasses

    from maimai_py import Score, RateType, SongType, LevelIndex, ScoreExtend

    from nonebot_plugin_awmc_helper.core.render.score import DrawScore
    from nonebot_plugin_awmc_helper.core.render.best50 import best50_bytes

    base = Score(
        id=1,
        level="13",
        level_index=LevelIndex.MASTER,
        achievements=99.5,
        fc=None,
        fs=None,
        dx_score=2000,
        dx_rating=250,
        play_count=None,
        play_time=None,
        rate=RateType.SSP,
        type=SongType.DX,
    )
    sc = ScoreExtend(
        **dataclasses.asdict(base),
        title="RenderSong",
        level_value=13.0,
        level_dx_score=2400,
        dx_star=4,
        version=25000,
    )
    png = await best50_bytes("tester", 250, 250, 0, [sc], [])
    assert png.startswith(b"\x89PNG")
    assert len(png) > 1000
    listing = DrawScore(280 + 4 * 109 + 130, service=None).draw_score_list(
        "13", [sc], 1, 1
    )
    assert listing.startswith(b"\x89PNG")


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
        "id": 10231,
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
async def test_lxns_refresh_concurrent_single_call(db, songs, monkeypatch):
    """并发续期对拍：同用户两协程同时 401 → 落雪 refresh 只调一次，
    后到者经锁内重读采用新凭据直接返回（不重放旧 rt）。"""
    import asyncio

    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.config import plugin_config
    from nonebot_plugin_awmc_helper.core.ext import lxns as lxns_ext
    from nonebot_plugin_awmc_helper.core.binding import binding_service

    monkeypatch.setattr(plugin_config, "awmc_lxns_client_id", "cid")
    monkeypatch.setattr(plugin_config, "awmc_lxns_client_secret", "sec")
    monkeypatch.setattr(plugin_config, "awmc_lxns_redirect_uri", "http://localhost/cb")

    binding = await binding_service.ensure("OneBot V11", "30007")
    await binding_service.bind_lxns(binding, token="old-token", friend_code=123)
    binding.lxns_refresh_token = "rt-old"
    await store.save_binding(binding)

    calls = []

    async def fake_refresh(rt):
        calls.append(rt)
        await asyncio.sleep(0.05)  # 制造并发窗口

        class _T:
            access_token = "new-token"
            refresh_token = "rt-new"
            friend_code = 123

        return _T()

    monkeypatch.setattr(lxns_ext, "refresh_token", fake_refresh)

    b1 = await binding_service.get("OneBot V11", "30007")
    b2 = await binding_service.get("OneBot V11", "30007")
    exc = InvalidPlayerIdentifierError("401")
    results = await asyncio.gather(
        binding_service.refresh_lxns_if_expired(b1, exc),
        binding_service.refresh_lxns_if_expired(b2, exc),
    )
    assert results == [True, True]
    assert calls == ["rt-old"]  # 落雪只被消耗一次
    assert b1.lxns_token == "new-token"
    assert b2.lxns_token == "new-token"  # 后到者经锁内重读采用新凭据
