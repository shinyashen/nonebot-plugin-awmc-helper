"""render/best50 纯函数单测：ID 归一化、标题截断、段位牌/段位徽章序号、徽章映射。

导入放函数内：顶层 import 会在 nonebug 初始化前触发插件包加载（见 conftest）。
"""

import dataclasses


def _score(song_id: int, type_):
    from maimai_py import Score, RateType, LevelIndex, ScoreExtend

    base = Score(
        id=song_id,
        level="14",
        level_index=LevelIndex.MASTER,
        achievements=100.5,
        fc=None,
        fs=None,
        dx_score=2000,
        dx_rating=300,
        play_count=None,
        play_time=None,
        rate=RateType.SSS,
        type=type_,
    )
    return ScoreExtend(
        **dataclasses.asdict(base),
        title="t",
        level_value=14.0,
        level_dx_score=3000,
        dx_star=None,
        version=22000,
    )


def test_game_song_id_lxns_dx_needs_10000():
    """落雪成绩 DX 谱 id 缺 10000 位（1315 → 11315）。"""
    from maimai_py import SongType

    from nonebot_plugin_awmc_helper.core.render.best50 import game_song_id

    assert game_song_id(_score(1315, SongType.DX)) == 11315


def test_game_song_id_divingfish_dx_already_full():
    """水鱼成绩已是全 id（10231），不重复加 10000。"""
    from maimai_py import SongType

    from nonebot_plugin_awmc_helper.core.render.best50 import game_song_id

    assert game_song_id(_score(10231, SongType.DX)) == 10231


def test_game_song_id_standard_unchanged():
    """SD 谱 id 不动。"""
    from maimai_py import SongType

    from nonebot_plugin_awmc_helper.core.render.best50 import game_song_id

    assert game_song_id(_score(231, SongType.STANDARD)) == 231


def test_truncate_title_ascii():
    """截断规则与 Hoshino 一致：>18 列截到 17 列加省略号（与预期图逐字符一致）。"""
    from nonebot_plugin_awmc_helper.core.render.best50 import truncate_title

    assert truncate_title("short") == "short"
    assert truncate_title("BULK UP (GAME EXCLUSIVE ver.)") == "BULK UP (GAME EXC..."
    assert truncate_title("Love's Theme of BADASS~") == "Love's Theme of B..."


def test_truncate_title_cjk():
    """全角按 2 列计：恰好 18 列不截断，超限按宽度保留。"""
    from nonebot_plugin_awmc_helper.core.render.best50 import truncate_title

    assert truncate_title("一か罰一か罰一か罰") == "一か罰一か罰一か罰"
    assert truncate_title("超最終鬼畜妹フランドール・") == "超最終鬼畜妹フラ..."


def test_dani_plate_num_skips_after_10():
    """段位 >10 文件号跳一位（Hoshino 同款）。"""
    from nonebot_plugin_awmc_helper.core.render.best50 import dani_plate_num

    assert dani_plate_num(0) == "00"
    assert dani_plate_num(10) == "10"
    assert dani_plate_num(11) == "12"
    assert dani_plate_num(15) == "16"


def test_ra_badge_num_thresholds():
    """边界与 Hoshino 一致（rating < limit 才归下一段，14500 归 10 号牌）。"""
    from nonebot_plugin_awmc_helper.core.render.best50 import ra_badge_num

    assert ra_badge_num(999) == "01"
    assert ra_badge_num(14499) == "09"
    assert ra_badge_num(14500) == "10"
    assert ra_badge_num(15500) == "11"
    assert ra_badge_num(20000) == "11"
    # circle 主题 16000–16999 用 12 号牌，其余与 prism 相同
    assert ra_badge_num(15999, "circle") == "11"
    assert ra_badge_num(16500, "circle") == "12"
    assert ra_badge_num(17000, "circle") == "11"


def test_ra_star_num_matches_hoshino():
    from nonebot_plugin_awmc_helper.core.render.best50 import ra_star_num

    assert ra_star_num(14000) == "01"
    assert ra_star_num(15800) == "04"
    assert ra_star_num(16750) == "04"


def test_combo_sync_icon_files_cover_all_enum_members():
    """FC/FS 全枚举有素材映射，含落雪 sync → Sync。"""
    from maimai_py import FCType, FSType

    from nonebot_plugin_awmc_helper.constants import SYNC_FILE, COMBO_FILE

    assert set(COMBO_FILE) == {fc.name.lower() for fc in FCType}
    assert set(SYNC_FILE) == {fs.name.lower() for fs in FSType}
    assert SYNC_FILE["sync"] == "Sync"
    assert COMBO_FILE["fcp"] == "FCp"
