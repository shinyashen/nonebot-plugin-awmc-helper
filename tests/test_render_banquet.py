"""render/nb_chart 宴会卡：buddy 左右手物量行与日服新曲标口径。

渲染回归用「字节差」断言：PNG 编码确定，同一输入两次渲染字节一致；
修复前 buddy_notes 不参与绘制、jp 不影响新曲标，两组输入会得到相同字节。
"""

from mocks import make_song, make_utage, requires_assets, make_buddy_notes
from maimai_py import Genre


def _buddy_host(buddy_notes):
    """buddy 宴曲宿主：谱面行顶层五项为 0（规范表实际形态），物量在 buddy_notes。"""
    return make_song(
        363,
        "Milky Beat",
        genre=Genre.宴会場,
        utage=[
            make_utage(
                diff_id=100363,
                kanji="牛",
                is_buddy=True,
                buddy_notes=buddy_notes,
                tap_num=0,
                hold_num=0,
                slide_num=0,
                touch_num=0,
                break_num=0,
            )
        ],
    )


@requires_assets
def test_banquet_buddy_notes_drawn():
    """buddy 谱面：左右手物量参与绘制（修复前被忽略，两张卡字节相同）。"""
    from nonebot_plugin_awmc_helper.core.render import nb_chart

    with_notes = nb_chart.song_chart_banquet_info(_buddy_host(make_buddy_notes()))
    # 快照降级路径 buddy_notes=None → 回退顶层行（全 0），与有物量卡可区分
    without_notes = nb_chart.song_chart_banquet_info(_buddy_host(None))
    assert with_notes != without_notes
    # 渲染确定性（字节差断言的前提）
    assert with_notes == nb_chart.song_chart_banquet_info(
        _buddy_host(make_buddy_notes())
    )


@requires_assets
def test_banquet_jp_suppresses_new_song_badge():
    """日服限定宴谱不渲染国服「新曲」标；旧版本宴谱 jp 与否无差。

    新曲标口径随宴谱组版本（非宿主曲整曲版本），故用宴谱版本控制判定。
    """
    from nonebot_plugin_awmc_helper.core.render import nb_chart

    def host(utage_version: int):
        return make_song(
            363,
            "Milky Beat",
            genre=Genre.宴会場,
            utage=[make_utage(diff_id=100363, version=utage_version)],
        )

    # 宴谱版本 99999 恒判「当前版本」：jp 卡应无新曲标 → 与默认（渲染标）字节不同
    new_song = host(99999)
    assert nb_chart.song_chart_banquet_info(new_song) != (
        nb_chart.song_chart_banquet_info(new_song, jp=True)
    )
    # 旧版本宴谱（10000）本就不挂标：jp 口径不影响字节
    old_song = host(10000)
    assert nb_chart.song_chart_banquet_info(old_song) == (
        nb_chart.song_chart_banquet_info(old_song, jp=True)
    )


@requires_assets
def test_banquet_version_follows_chart():
    """版本标志/新曲标口径 = 宴谱组登场版本，而非宿主曲整曲最小版本。

    宿主 DX 组 10000 / 宴谱组 24000 的混合曲（悪戯センセーション形态）：
    应与整曲 24000 的纯宴曲同字节；修复前按整曲版本取，与整曲 10000 同字节。
    """
    from nonebot_plugin_awmc_helper.core.render import nb_chart

    def host(song_version: int, utage_version: int):
        return make_song(
            363,
            "Milky Beat",
            version=song_version,
            utage=[make_utage(diff_id=100363, version=utage_version)],
        )

    mixed = host(10000, 24000)
    assert nb_chart.song_chart_banquet_info(mixed) == (
        nb_chart.song_chart_banquet_info(host(24000, 24000))
    )
    assert nb_chart.song_chart_banquet_info(mixed) != (
        nb_chart.song_chart_banquet_info(host(10000, 10000))
    )


@requires_assets
def test_banquet_draws_first_chart_only():
    """一个宴谱 id 只对应一张谱面：多张宴谱的歌也只画第一张（不叠行）。"""
    from nonebot_plugin_awmc_helper.core.render import nb_chart

    first_only = make_song(
        363,
        "Milky Beat",
        genre=Genre.宴会場,
        utage=[make_utage(diff_id=100363, tap_num=300)],
    )
    multi = make_song(
        363,
        "Milky Beat",
        genre=Genre.宴会場,
        utage=[
            make_utage(diff_id=100363, tap_num=300),
            make_utage(diff_id=110363, kanji="再", tap_num=999),
        ],
    )
    # 第二张宴谱不参与绘制：与只有第一张的歌逐字节一致
    assert nb_chart.song_chart_banquet_info(multi) == (
        nb_chart.song_chart_banquet_info(first_only)
    )
