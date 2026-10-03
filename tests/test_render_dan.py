"""core/render/dan 段位认定卡渲染测试：取值规则、精灵图裁格、渲染确定性。

导入放函数内：顶层 import 会在 nonebug 初始化前触发插件包加载（见 conftest）。
"""

import pytest
from PIL import Image
from mocks import requires_assets


def test_score_variant_boundaries():
    """达成率分色：[0,80) Blue / [80,97) Red / [97,101] Gold（边界含）。"""
    from nonebot_plugin_awmc_helper.core.render.dan import score_variant

    assert score_variant(0.0) == "Blue"
    assert score_variant(79.9999) == "Blue"
    assert score_variant(80.0) == "Red"
    assert score_variant(96.9999) == "Red"
    assert score_variant(97.0) == "Gold"
    assert score_variant(101.0) == "Gold"


def test_life_variant_tiers():
    """表盘/数字序号：>100→1、(10,100]→2、(0,10]→3，04 暂不产出。"""
    from nonebot_plugin_awmc_helper.core.render.dan import life_variant

    assert life_variant(350) == 1
    assert life_variant(101) == 1
    assert life_variant(100) == 2
    assert life_variant(11) == 2
    assert life_variant(10) == 3
    assert life_variant(1) == 3


def test_sprite_cells_rejects_unknown_char():
    """精灵图文本只收数字与 +-,., 符号，其余字符显式报错。"""
    from nonebot_plugin_awmc_helper.core.render.dan import _sprite_cells

    assert _sprite_cells("95.5930") == [9, 5, 13, 5, 9, 3, 0]
    assert _sprite_cells("14+") == [1, 4, 10]
    with pytest.raises(ValueError, match="精灵图不支持"):
        _sprite_cells("12a")


def _card_data(achievement: float, song_id: int | None = 834):
    from nonebot_plugin_awmc_helper.core.render.dan import DanCardData, DanSongCard

    songs = [
        DanSongCard(
            title="PANDORA PARADOXXX",
            kind="std" if i % 2 == 0 else "dx",
            level="15" if i % 2 == 0 else "14+",
            level_index=4 if i % 2 == 0 else 3,
            ds="15.0" if i % 2 == 0 else "14.9",
            charter="PANDORA PARADOXXX",
            bpm="150",
            base_score="0 (+0)",
            achievement=achievement if i == 0 else 0.0,
            song_id=song_id,
        )
        for i in range(4)
    ]
    return DanCardData(
        dan_id="ura_kaiden",
        life=10,
        damage_great=1,
        damage_good=3,
        damage_miss=10,
        clear_bonus=0,
        songs=songs,
    )


@requires_assets
def test_render_deterministic():
    """同输入两次渲染字节一致（PNG 编码确定）。"""
    from nonebot_plugin_awmc_helper.core.render.dan import render_dan_card

    data = _card_data(95.5930)
    assert render_dan_card(data) == render_dan_card(data)


@requires_assets
def test_render_random_differs_from_shin():
    """随机段位走 03 底图 + 票奖励，与真段位（02 底图 + 牌）渲染可区分。"""
    import dataclasses

    from nonebot_plugin_awmc_helper.core.render.dan import render_dan_card

    data = _card_data(99.5)
    shin = render_dan_card(data)
    random_card = render_dan_card(
        dataclasses.replace(data, dan_id="random_master_4", life=100)
    )
    assert shin != random_card


@requires_assets
def test_deleted_song_falls_back_default_cover():
    """削除曲（song_id=None、无显式封面）走默认封面，渲染不失败且与有封面可区分。"""
    from nonebot_plugin_awmc_helper.core.render.dan import render_dan_card

    with_cover = render_dan_card(_card_data(0.0, song_id=834))
    deleted = render_dan_card(_card_data(0.0, song_id=None))
    assert with_cover != deleted


@requires_assets
def test_explicit_cover_overrides():
    """显式传封面时优先于 song_id 取图（纯色图可判定字节不同）。"""
    from nonebot_plugin_awmc_helper.core.render.dan import render_dan_card

    data = _card_data(0.0)
    data.songs[0].cover = Image.new("RGBA", (120, 120), (255, 0, 0, 255))
    assert render_dan_card(data) != render_dan_card(_card_data(0.0))
