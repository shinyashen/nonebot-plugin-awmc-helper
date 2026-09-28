"""水鱼凭据路由（maimai-py 1.6.0，OAuth-only）：装配 / 收口文案 / 回退测试。

库侧（maimai-py 1.6.0 的 ``DivingFishProvider``）：developer_token 整体移除、
OAuth Bearer 为全量/单曲唯一凭据形态、换票缓存迁 ``client._cache``。本文件验证
插件层的 subject 装配、按绑定标志的确定性路由（Q50）、b50 公开回退与收口文案。
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
async def oauth(monkeypatch):
    """打开 OAuth 配置（provider 单例属性 + 配置对象），并清 client 级换票缓存。

    1.6.0 起换票缓存迁到 ``client._cache``（namespace ``divingfish_oauth``），
    provider 实例内已无 ``_oauth_tokens``、构造函数已无 ``developer_token``；
    跨测试全清缓存防 token 桩泄漏（db/songs 种子各自重建，全清无副作用）。
    """
    from nonebot_plugin_awmc_helper.config import plugin_config
    from nonebot_plugin_awmc_helper.core.client import client as maimai_client
    from nonebot_plugin_awmc_helper.core.client import divingfish_provider

    monkeypatch.setattr(
        plugin_config, "awmc_divingfish_oauth_client_id", CID, raising=False
    )
    monkeypatch.setattr(
        plugin_config, "awmc_divingfish_oauth_client_secret", SECRET, raising=False
    )
    monkeypatch.setattr(divingfish_provider, "client_id", CID, raising=False)
    monkeypatch.setattr(divingfish_provider, "client_secret", SECRET, raising=False)
    # 只清换票命名空间：全清会连 songs fixture 经 client.songs 种下的
    # 「provider/ids」键一起清掉，configure() 内 songs() 将回落默认 lxns 源
    await maimai_client._cache.clear(namespace="divingfish_oauth")
    yield
    await maimai_client._cache.clear(namespace="divingfish_oauth")


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
    在 oauth=0 档直走 token（无谓换票零浪费，flag 路由另有专项测试）。"""
    from nonebot_plugin_awmc_helper.core.binding import binding_service

    binding = await binding_service.ensure("qq", "10001")
    ident = binding_service.identifier(binding)
    assert ident.qq == 10001
    assert ident.credentials == _subject("10001")
    full = binding_service.full_identifier(binding)
    assert full.credentials == _subject("10001")

    # oauth=0 档：token 直走全量（10-01 后仅剩全量只读，不做无谓换票）
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
async def test_full_identifier_flag_routing(db, oauth):
    """Q50 路由定稿：oauth=1（设备授权）直走 subject——token 死了也不回落；
    oauth=0 的纯 token 绑定直走 token。"""
    from nonebot_plugin_awmc_helper.core.binding import binding_service

    binding = await binding_service.ensure("qq", "10001")
    await binding_service.bind_divingfish_oauth(binding, sub=20560)
    await binding_service.bind_divingfish_token(binding, "dead-token")
    assert binding_service.full_identifier(binding).credentials == _subject("10001")

    other = await binding_service.ensure("qq", "10002")
    await binding_service.bind_divingfish_token(other, "import-token-1")
    assert binding_service.full_identifier(other).credentials == "import-token-1"


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
    """设备授权用户：全量成绩走 OAuth Bearer（records 端点）。"""
    from nonebot_plugin_awmc_helper.core.score import score_service
    from nonebot_plugin_awmc_helper.core.binding import binding_service

    binding = await binding_service.ensure("qq", "10001")
    with respx.mock(assert_all_called=False) as m:
        token_route = m.post(AUTH_TOKEN).respond(json=_token_ok())
        records_route = m.get(f"{BASE_DF}/player/records").respond(
            json={"records": [SCORE_JSON]}
        )
        scores = await score_service.get_scores_all(binding)

    assert token_route.called
    assert records_route.called
    assert len(scores.scores) == 1
    assert scores.scores[0].id == 231  # maimai_py 归一化：10231 % 10000


@pytest.mark.asyncio
async def test_scores_full_unauthorized_copy(db, songs, oauth):
    """未授权用户（oauth=0 无 token）：全量路径单条可行动文案收口。

    公开键回退已随 1.6.0 删除（全量成绩无公开键形态，developer 端点不存在）。
    """
    from nonebot_plugin_awmc_helper.core.score import UserScoreError, score_service
    from nonebot_plugin_awmc_helper.core.binding import binding_service

    binding = await binding_service.ensure("qq", "10001")
    with respx.mock(assert_all_called=False) as m:
        m.post(AUTH_TOKEN).respond(
            status_code=400,
            json={"error": "consent_required", "error_description": "not consented"},
        )
        with pytest.raises(UserScoreError, match="未授权本 bot"):
            await score_service.get_scores_all(binding)


@pytest.mark.asyncio
async def test_scores_reset_token_copy(db, songs, oauth):
    """oauth=0 纯 token 用户：token 已在水鱼侧重置（400 导入token有误）→
    token 专属收口文案（与未授权文案区分，不做跨凭据回落）。"""
    from nonebot_plugin_awmc_helper.core.score import UserScoreError, score_service
    from nonebot_plugin_awmc_helper.core.binding import binding_service

    binding = await binding_service.ensure("qq", "10001")
    await binding_service.bind_divingfish_token(binding, "reset-token")
    with respx.mock(assert_all_called=False) as m:
        records_route = m.get(f"{BASE_DF}/player/records").respond(
            status_code=400, json={"message": "导入token有误", "status": "error"}
        )
        with pytest.raises(UserScoreError, match="Import-Token 已失效"):
            await score_service.get_scores_all(binding)
    assert records_route.called


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
async def test_b50_public_fallback(db, songs, oauth):
    """oauth=0 用户：b50 首跳 subject 换票被拒 → 回退公开键 + 无凭据 provider。"""
    from nonebot_plugin_awmc_helper.core.score import score_service
    from nonebot_plugin_awmc_helper.core.binding import binding_service

    binding = await binding_service.ensure("qq", "10001")
    player_payload = {
        "username": "tester",
        "rating": 300,
        "nickname": "tester",
        "plate": "",
        "additional_rating": 1,
        "charts": {"sd": [SCORE_JSON], "dx": []},
    }
    with respx.mock(assert_all_called=False) as m:
        m.post(AUTH_TOKEN).respond(
            status_code=400,
            json={"error": "consent_required", "error_description": "not consented"},
        )
        public_route = m.post(f"{BASE_DF}/query/player").respond(json=player_payload)
        bests = await score_service.get_b50(binding)

    assert public_route.called
    assert bests.rating == 300


@pytest.mark.asyncio
async def test_minfo_oauth_covered(db, songs, oauth):
    """设备授权用户：单曲成绩走 OAuth Bearer（music_id-only 请求体）。"""
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
