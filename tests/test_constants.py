"""constants 派生表与 maimai_py 的同步性（fork 对齐回归）。

导入须放函数内：nonebot 驱动在 session 级 fixture 中初始化，
模块导入期（收集阶段）尚未就绪。
"""


def test_achievement_list_from_coefficient_table():
    """阈值表自 SCORE_COEFFICIENT_TABLE 推导：剔除哨兵行后与原手抄一致。"""
    from nonebot_plugin_awmc_helper.constants import ACHIEVEMENT_LIST

    assert ACHIEVEMENT_LIST == [
        50,
        60,
        70,
        75,
        80,
        90,
        94,
        97,
        98,
        99,
        99.5,
        100,
        100.5,
    ]


def test_version_display_names_incl_magical():
    """MAGiCAL(27000) 已入库枚举，显示名走 VERSION_TO_ZH 精确命中。"""
    from nonebot_plugin_awmc_helper.constants import version_zh

    assert version_zh(27000) == "MAGiCAL"
    assert version_zh(26500) == "舞萌DX CiRCLE PLUS"
    assert version_zh(26000) == "舞萌DX CiRCLE"


def test_zh_to_genre_covers_official_and_aliases():
    """官方中/日文名以库 name_to_genre 为基底，别名入口本地补充。"""
    from maimai_py import Genre

    from nonebot_plugin_awmc_helper.constants import ZH_TO_GENRE

    assert ZH_TO_GENRE["流行&动漫"] == Genre.POPSアニメ
    assert ZH_TO_GENRE["POPSアニメ"] == Genre.POPSアニメ
    assert ZH_TO_GENRE["音击&中二节奏"] == Genre.オンゲキCHUNITHM
    assert ZH_TO_GENRE["宴"] == Genre.宴会場
    assert ZH_TO_GENRE["东方"] == Genre.東方Project


def test_plate_chars_align_with_library():
    """牌字穷举与库 plate_to_version 对齐（舞/霸为旧作全集特判，回 = CiRCLE PLUS）。"""
    from maimai_py import Version, plate_to_version

    from nonebot_plugin_awmc_helper.constants import PLATE_CHARS

    for ch in PLATE_CHARS:
        if ch in ("舞", "霸"):
            continue
        assert ch in plate_to_version, f"牌字 {ch} 不在库 plate_to_version 中"
    assert plate_to_version["回"] == Version.MAIMAI_DX_CIRCLE_PLUS


def test_wu_plate_kinds_and_validity():
    """舞/霸真实牌表：舞代仅 将/极/神/舞舞，霸仅 者（唯一「者」尾牌）。"""
    from nonebot_plugin_awmc_helper.core.plates import (
        is_valid_plate,
        plate_kinds_of,
    )

    assert plate_kinds_of("舞") == ("将", "极", "神", "舞舞")
    assert plate_kinds_of("霸") == ("者",)
    assert plate_kinds_of("樱") == ("将",)
    assert is_valid_plate("舞", "将")
    assert is_valid_plate("霸", "者")
    for kind in ("者",):
        assert not is_valid_plate("舞", kind)
    for kind in ("将", "极", "神", "舞舞"):
        assert not is_valid_plate("霸", kind)
    # 非舞/霸版本不设限
    assert is_valid_plate("樱", "极")


def test_chart_display_id_rules():
    """展示 id 规则：SD=根 id、DX=根 id+10000、宴=diff_id。"""
    from mocks import make_song, make_utage
    from maimai_py import SongType

    from nonebot_plugin_awmc_helper.constants import chart_display_id

    song = make_song(835, "DisplayId")
    sd_diff = song.get_difficulties(SongType.STANDARD)[0]
    dx_diff = song.get_difficulties(SongType.DX)[0]
    assert chart_display_id(song, sd_diff) == 835
    assert chart_display_id(song, dx_diff) == 10835
    utage = make_utage(diff_id=119670)
    assert chart_display_id(song, utage) == 119670
