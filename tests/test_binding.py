"""core/binding + core/score：绑定与查分服务测试。"""

from pathlib import Path

import respx
import pytest
from mocks import requires_assets

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
async def test_identifier_unknown_platform_is_qq(db):
    """运行时真实键是 platform="unknown"（OneBot v11 下 uninfo 不填 platform，
    插件层兜底）：user_id 应照常按 QQ 号装配水鱼凭据，与迁移数据（原 platform=
    "qq"）同等对待；真正的非 QQ 平台仍不可查。"""
    from nonebot_plugin_awmc_helper.core.binding import BindingError, binding_service

    binding = await binding_service.ensure("unknown", "30003")
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
    assert pending_bindings.consume("qq", "1") == "lxns"
    assert not pending_bindings.is_active("qq", "1")

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

    from nonebot_plugin_awmc_helper.core.render.best50 import (
        best50_bytes,
        score_list_bytes,
    )

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
    png = best50_bytes("tester", 250, 250, 0, [sc], [])
    assert png.startswith(b"\x89PNG")
    assert len(png) > 1000
    listing = score_list_bytes("AP50", [sc])
    assert listing.startswith(b"\x89PNG")
