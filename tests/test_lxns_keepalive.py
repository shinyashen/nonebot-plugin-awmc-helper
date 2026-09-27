"""落雪令牌每日保活任务测试：三态统计/摘要、无绑定短路。"""

from pathlib import Path

import pytest


@pytest.fixture
async def db(tmp_path: Path):
    from nonebot_plugin_awmc_helper.core import store

    store.set_db_file(tmp_path / "awmc.db")
    await store.init_db()
    yield
    store.set_db_file(None)


@pytest.mark.asyncio
async def test_keepalive_summary_and_dead_report(db, monkeypatch):
    """三态计数进摘要；dead 附用户清单（供 SUPERUSER 汇总推送的门槛）。"""
    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.core.store import UserBinding
    from nonebot_plugin_awmc_helper.core.binding import binding_service
    from nonebot_plugin_awmc_helper.core.lxns_keepalive import lxns_keepalive

    for uid, rt in (("40001", "rt1"), ("40002", "rt2"), ("40003", "rt3")):
        await store.save_binding(
            UserBinding(platform="OneBot V11", user_id=uid, lxns_refresh_token=rt)
        )

    async def fake_refresh(b):
        return {"40001": "refreshed", "40002": "dead", "40003": "skip"}[b.user_id]

    monkeypatch.setattr(binding_service, "refresh_lxns", fake_refresh)
    summary, dead = await lxns_keepalive()
    assert dead == 1
    assert "成功 1 个" in summary
    assert "需重新绑定 1 个" in summary
    assert "40002" in summary
    assert "暂时跳过 1 个" in summary


@pytest.mark.asyncio
async def test_keepalive_no_rows(db):
    """无持有 refresh_token 的绑定：空转短路。"""
    from nonebot_plugin_awmc_helper.core.lxns_keepalive import lxns_keepalive

    summary, dead = await lxns_keepalive()
    assert dead == 0
    assert "无绑定需要处理" in summary
