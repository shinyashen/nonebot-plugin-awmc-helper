"""core/songdb 解析层：纯函数与源解析（不走网络、不写库）。

除少数「机制构造」迷你条目（已注明）外，断言值全部取自 tests/data/snapshots/
真实快照（2026-09-29 取材，maimaiinfo 部分 2026-10-05 刷新至 638126f）。
"""

from songdb_fixtures import (
    make_lxns,
    make_all_data,
    make_dschange,
    make_otoge_live,
    make_pending_item,
    make_otoge_deleted,
    make_pending_revealed,
)


def test_norm_title_and_utage_ids():
    from nonebot_plugin_awmc_helper.core.songdb import (
        utage_ids,
        norm_title,
        utage_diff_id,
    )

    assert norm_title("LOVE ＆ JOY") == norm_title("love&joy")
    assert norm_title("Garakuta  Doll Play") == "garakutadollplay"
    # 宴 6 位 id 双向（真实样本：100018→(18,0)、161852→(1852,6)）
    assert utage_ids(100018) == (18, 0)
    assert utage_ids(161852) == (1852, 6)
    # 快照真实宴体：100199（蛸チルノ）→(199,0)、111355（協ラグトレイン）→(1355,1)、
    # 121634（協青春コンプレックス第二体）→(1634,2)
    assert utage_ids(100199) == (199, 0)
    assert utage_ids(111355) == (1355, 1)
    assert utage_ids(121634) == (1634, 2)
    assert all(
        utage_diff_id(si, lv) == 100000 + lv * 10000 + si
        for si, lv in [(18, 0), (1852, 6), (199, 0), (1355, 1)]
    )


def test_dx_version_stride_matches_enum():
    """songdb 的 +500 递推常量与 maimai_py Version 枚举对齐（防枚举改动破坏递推）。"""
    from maimai_py import Version

    from nonebot_plugin_awmc_helper.core.songdb import _DX_VERSION_STRIDE

    assert Version.MAIMAI_DX_PLUS - Version.MAIMAI_DX == _DX_VERSION_STRIDE


def test_parse_level_float_and_level_from_value():
    from nonebot_plugin_awmc_helper.constants import level_from_value
    from nonebot_plugin_awmc_helper.core.songdb import parse_level_float

    # 作者口径：宴标级有 + 一律 .7、无 + 一律 .0
    assert parse_level_float("12+?") == 12.7
    assert parse_level_float("13?") == 13.0
    assert parse_level_float("14+") == 14.7
    assert parse_level_float("abc") is None
    # 标级=定数纯函数（maimaiinfo 全量机台数据验证：1-6 无 + 记法、
    # 「x」覆盖 x.0-x.9——6.7/6.8/6.9 实测均显示「6」；7 起 .6 边界，
    # 7.6 实测显示「7+」）
    assert level_from_value(12.0) == "12"
    assert level_from_value(12.5) == "12"
    assert level_from_value(12.6) == "12+"
    assert level_from_value(12.9) == "12+"
    assert level_from_value(13.5) == "13"
    assert level_from_value(13.6) == "13+"
    # 低于 LEVEL_PLUS_MIN 一律无 +
    assert level_from_value(6.0) == "6"
    assert level_from_value(6.6) == "6"
    assert level_from_value(6.9) == "6"
    assert level_from_value(5.8) == "5"
    # 阈值档位本身有 +（7.6 → 「7+」，Luminescence 等机台实测）
    assert level_from_value(7.0) == "7"
    assert level_from_value(7.5) == "7"
    assert level_from_value(7.6) == "7+"


def test_parse_maimaiinfo_skeleton_and_history():
    from nonebot_plugin_awmc_helper.core.songdb import parse_maimaiinfo

    jp = parse_maimaiinfo(make_all_data(), make_dschange())
    # SD 8 True Love Song：BASIC 自初代 4.0（旧框历史恢复后首点=登场版本 10000），
    # UNiVERSE PLUS 4.0→5.0（快照真实变化点）
    h = jp[8].charts["sd"][0].history
    assert h == [(10000, 4.0), (22500, 5.0)]
    # ADV 自初代 6.0、GreeN 6.4→FESTiVAL 7.2，MAGiCAL 7.2→7.9（2026-10 真实调整）
    assert jp[8].charts["sd"][1].history == [
        (10000, 6.0),
        (12000, 6.4),
        (23000, 7.2),
        (27000, 7.9),
    ]
    # 11396 テリトリーバトル MASTER：dschange 末值 13.5 旧于 all_data 13.6，
    # 末变化点以 all_data 校正（§2.13，真实数据源滞后样本）
    assert jp[1396].charts["dx"][3].history == [
        (22500, 13.0),
        (23000, 13.2),
        (23500, 13.6),
    ]
    # 30/10030 ネコ日和。：双谱曲——SD 自 maimai（10000）、DX 自 PRiSM PLUS（25500），
    # SD MASTER 带旧框全程与 PRiSM 变化点 11.8→12.6（旧框段随上游 2026-10-04 恢复）
    assert jp[30].versions["sd"] == 10000
    assert jp[30].versions["dx"] == 25500
    assert jp[30].charts["sd"][3].history == [
        (10000, 10.0),
        (11000, 9.0),
        (12000, 9.3),
        (15000, 9.5),
        (16000, 9.7),
        (18000, 10.6),
        (20500, 10.8),
        (21000, 11.1),
        (22500, 11.8),
        (25000, 12.6),
    ]
    # 宴：定数由标级串 12+? 推导 12.7，ds/level 垃圾不采信；kanji 取自标题前缀
    utage_chart = jp[199].charts["utage"][0]
    assert utage_chart.history == [(24000, 12.7)]
    assert utage_chart.kanji == "蛸"
    # 宴条目保留自身标题（带前缀、≠ 基曲标题）：otoge join 与 pending 判定的依据
    assert utage_chart.title == "[蛸]チルノのパーフェクトさんすう教室"
    # 宴谱物量（入库即存主物量五元组）：5 元组含 touch
    assert utage_chart.notes == (58, 217, 27, 0, 7)
    # 宴 buddy（真实 [協]ラグトレイン）：charts 2 张 = 左右两组，主物量入库即存左右合计
    buddy = jp[1355].charts["utage"][1]
    assert buddy.is_buddy
    assert buddy.left == [183, 76, 53, 164, 173]
    assert buddy.right == [172, 63, 53, 102, 216]
    assert buddy.notes == (355, 139, 106, 266, 389)
    # 4 元组语义（机制构造迷你条目）：末位是 break（SD 约定），5 元组才含 touch
    buddy_data = {
        "100020": {
            "id": "100020",
            "title": "[宴]Test Four",
            "type": "SD",
            "ds": [12.7],
            "level": ["12+?"],
            "charts": [{"notes": [51, 0, 292, 6], "charter": "-"}],
            "basic_info": {
                "title": "[宴]Test Four",
                "artist": "A",
                "genre": "宴会場",
                "bpm": "150",
                "from": "maimai でらっくす",
            },
        },
    }
    four_jp = parse_maimaiinfo(buddy_data, {})
    four = four_jp[20].charts["utage"][0]
    assert not four.is_buddy
    assert four.notes == (51, 0, 292, 0, 6)
    # from=未知 且无 dschange → 版本不可知（12 レーザービーム，otoge 亦未收录）
    assert jp[12].versions["sd"] is None
    assert jp[12].charts["sd"][0].history == []
    # 新格式内联（上游 2026-10-04 起 __increments__ 已废）：10267 Ignite Infinity
    # （CiRCLE PLUS 26500 登场 → 根 267）
    assert jp[267].versions["dx"] == 26500
    assert jp[267].charts["dx"][0].history == [(26500, 3.0)]
    # 旧格式 ``__increments__`` 合并兼容（快照内该段继承自上一轮采集）：
    # 854 全世界共通リズム感テスト 仅存在于增量段（all_data 亦无）
    from nonebot_plugin_awmc_helper.core.songdb import _parse_dschange

    inc_hist = _parse_dschange(make_dschange()).get("854", {}).get("sd", {})
    assert inc_hist == {
        idx: [(26500, v)] for idx, v in enumerate([6.0, 8.0, 10.0, 12.0])
    }
    # SD/DX 谱面物量（SD 4 元组 touch=0 / DX 5 元组，真实值）
    assert jp[8].charts["sd"][0].notes == (63, 23, 8, 0, 2)
    assert jp[30].charts["dx"][0].notes == (97, 11, 6, 4, 8)
    # 谱师 '-' 视为未知
    assert jp[8].charts["sd"][1].designer is None


def test_parse_otoge_live_and_deleted():
    from nonebot_plugin_awmc_helper.core.songdb import norm_title, parse_otoge

    data = parse_otoge(make_otoge_live(), make_otoge_deleted())
    # 当前下架集 = 下架记录 ∖ 现役列表（复活的不算）：
    # 青春コンプレックス 2026-08-07 下架且不在现役 → 在；
    # True Love Song 有下架记录（20240321）但已复活在役 → 不在
    assert norm_title("青春コンプレックス") in data.deleted_titles
    assert norm_title("[協]青春コンプレックス") in data.deleted_titles
    assert norm_title("True Love Song") not in data.deleted_titles
    # 同名多义保留为列表（真实 'Link' ×2）
    assert len(data.by_title[norm_title("Link")]) == 2


def test_parse_lxns_cn_structure():
    from nonebot_plugin_awmc_helper.core.songdb import parse_lxns

    cn = parse_lxns(make_lxns())
    # 落雪 raw id % 10000 取根：CN 本地 id 1001（BLACK ROSE ↔ 日服 11001）同规则
    assert set(cn) == {8, 30, 131, 199, 239, 267, 383, 624, 665, 9002, 1001, 1355}
    # 国服谱面 version / 定数实测值（8 BASIC：国服 10000 / 5.0）
    chart = cn[8].charts["sd"][0]
    assert chart.cn_version == 10000
    assert chart.cn_level_value == 5.0
    # 谱师 '-' 视为未知
    assert chart.designer is None
    # 宴：level_id 取 6 位 id 右起第 5 位（真实蛸チルノ 100199 → level_id 0）
    assert cn[199].charts["utage"][0].cn_version == 24010
    # buddy 左右物量（真实 [協]ラグトレイン 111355 → (1355, 1)）
    buddy = cn[1355].charts["utage"][1]
    assert buddy.is_buddy
    assert buddy.left == [183, 76, 53, 164, 173]
    assert buddy.right == [172, 63, 53, 102, 216]
    # 落雪不提供 comment（解析层无该字段；comment 仅来自 otoge-db，在合并层验证）
    assert not hasattr(cn[199].charts["utage"][0], "comment")


def test_parse_lxns_disabled_flag():
    from nonebot_plugin_awmc_helper.core.songdb import parse_lxns

    cn = parse_lxns(make_lxns(disable_ids={8}))
    assert cn[8].disabled
    assert not cn[30].disabled


def test_detect_cn_update():
    from nonebot_plugin_awmc_helper.core.songdb import DetectKey, detect_cn_update

    # 检测键为 (song_id, kind)；宴排除由调用方负责（songs._poll_keys 侧过滤），
    # 本函数是纯集合运算——宴键同样触发（真实宴轮换新曲 ハム太郎 根 11113 验证）。
    # 新增锚另用真实曲 BLACK ROSE DX 组（根 1001）
    known: set[DetectKey] = {(8, "sd"), (9002, "sd")}
    lx = known
    df = known
    # 首次运行（无基线）不触发
    assert detect_cn_update(set(), lx, df) is None
    # 无变化
    assert detect_cn_update(known, lx, df) is None
    # 单源新增不触发
    assert detect_cn_update(known, lx | {(1001, "dx")}, df) is None
    assert detect_cn_update(known, lx, df | {(1001, "dx")}) is None
    # 双源新增交集 → 触发（国服更新确定）
    result = detect_cn_update(known, lx | {(1001, "dx")}, df | {(1001, "dx")})
    assert result is not None
    added, removed = result
    assert added == {(1001, "dx")}
    assert not removed
    # 宴键同语义（真实宴轮换新曲 [回]ハム太郎とっとこうた）
    result = detect_cn_update(known, lx | {(11113, "utage")}, df | {(11113, "utage")})
    assert result is not None
    assert result[0] == {(11113, "utage")}
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
    assert strip_chart_prefix("标准チルノ") == ("チルノ", "标准", "prefix")
    assert strip_chart_prefix("宴Oshama") == ("Oshama", "宴", "prefix")
    # 叠层只剥最外层：「dx标39」→「标39」（去前缀库中不存在，自然不命中）
    assert strip_chart_prefix("dx标39") == ("标39", "dx", "prefix")
    # 纯前缀 / 无前缀
    assert strip_chart_prefix("dx") is None
    assert strip_chart_prefix("圣诞") is None
    # 宴谱汉字：动态前缀 + 简繁归一（協/协、蔵/藏 互认；快照真实宴字 協/蛸）
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


def test_parse_gate_requires_constant():
    """可查 gate：至少一张谱面有具体定数；无定数/问号定数/空标题均不可查。

    真实形态：otoge 现役 MAGiCAL 曲定数未揭（无 *_i）→ gate 拒之门外；
    揭晓形态以 MuNET 实测值构造（4.0/7.5/10.9/13.5）。
    """
    from nonebot_plugin_awmc_helper.core.songdb import parse_pending_item

    # 现役形态（定数未揭）：不可查
    assert parse_pending_item(make_pending_item()) is None

    pending = parse_pending_item(make_pending_revealed())
    assert pending is not None
    assert [c.level_value for c in pending.charts] == [4.0, 7.5, 10.9, 13.5]
    assert all(c.is_dx for c in pending.charts)
    assert pending.genre == "maimai"
    assert pending.version == 27000
    assert pending.bpm is None  # 真实现役条目未收录 bpm

    # 全部定数缺失 → None（继续等 otoge 补数）
    item = make_pending_revealed()
    for k in list(item):
        if k.endswith("_i"):
            del item[k]
    assert parse_pending_item(item) is None
    # 定数为 "?" 不可解析；但其余谱面仍有值时整曲可查
    assert parse_pending_item(make_pending_revealed(dx_lev_mas_i="?")) is not None
    item = make_pending_revealed()
    for suffix in ("bas", "adv", "exp", "mas"):
        item[f"dx_lev_{suffix}_i"] = "?"
    assert parse_pending_item(item) is None
    # 空标题不可查
    assert parse_pending_item(make_pending_revealed(title="")) is None


def test_parse_missing_fields_render_as_none():
    """缺字段 → None（渲染层画 -）：标级 ?、物量缺、曲师/BPM/分类/封面缺。"""
    from nonebot_plugin_awmc_helper.core.songdb import parse_pending_item

    pending = parse_pending_item(
        make_pending_revealed(
            artist=None,
            bpm="190",
            catcode="未知分类",
            image_url=None,
            dx_lev_mas="?",
            dx_lev_exp_designer=None,
            dx_lev_mas_notes_tap=None,
        )
    )
    assert pending is not None
    assert pending.artist is None
    assert pending.bpm == "190"
    assert pending.genre is None  # catcode 映射不到
    assert pending.image_url is None
    mas = next(c for c in pending.charts if c.level_id == 3)
    assert mas.level is None  # "?" 形标级视为未知
    assert mas.level_value == 13.5
    assert mas.notes is None  # 无物量锚点（tap 被置空）
    exp = next(c for c in pending.charts if c.level_id == 2)
    assert exp.designer is None
    # 标级 "13+" 与 "13?" 均为可展示形态
    p_plus = parse_pending_item(make_pending_revealed(dx_lev_mas="13+"))
    p_q = parse_pending_item(make_pending_revealed(dx_lev_mas="13?"))
    assert p_plus is not None
    assert p_plus.charts[3].level == "13+"
    assert p_q is not None
    assert p_q.charts[3].level == "13?"


def test_parse_notes_partial_missing():
    """物量部分缺失：「0 即 -」约定——缺失列填 0（渲染层画 -），tap 为锚点。

    物量值取 MuNET 真实实测（mas 564/65/80/32/68）。
    """
    from nonebot_plugin_awmc_helper.core.songdb import parse_pending_item

    pending = parse_pending_item(
        make_pending_revealed(
            dx_lev_mas_notes_slide=None,
            dx_lev_mas_notes_touch=None,
        )
    )
    assert pending is not None
    mas = next(c for c in pending.charts if c.level_id == 3)
    assert mas.notes == (564, 65, 0, 0, 68)
    bas = parse_pending_item(make_pending_revealed(dx_lev_bas_notes_tap=None))
    assert bas is not None
    bas_chart = next(c for c in bas.charts if c.level_id == 0)
    assert bas_chart.notes is None  # tap 缺 → 整行无物量（物化为全 0，画 -）
