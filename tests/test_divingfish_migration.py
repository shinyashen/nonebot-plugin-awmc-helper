"""水鱼 Developer-Token 日落迁移：OAuth 路由 / 回退 / 迁移文案测试。

库侧（maimai-py ≥1.5.3 的 ``DivingFishProvider`` OAuth Bearer 支持，PR #63 合入
上游）提供 OAuth Bearer 路径；本文件验证
插件层的 subject 装配、Import-Token 优先序、未覆盖用户的回退与专项文案。
subject 口径：``sha256(f"{client_id}:{external_id}")``，external_id 与 dev 时代
``/dev/*`` 实际传参一致（用户名 > QQ 号），与水鱼迁移快照的等值映射对齐。
"""

import hashlib
from pathlib import Path

import respx
import pytest

AUTH_TOKEN = "https://auth.diving-fish.com/oauth/token"
BASE_DF = "https://www.diving-fish.com/api/maimaidxprober"
CID, SECRET = "test-client-id", "test-client-secret"

# 231 的 DX 谱（songs fixture 已种子），成绩结构对齐 maimai_py _deser_score
SCORE_JSON = {
    "song_id": 10231,
    "level": "13",
    "level_index": 3,
    "achievements": 100.5,
    "fc": "ap",
    "fs": "fsd",
    "dxScore": 2000,
    "rate": "sssp",
    "ra": 300,
}


def _subject(external_id: str) -> str:
    return "ref:" + hashlib.sha256(f"{CID}:{external_id}".encode()).hexdigest()


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


@pytest.fixture
def oauth(monkeypatch):
    """打开 OAuth 配置（provider 单例属性 + 配置对象），结束自动还原并清换票缓存。

    developer_token 一并补位：生产环境 .env 仍保留该 token（未覆盖用户回退
    developer 端点拿 410 → 迁移文案，token 缺失会退化为「令牌缺失」的误导文案）。
    """
    from nonebot_plugin_awmc_helper.config import plugin_config
    from nonebot_plugin_awmc_helper.core.client import divingfish_provider

    monkeypatch.setattr(
        plugin_config, "awmc_divingfish_oauth_client_id", CID, raising=False
    )
    monkeypatch.setattr(
        plugin_config, "awmc_divingfish_oauth_client_secret", SECRET, raising=False
    )
    monkeypatch.setattr(divingfish_provider, "client_id", CID, raising=False)
    monkeypatch.setattr(divingfish_provider, "client_secret", SECRET, raising=False)
    monkeypatch.setattr(
        divingfish_provider, "developer_token", "legacy-developer-token", raising=False
    )
    divingfish_provider._oauth_tokens.clear()
    yield
    divingfish_provider._oauth_tokens.clear()


def _token_ok() -> dict:
    return {
        "access_token": "token-1",
        "token_type": "Bearer",
        "expires_in": 300,
        "scope": "prober.records.read",
    }


@pytest.mark.asyncio
async def test_identifier_oauth_routing(db, oauth):
    """subject 装配：identifier 带 subject（minfo 走 Bearer），full_identifier
    无 token 时尝试 OAuth、有 token 时 Import-Token 最优先。"""
    from nonebot_plugin_awmc_helper.core.binding import binding_service

    binding = await binding_service.ensure("qq", "10001")
    ident = binding_service.identifier(binding)
    assert ident.qq == 10001
    assert ident.credentials == _subject("10001")
    full = binding_service.full_identifier(binding)
    assert full.credentials == _subject("10001")

    # Import-Token 最优先（读/写/传分实证路径，OAuth 不抢全量）
    await binding_service.bind_divingfish_token(binding, "import-token-1")
    assert binding_service.full_identifier(binding).credentials == "import-token-1"
    # minfo 公开键仍带 subject（token 用户也能走 Bearer 单曲）
    assert binding_service.identifier(binding).credentials == _subject("10001")

    # 用户名绑定：external_id 口径 = 用户名（与 dev 时代传参一致），且不带 qq
    await binding_service.bind_divingfish_username(binding, "tester")
    ident3 = binding_service.identifier(binding)
    assert ident3.username == "tester"
    assert ident3.qq is None
    assert ident3.credentials == _subject("tester")


@pytest.mark.asyncio
async def test_identifier_without_oauth_unchanged(db, monkeypatch):
    """OAuth 未配置 → 装配行为与迁移前完全一致（credentials 为空）。"""
    from nonebot_plugin_awmc_helper.config import plugin_config
    from nonebot_plugin_awmc_helper.core.binding import binding_service

    assert plugin_config.awmc_divingfish_oauth_client_id is None
    binding = await binding_service.ensure("qq", "10001")
    ident = binding_service.identifier(binding)
    assert ident.qq == 10001
    assert ident.credentials is None
    assert binding_service.full_identifier(binding).credentials is None


@pytest.mark.asyncio
async def test_scores_all_oauth_covered(db, songs, oauth):
    """补齐名单内用户：全量成绩走 OAuth Bearer，不触 developer 端点。"""
    from nonebot_plugin_awmc_helper.core.score import score_service
    from nonebot_plugin_awmc_helper.core.binding import binding_service

    binding = await binding_service.ensure("qq", "10001")
    with respx.mock(assert_all_called=False) as m:
        token_route = m.post(AUTH_TOKEN).respond(json=_token_ok())
        records_route = m.get(f"{BASE_DF}/player/records").respond(
            json={"records": [SCORE_JSON]}
        )
        dev_route = m.get(f"{BASE_DF}/dev/player/records").respond(
            status_code=410, json={"message": "sunset"}
        )
        scores = await score_service.get_scores_all(binding)

    assert token_route.called
    assert records_route.called
    assert not dev_route.called
    assert len(scores.scores) == 1
    assert scores.scores[0].id == 231  # maimai_py 归一化：10231 % 10000


@pytest.mark.asyncio
async def test_scores_all_uncovered_falls_back_to_migration_copy(db, songs, oauth):
    """未覆盖用户：OAuth consent_required → 回退公开键 → 日落 410 → 迁移文案。"""
    from nonebot_plugin_awmc_helper.core.score import UserScoreError, score_service
    from nonebot_plugin_awmc_helper.core.binding import binding_service

    binding = await binding_service.ensure("qq", "10001")
    with respx.mock(assert_all_called=False) as m:
        m.post(AUTH_TOKEN).respond(
            status_code=400,
            json={"error": "consent_required", "error_description": "not consented"},
        )
        m.get(f"{BASE_DF}/dev/player/records").respond(
            status_code=410, json={"message": "gone"}
        )
        with pytest.raises(UserScoreError, match="2026-10-01"):
            await score_service.get_scores_all(binding)


@pytest.mark.asyncio
async def test_scores_all_quota_copy(db, songs, oauth):
    """429 → 每日配额文案（不再裸抛 HTTP 错误）。"""
    from nonebot_plugin_awmc_helper.core.score import UserScoreError, score_service
    from nonebot_plugin_awmc_helper.core.binding import binding_service

    binding = await binding_service.ensure("qq", "10001")
    with respx.mock(assert_all_called=False) as m:
        m.post(AUTH_TOKEN).respond(json=_token_ok())
        m.get(f"{BASE_DF}/player/records").respond(
            status_code=429, json={"message": "已超出今日请求上限"}
        )
        with pytest.raises(UserScoreError, match="配额"):
            await score_service.get_scores_all(binding)


@pytest.mark.asyncio
async def test_plates_oauth_covered(db, songs, oauth):
    """补齐名单内用户：牌子进度走 OAuth Bearer 全量（get_plates 装配回归）。

    55f52a4 曾把 ``_run_with_public_fallback`` 传入的标识工厂未调用直接传给
    ``client.plates`` → maimai_py 读 ``identifier.credentials`` AttributeError；
    本用例走真实 maimai_py 链路（respx 只 mock 上游 HTTP），装配错即炸。
    """
    from nonebot_plugin_awmc_helper.core.score import score_service
    from nonebot_plugin_awmc_helper.core.binding import binding_service

    binding = await binding_service.ensure("qq", "10001")
    with respx.mock(assert_all_called=False) as m:
        token_route = m.post(AUTH_TOKEN).respond(json=_token_ok())
        records_route = m.get(f"{BASE_DF}/player/records").respond(
            json={"records": [SCORE_JSON]}
        )
        plates = await score_service.get_plates(binding, "真将")

    assert token_route.called
    assert records_route.called
    assert plates._version == "真"
    assert plates._kind == "将"


@pytest.mark.asyncio
async def test_minfo_oauth_covered(db, songs, oauth):
    """补齐名单内用户：单曲成绩走 OAuth Bearer（music_id-only 请求体）。"""
    from nonebot_plugin_awmc_helper.core.score import score_service
    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.binding import binding_service

    song = await song_service.by_id(231)
    assert song is not None
    binding = await binding_service.ensure("qq", "10001")
    with respx.mock(assert_all_called=False) as m:
        m.post(AUTH_TOKEN).respond(json=_token_ok())
        record_route = m.post(f"{BASE_DF}/player/record").respond(
            json={"10231": [SCORE_JSON]}
        )
        result = await score_service.get_minfo(song, binding)

    assert record_route.called
    assert result is not None
    assert len(result.scores) == 1


@pytest.mark.asyncio
async def test_minfo_uncovered_copy(db, songs, oauth):
    """未覆盖用户 minfo：无回退路径 → 专项文案（Import-Token 恢复不了单曲）。"""
    from nonebot_plugin_awmc_helper.core.score import UserScoreError, score_service
    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.binding import binding_service

    song = await song_service.by_id(231)
    assert song is not None
    binding = await binding_service.ensure("qq", "10001")
    with respx.mock(assert_all_called=False) as m:
        m.post(AUTH_TOKEN).respond(
            status_code=400,
            json={"error": "consent_required", "error_description": "not consented"},
        )
        with pytest.raises(UserScoreError, match="「绑定水鱼」完成一次授权"):
            await score_service.get_minfo(song, binding)
