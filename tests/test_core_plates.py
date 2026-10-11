"""core/plates 牌单口径测试：CN/JP 牌单与版本区间、数据源可查限制、
判牌单源（plate_score_ok）与日服本地判牌（build_local_plates）。"""

from mocks import make_diff, make_song
from mocks import make_score as _score

# ---------------------------------------------------------------- 牌单口径


def test_plate_roster_chars():
    """CN 牌单不变（≤ current_version）；JP 牌单 = CN ∪ {丸,回}（≤ CiRCLE PLUS）。"""
    from nonebot_plugin_awmc_helper.constants import PLATE_CHARS
    from nonebot_plugin_awmc_helper.core.plates import PLATE_CHARS_JP

    assert PLATE_CHARS.endswith("彩")
    assert "丸" not in PLATE_CHARS
    assert "回" not in PLATE_CHARS
    # 日服 PLUS 各代独立成牌，CN 罗盘键序下 丸/回 恰为追加尾段
    assert PLATE_CHARS_JP == PLATE_CHARS + "丸回"
    assert PLATE_CHARS_JP[-1] == "回"
    # 「未」不进牌单：口径边界展示仍为 latest-1（回），未仅作 latest 特殊查询
    assert "未" not in PLATE_CHARS_JP


def test_plate_version_range_jp():
    """JP 口径区间：PLUS 各代独立收口；回 封到 current_version_jp（MAGiCAL 无牌字）。"""
    from nonebot_plugin_awmc_helper.core.plates import plate_version_range

    # CN 口径（PLUS 并代）不变——test_constants 的既有断言之外补 JP 对照
    assert plate_version_range("华") == (20000, 20999)
    assert plate_version_range("回") == (26500, 29999)
    # JP 口径：熊/华 各管半代
    assert plate_version_range("熊", jp=True) == (20000, 20499)
    assert plate_version_range("华", jp=True) == (20500, 20999)
    assert plate_version_range("爽", jp=True) == (21000, 21499)
    assert plate_version_range("双", jp=True) == (24000, 24499)
    assert plate_version_range("宴", jp=True) == (24500, 24999)
    assert plate_version_range("彩", jp=True) == (25500, 25999)
    assert plate_version_range("丸", jp=True) == (26000, 26499)
    # MAGiCAL（27000）尚无牌字：回 的上界必须封 26999，不得吞 MAGiCAL 曲目
    assert plate_version_range("回", jp=True) == (26500, 26999)
    # 真含初代、舞/霸全集（上界 = FiNALE 枚举值）：两口径同规
    assert plate_version_range("真", jp=True) == (10000, 11999)
    assert plate_version_range("舞", jp=True) == (10000, 19900)
    assert plate_version_range("霸") == (10000, 19900)


def test_plate_in_roster_and_hint():
    """数据源可查限制：国服查 丸/廻 拒、日服查到 回 为止；拒绝文案带口径上限。"""
    from nonebot_plugin_awmc_helper.core.plates import (
        plate_in_roster,
        plate_roster_hint,
    )

    assert not plate_in_roster("丸", jp=False)
    assert not plate_in_roster("廻", jp=False)  # 繁体归一 → 回，仍不在国服牌单
    assert plate_in_roster("丸", jp=True)
    assert plate_in_roster("廻", jp=True)
    assert plate_in_roster("彩", jp=True)
    assert plate_in_roster("舞", jp=False)
    assert plate_in_roster("霸", jp=True)
    assert plate_roster_hint(False) == "国服数据源可查至「彩」代牌子"
    assert plate_roster_hint(True) == "日服数据源可查至「回」代牌子"
    # 未 = 现行代占位（不进牌单/口径提示）：仅日服口径的特殊查询放行
    assert plate_in_roster("未", jp=True)
    assert not plate_in_roster("未", jp=False)


def test_plate_latest_version_range():
    """未 = 现行代占位版本字：区间即 current_version_jp 单代闭区间，CN 无此语义。"""
    from maimai_py import current_version_jp

    from nonebot_plugin_awmc_helper.core.plates import plate_version_range

    v = current_version_jp.value
    assert v == 27000  # MAGiCAL（2026-10 口径，maimai_py 推进后随库前移）
    assert plate_version_range("未", jp=True) == (v, v)
    assert plate_version_range("未", jp=False) is None


def test_plate_jp_leading_and_latest_name():
    """国服预览口径分类（2026-10-05 定案）：丸/回（含繁体 廻）与 未 为日服
    领先牌，两口径共有牌字（舞/华/彩 等）不是；提示文案版本名钉住 MAGiCAL。"""
    from maimai_py import Version, current_version_jp

    from nonebot_plugin_awmc_helper.core.plates import (
        PLATE_LATEST_VERSION_NAME,
        plate_is_jp_leading,
    )

    for ch in ("丸", "回", "廻", "未"):
        assert plate_is_jp_leading(ch)
    for ch in ("舞", "霸", "华", "彩", "超", "祝"):
        assert not plate_is_jp_leading(ch)
    # 版本名与 current_version_jp 同步维护：枚举推进（公布新牌字/新代）时
    # 本断言失败提醒换名
    assert current_version_jp is Version.MAIMAI_DX_MAGICAL
    assert PLATE_LATEST_VERSION_NAME == "MAGiCAL"


# ---------------------------------------------------------------- 判牌与本地判牌


def _jp_songs():
    """四曲样例：CiRCLE / MAGiCAL / PRiSM PLUS / CiRCLE PLUS 各一（DX MASTER）。"""
    return [
        make_song(8001, "（构造）CiRCLE 曲", diffs=[make_diff(version=26000)]),
        make_song(8002, "（构造）MAGiCAL 曲", diffs=[make_diff(version=27000)]),
        make_song(8003, "（构造）PRiSM PLUS 曲", diffs=[make_diff(version=25500)]),
        make_song(8004, "（构造）CiRCLE PLUS 曲", diffs=[make_diff(version=26500)]),
    ]


async def test_build_local_plates_scope():
    """本地判牌范围：丸 只含 CiRCLE 段；回 不吞 MAGiCAL 曲（上界封 26999）。"""
    from nonebot_plugin_awmc_helper.core.plates import build_local_plates

    songs = _jp_songs()
    scores = [_score(8001, 100.0), _score(8004, 99.0)]

    maru = build_local_plates("丸", "将", songs, scores)
    assert [p.song.id for p in await maru.get_cleared()] == [8001]
    # 丸 范围内仅 8001 且已达成；8004（CiRCLE PLUS）属回、MAGiCAL/PRiSM PLUS 曲范围外
    assert [p.song.id for p in await maru.get_remained()] == []

    kai = build_local_plates("回", "将", songs, scores)
    assert [p.song.id for p in await kai.get_cleared()] == []
    assert [p.song.id for p in await kai.get_remained()] == [8004]

    # 未 = 现行代（MAGiCAL）占位：只含 27000 一段，8002 无分留在 remained
    mado = build_local_plates("未", "将", songs, scores)
    assert [p.song.id for p in await mado.get_cleared()] == []
    assert [p.song.id for p in await mado.get_remained()] == [8002]


async def test_build_local_plates_remaster_not_required():
    """非舞/霸牌不要求 Re:MASTER（库 no_remaster 同语义）：仅 Master 槽计入。"""
    from maimai_py import LevelIndex

    from nonebot_plugin_awmc_helper.core.plates import build_local_plates

    song = make_song(
        8005,
        "（构造）双谱 CiRCLE 曲",
        diffs=[
            make_diff(version=26000),
            make_diff(version=26000, level_index=LevelIndex.ReMASTER, level_value=14.0),
        ],
    )
    plates = build_local_plates("丸", "将", [song], [_score(8005, 100.0)])
    # Master 已 SSS、ReM 不要求 → 整曲完成：remained 空、cleared 命中
    assert [p.song.id for p in await plates.get_remained()] == []
    cleared = await plates.get_cleared()
    assert [p.song.id for p in cleared] == [8005]
    assert cleared[0].levels == {LevelIndex.MASTER}


async def test_build_local_plates_kind_semantics():
    """牌种判定走 plate_score_ok 单源：舞舞 FSD 才算、未达标留在 remained。"""
    from maimai_py import FSType

    from nonebot_plugin_awmc_helper.core.plates import (
        plate_score_ok,
        build_local_plates,
    )

    assert not plate_score_ok("舞舞", _score(1, fs=None))
    assert plate_score_ok("舞舞", _score(1, fs=FSType.FSD))

    songs = _jp_songs()
    scores = [_score(8001, 100.0, fs=FSType.FSP)]  # FSP 不满足舞舞
    plates = build_local_plates("丸", "舞舞", songs, scores)
    assert [p.song.id for p in await plates.get_cleared()] == []
    assert [p.song.id for p in await plates.get_remained()] == [8001]


async def test_in_plate_scope_both_types():
    """跨类型追加谱面按条目登场版本归属（2026-10-11 定案）：DX 曲追加的 SD 谱
    进追加版本的牌（Technicians High 形态），SD 曲追加的 DX 谱同理。"""
    from mocks import make_diff, make_song
    from maimai_py import SongType

    from nonebot_plugin_awmc_helper.core.plates import in_plate_scope

    song = make_song(
        8021,
        "（构造）DX曲追加SD谱",
        diffs=[
            make_diff(type=SongType.DX, version=20003),
            make_diff(type=SongType.STANDARD, version=22005),
        ],
    )
    dx_diff = song.difficulties.dx[0]
    sd_diff = song.difficulties.standard[0]
    # 熊&华 [20000, 20999]：DX 条目计入、SD 条目（22005）出区间
    assert in_plate_scope(song, dx_diff, 20000, 20999)
    assert not in_plate_scope(song, sd_diff, 20000, 20999)
    # 宙&星 [22000, 22999]：SD 条目计入（类型门槛移除前此处被排除）、DX 出区间
    assert in_plate_scope(song, sd_diff, 22000, 22999)
    assert not in_plate_scope(song, dx_diff, 22000, 22999)


async def test_build_local_plates_cross_type_scores():
    """本地判牌双类型：跨类型追加曲的要求槽按牌内条目计，他牌类型成绩不串槽。"""
    from mocks import make_diff, make_song
    from maimai_py import SongType

    from nonebot_plugin_awmc_helper.core.plates import build_local_plates

    song = make_song(
        8022,
        "（构造）DX曲追加SD谱",
        diffs=[
            make_diff(type=SongType.DX, version=20003),
            make_diff(type=SongType.STANDARD, version=22005),
        ],
    )

    # JP 星（UNIVERSE [22000,22499]）：只要求 SD 条目——DX 成绩（他牌条目）不得清槽
    plates = build_local_plates(
        "星",
        "将",
        [song],
        [_score(8022, 100.0, song_type=SongType.DX)],
    )
    assert [p.song.id for p in await plates.get_remained()] == [8022]
    plates = build_local_plates(
        "星",
        "将",
        [song],
        [_score(8022, 100.0, song_type=SongType.STANDARD)],
    )
    assert [p.song.id for p in await plates.get_remained()] == []
    assert [p.song.id for p in await plates.get_cleared()] == [8022]
    # JP 熊（DX [20000,20499]）：只要求 DX 条目——SD 成绩不得清槽
    plates = build_local_plates(
        "熊",
        "将",
        [song],
        [_score(8022, 100.0, song_type=SongType.STANDARD)],
    )
    assert [p.song.id for p in await plates.get_remained()] == [8022]
