"""core/songdb 解析层：纯函数与源解析（不走网络、不写库）。"""

from songdb_fixtures import (
    make_lxns,
    make_all_data,
    make_dschange,
    make_otoge_live,
    make_otoge_deleted,
)


def test_norm_title_and_utage_ids():
    from nonebot_plugin_awmc_helper.core.songdb import (
        utage_ids,
        norm_title,
        utage_diff_id,
    )

    assert norm_title("LOVE ＆ JOY") == norm_title("love&joy")
    assert norm_title("Garakuta  Doll Play") == "garakutadollplay"
    # 宴 6 位 id 双向（实测样本：100018→(18,0)、161852→(1852,6)）
    assert utage_ids(100018) == (18, 0)
    assert utage_ids(161852) == (1852, 6)
    assert all(
        utage_diff_id(si, lv) == 100000 + lv * 10000 + si
        for si, lv in [(18, 0), (1852, 6)]
    )


def test_parse_level_float_and_level_from_value():
    from nonebot_plugin_awmc_helper.constants import level_from_value
    from nonebot_plugin_awmc_helper.core.songdb import parse_level_float

    # 作者口径：宴标级有 + 一律 .7、无 + 一律 .0
    assert parse_level_float("12+?") == 12.7
    assert parse_level_float("13?") == 13.0
    assert parse_level_float("14+") == 14.7
    assert parse_level_float("abc") is None
    # 标级=定数纯函数（otoge-db 全量 85 值 0 冲突验证）
    assert level_from_value(12.0) == "12"
    assert level_from_value(12.5) == "12"
    assert level_from_value(12.6) == "12+"
    assert level_from_value(12.9) == "12+"
    assert level_from_value(13.5) == "13"
    assert level_from_value(13.6) == "13+"


def test_parse_maimaiinfo_skeleton_and_history():
    from nonebot_plugin_awmc_helper.core.songdb import parse_maimaiinfo

    jp = parse_maimaiinfo(make_all_data(), make_dschange())
    # SD 8：变化点 20000→4.0、23000→4.4，末值以 all_data 4.5 校正（§2.13）
    h = jp[8].charts["sd"][0].history
    assert h == [(20000, 4.0), (23000, 4.5)]
    # 无变化的谱面单变化点
    assert jp[8].charts["sd"][1].history == [(20000, 6.0)]
    # DX 10021：登场版本 20500 起 + CiRCLE 变化点
    assert jp[21].charts["dx"][3].history == [(20500, 12.3), (26000, 12.5)]
    assert jp[21].versions["dx"] == 20500
    # 宴：定数由标级串 12? 推导 12.0，ds 垃圾不采信；kanji 取自标题前缀
    utage_chart = jp[18].charts["utage"][0]
    assert utage_chart.history == [(24000, 12.0)]
    assert utage_chart.kanji == "宴"
    # 宴条目保留自身标题（带前缀、≠ 基曲标题）：otoge join 与 pending 判定的依据
    assert utage_chart.title == "[宴]Test Party"
    # from=未知 且无 dschange → 版本不可知
    assert jp[12].versions["sd"] is None
    assert jp[12].charts["sd"][0].history == []
    # __increments__ 合并：CiRCLE PLUS (26500) 登场的新曲
    assert jp[777].versions["dx"] == 26500
    assert jp[777].charts["dx"][0].history == [(26500, 3.0)]
    # SD/DX 谱面物量（SD 4 元组 touch=0 / DX 5 元组）
    assert jp[8].charts["sd"][0].notes == (63, 23, 8, 0, 2)
    assert jp[21].charts["dx"][0].notes == (40, 10, 5, 0, 3)
    # 谱师 '-' 视为未知
    assert jp[8].charts["sd"][1].designer is None


def test_parse_otoge_live_and_deleted():
    from nonebot_plugin_awmc_helper.core.songdb import norm_title, parse_otoge

    data = parse_otoge(make_otoge_live(), make_otoge_deleted())
    # 当前下架集 = 下架记录 ∖ 现役列表（复活的不算）
    assert norm_title("Stale Song") in data.deleted_titles
    assert norm_title("[宴]Rotated Out") not in data.deleted_titles
    assert norm_title("Test Song SD") not in data.deleted_titles
    # 同名多义保留为列表（'Link' ×2）
    assert len(data.by_title[norm_title("Link")]) == 2


def test_parse_lxns_cn_structure():
    from nonebot_plugin_awmc_helper.core.songdb import parse_lxns

    cn = parse_lxns(make_lxns())
    assert set(cn) == {8, 21, 18, 355, 9002}
    # 国服谱面 version / 定数实测值
    chart = cn[8].charts["sd"][0]
    assert chart.cn_version == 20000
    assert chart.cn_level_value == 4.5
    assert chart.designer == "譜面-100号"
    # 宴：level_id 取 6 位 id 右起第 5 位；buddy 左右物量
    assert cn[18].charts["utage"][0].cn_version == 24000
    buddy = cn[355].charts["utage"][1]  # 6 位 id 110355 → level_id 1
    assert buddy.is_buddy
    assert buddy.left == [150, 20, 25, 0, 5]
    assert buddy.right == [130, 25, 20, 0, 5]
    # 落雪不提供 comment（解析层无该字段；comment 仅来自 otoge-db，在合并层验证）
    assert not hasattr(cn[18].charts["utage"][0], "comment")


def test_parse_lxns_disabled_flag():
    from nonebot_plugin_awmc_helper.core.songdb import parse_lxns

    cn = parse_lxns(make_lxns(disable_ids={9002}))
    assert cn[9002].disabled
    assert not cn[8].disabled


def test_detect_cn_update():
    from nonebot_plugin_awmc_helper.core.songdb import DetectKey, detect_cn_update

    # 检测键为 (song_id, kind)；宴键不参与检测（见 songs._poll_keys）
    known: set[DetectKey] = {(8, "sd"), (21, "dx"), (9002, "sd")}
    lx = known
    df = known
    # 首次运行（无基线）不触发
    assert detect_cn_update(set(), lx, df) is None
    # 无变化
    assert detect_cn_update(known, lx, df) is None
    # 单源新增不触发
    assert detect_cn_update(known, lx | {(9001, "sd")}, df) is None
    assert detect_cn_update(known, lx, df | {(9001, "sd")}) is None
    # 双源新增交集 → 触发（国服更新确定）
    result = detect_cn_update(known, lx | {(9001, "sd")}, df | {(9001, "sd")})
    assert result is not None
    added, removed = result
    assert added == {(9001, "sd")}
    assert not removed
    # 双源消失交集 → 下架确认；单源消失不触发
    assert detect_cn_update(known, lx - {(9002, "sd")}, df) is None
    result = detect_cn_update(known, lx - {(9002, "sd")}, df - {(9002, "sd")})
    assert result is not None
    added, removed = result
    assert not added
    assert removed == {(9002, "sd")}


def test_level_flat_matches_doc_example():
    """标准 JSON level 线格式：以 01 文档自带谱例（チルノ，id=199）为基准。"""
    from nonebot_plugin_awmc_helper.core.songdb import _level_flat, _points_from_flat

    # 文档谱例：SD 组 version=12000（旧框）→ 不含旧框值、自 DX 初代起 14 个值
    history = [(20000, 4.0), (23000, 3.0)]
    flat = _level_flat(history)
    assert len(flat) == 14
    assert flat == [4.0] * 6 + [3.0] * 8  # 23000 = 轴上第 7 位
    # 文档谱例：DX 组 version=26000（CiRCLE 登场）→ level [3, 3] 共 2 值
    assert _level_flat([(26000, 3.0)]) == [3.0, 3.0]
    # 宴为单元素列表（[12.7] 表示标级 12+?），由调用方取末值实现，不走进此函数

    # 导入往返：扁平列表 → 变化点（含文档谱例 2 值情形与端对齐）
    assert _points_from_flat([4.0] * 6 + [3.0] * 8, 12000) == [
        (20000, 4.0),
        (23000, 3.0),
    ]
    assert _points_from_flat([3.0, 3.0], 26000) == [(26000, 3.0)]
    # 未知名曲（debut 未知）：按「末位 = 日服最新版本」端对齐
    assert _points_from_flat([3.0, 3.0], None) == [(26000, 3.0)]
    # 15 值（收录 MAGiCAL 27000 后的文档）：DX 14 版 + MAGiCAL，变化点两处
    assert _points_from_flat([5.0] * 14 + [5.5], None) == [
        (20000, 5.0),
        (27000, 5.5),
    ]


def test_normalize_text_and_strip_chart_prefix():
    """别名归一与谱面前后缀剥离（Q31 最终语义：单层、动态 kanji、简繁兼容）。"""
    from nonebot_plugin_awmc_helper.constants import normalize_text, strip_chart_prefix

    # 归一：小写 + 全角 NFKC + 简体化（含 zhconv 未覆盖的和制汉字补充表）
    assert normalize_text("ＤＸチルノ") == "dxチルノ"
    assert normalize_text("撫チルノ") == normalize_text("抚チルノ")
    assert normalize_text("蔵") == "藏"
    assert normalize_text("発売前夜") == "发売前夜"  # 発→发；売 为日文写法保留
    assert normalize_text("蔵") != normalize_text("表")

    # 剥离：单层；静态前缀穷举 dx/标准/标/宴
    assert strip_chart_prefix("dx圣诞") == ("圣诞", "dx", "prefix")
    assert strip_chart_prefix("标39") == ("39", "标", "prefix")
    assert strip_chart_prefix("标准企鹅") == ("企鹅", "标准", "prefix")
    assert strip_chart_prefix("宴Oshama") == ("Oshama", "宴", "prefix")
    # 叠层只剥最外层：「dx标39」→「标39」（去前缀库中不存在，自然不命中）
    assert strip_chart_prefix("dx标39") == ("标39", "dx", "prefix")
    # 纯前缀 / 无前缀
    assert strip_chart_prefix("dx") is None
    assert strip_chart_prefix("圣诞") is None
    # 宴谱汉字：动态前缀 + 简繁归一（協/协、蔵/藏 互认）
    assert strip_chart_prefix("協love you", extra_prefixes={"協"}) == (
        "love you",
        "協",
        "prefix",
    )
    # 简体输入「协」命中库内规范形「協」（归一化匹配）
    assert strip_chart_prefix("协love you", extra_prefixes={"協"}) == (
        "love you",
        "協",
        "prefix",
    )
    assert strip_chart_prefix("蔵Glorious", extra_prefixes={"蔵"}) == (
        "Glorious",
        "蔵",
        "prefix",
    )
    assert strip_chart_prefix("藏Glorious", extra_prefixes={"蔵"}) == (
        "Glorious",
        "蔵",
        "prefix",
    )  # 报告的前缀为库内规范形（繁/和制原字），简体输入同样命中

    # [汉字] 括号前缀（柚子库实测形态）+ [宴] 通用括号；[x] 非谱面字不剥
    assert strip_chart_prefix("[協]love you", extra_prefixes={"協"}) == (
        "love you",
        "協",
        "prefix",
    )
    assert strip_chart_prefix("[协]love you", extra_prefixes={"協"}) == (
        "love you",
        "協",
        "prefix",
    )
    assert strip_chart_prefix("[宴]cycles") == ("cycles", "宴", "prefix")
    assert strip_chart_prefix("[x]garakuta", extra_prefixes={"蔵"}) is None
    assert strip_chart_prefix("[宴]") is None  # 剥完为空

    # 后缀（入库与查询共用同一规则；2026-09 柚子库实测 dx/标准）
    assert strip_chart_prefix("牛奶猫dx") == ("牛奶猫", "dx", "suffix")
    assert strip_chart_prefix("牛奶猫标准") == ("牛奶猫", "标准", "suffix")
    assert strip_chart_prefix("39标准") == ("39", "标准", "suffix")
    # iidx 等英文词保护：dx 前置 ASCII 字母不剥，库内形态原样保留
    assert strip_chart_prefix("iidx") is None
    # 「标」与汉字后缀实测不存在，不纳入：「oshama宴」「39标」不剥
    assert strip_chart_prefix("oshama宴") is None
    assert strip_chart_prefix("39标") is None
    assert strip_chart_prefix("dx") is None  # 剥完为空
    # 只剥一层：前缀命中即返回，不再剥后缀
    assert strip_chart_prefix("dx牛奶猫dx") == ("牛奶猫dx", "dx", "prefix")
