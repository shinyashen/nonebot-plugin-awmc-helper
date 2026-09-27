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
    后到者经锁内重读采用新凭据直接返回（不重放旧 rt）。
    回归保护：落库必须在锁内完成——先释放锁再落库时，save 的 commit 与
    后到者的重读走不同池化连接，全量压测下本测试偶发抓到双次续期。"""
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
    assert b1 is not None
    assert b2 is not None
    exc = InvalidPlayerIdentifierError("401")
    results = await asyncio.gather(
        binding_service.refresh_lxns_if_expired(b1, exc),
        binding_service.refresh_lxns_if_expired(b2, exc),
    )
    assert results == [True, True]
    assert calls == ["rt-old"]  # 落雪只被消耗一次
    assert b1.lxns_token == "new-token"
    assert b2.lxns_token == "new-token"  # 后到者经锁内重读采用新凭据


@pytest.mark.asyncio
async def test_divingfish_identifier_shapes(db):
    """水鱼凭据装配形状（2026-09-26 at 代查定案）：

    - 公开键（identifier，bests/players/minfo）：绑定用户名优先且不带 qq——
      maimai_py 的 ``_as_diving_fish`` 中 qq 优先，同时携带会在
      「聊天 QQ ≠ 水鱼账号 QQ」时查错账号；
    - 全量键（full_identifier，scores/plates）：Import-Token 优先且不带
      username——maimai_py 把 username+credentials 当「账号+密码」登录水鱼；
    - 仅 Import-Token：公开键缺失（非 QQ 平台给专项文案），全量键
      credentials-only（对齐 score-updater 的用法），has_usable 仍为可用。
    """
    from nonebot_plugin_awmc_helper.core.store import UserBinding
    from nonebot_plugin_awmc_helper.core.binding import (
        SERVICE_DIVINGFISH,
        BindingError,
        binding_service,
    )

    # 用户名绑定：公开键 = 用户名（不带 qq）
    b = UserBinding(
        platform="OneBot V11",
        user_id="10001",
        service=SERVICE_DIVINGFISH,
        divingfish_username="fish",
    )
    ident = binding_service.identifier(b)
    assert ident.username == "fish"
    assert ident.qq is None
    assert ident.credentials is None

    # 用户名 + Import-Token 并存：公开键仍为用户名；全量键走 Import-Token
    b.divingfish_import_token = "tok"
    ident_full = binding_service.full_identifier(b)
    assert ident_full.credentials == "tok"
    assert ident_full.username is None

    # 仅 Import-Token（非 QQ 平台）：公开键缺失 → 专项文案；全量可查
    b2 = UserBinding(
        platform="telegram",
        user_id="u1",
        service=SERVICE_DIVINGFISH,
        divingfish_import_token="tok",
    )
    with pytest.raises(BindingError, match="用户名或 QQ"):
        binding_service.identifier(b2)
    full = binding_service.full_identifier(b2)
    assert full.credentials == "tok"
    assert full.qq is None
    assert binding_service.has_usable_credentials(b2)

    # 无任何凭据（QQ 平台）：公开键 = QQ 兜底
    b3 = UserBinding(platform="OneBot V11", user_id="10002", service=SERVICE_DIVINGFISH)
    assert binding_service.identifier(b3).qq == 10002


@pytest.mark.asyncio
async def test_resolve_query_at_chain(db):
    """代查目标解析链：无 at → ensure 发送者；有 at → 目标行只读 →
    QQ 平台水鱼临时绑定（不落库）→ 非 QQ 平台 None（调用方降级）。"""
    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.core.binding import (
        SERVICE_DIVINGFISH,
        binding_service,
    )

    # 无 at：发送者 ensure（落库，对齐 auto_create）
    b = await binding_service.resolve_query("OneBot V11", "10001", None)
    assert b is not None
    assert b.user_id == "10001"
    assert await store.get_binding("OneBot V11", "10001") is not None

    # at 有行：只读复用目标行，不新建
    await binding_service.bind_divingfish_username(b, "fishuser")
    b2 = await binding_service.resolve_query("OneBot V11", "10002", "10001")
    assert b2 is not None
    assert b2.divingfish_username == "fishuser"
    assert await store.get_binding("OneBot V11", "10002") is None  # 发送者未落库

    # at 无行（QQ 平台）：临时水鱼绑定，查询后不落库
    b3 = await binding_service.resolve_query("OneBot V11", "10002", "10003")
    assert b3 is not None
    assert b3.service == SERVICE_DIVINGFISH
    assert b3.user_id == "10003"
    assert await store.get_binding("OneBot V11", "10003") is None

    # at 无行（非 QQ 平台）：None → 由调用方给出「无法代查」降级
    assert await binding_service.resolve_query("telegram", "u1", "u2") is None


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
    from nonebot_plugin_awmc_helper.core import score as score_module
    from nonebot_plugin_awmc_helper.core.score import UserScoreError, score_service
    from nonebot_plugin_awmc_helper.core.binding import binding_service

    binding = await binding_service.ensure("OneBot V11", "30009")

    sleeps: list[float] = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(score_module.asyncio, "sleep", fake_sleep)

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
    assert await score_service._run(binding, factory) == "ok"
    assert calls["n"] == 2
    assert sleeps == []

    # 5s 后成功
    factory, calls = await make("refreshed", 2)
    assert await score_service._run(binding, factory) == "ok"
    assert calls["n"] == 3
    assert sleeps == [5]

    # 10s 后成功；进入 10s 档时慢查询提示触发一次
    notices = []

    async def notify_slow():
        notices.append(1)

    factory, calls = await make("refreshed", 3)
    assert await score_service._run(binding, factory, notify_slow) == "ok"
    assert calls["n"] == 4
    assert sleeps == [5, 10]
    assert len(notices) == 1

    # 全败：非技术兜底文案（不暴露令牌/续期细节）
    factory, calls = await make("refreshed", 99)
    with pytest.raises(UserScoreError, match="暂时无法访问"):
        await score_service._run(binding, factory, notify_slow)
    assert calls["n"] == 4
    assert sleeps == [5, 10]
    assert len(notices) == 2

    # dead：重绑文案
    factory, calls = await make("dead", 99)
    with pytest.raises(UserScoreError, match="重新「绑定落雪」"):
        await score_service._run(binding, factory)
    assert calls["n"] == 1
    assert sleeps == []

    # skip（无凭据/网络）：按原错误映射
    factory, calls = await make("skip", 99)
    with pytest.raises(UserScoreError, match="没有找到这个玩家"):
        await score_service._run(binding, factory)
    assert calls["n"] == 1
    assert sleeps == []

    # 续期后遇到非 401 异常：立即映射，不进阶梯
    factory, calls = await make_other_error(1)
    with pytest.raises(UserScoreError, match="未授权第三方查询"):
        await score_service._run(binding, factory)
    assert calls["n"] == 2
    assert sleeps == []
