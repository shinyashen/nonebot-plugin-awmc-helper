"""core/provider.DivingFishCurveProvider：水鱼 chart_stats 曲线解析测试。

档位口径以 2026-09 全量实测为准（见 provider 类注释）：
``dist`` 14 档达成率分布升序 D..SSSP；``fc_dist`` 5 档 [未FC, FC, FCP, AP, APP]。
"""

import pytest


def _chart(**overrides):
    """对齐水鱼真实响应的单谱面条目（曲 8 MASTER 实测样本裁剪）。"""
    base = {
        "cnt": 5262.0,
        "diff": "12",
        "fit_diff": 11.977104339850483,
        "avg": 99.2785099158366,
        "avg_dx": 5007.113162175543,
        "std_dev": 1.7406669216376824,
        # 升序 D..SSSP（14 档）
        "dist": [45, 8, 11, 6, 12, 168, 275, 598, 583, 1019, 880, 1318, 3101, 6115],
        # [未FC, FC, FCP, AP, APP]
        "fc_dist": [3797.0, 3156, 5353, 950, 883],
    }
    base.update(overrides)
    return base


def test_deser_new_format_fc_dist():
    """新版响应：全连分布取 fc_dist，未FC 档不进入 fc_sample_size。"""
    from maimai_py import FCType, RateType

    from nonebot_plugin_awmc_helper.core.provider import DivingFishCurveProvider

    curve = DivingFishCurveProvider._deser_curve(_chart())
    assert curve.sample_size == 5262
    assert curve.fit_level_value == pytest.approx(11.977, abs=1e-3)
    assert curve.avg_achievements == pytest.approx(99.2785, abs=1e-3)
    assert curve.stdev_achievements == pytest.approx(1.7407, abs=1e-3)
    assert curve.avg_dx_score == pytest.approx(5007.11, abs=1e-2)
    # [未FC, FC, FCP, AP, APP] → FCType 值序 APP/AP/FCP/FC
    assert curve.fc_sample_size == {
        FCType.APP: 883,
        FCType.AP: 950,
        FCType.FCP: 5353,
        FCType.FC: 3156,
    }
    # 达成率分布升序 D..SSSP，RateType 值序 SSSP..D（两端各验一档）
    assert curve.rate_sample_size[RateType.SSSP] == 6115
    assert curve.rate_sample_size[RateType.D] == 45
    # 未FC 档（3797）不进入 fc_sample_size
    assert sum(curve.fc_sample_size.values()) == 3156 + 5353 + 950 + 883


def test_deser_legacy_format_without_fc_dist():
    """旧版响应（无 fc_dist 字段）：全连分布回退合并式 dist [1..4] 槽。"""
    from maimai_py import FCType

    from nonebot_plugin_awmc_helper.core.provider import DivingFishCurveProvider

    chart = _chart()
    del chart["fc_dist"]
    curve = DivingFishCurveProvider._deser_curve(chart)
    # 旧布局：APP=dist[4]、AP=dist[3]、FCP=dist[2]、FC=dist[1]
    assert curve.fc_sample_size == {
        FCType.APP: chart["dist"][4],
        FCType.AP: chart["dist"][3],
        FCType.FCP: chart["dist"][2],
        FCType.FC: chart["dist"][1],
    }


@pytest.mark.asyncio
async def test_get_curves_maps_ids_and_filters_empty():
    """id → (根id, 类型) 映射；尾部 {} 占位过滤后按位对齐。"""
    import respx
    from maimai_py import SongType

    from nonebot_plugin_awmc_helper.core.provider import DivingFishCurveProvider

    payload = {
        "charts": {
            # SD：4 谱面有数据 + remaster 槽 {} 占位
            "8": [_chart(), _chart(), _chart(), _chart(), {}],
            # DX id：映射为 (999, DX)
            "10999": [_chart(), {}],
        }
    }
    with respx.mock(assert_all_called=True) as m:
        m.get("https://www.diving-fish.com/api/maimaidxprober/chart_stats").respond(
            200, json=payload
        )
        provider = DivingFishCurveProvider()
        curves = await provider.get_curves(None)  # type: ignore[arg-type]
    assert set(curves) == {(8, SongType.STANDARD), (999, SongType.DX)}
    # {} 过滤后不占位：SD 列表 4 项、DX 列表 1 项
    assert len(curves[(8, SongType.STANDARD)]) == 4
    assert len(curves[(999, SongType.DX)]) == 1
    assert provider._hash() == DivingFishCurveProvider._OK_HASH


@pytest.mark.asyncio
async def test_get_curves_failure_degrades():
    """拉取失败返回空表（不阻断曲库加载），哈希变化保证下次重试。"""
    import respx

    from nonebot_plugin_awmc_helper.core.provider import DivingFishCurveProvider

    with respx.mock(assert_all_called=True) as m:
        m.get(
            "https://www.diving-fish.com/api/maimaidxprober/chart_stats"
        ).side_effect = (
            RuntimeError("network down")
        )
        provider = DivingFishCurveProvider()
        assert await provider.get_curves(None) == {}  # type: ignore[arg-type]
    assert provider._hash() != DivingFishCurveProvider._OK_HASH
