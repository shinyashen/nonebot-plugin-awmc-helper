"""core/sources 数据源注册表测试：能力声明与实现一致性、视图、门禁与派生文案。

核心守护：``capabilities`` 显式声明 ⟺ 适配器覆写了对应方法——「支持什么」
的单一事实如果两处漂移（声明漏覆写 / 覆写漏声明），在这里显式失败。
"""

import pytest

# 能力域 → SourceBase 方法名（一致性守护的映射表；加能力域必须同步这里）
_CAP_METHODS = {
    "B50": "get_b50",
    "MINFO": "get_minfo",
    "SCORES_ALL": "get_scores_all",
    "PLATES": "get_plates",
    "PLAYER": "get_player",
    "MY_RANKING": "get_my_ranking",
}


@pytest.fixture
async def db(tmp_path):
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


# ---------------------------------------------------------------- 注册表一致性


def test_capabilities_match_overridden_methods():
    """每个源 capabilities 声明 ⟺ 覆写了对应方法（注册表单一事实的防漂移守护）。"""
    from nonebot_plugin_awmc_helper.core.sources import SOURCES, Capability, SourceBase

    for src in SOURCES.values():
        for cap in Capability:
            declared = cap in src.capabilities
            overridden = getattr(type(src), _CAP_METHODS[cap.name]) is not getattr(
                SourceBase, _CAP_METHODS[cap.name]
            )
            assert declared == overridden, (
                f"{src.key}.{cap.name}：声明/覆写不一致"
                f"（declared={declared}, overridden={overridden}）"
            )


def test_registry_shape():
    """三源注册齐全，key/视图与 SERVICE_* 对应。"""
    from nonebot_plugin_awmc_helper.core.binding import (
        SERVICE_NET,
        SERVICE_LXNS,
        SERVICE_DIVINGFISH,
    )
    from nonebot_plugin_awmc_helper.core.sources import SOURCES, source_of

    assert set(SOURCES) == {SERVICE_DIVINGFISH, SERVICE_LXNS, SERVICE_NET}
    assert source_of(SERVICE_NET).view == "jp"
    assert source_of(SERVICE_LXNS).view == "cn"
    assert source_of(SERVICE_DIVINGFISH).view == "cn"
    # 未知键回落水鱼（与 binding_service.provider 同口径）
    assert source_of("??").key == SERVICE_DIVINGFISH


def test_capability_matrix():
    """能力矩阵：NET 仅 b50/minfo；落雪缺 MY_RANKING；水鱼全量。"""
    from nonebot_plugin_awmc_helper.core.sources import (
        Capability,
        source_of,
    )

    assert source_of("net").capabilities == {
        Capability.B50,
        Capability.MINFO,
        Capability.SCORES_ALL,
    }
    assert Capability.MY_RANKING not in source_of("lxns").capabilities
    assert source_of("divingfish").capabilities == set(Capability)


# ---------------------------------------------------------------- 门禁与派生文案


async def test_facade_gates_unsupported(db, songs, monkeypatch):
    """门面路由：NET 牌子（牌单/素材未定）与落雪我的排名 → 统一暂不支持。"""
    from nonebot_plugin_awmc_helper.core.score import UserScoreError, score_service
    from nonebot_plugin_awmc_helper.core.binding import binding_service
    from nonebot_plugin_awmc_helper.core.net_score import net_score_service

    net_binding = await binding_service.ensure("qq", "70001")
    await binding_service.bind_net(net_binding, sega_id="sid", password="pw")
    with pytest.raises(UserScoreError, match="日服数据源（NET）暂不支持牌子进度"):
        await score_service.get_plates(net_binding, "真将")

    # NET 全量成绩已接入：窗口缓存组装 → MaimaiScores 同构（b35/b15 为空库）
    async def fake_scores(_b):
        return [], True

    monkeypatch.setattr(net_score_service, "get_scores", fake_scores)
    ms = await score_service.get_scores_all(net_binding)
    assert ms.scores == []
    assert ms.rating == 0
    assert ms.rating_b35 == 0
    assert ms.rating_b15 == 0

    lx_binding = await binding_service.ensure("qq", "70002")
    await binding_service.bind_lxns(lx_binding, token="t", friend_code=123456)
    with pytest.raises(UserScoreError, match="落雪数据源暂不支持RA 排名"):
        await score_service.get_my_ranking(lx_binding)


async def test_my_ranking_divingfish(db, songs, monkeypatch):
    """水鱼我的排名：榜单定位逻辑随适配器迁移后的正常路径与未上榜路径。"""
    import respx

    from nonebot_plugin_awmc_helper.core.score import score_service
    from nonebot_plugin_awmc_helper.core.binding import binding_service
    from nonebot_plugin_awmc_helper.core.ext.divingfish import RankUser

    async def fake_ranking():
        return [
            RankUser(username="someone", ra=12000),
            RankUser(username="tester", ra=300),
        ]

    monkeypatch.setattr(
        "nonebot_plugin_awmc_helper.core.sources.df_ext.rating_ranking",
        fake_ranking,
    )
    payload = {
        "username": "tester",
        "rating": 300,
        "nickname": "tester",
        "plate": "彩将",
        "additional_rating": 1,
        "charts": {"sd": [], "dx": []},
    }
    binding = await binding_service.ensure("qq", "70003")
    await binding_service.bind_divingfish_username(binding, "tester")
    with respx.mock(assert_all_called=False) as m:
        m.post("https://www.diving-fish.com/api/maimaidxprober/query/player").respond(
            json=payload
        )
        hit = await score_service.get_my_ranking(binding)
    assert hit is not None
    entry, rank = hit
    assert entry.username == "tester"
    assert rank == 2


def test_derived_texts():
    """帮助标注 / 绑定提示清单 / 拦截文案均从注册表派生。"""
    from nonebot_plugin_awmc_helper.core.sources import (
        Capability,
        source_of,
        support_note,
        command_hints,
    )

    assert support_note(Capability.B50) is None  # 全源支持 → 无标注
    assert support_note(Capability.SCORES_ALL) is None  # 2026-09-30 起 NET 接入
    assert support_note(Capability.MY_RANKING) == "仅水鱼数据源"
    assert command_hints("net") == "b50、minfo、ap50"
    # 拦截文案 = 全名 + 能力标签（牌子为 NET 当前唯一保持门禁的查询能力）
    assert str(source_of("net").unsupported(Capability.PLATES)) == (
        "日服数据源（NET）暂不支持牌子进度，敬请期待后续版本"
    )


async def test_net_hooks_route_via_facade(db, songs):
    """needs_fetch / player_profile / 视图与能力查询经门面按源路由。"""
    from nonebot_plugin_awmc_helper.core.score import score_service
    from nonebot_plugin_awmc_helper.core.binding import binding_service
    from nonebot_plugin_awmc_helper.core.net_score import net_score_service

    net_binding = await binding_service.ensure("qq", "70004")
    await binding_service.bind_net(net_binding, sega_id="sid", password="pw")
    net_score_service._window_cache.clear()
    net_score_service._fail_until.clear()
    assert score_service.needs_fetch(net_binding) is True
    assert score_service.player_profile(net_binding) is None

    df_binding = await binding_service.ensure("qq", "70005")
    assert score_service.needs_fetch(df_binding) is False
    assert score_service.player_profile(df_binding) is None
    # 视图与能力查询
    assert score_service.view_of(net_binding.service) == "jp"
    assert score_service.view_of(df_binding.service) == "cn"
    assert score_service.supports(net_binding.service, "b50") is True
    assert score_service.supports(net_binding.service, "scores_all") is True
    assert score_service.supports(net_binding.service, "plates") is False
