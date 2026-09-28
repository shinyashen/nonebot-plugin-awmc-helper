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
