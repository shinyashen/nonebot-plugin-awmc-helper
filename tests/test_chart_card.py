"""core/chart_card 主题取值单测（无渲染，mock 出图函数）。

导入放函数内：顶层 import 会在 nonebug 初始化前触发插件包加载（见 conftest）。
"""

import pytest


def _plain_song():
    """无谱面数据的普通曲（is_banquet 判 False 即可）。"""
    from types import SimpleNamespace

    return SimpleNamespace(
        id=231, difficulties=SimpleNamespace(standard=[], dx=[], utage=[])
    )


@pytest.mark.asyncio
async def test_theme_survives_b50_failure(monkeypatch):
    """B50 拉取失败只丢成绩嵌入，查歌卡仍用绑定主题（修复前退默认配色）。"""
    from types import SimpleNamespace

    from nonebot_plugin_awmc_helper.core import chart_card
    from nonebot_plugin_awmc_helper.core.score import UserScoreError

    async def fail_b50(binding):
        raise UserScoreError("boom")

    seen = {}

    def fake_chart_info(song, calc, is_full, best_list, theme, prefer_type, jp):
        seen.update(calc=calc, best_list=best_list, theme=theme)
        return b"img"

    binding = SimpleNamespace(theme="circle")
    monkeypatch.setattr(chart_card, "binding_service_ident", lambda b: object())
    monkeypatch.setattr(chart_card.score_service, "get_b50", fail_b50)
    monkeypatch.setattr(chart_card.nb_chart, "song_chart_info", fake_chart_info)

    out = await chart_card.chart_card_bytes(_plain_song(), binding)
    assert out == b"img"
    assert seen["theme"] == "circle"
    assert seen["calc"] is False and seen["best_list"] == []


@pytest.mark.asyncio
async def test_theme_applies_without_identifier(monkeypatch):
    """绑定无可用凭据（ident=None）时同样按绑定主题出卡。"""
    from types import SimpleNamespace

    from nonebot_plugin_awmc_helper.core import chart_card

    seen = {}

    def fake_chart_info(song, calc, is_full, best_list, theme, prefer_type, jp):
        seen["theme"] = theme
        return b"img"

    binding = SimpleNamespace(theme="circle")
    monkeypatch.setattr(chart_card, "binding_service_ident", lambda b: None)
    monkeypatch.setattr(chart_card.nb_chart, "song_chart_info", fake_chart_info)

    await chart_card.chart_card_bytes(_plain_song(), binding)
    assert seen["theme"] == "circle"
