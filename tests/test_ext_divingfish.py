"""core/ext/divingfish 直连层测试（respx mock）。"""

import respx
import pytest

BASE_DF = "https://www.diving-fish.com/api/maimaidxprober"


@pytest.mark.asyncio
async def test_rating_ranking():
    from nonebot_plugin_awmc_helper.core.ext import divingfish as df_ext

    df_ext._ranking_cache_clear()  # 进程级缓存，用例前清防跨文件污染
    with respx.mock(assert_all_called=False) as m:
        m.get(f"{BASE_DF}/rating_ranking").respond(
            json=[
                {"username": "b", "ra": 12000},
                {"username": "a", "ra": 15000},
            ]
        )
        users = await df_ext.rating_ranking()
        # TTL 内二次调用走缓存：不再发起请求（respx 未放行即触网必失败）
        assert await df_ext.rating_ranking() is users
    assert users[0].username == "a"  # 降序
    assert users[0].ra == 15000
    # 测试助手整体清空（清后命中失效，需重新拉取）
    df_ext._ranking_cache_clear()
    assert df_ext._RANKING_CACHE.get(df_ext._RANKING_CACHE_KEY) is None


def test_jwt_payload_unverified_shared_helper():
    """JWT「只解不验」payload 解码单源（D-1）：水鱼 token_subject 与落雪
    token_expiry 共用；非 JWT / payload 非对象返回 None。"""
    import json
    import base64

    from nonebot_plugin_awmc_helper.core.ext import jwt_payload_unverified
    from nonebot_plugin_awmc_helper.core.ext.lxns import token_expiry
    from nonebot_plugin_awmc_helper.core.ext.divingfish import token_subject

    def _jwt(payload: dict | list) -> str:
        seg = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
        return f"header.{seg}.signature"

    assert jwt_payload_unverified(_jwt({"sub": "42", "exp": 123})) == {
        "sub": "42",
        "exp": 123,
    }
    assert token_subject(_jwt({"sub": "42"})) == "42"
    assert token_subject(_jwt({})) is None
    assert token_expiry(_jwt({"exp": 100.5})) == 100.5
    assert token_expiry(_jwt({"iat": 1000})) == 1900.0
    # 非 JWT（缺段）/ payload 非对象 → None，调用方回退既有链路
    assert jwt_payload_unverified("not-a-jwt") is None
    assert jwt_payload_unverified(_jwt([1, 2])) is None
    assert token_subject("garbage") is None
    assert token_expiry("garbage") is None


@pytest.mark.asyncio
async def test_query_player_public():
    """公开 query/player 直连探针（绑定即验证/自愈共用）：200 返回原始 dict、
    400 查无此人专项异常、403（隐私关闭）与其余非 200 一般 ExtError、
    请求体形状对齐 maimai_py（qq 序列化为字符串）。"""
    from nonebot_plugin_awmc_helper.core.ext import ExtError
    from nonebot_plugin_awmc_helper.core.ext.divingfish import (
        DivingFishUserNotFoundError,
        query_player_public,
    )

    with respx.mock(assert_all_called=False) as m:
        route = m.post(f"{BASE_DF}/query/player").respond(
            200,
            json={
                "username": "tester",
                "rating": 3000,
                "nickname": "tester",
                "plate": "初将",
                "additional_rating": [],
            },
        )
        profile = await query_player_public(qq=12345678)
        assert profile["username"] == "tester"
        # qq 序列化为字符串（maimai_py _as_diving_fish 同形状）
        body = route.calls[0].request.content.decode()
        assert '"qq":"12345678"' in body.replace(" ", "")

        # 查无此人（400）→ 专项异常
        m.post(f"{BASE_DF}/query/player").respond(
            400, json={"message": "user not exists"}
        )
        with pytest.raises(DivingFishUserNotFoundError):
            await query_player_public(username="no_such_user")

        # 隐私关闭（403）→ 一般 ExtError（账号存在，调用方按暂时性语义放行）
        m.post(f"{BASE_DF}/query/player").respond(
            403, json={"message": "该用户开启了隐私保护"}
        )
        with pytest.raises(ExtError) as ei:
            await query_player_public(username="privacy_user")
        assert not isinstance(ei.value, DivingFishUserNotFoundError)

    # 网络异常 → ExtNetworkError（ExtError 子类）
    import httpx

    with respx.mock(assert_all_called=False) as m:
        m.post(f"{BASE_DF}/query/player").side_effect = httpx.ConnectError("boom")
        with pytest.raises(ExtError):
            await query_player_public(username="x")

    # qq/username 都不给 → ValueError（编程错误，不走网络）
    with pytest.raises(ValueError, match="需要 qq 或 username"):
        await query_player_public()


@pytest.mark.asyncio
async def test_oauth_profile_fetch(monkeypatch):
    """OAuth 本人资料（2026-11-06）：on-behalf-of 换票 + Bearer /player/records
    顶层资料字段；403「该用户未同意用户协议」专项异常，其余失败一般 ExtError。"""
    import httpx

    from nonebot_plugin_awmc_helper.config import plugin_config
    from nonebot_plugin_awmc_helper.core.ext import ExtError
    from nonebot_plugin_awmc_helper.core.ext.divingfish import (
        RECORDS_URL,
        DivingFishAgreementRequired,
        fetch_oauth_profile,
        exchange_subject_token,
    )

    monkeypatch.setattr(plugin_config, "awmc_divingfish_oauth_client_id", "cid")
    monkeypatch.setattr(plugin_config, "awmc_divingfish_oauth_client_secret", "sec")
    token_url = "https://auth.diving-fish.com/oauth/token"

    with respx.mock(assert_all_called=False) as m:
        route = m.post(token_url).respond(
            200, json={"access_token": "at-1", "expires_in": 300}
        )
        assert await exchange_subject_token("ref:abc") == "at-1"
        form = route.calls[0].request.content.decode()
        assert "on-behalf-of" in form
        assert "subject=ref%3Aabc" in form

        m.post(token_url).respond(400, json={"error": "consent_required"})
        with pytest.raises(ExtError, match="consent_required"):
            await exchange_subject_token("ref:abc")

        records = m.get(RECORDS_URL).respond(
            200,
            json={
                "username": "fish",
                "nickname": "Fish",
                "rating": 770,
                "additional_rating": 0,
                "plate": None,
                "records": [{"song_id": 1}],
            },
        )
        profile = await fetch_oauth_profile("at-1")
        assert profile == {
            "username": "fish",
            "nickname": "Fish",
            "rating": 770,
            "additional_rating": 0,
            "plate": None,
        }
        assert records.calls[0].request.headers["Authorization"] == "Bearer at-1"

        m.get(RECORDS_URL).respond(
            403, json={"message": "该用户未同意用户协议", "status": "error"}
        )
        with pytest.raises(DivingFishAgreementRequired):
            await fetch_oauth_profile("at-1")

        # 其余 403（如缺权限）不归为用户协议
        m.get(RECORDS_URL).respond(
            403, json={"message": "缺少权限：prober.records.read"}
        )
        with pytest.raises(ExtError) as ei:
            await fetch_oauth_profile("at-1")
        assert not isinstance(ei.value, DivingFishAgreementRequired)

        m.get(RECORDS_URL).respond(502, text="bad gateway")
        with pytest.raises(ExtError):
            await fetch_oauth_profile("at-1")

    with respx.mock(assert_all_called=False) as m:
        m.get(RECORDS_URL).side_effect = httpx.ConnectError("boom")
        with pytest.raises(ExtError):
            await fetch_oauth_profile("at-1")
