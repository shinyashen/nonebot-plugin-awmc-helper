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
    """牌单派生口径：舞/霸特牌置首 + plate_to_version 键序（=发售序）×
    「版本 ≤ current_version」× 排除初——库推进 current_version 后新牌自动纳入。"""
    from maimai_py import Version, current_version, plate_to_version

    from nonebot_plugin_awmc_helper.constants import PLATE_CHARS

    derived = "舞霸" + "".join(
        ch
        for ch, ver in plate_to_version.items()
        if ver <= current_version and ch != "初"
    )
    assert PLATE_CHARS == derived
    assert PLATE_CHARS.startswith("舞霸")
    # 当前国服（PRiSM PLUS）未实装的 CiRCLE 代与无牌的 初/未 不在牌单
    assert not ({"初", "丸", "回", "未"} & set(PLATE_CHARS))
    assert plate_to_version["回"] == Version.MAIMAI_DX_CIRCLE_PLUS


def test_dx_version_codes_matches_enum_slice():
    """DX 版本轴 = 枚举 MAIMAI_DX..MAIMAI_DX_CIRCLE_PLUS 值切片（01 文档 14 列）。"""
    from maimai_py import Version

    from nonebot_plugin_awmc_helper.constants import DX_VERSION_CODES

    assert DX_VERSION_CODES == [
        v.value
        for v in Version
        if Version.MAIMAI_DX.value <= v.value <= Version.MAIMAI_DX_CIRCLE_PLUS.value
    ]
    assert len(DX_VERSION_CODES) == 14


def test_source_name_table_only_holds_lib_missing_names():
    """与库 divingfish_to_version 重叠的名字不重复收录（songdb 查表兜底库表）；
    值为 Version 枚举成员；MAGiCAL 段为按命名规律的预收。"""
    from maimai_py import Version
    from maimai_py.enums import divingfish_to_version

    from nonebot_plugin_awmc_helper.constants import SOURCE_NAME_TO_VERSION

    overlap = set(SOURCE_NAME_TO_VERSION) & set(divingfish_to_version)
    assert not overlap, f"应交给库表兜底的重复条目：{sorted(overlap)}"
    assert all(isinstance(v, Version) for v in SOURCE_NAME_TO_VERSION.values())
    # 两个命名域的代表条目仍在：dschange 英文域 + でらっくす PLUS 补收
    assert SOURCE_NAME_TO_VERSION["maimai DX Splash"] == Version.MAIMAI_DX_SPLASH
    assert SOURCE_NAME_TO_VERSION["maimai でらっくす PLUS"] == Version.MAIMAI_DX_PLUS
    assert SOURCE_NAME_TO_VERSION["maimai MiLK PLUS"] == Version.MAIMAI_MILK_PLUS
    assert "maimai DX FUTURE" not in SOURCE_NAME_TO_VERSION  # 占位枚举不生成名字


def test_jp_version_image_matches_dx_names():
    """日服 logo 表 = _DX_VERSION_NAMES 官方尾名派生（文件名随表）；旧框版本不收。"""
    from maimai_py import Version

    from nonebot_plugin_awmc_helper.constants import (
        JP_VERSION_IMAGE,
        _DX_VERSION_NAMES,
    )

    assert JP_VERSION_IMAGE == dict(_DX_VERSION_NAMES)
    assert JP_VERSION_IMAGE[Version.MAIMAI_DX_SPLASH] == "SPLASH"
    assert JP_VERSION_IMAGE[Version.MAIMAI_DX_PLUS] == "PLUS"
    assert JP_VERSION_IMAGE[Version.MAIMAI_DX_MAGICAL] == "MAGiCAL"
    assert Version.MAIMAI_FINALE not in JP_VERSION_IMAGE
    assert Version.MAIMAI_DX_FUTURE not in JP_VERSION_IMAGE  # 占位枚举无 logo


def test_version_to_zh_covers_all_enum_members():
    """显示名全覆盖枚举成员（派生改动防漏）；派生规则与特例抽查。"""
    from maimai_py import Version

    from nonebot_plugin_awmc_helper.constants import VERSION_TO_ZH

    assert set(VERSION_TO_ZH) == set(Version)
    assert VERSION_TO_ZH[Version.MAIMAI] == "maimai"
    assert VERSION_TO_ZH[Version.MAIMAI_MILK_PLUS] == "maimai MiLK PLUS"
    assert VERSION_TO_ZH[Version.MAIMAI_DX] == "舞萌DX"  # 品牌名即初代版本名
    assert VERSION_TO_ZH[Version.MAIMAI_DX_SPLASH] == "舞萌DX SPLASH"
    assert VERSION_TO_ZH[Version.MAIMAI_DX_MAGICAL] == "MAGiCAL"
    assert VERSION_TO_ZH[Version.MAIMAI_DX_FUTURE] == "舞萌DX FUTURE"


def test_rate_file_derived_from_rate_to_zh():
    """素材名后缀表由 RATE_TO_ZH 派生：显示名的 + 写作小写 p。"""
    from nonebot_plugin_awmc_helper.constants import RATE_FILE, RATE_TO_ZH

    assert RATE_FILE == {m.name: zh.replace("+", "p") for m, zh in RATE_TO_ZH.items()}
    assert RATE_FILE["SSSP"] == "SSSp"
    assert RATE_FILE["SSP"] == "SSp"


def test_level_index_tables_share_axis():
    """难度四表同轴：元表派生的键集/顺序/反向映射一致。"""
    from maimai_py import LevelIndex

    from nonebot_plugin_awmc_helper.constants import (
        LEVEL_INDEX_EN,
        LEVEL_INDEX_ZH,
        LEVEL_INDEX_COLOR,
        DIFF_DISPLAY_NAMES,
        COLOR_TO_LEVEL_INDEX,
    )

    assert list(LEVEL_INDEX_ZH) == list(LevelIndex)
    assert list(LEVEL_INDEX_COLOR) == list(LevelIndex)
    assert COLOR_TO_LEVEL_INDEX == {v: k for k, v in LEVEL_INDEX_COLOR.items()}
    assert LEVEL_INDEX_EN == ("basic", "advanced", "expert", "master", "remaster")
    assert DIFF_DISPLAY_NAMES == ("Basic", "Advanced", "Expert", "Master", "Re:Master")


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
