"""core/render/nb_chart 渲染测试：宴会卡（buddy 物量/日服新曲标/版本口径）、
版本 logo 适配、查歌卡物量单元格约定与 pending 临时卡冒烟。

渲染回归用「字节差」断言：PNG 编码确定，同一输入两次渲染字节一致；
修复前 buddy_notes 不参与绘制、jp 不影响新曲标，两组输入会得到相同字节。
"""

import pytest
from mocks import make_song, make_utage, requires_assets, make_buddy_notes
from songdb_fixtures import make_pending_item
from maimai_py import Genre
from PIL import Image, ImageDraw

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


def _make_logo(content_w: int, content_h: int, pad: int = 50, glow: bool = False):
    """合成 logo：居中不透明内容矩形 + 透明留白（glow 再加一圈低于阈值的淡光晕）。"""
    extra = 30 if glow else 0
    w, h = content_w + pad * 2 + extra * 2, content_h + pad * 2 + extra * 2
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    if glow:  # alpha=8 < 裁边阈值 16，应被当作留白裁掉
        d.rectangle(
            [extra - 1, extra - 1, w - extra, h - extra], fill=(200, 230, 220, 8)
        )
    d.rectangle(
        [
            extra + pad,
            extra + pad,
            extra + pad + content_w - 1,  # rectangle 坐标为闭区间，-1 才是标称尺寸
            extra + pad + content_h - 1,
        ],
        fill=(80, 160, 140, 255),
    )
    return im


def test_fit_wide_logo_width_bound_no_distortion():
    """长条 logo（AR 3.3）：等比、宽度顶到槽位 182。"""
    from nonebot_plugin_awmc_helper.core.render.nb_chart import fit_version_logo

    out = fit_version_logo(_make_logo(1000, 300))
    assert out.size == (182, 55)
    assert abs(out.width / out.height - 1000 / 300) < 0.05


def test_fit_tallish_logo_meets_min_width():
    """高瘦 logo（AR 1.77，如 BUDDiES）：保底宽 140，不被统一高度压小。"""
    from nonebot_plugin_awmc_helper.core.render.nb_chart import fit_version_logo

    out = fit_version_logo(_make_logo(790, 447))
    assert out.size == (140, 79)


def test_fit_clamps_to_box():
    """近方形 logo：宽度保底会顶破高度上限时，以槽位高 90 为准。"""
    from nonebot_plugin_awmc_helper.core.render.nb_chart import fit_version_logo

    out = fit_version_logo(_make_logo(700, 500))
    assert out.size == (126, 90)
    assert abs(out.width / out.height - 700 / 500) < 0.05


def test_fit_small_source_upscaled():
    """小尺寸源图（国服旧世代 332×160 级别）：等比放大到下限而非原样贴上。"""
    from nonebot_plugin_awmc_helper.core.render.nb_chart import fit_version_logo

    out = fit_version_logo(_make_logo(267, 131, pad=10))
    assert out.size == (147, 72)


def test_fit_trims_faint_glow_margin():
    """淡光晕（alpha<16）视为留白裁掉：带光晕与不带光晕结果完全一致。"""
    from nonebot_plugin_awmc_helper.core.render.nb_chart import fit_version_logo

    plain = fit_version_logo(_make_logo(790, 447))
    glowy = fit_version_logo(_make_logo(790, 447, glow=True))
    assert plain.size == glowy.size


def test_jp_logo_name_batch_codes_fall_back_to_base():
    """日服追加批次码（基础码+批内序号）回落基础码，旧框批次码走通用路径。"""
    from nonebot_plugin_awmc_helper.core.render.nb_chart import (
        _jp_version_logo_name,
    )

    assert _jp_version_logo_name(26500) == "CiRCLE PLUS"
    assert _jp_version_logo_name(26513) == "CiRCLE PLUS"  # 終幕の傀儡等追加批
    assert _jp_version_logo_name(27000) == "MAGiCAL"
    assert _jp_version_logo_name(20506) == "PLUS"
    assert _jp_version_logo_name(25518) == "PRiSM PLUS"
    assert _jp_version_logo_name(19999) is None  # FiNALE 批次码：中日 logo 相同


# ---------------------------------------------------------------------------
# 卡片版本跟随主类型谱面组（老曲补 DX：835 Believe the Rainbow / 10835）
# ---------------------------------------------------------------------------


def _make_song(standard_ver: int | None, dx_ver: int | None, song_version: int):
    from maimai_py.enums import Genre, SongType, LevelIndex
    from maimai_py.models import Song, SongDifficulty, SongDifficulties

    def diff(ver: int | None, type_: SongType):
        return SongDifficulty(
            type=type_,
            level="14",
            level_value=14.0,
            level_index=LevelIndex(3),
            note_designer="-",
            version=ver or 0,
            tap_num=1,
            hold_num=0,
            slide_num=0,
            touch_num=0,
            break_num=0,
            curve=None,
        )

    return Song(
        id=835,
        title="Believe the Rainbow",
        artist="-",
        genre=Genre.maimai,
        bpm=180,
        map=None,
        version=song_version,
        rights=None,
        aliases=None,
        disabled=False,
        difficulties=SongDifficulties(
            standard=[diff(standard_ver, SongType.STANDARD)] if standard_ver else [],
            dx=[diff(dx_ver, SongType.DX)] if dx_ver else [],
            utage=[],
        ),
    )


def test_chart_version_follows_main_type_for_old_song_new_dx():
    """老曲补 DX：卡片版本取主类型谱面组的登场版本，而非曲级最小值。"""
    from nonebot_plugin_awmc_helper.core.render.nb_chart import chart_version_of

    # 835 实库形态：SD 19999（FiNALE）、DX 25504（PRiSM PLUS）、曲级 = min
    song = _make_song(19999, 25504, 19999)
    assert chart_version_of(song, prefer_sd=False) == 25504  # 默认卡（有 DX 用 DX）
    assert chart_version_of(song, prefer_sd=True) == 19999  # 「标准」前缀卡


def test_chart_version_falls_back_to_song_version():
    """无谱面或谱面版本缺失（0）时回落曲级 version。"""
    from nonebot_plugin_awmc_helper.core.render.nb_chart import chart_version_of

    assert chart_version_of(_make_song(19999, 25504, 19999), prefer_sd=False) == 25504
    assert chart_version_of(_make_song(0, 0, 19999), prefer_sd=False) == 19999
    assert chart_version_of(_make_song(None, None, 20000), prefer_sd=False) == 20000


def test_card_display_id_follows_main_type():
    """卡片展示 id 跟随主类型：DX 卡 = 根 id + 10000（10835），SD 卡 = 根 id。"""
    from nonebot_plugin_awmc_helper.core.render.nb_chart import _display_card_id

    song = _make_song(19999, 25504, 19999)
    assert _display_card_id(song, prefer_sd=False) == 10835
    assert _display_card_id(song, prefer_sd=True) == 835
    # 单一谱面组：DX 专用曲显示 +10000，SD 曲显示根 id
    assert _display_card_id(_make_song(None, 25504, 25504), prefer_sd=False) == 10835
    assert _display_card_id(_make_song(19999, None, 19999), prefer_sd=False) == 835


def test_chart_display_id_mapping():
    """谱面级查分器 id 映射：SD = 根 id、DX = 根 id + 10000、宴 = diff_id。"""
    from maimai_py.enums import SongType, LevelIndex
    from maimai_py.models import SongDifficulty, SongDifficultyUtage

    from nonebot_plugin_awmc_helper.constants import chart_display_id

    song = _make_song(19999, 25504, 19999)

    def mk(type_: SongType):
        return SongDifficulty(
            type=type_,
            level="14",
            level_value=14.0,
            level_index=LevelIndex(3),
            note_designer="-",
            version=25504,
            tap_num=1,
            hold_num=0,
            slide_num=0,
            touch_num=0,
            break_num=0,
            curve=None,
        )

    assert chart_display_id(song, mk(SongType.DX)) == 10835
    assert chart_display_id(song, mk(SongType.STANDARD)) == 835
    utage = SongDifficultyUtage(
        type=SongType.UTAGE,
        level="?",
        level_value=0.0,
        level_index=LevelIndex(0),
        note_designer="-",
        version=26001,
        tap_num=0,
        hold_num=0,
        slide_num=0,
        touch_num=0,
        break_num=0,
        curve=None,
        kanji="宴",
        description="",
        diff_id=119670,
        is_buddy=False,
        buddy_notes=None,
    )
    assert chart_display_id(song, utage) == 119670


def test_notes_cell_text_touch_convention():
    """物量单元格约定：SD touch 恒 -、DX 真 0 显示 0、整行全 0 恒 -（Q33 口径）。"""
    from nonebot_plugin_awmc_helper.core.render.nb_chart import notes_cell_text

    # 有值 → 数字（两种谱型一致）
    assert notes_cell_text(40, 3, is_dx=True, row_has_notes=True) == "40"
    assert notes_cell_text(40, 3, is_dx=False, row_has_notes=True) == "40"
    # touch=0：DX 谱真 0（实测 15 张，如ローリンガール exp）→ 0；
    # SD 谱恒 -（touch 为 DX 谱面机制，SD 全量 2336 行无 touch）
    assert notes_cell_text(0, 3, is_dx=True, row_has_notes=True) == "0"
    assert notes_cell_text(0, 3, is_dx=False, row_has_notes=True) == "-"
    # 整行全 0 = 物量未收录：两种谱型 touch 都画 -
    assert notes_cell_text(0, 3, is_dx=True, row_has_notes=False) == "-"
    assert notes_cell_text(0, 3, is_dx=False, row_has_notes=False) == "-"
    # 其余列 0 → -（SD 旧框移植谱 hold=0 等按未收录口径）
    assert notes_cell_text(0, 1, is_dx=True, row_has_notes=True) == "-"
    assert notes_cell_text(0, 0, is_dx=False, row_has_notes=False) == "-"



@requires_assets
@pytest.mark.asyncio
async def test_pending_card_renders():
    """pending 卡渲染冒烟（合并路径）：缺字段与带物量两种形态均出非空 PNG。"""
    from nonebot_plugin_awmc_helper.core.songdb import (
        pending_to_song,
        parse_pending_item,
    )
    from nonebot_plugin_awmc_helper.core.render.nb_chart import song_chart_info

    bare = parse_pending_item(make_pending_item())
    assert bare is not None
    png = song_chart_info(
        pending_to_song(bare),
        calc=False,
        is_full=False,
        best_list=[],
        jp=True,
        id_text="ID —",
        genre_text=bare.genre_display or "-",
    )
    assert png.startswith(b"\x89PNG")
    assert len(png) > 1000

    full = parse_pending_item(
        make_pending_item(
            image_url="x.png",
            dx_lev_bas_notes_tap="192",
            dx_lev_bas_notes_hold="12",
            dx_lev_bas_notes_slide="4",
            dx_lev_bas_notes_touch="4",
            dx_lev_bas_notes_break="4",
        )
    )
    assert full is not None
    png = song_chart_info(
        pending_to_song(full),
        calc=False,
        is_full=False,
        best_list=[],
        jp=True,
        id_text="ID —",
        genre_text=full.genre_display or "-",
    )
    assert png.startswith(b"\x89PNG")
