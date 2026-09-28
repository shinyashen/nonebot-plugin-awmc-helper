"""core/ext/wahlap 机厅数据源直连层测试（respx mock）。"""

import respx
import pytest


@pytest.mark.asyncio
async def test_wahlap_fetch():
    from nonebot_plugin_awmc_helper.core.ext import wahlap as wahlap_ext

    payload = [
        {
            "id": 345,
            "arcadeName": "华立乐园",
            "address": "某某路 1 号",
            "province": "广东",
            "mall": "某某广场",
            "machineCount": 8,
        }
    ]
    with respx.mock(assert_all_called=False) as m:
        m.get("https://wc.wahlap.net/maidx/rest/location").respond(json=payload)
        data = await wahlap_ext.fetch_locations()
    assert len(data) == 1
    assert data[0].name == "华立乐园"
    assert data[0].machine_count == 8
