"""core/ext/divingfish 直连层测试（respx mock）。"""

import respx
import pytest

BASE_DF = "https://www.diving-fish.com/api/maimaidxprober"


@pytest.mark.asyncio
async def test_rating_ranking():
    from nonebot_plugin_awmc_helper.core.ext import divingfish as df_ext

    with respx.mock(assert_all_called=False) as m:
        m.get(f"{BASE_DF}/rating_ranking").respond(
            json=[
                {"username": "b", "ra": 12000},
                {"username": "a", "ra": 15000},
            ]
        )
        users = await df_ext.rating_ranking()
    assert users[0].username == "a"  # 降序
    assert users[0].ra == 15000


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
