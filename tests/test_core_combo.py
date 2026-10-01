"""core/combo 条件组合查询测试：tokenizer 表驱动、assembler 四态邻接、执行器集成。

样例曲库经 ``mocks.seed_service``（真实快照锚：199 チルノ SD[超/12000]+DX[丸
回/26000]+蛸宴 100199、8 True Love Song[初/maimai]、624 KISS CANDY FLAVOR
[堇/18500 其他游戏]）；成绩数值按「真实曲目 + 合理值」构造。
导入放函数内：顶层 import 会在 nonebug 初始化前触发插件包加载（见 conftest）。
"""

from types import SimpleNamespace

import pytest
from mocks import seed_service


def _score(
    song_id: int,
    *,
    title: str = "t",
    level_index=None,
    type_=None,
    achievements: float = 100.0,
    fc=None,
    fs=None,
    dx_star: int | None = None,
    version: int = 26000,
    dx_rating: int | None = None,
    level_value: float = 13.0,
):
    """构造 ScoreExtend（RA 缺省按定数 13.0 现算；rate 由达成率派生）。"""
    from maimai_py import RateType, SongType, LevelIndex, ScoreExtend
    from maimai_py.utils import ScoreCoefficient

    return ScoreExtend(
        id=song_id,
        level="13",
        level_index=level_index or LevelIndex.MASTER,
        achievements=achievements,
        fc=fc,
        fs=fs,
        dx_score=2000,
        dx_rating=dx_rating
        if dx_rating is not None
        else int(ScoreCoefficient(achievements).ra(level_value)),
        play_count=None,
        play_time=None,
        rate=RateType._from_achievement(achievements),
        type=type_ or SongType.STANDARD,
        title=title,
        level_value=level_value,
        level_dx_score=3000,
        dx_star=dx_star,
        version=version,
    )


class _FakeScoreService:
    """run_combo 成绩侧替身：视图固定 CN，全量成绩静态给定。"""

    def __init__(self, scores):
        self.scores = scores

    def view_of(self, service):
        return "cn"

    async def get_scores_all(self, binding, notify_slow=None):
        return SimpleNamespace(scores=self.scores)


def _binding():
    from nonebot_plugin_awmc_helper.core.store import UserBinding

    return UserBinding(platform="qq", user_id="10001", service="divingfish")


@pytest.fixture
async def db(tmp_path):
    from nonebot_plugin_awmc_helper.core import store

    store.set_db_file(tmp_path / "awmc.db")
    await store.init_db()
    yield
    store.set_db_file(None)


@pytest.fixture
async def songs(db):
    from nonebot_plugin_awmc_helper.core.songs import song_service

    await seed_service(song_service)
    yield
    song_service._ready.clear()


# ---------------------------------------------------------------- tokenizer


@pytest.mark.parametrize(
    ("text", "kinds"),
    [
        # 层 1 复合/消歧词
        ("紫谱", ["diff"]),
        ("白谱", ["diff"]),
        ("紫代", ["version"]),
        ("白代", ["version"]),
        ("舞舞", ["sync"]),
        ("fdx", ["sync"]),
        ("fsd", ["sync"]),
        ("fdx+", ["sync"]),
        ("fsdp", ["sync"]),
        # 层 2 多字词
        ("东方", ["genre"]),
        ("音击中二", ["genre"]),
        ("maimai", ["genre"]),
        ("v家", ["genre"]),
        ("大将", ["rate"]),
        ("鸟加", ["rate"]),
        ("纯", ["rate_mod"]),
        ("仅", ["rate_mod"]),
        ("牛逼", ["badge"]),
        ("NB", ["badge"]),
        ("越级", ["badge"]),
        ("丢人", ["badge"]),
        ("三星", ["star"]),
        ("4星", ["star"]),
        ("寸", ["cun"]),
        ("名刀", ["kill"]),
        ("锁血", ["kill"]),
        ("锁", ["kill"]),
        ("宴谱", ["utage"]),
        ("宴会场", ["utage"]),
        ("旧框", ["era"]),
        ("DX", ["era"]),
        ("dx谱", ["chart_type"]),
        ("标准", ["chart_type"]),
        ("新版本", ["newness"]),
        ("新歌", ["newness"]),
        ("旧版本", ["newness"]),
        ("理想", ["ideal"]),
        ("13级", ["level"]),
        ("14+级", ["level"]),
        ("14.5定数", ["ds"]),
        ("SSS+", ["rate"]),
        ("sss", ["rate"]),
        ("ssp", ["rate"]),
        ("aa", []),
        ("s", ["rate"]),
        ("霸", ["rate"]),
        ("clear", ["rate"]),
        ("全连", ["combo"]),
        ("FC", ["combo"]),
        ("ap", ["combo"]),
        ("ap+", ["combo"]),
        ("理论", ["combo"]),
        # 层 3 原子
        ("辉", ["version"]),
        ("辉代", ["version"]),
        ("真超檄", ["version"]),
        ("雪辉", ["version"]),
        ("暁", ["version"]),  # 繁体/和制牌字归一
        ("宴", ["version"]),  # 裸宴 = 版本 token（歧义在装配层中止）
        ("绿", ["diff"]),
        ("黄", ["diff"]),
        ("红", ["diff"]),
        ("将", ["kind"]),
        ("極", ["kind"]),  # 牌种字归一
        ("神", ["kind"]),
        ("者", ["kind"]),
        # FMM 邻接口径：版本段连续成段、层 1 先于层 3
        ("祝将", ["version", "kind"]),
        ("紫将", ["version", "kind"]),
        ("雪辉dx", ["version", "era"]),
        ("紫谱将", ["diff", "kind"]),
    ],
)
def test_tokenizer_rules(text: str, kinds: list[str]):
    """规则表逐条正例：kind 序列即词法形态（同层最长、层小优先）。

    开头清谱师动态规则：本文件若在其他加载了完整曲库的测试（tables 等，
    会触发 ensure_designer_rules）之后运行，进程级层 1 规则会把「鸟加」等
    兼任谱师别名的条件词抢走——词法正例必须在裸规则表上断言。
    """
    from nonebot_plugin_awmc_helper.core import combo
    from nonebot_plugin_awmc_helper.core.combo import tokenize

    combo.set_designer_rules(())
    assert [t.kind for t in tokenize(text)] == kinds


@pytest.mark.parametrize(
    "text",
    [
        "1350",  # 纯数字串：无任何 token（级/定数必带）
        "13.50",
        "650",
        "干饭去不吃",  # 零条件闲聊
        "abc",
    ],
)
def test_tokenizer_no_hits(text: str):
    from nonebot_plugin_awmc_helper.core.combo import tokenize

    assert tokenize(text) == []


# ---------------------------------------------------------------- assembler 四态


def test_parse_silent():
    """零条件/纯数字串 → None（静默不回话，防闲聊误触发）。"""
    from nonebot_plugin_awmc_helper.core.combo import parse_combo

    assert parse_combo("1350") is None
    assert parse_combo("13.50") is None
    assert parse_combo("干饭去不吃") is None
    assert parse_combo("") is None


def test_parse_ambiguity():
    """裸紫/白/宴 → 歧义中止；显式组合（代/谱/牌绑定）豁免。"""
    from nonebot_plugin_awmc_helper.core.combo import ComboAmbiguity, parse_combo

    assert isinstance(parse_combo("紫"), ComboAmbiguity)
    assert isinstance(parse_combo("白"), ComboAmbiguity)
    utage = parse_combo("宴")
    assert isinstance(utage, ComboAmbiguity)
    # 宴的引导文案指向宴谱/宴代（2026-10-01 与紫白同口径）
    assert "宴谱50" in utage.message
    assert "宴代50" in utage.message
    assert not isinstance(parse_combo("紫代"), ComboAmbiguity)
    assert not isinstance(parse_combo("白谱"), ComboAmbiguity)
    assert not isinstance(parse_combo("紫将"), ComboAmbiguity)
    assert not isinstance(parse_combo("宴代"), ComboAmbiguity)
    assert not isinstance(parse_combo("宴谱"), ComboAmbiguity)
    assert not isinstance(parse_combo("宴将"), ComboAmbiguity)


def test_parse_version_segment():
    """版本字连续段：段内多版本 OR 成一个 Cond；真=初+真两码。"""
    from maimai_py import Version

    from nonebot_plugin_awmc_helper.core.combo import parse_combo

    conds = parse_combo("雪辉")
    assert isinstance(conds, list)
    assert len(conds) == 1
    codes = conds[0].value
    assert codes == {Version.MAIMAI_MILK_PLUS.value, Version.MAIMAI_FINALE.value}
    zhen = parse_combo("真")
    assert isinstance(zhen, list)
    assert Version.MAIMAI.value in zhen[0].value
    assert Version.MAIMAI_PLUS.value in zhen[0].value


def test_parse_three_types_and():
    """S-1/S-3/S-5 三个不同 CondType：雪辉dx50 是 AND（版本 ∩ 世代）。"""
    from nonebot_plugin_awmc_helper.core.combo import CondType, parse_combo

    conds = parse_combo("雪辉dx")
    assert [c.ctype for c in conds] == [CondType.VERSION, CondType.ERA]


def test_parse_plate_binding():
    """牌字+牌种 = 版本 + 判型分解（S-11 方案 B）；「者」落单丢弃。"""
    from nonebot_plugin_awmc_helper.core.combo import CondType, parse_combo

    assert [c.ctype for c in parse_combo("祝将")] == [
        CondType.VERSION,
        CondType.RATE,
    ]
    assert [c.ctype for c in parse_combo("紫极")] == [
        CondType.VERSION,
        CondType.COMBO,
    ]
    # 宴牌绑定（2026-10-01 宴与紫白同口径）：宴将 = 宴版本 ∩ 将
    assert [c.ctype for c in parse_combo("宴将")] == [
        CondType.VERSION,
        CondType.RATE,
    ]
    # 「者」不构成独立条件词：霸者50 退化为 ≥A 档
    assert [c.ctype for c in parse_combo("霸者")] == [CondType.RATE]


def test_parse_rate_exact():
    """纯/仅 + 档位词 → 精确档；落单 rate_mod 残片忽略。"""
    from nonebot_plugin_awmc_helper.core.combo import parse_combo

    conds = parse_combo("纯sss")
    assert len(conds) == 1
    assert conds[0].value[1] is True  # (档位, exact)
    assert parse_combo("纯") is None


def test_parse_dedup():
    """同型同键去重：神+ap 同为 AP 族（key=ap_all）只保留先声明者。"""
    from nonebot_plugin_awmc_helper.core.combo import parse_combo

    conds = parse_combo("神ap")
    assert len(conds) == 1
    assert conds[0].label == "神"


def test_combo_family_inclusive():
    """包含式语义（§9）：FC 族含 AP/APP；AP 族不含 FC；FSD 族含 FSDP。"""
    from maimai_py import FCType, FSType

    from nonebot_plugin_awmc_helper.core.combo import parse_combo

    fc = parse_combo("fc")[0]
    assert fc.record(_score(1, fc=FCType.FC))
    assert fc.record(_score(1, fc=FCType.AP))  # AP 必然也是全连
    ap = parse_combo("ap")[0]
    assert ap.record(_score(1, fc=FCType.APP))
    assert not ap.record(_score(1, fc=FCType.FC))
    fsd = parse_combo("舞舞")[0]
    assert fsd.record(_score(1, fs=FSType.FSDP))
    assert not fsd.record(_score(1, fs=FSType.FS))


def test_applicability_all_b50():
    """P1 单输出：全部条件默认适用 b50（§9.7 机制先建、暂不触发）。"""
    from nonebot_plugin_awmc_helper.core.combo import (
        OutputKind,
        parse_combo,
        inapplicable,
    )

    assert inapplicable(parse_combo("雪辉dx神寸理想"), OutputKind.B50) == []


# ---------------------------------------------------------------- 执行器


@pytest.mark.asyncio
async def test_run_combo_version_slice_flat(db, songs, monkeypatch):
    """版本条件 → 库切片（平铺）：超(GREEN) 命中 199 的 SD 四谱键集。"""
    from maimai_py import SongType, LevelIndex

    from nonebot_plugin_awmc_helper.core import combo as combo_mod
    from nonebot_plugin_awmc_helper.core.combo import ComboResult, parse_combo

    scores = [
        _score(199, level_index=LevelIndex.EXPERT, version=12000, achievements=99.5),
        _score(199, version=12000, achievements=98.0),
    ]
    monkeypatch.setattr(combo_mod, "score_service", _FakeScoreService(scores))
    result = await combo_mod.run_combo(parse_combo("超"), _binding())
    assert isinstance(result, ComboResult)
    assert result.flat is True  # 谱面类条件在场 → 平铺
    assert {(s.id, s.type, s.level_index) for s in result.scores} == {
        (199, SongType.STANDARD, LevelIndex.EXPERT),
        (199, SongType.STANDARD, LevelIndex.MASTER),
    }
    assert result.total_ra == sum(s.dx_rating or 0 for s in result.scores)
    assert result.bests.scores_b15 == []


@pytest.mark.asyncio
async def test_run_combo_empty_charts_hint(db, songs, monkeypatch):
    """雪辉dx：旧作版本 ∩ DX 世代 = ∅ → ComboEmpty 附点破提示。"""
    from nonebot_plugin_awmc_helper.core import combo as combo_mod
    from nonebot_plugin_awmc_helper.core.combo import ComboEmpty, parse_combo

    monkeypatch.setattr(combo_mod, "score_service", _FakeScoreService([]))
    result = await combo_mod.run_combo(parse_combo("雪辉dx"), _binding())
    assert isinstance(result, ComboEmpty)
    assert "无交集" in result.message


@pytest.mark.asyncio
async def test_run_combo_record_only_split(db, songs, monkeypatch):
    """神50：纯成绩类 → 拆分（35/15），只保留 AP 族成绩。"""
    from maimai_py import FCType, SongType, LevelIndex

    from nonebot_plugin_awmc_helper.core import combo as combo_mod
    from nonebot_plugin_awmc_helper.core.combo import ComboResult, parse_combo

    ap_new = _score(
        199, type_=SongType.DX, fc=FCType.AP, version=25500, achievements=100.0
    )
    ap_old = _score(624, fc=FCType.APP, version=18500, achievements=100.5)
    fc_only = _score(199, level_index=LevelIndex.EXPERT, fc=FCType.FC, version=12000)
    monkeypatch.setattr(
        combo_mod, "score_service", _FakeScoreService([ap_new, ap_old, fc_only])
    )
    result = await combo_mod.run_combo(parse_combo("神"), _binding())
    assert isinstance(result, ComboResult)
    assert result.flat is False  # 纯成绩类 → 拆分
    assert {(s.id, s.level_index) for s in result.scores} == {
        (199, LevelIndex.MASTER),
        (624, LevelIndex.MASTER),
    }
    # 拆分：25500=CN 现行 → b15 侧；18500 → b35 侧
    assert [s.id for s in result.bests.scores_b15] == [199]
    assert [s.id for s in result.bests.scores_b35] == [624]


@pytest.mark.asyncio
async def test_run_combo_utage_only(db, songs, monkeypatch):
    """宴谱50：仅宴谱（默认排除的反向条件），键=（diff_id, UTAGE, BASIC）。"""
    from maimai_py import SongType, LevelIndex

    from nonebot_plugin_awmc_helper.core import combo as combo_mod
    from nonebot_plugin_awmc_helper.core.combo import ComboResult, parse_combo

    utage = _score(
        100199,
        type_=SongType.UTAGE,
        level_index=LevelIndex.BASIC,
        version=24000,
        achievements=99.9,
    )
    normal = _score(199, achievements=100.5)
    monkeypatch.setattr(combo_mod, "score_service", _FakeScoreService([utage, normal]))
    result = await combo_mod.run_combo(parse_combo("宴谱"), _binding())
    assert isinstance(result, ComboResult)
    assert result.flat is True
    assert [s.id for s in result.scores] == [100199]


@pytest.mark.asyncio
async def test_run_combo_achievement_set_empty_renders(db, songs, monkeypatch):
    """成绩集空（有谱面、无成绩）→ 照常返回空组装（渲染全空槽卡）。"""
    from nonebot_plugin_awmc_helper.core import combo as combo_mod
    from nonebot_plugin_awmc_helper.core.combo import ComboResult, parse_combo

    monkeypatch.setattr(combo_mod, "score_service", _FakeScoreService([]))
    result = await combo_mod.run_combo(parse_combo("超"), _binding())
    assert isinstance(result, ComboResult)
    assert result.scores == []
    assert result.total_ra == 0


@pytest.mark.asyncio
async def test_run_combo_cun_sort_override(db, songs, monkeypatch):
    """寸50：区间过滤 + 距目标线升序（排序覆盖默认 RA 降序）。"""
    from nonebot_plugin_awmc_helper.core import combo as combo_mod
    from nonebot_plugin_awmc_helper.core.combo import parse_combo

    near_1005 = _score(1, achievements=100.47, version=12000)  # 距 100.5 = 0.03
    near_1000 = _score(2, achievements=99.95, version=12000)  # 距 100.0 = 0.05
    outside = _score(3, achievements=100.44, version=12000)  # 寸区间外
    milestone = _score(4, achievements=100.0, version=12000)  # 归锁不归寸
    monkeypatch.setattr(
        combo_mod,
        "score_service",
        _FakeScoreService([near_1000, outside, near_1005, milestone]),
    )
    result = await combo_mod.run_combo(parse_combo("寸"), _binding())
    assert [s.id for s in result.scores] == [1, 2]


@pytest.mark.asyncio
async def test_run_combo_ideal_modifier_copy(db, songs, monkeypatch):
    """理想50：副本升一档重算 RA；原成绩对象不被修改（NET 窗口缓存防污染）。"""
    from maimai_py import RateType

    from nonebot_plugin_awmc_helper.core import combo as combo_mod
    from nonebot_plugin_awmc_helper.core.combo import parse_combo

    s = _score(199, achievements=99.0, version=12000)  # SS → 升 SSP(99.5)
    monkeypatch.setattr(combo_mod, "score_service", _FakeScoreService([s]))
    result = await combo_mod.run_combo(parse_combo("理想"), _binding())
    (ideal,) = result.scores
    assert ideal is not s
    assert ideal.rate == RateType.SSP
    assert ideal.achievements == 99.5
    assert ideal.dx_rating > s.dx_rating
    # 原对象分毫未动
    assert s.rate == RateType.SS
    assert s.achievements == 99.0


@pytest.mark.asyncio
async def test_run_combo_genre_slice(db, songs, monkeypatch):
    """东方50：分类切片（谱面类 → 平铺）；非东方曲成绩不入选。"""
    from nonebot_plugin_awmc_helper.core import combo as combo_mod
    from nonebot_plugin_awmc_helper.core.combo import ComboResult, parse_combo

    chirno = _score(199, achievements=99.0, version=12000)
    other = _score(624, achievements=100.5, version=18500)  # 其他游戏分类
    monkeypatch.setattr(combo_mod, "score_service", _FakeScoreService([chirno, other]))
    result = await combo_mod.run_combo(parse_combo("东方"), _binding())
    assert isinstance(result, ComboResult)
    assert result.flat is True
    assert [s.id for s in result.scores] == [199]


@pytest.mark.asyncio
async def test_run_combo_plate_binding(db, songs, monkeypatch):
    """堇将50：牌绑定 = 堇(MURASAKi PLUS) 版本切片 ∩ ≥SSS 成绩判型。"""
    from maimai_py import LevelIndex

    from nonebot_plugin_awmc_helper.core import combo as combo_mod
    from nonebot_plugin_awmc_helper.core.combo import ComboResult, parse_combo

    hit = _score(
        624,
        level_index=LevelIndex.MASTER,
        version=18500,
        achievements=100.5,  # SSS+ ≥ SSS 命中
    )
    miss = _score(
        624,
        level_index=LevelIndex.BASIC,
        version=18500,
        achievements=96.0,  # S 档不达 SSS
    )
    other = _score(199, version=12000, achievements=100.5)  # 非堇代谱面
    monkeypatch.setattr(
        combo_mod, "score_service", _FakeScoreService([hit, miss, other])
    )
    result = await combo_mod.run_combo(parse_combo("堇将"), _binding())
    assert isinstance(result, ComboResult)
    assert result.flat is True  # 牌绑定含版本条件 → 平铺
    assert [s.id for s in result.scores] == [624]
    assert result.title == "堇·将"


# ---------------------------------------------------------------- P2-a：拟合 / 谱师


def _curve(fit: float):
    from maimai_py import CurveObject

    return CurveObject(
        sample_size=100,
        fit_level_value=fit,
        avg_achievements=99.0,
        stdev_achievements=1.0,
        avg_dx_score=2000.0,
        rate_sample_size={},
        fc_sample_size={},
    )


@pytest.mark.parametrize(
    ("text", "kinds"),
    [
        ("拟合", ["fit"]),
        ("nh", ["fit"]),
        ("拟合定数", ["fit"]),
        ("拟合理想", ["fit", "ideal"]),
    ],
)
def test_tokenizer_fit(text: str, kinds: list[str]):
    from nonebot_plugin_awmc_helper.core.combo import tokenize

    assert [t.kind for t in tokenize(text)] == kinds


@pytest.mark.asyncio
async def test_run_combo_fit_recalc(db, songs, monkeypatch):
    """拟合50：带 curve 的谱面按 1 位舍入拟合定数重算 RA 与副行定数；无
    curve 的谱面 fallback 实际定数（原值不动）。纯修改类 → 拆分。"""
    from mocks import make_diff, make_song

    from nonebot_plugin_awmc_helper.core import combo as combo_mod
    from nonebot_plugin_awmc_helper.core.combo import ComboResult, parse_combo
    from nonebot_plugin_awmc_helper.core.songs import song_service

    fitted = make_song(
        199,
        "チルノのパーフェクトさんすう教室",
        version=12000,
        diffs=[make_diff(curve=_curve(13.5), version=12000)],
    )
    plain = make_song(
        8,
        "True Love Song",
        genre=__import__("maimai_py", fromlist=["Genre"]).Genre.maimai,
        version=10000,
        diffs=[
            make_diff(
                type=__import__("maimai_py", fromlist=["SongType"]).SongType.STANDARD,
                level_index=__import__(
                    "maimai_py", fromlist=["LevelIndex"]
                ).LevelIndex.MASTER,
                level="12",
                level_value=12.4,
                version=10000,
            )
        ],
    )
    await seed_service(song_service, [fitted, plain])
    from maimai_py import SongType as _ST

    scores = [
        _score(199, type_=_ST.DX, version=12000, achievements=99.0, level_value=13.0),
        _score(
            8, type_=_ST.STANDARD, version=10000, achievements=99.0, level_value=12.4
        ),
    ]
    monkeypatch.setattr(combo_mod, "score_service", _FakeScoreService(scores))
    result = await combo_mod.run_combo(parse_combo("拟合"), _binding())
    assert isinstance(result, ComboResult)
    assert result.flat is False  # 纯修改类 → 拆分
    by_id = {s.id: s for s in result.scores}
    # 有 curve：level_value=13.5、RA 按 13.5 重算
    assert by_id[199].level_value == 13.5
    assert (
        by_id[199].dx_rating
        > _score(199, achievements=99.0, level_value=13.0).dx_rating
    )
    # 无 curve：fallback 实际定数，原值不动
    assert by_id[8].level_value == 12.4


@pytest.mark.asyncio
async def test_run_combo_fit_ideal_chain(db, songs, monkeypatch):
    """拟合理想50：声明序链式——理想升档后按拟合定数重算 RA（副本）。"""
    from mocks import make_diff, make_song
    from maimai_py import RateType

    from nonebot_plugin_awmc_helper.core import combo as combo_mod
    from nonebot_plugin_awmc_helper.core.combo import parse_combo
    from nonebot_plugin_awmc_helper.core.songs import song_service

    await seed_service(
        song_service,
        [
            make_song(
                199,
                "チルノのパーフェクトさんすう教室",
                version=12000,
                diffs=[make_diff(curve=_curve(13.5), version=12000)],
            )
        ],
    )
    from maimai_py import SongType as _ST

    s = _score(199, type_=_ST.DX, version=12000, achievements=99.0, level_value=13.0)
    monkeypatch.setattr(combo_mod, "score_service", _FakeScoreService([s]))
    result = await combo_mod.run_combo(parse_combo("拟合理想"), _binding())
    (ideal,) = result.scores
    assert ideal is not s
    assert ideal.rate == RateType.SSP  # 理想升档（SS → SSP）
    assert ideal.achievements == 99.5
    assert ideal.level_value == 13.5  # 拟合定数替换
    assert ideal.dx_rating == __import__(
        "nonebot_plugin_awmc_helper.core.calc", fromlist=["compute_rating"]
    ).compute_rating(13.5, 99.5)
    assert s.rate == RateType.SS  # 原对象分毫未动
    assert s.level_value == 13.0


@pytest.mark.asyncio
async def test_run_combo_designer(db, songs, monkeypatch):
    """谱师50：实名词动态注册（归一包含式），谱面类 → 平铺。"""
    from maimai_py import SongType, LevelIndex

    from nonebot_plugin_awmc_helper.core import combo as combo_mod
    from nonebot_plugin_awmc_helper.core.combo import (
        ComboResult,
        parse_combo,
        ensure_designer_rules,
    )

    await ensure_designer_rules()  # songs fixture 曲库已加载 → 实名注册
    score = _score(199, type_=SongType.DX, level_index=LevelIndex.MASTER, version=26000)
    other = _score(199, level_index=LevelIndex.EXPERT, version=12000)  # SD 红谱面
    monkeypatch.setattr(combo_mod, "score_service", _FakeScoreService([score, other]))
    result = await combo_mod.run_combo(parse_combo("まぐランド"), _binding())
    assert isinstance(result, ComboResult)
    assert result.flat is True
    # 199 DX MASTER 谱面 note_designer=まぐランド；SD 红谱（サファ太）不命中
    assert [(s.id, s.type, s.level_index) for s in result.scores] == [
        (199, SongType.DX, LevelIndex.MASTER)
    ]


@pytest.mark.asyncio
async def test_designer_normalized_hit(db, songs, monkeypatch):
    """谱师归一口径：ASCII 大小写归一命中；合作谱形态包含式命中。"""
    from mocks import make_diff, make_song

    from nonebot_plugin_awmc_helper.core import combo as combo_mod
    from nonebot_plugin_awmc_helper.core.combo import (
        parse_combo,
        ensure_designer_rules,
    )
    from nonebot_plugin_awmc_helper.core.songs import song_service

    collab = make_song(
        624,
        "KISS CANDY FLAVOR",
        version=18500,
        diffs=[
            make_diff(note_designer="Moon Strix×ニャイン", version=18500),
        ],
    )
    await seed_service(song_service, [collab])
    await ensure_designer_rules()
    monkeypatch.setattr(combo_mod, "score_service", _FakeScoreService([]))
    # 「moon strix」归一小写包含命中合作谱；单字谱师不注册
    conds = parse_combo("moon strix")
    assert conds is not None
    assert conds[0].ctype.name == "DESIGNER"
    assert not parse_combo("A")  # 单字符实名不注册（静默）


@pytest.mark.asyncio
async def test_designer_rules_skipped_when_cold(monkeypatch):
    """曲库未就绪时 ensure 跳过注册（不在闲聊路径触发加载），谱师词不生效。"""
    from nonebot_plugin_awmc_helper.core import combo as combo_mod
    from nonebot_plugin_awmc_helper.core.songs import song_service

    song_service._ready.clear()
    await combo_mod.ensure_designer_rules()
    assert combo_mod.parse_combo("まぐランド") is None
    combo_mod.set_designer_rules(())  # 清理，防污染后续用例


# ---------------------------------------------------------------- P2-b：表格条件化


def test_plan_of_matches_legacy_checkers():
    """判型推导对齐既有 plan 语义（收编触发文本逐字不变的核心）。"""
    from maimai_py import FCType, FSType

    from nonebot_plugin_awmc_helper.core.combo import plan_of, parse_combo

    def probe(checker, ach=90.0, fc=None, fs=None):
        return checker(ach, fc, fs)

    # fc 族：包含式（FC/FCP/AP/APP 全算），与旧判型管线（已删）同口径
    checker, plan, kind = plan_of(parse_combo("fc"))
    assert (plan, kind) == ("fc", "fc")
    assert probe(checker, fc=FCType.FC)
    assert probe(checker, fc=FCType.FCP)
    assert probe(checker, fc=FCType.APP)
    assert not probe(checker)
    # ap：只 AP/APP
    checker, plan, _ = plan_of(parse_combo("神"))
    assert plan == "ap"
    assert probe(checker, fc=FCType.AP)
    assert not probe(checker, fc=FCType.FCP)
    # fcp：FCP 以上（收编词）
    checker, plan, _ = plan_of(parse_combo("fcp"))
    assert plan == "fcp"
    assert probe(checker, fc=FCType.FCP)
    assert not probe(checker, fc=FCType.FC)
    # fs 族（收编词）/fdx/fsp
    checker, plan, kind = plan_of(parse_combo("fs"))
    assert (plan, kind) == ("fs", "fs")
    assert probe(checker, fs=FSType.FS)
    assert not probe(checker, fs=None)
    checker, plan, _ = plan_of(parse_combo("舞舞"))
    assert plan == "fdx"
    assert probe(checker, fs=FSType.FSD)
    assert not probe(checker, fs=FSType.FSP)
    checker, plan, _ = plan_of(parse_combo("fsp"))
    assert plan == "fsp"
    assert probe(checker, fs=FSType.FSP)
    # rate 档：阈值按 _from_achievement 边界
    checker, plan, kind = plan_of(parse_combo("sss+"))
    assert (plan, kind) == ("sssp", "rate")
    assert probe(checker, ach=100.5)
    assert not probe(checker, ach=100.4)
    # 无达标型条件 → 达成率 ≥80%
    checker, plan, kind = plan_of(parse_combo("东方"))
    assert (plan, kind) == ("", "rate")
    assert probe(checker, ach=80.0)
    assert not probe(checker, ach=79.9)


def test_inapplicability_matrix():
    """§9.7 适用矩阵：B1 进表格不进定数表；B2 不进表格；C 只进 b50。"""
    from nonebot_plugin_awmc_helper.core.combo import (
        OutputKind,
        parse_combo,
        inapplicable,
    )

    assert inapplicable(parse_combo("fc"), OutputKind.TABLE) == []
    assert [c.label for c in inapplicable(parse_combo("fc"), OutputKind.DS_TABLE)] == [
        "FC"
    ]
    assert [c.label for c in inapplicable(parse_combo("寸"), OutputKind.TABLE)] == [
        "寸"
    ]
    assert inapplicable(parse_combo("寸"), OutputKind.SCORE_LIST) == []
    assert [
        c.label for c in inapplicable(parse_combo("理想"), OutputKind.SCORE_LIST)
    ] == ["理想"]
    assert [c.label for c in inapplicable(parse_combo("拟合"), OutputKind.TABLE)] == [
        "拟合"
    ]
    assert inapplicable(parse_combo("东方"), OutputKind.DS_TABLE) == []


@pytest.mark.asyncio
async def test_combo_chart_entries_heuristic(db, songs):
    """§5 选谱启发式：一般条件每曲代表谱面；谱面级条件不收缩。"""
    from nonebot_plugin_awmc_helper.core.combo import (
        ComboEmpty,
        parse_combo,
        combo_chart_entries,
    )

    # 东方：非 single_chart → 每曲代表（199 取最高难度 DX 紫，无 ReM）
    entries = await combo_chart_entries(parse_combo("东方"))
    assert not isinstance(entries, ComboEmpty)
    # 同色双谱（SD 紫 13.3 / DX 紫 13.0）收定数高者
    assert [(s.id, d.type.name, d.level_index.name) for s, d in entries] == [
        (199, "STANDARD", "MASTER")
    ]
    # 紫谱：single_chart → 全部紫谱保留（199 SD/DX、8、624）
    entries = await combo_chart_entries(parse_combo("紫谱"))
    assert len(entries) == 4
    # 13级：single_chart → 199SD/199DX/624 紫（level="13"）
    entries = await combo_chart_entries(parse_combo("13级"))
    assert len(entries) == 3
    # 雪辉dx：谱面集空 → ComboEmpty
    assert isinstance(await combo_chart_entries(parse_combo("雪辉dx")), ComboEmpty)


def testplate_shape():
    """牌子形状检测：牌组合文本 → (版本, 牌种)；非牌形状 → None。

    形状与合法性两段式——「真将」形状成立但牌单无此牌，由调用方拒绝
    （保持旧「没有找到牌子」文案），不落入条件分解。
    """
    from nonebot_plugin_awmc_helper.core.plates import norm_plate, is_valid_plate
    from nonebot_plugin_awmc_helper.plugins.tables.sheet import plate_shape

    assert plate_shape("祝将") == ("祝", "将")
    assert plate_shape("舞神") == ("舞", "神")
    assert plate_shape("樱舞舞") == ("樱", "舞舞")
    assert plate_shape("暁極") == ("暁", "極")  # 形状层不归一
    assert plate_shape("辉") is None  # 无牌种
    assert plate_shape("东方") is None  # 非牌文本
    # 合法性校验（归一后按牌单例外表）
    ver, kind = plate_shape("暁極")
    assert (norm_plate(ver), norm_plate(kind)) == ("晓", "极")
    assert is_valid_plate(norm_plate("暁"), norm_plate("極"))
    assert not is_valid_plate("真", "将")  # 真无将
    assert not is_valid_plate("樱", "者")  # 樱无者


# ---------------------------------------------------------------- 中二/音击侧别


@pytest.fixture
def ongeki_titles(monkeypatch):
    """音击原创栏标题集（真实锚：STARTLINER/Perfect Shining!! 现役、Titania 下架）。"""
    from nonebot_plugin_awmc_helper.core import songdb

    titles = frozenset(
        {
            songdb.norm_title("STARTLINER"),
            songdb.norm_title("Perfect Shining!!"),
            songdb.norm_title("Titania"),  # 下架条目并入集合（§12.4）
        }
    )
    monkeypatch.setattr(songdb, "_ongeki_origin_titles", titles)
    return titles


@pytest.mark.parametrize(
    ("text", "expect"),
    [
        ("音击中二", "genre"),  # 全称 = 大类不区分（同层最长先于侧别词）
        ("中二节奏", "genre_sub"),
        ("chunithm", "genre_sub"),
        ("中二", "genre_sub"),
        ("ongeki", "genre_sub"),
        ("音击", "genre_sub"),
    ],
)
def test_genre_sub_tokenize(text: str, expect: str):
    from nonebot_plugin_awmc_helper.core.combo import tokenize

    assert [t.kind for t in tokenize(text)] == [expect]


def test_genre_sub_predicate(ongeki_titles):
    """侧别判定（§12.3）：原创栏 → 音击；大类其余（イロドリミドリ等）→ 中二。"""
    from mocks import make_song
    from maimai_py import Genre

    from nonebot_plugin_awmc_helper.core.combo import parse_combo

    ong = make_song(2001, "STARTLINER", genre=Genre.オンゲキCHUNITHM, version=26000)
    chunithm = make_song(
        2002, "Titania", genre=Genre.オンゲキCHUNITHM, version=26000
    )  # 下架音击原创，但在集合内 → 音击
    irodori = make_song(
        2003,
        "私たちは、花になる",
        genre=Genre.オンゲキCHUNITHM,
        version=26000,
    )  # イロドリミドリ → 默认中二
    other = make_song(2004, "True Love Song", version=10000)  # 非本大类

    ong_cond = parse_combo("音击")[0]
    chu_cond = parse_combo("中二")[0]
    assert ong_cond.chart(ong, None, 25500)
    assert ong_cond.chart(chunithm, None, 25500)
    assert not ong_cond.chart(irodori, None, 25500)
    assert not ong_cond.chart(other, None, 25500)
    assert chu_cond.chart(irodori, None, 25500)
    assert not chu_cond.chart(ong, None, 25500)
    assert not chu_cond.chart(other, None, 25500)  # 非本大类两侧都不命中


def test_genre_sub_types_and_n15(ongeki_titles):
    """GENRE_SUB 为谱面类（n15 平铺）；与音击中二全称是三个不同 key。"""
    from nonebot_plugin_awmc_helper.core.combo import (
        CHART_COND_TYPES,
        CondType,
        parse_combo,
    )

    conds = parse_combo("音击")
    assert conds[0].ctype is CondType.GENRE_SUB
    assert CondType.GENRE_SUB in CHART_COND_TYPES
    assert parse_combo("音击中二")[0].ctype is CondType.GENRE
    assert parse_combo("中二节奏")[0].key == "genre_sub:chunithm"


def test_ongeki_titles_unloaded_is_empty(monkeypatch):
    """集合未加载（冷启动 kv 无值）：音击=空集、中二=大类全部（断网降级）。"""
    from mocks import make_song
    from maimai_py import Genre

    from nonebot_plugin_awmc_helper.core import songdb
    from nonebot_plugin_awmc_helper.core.combo import parse_combo

    monkeypatch.setattr(songdb, "_ongeki_origin_titles", None)
    song = make_song(2001, "STARTLINER", genre=Genre.オンゲキCHUNITHM, version=26000)
    assert songdb.ongeki_titles() == frozenset()
    assert not parse_combo("音击")[0].chart(song, None, 25500)
    assert parse_combo("中二")[0].chart(song, None, 25500)


# ---------------------------------------------------------------- P3：回到过去


@pytest.mark.parametrize(
    ("text", "expect"),
    [
        ("dx2024", [("era_year", "dx2024", 24000)]),
        ("舞萌dx2024", [("era_year", "舞萌dx2024", 24000)]),
        ("dx无印", [("era_year", "dx无印", 20000)]),
        ("雪辉dx2024", [("version", "雪辉", None), ("era_year", "dx2024", 24000)]),
    ],
)
def test_era_year_parse(text: str, expect):
    """回到过去各形态：dx2024/舞萌dxYYYY/裸年（numeric 语境）/dx无印。"""
    from nonebot_plugin_awmc_helper.core.combo import parse_combo

    conds = parse_combo(text)
    got = [(c.ctype.value, c.label, c.value) for c in conds]
    for (ctype, label, value), (g_ctype, g_label, g_value) in zip(got, expect):
        assert ctype == g_ctype
        assert label == g_label
        if g_value is not None:
            assert value == g_value


@pytest.mark.parametrize(
    ("text", "expect"),
    [
        ("dx2027", None),  # 未收录年份：token 丢弃 → 零条件静默
        # dx1999：1999 不匹配 20\d{2}，「dx」按残片语义保留为 S-3 世代条件
        ("dx1999", [("era", "dx", None)]),
        ("dx", [("era", "dx", None)]),  # 裸 dx = S-3 世代（与 S-2 异型）
    ],
)
def test_era_year_edges(text: str, expect):
    from nonebot_plugin_awmc_helper.core.combo import parse_combo

    conds = parse_combo(text)
    if expect is None:
        assert conds is None
    else:
        assert [(c.ctype.value, c.label) for c in conds] == [
            (t, lab) for t, lab, _ in expect
        ]


def test_era_year_predicate():
    """era 谓词：版本 ≤ 码（边界含码本身、PLUS 尾码排除）。"""
    from mocks import make_diff, make_song
    from maimai_py import Version

    from nonebot_plugin_awmc_helper.core.combo import parse_combo

    cond = parse_combo("dx2024")[0]
    assert cond.value == 24000
    song = make_song(300, "x", version=20000)
    assert cond.chart(song, make_diff(version=24000), 25500)
    assert not cond.chart(song, make_diff(version=24500), 25500)
    assert cond.chart(song, make_diff(version=19900), 25500)
    assert Version.MAIMAI_DX_BUDDIES.value == 24000


def test_era_year_pure_number_context():
    """裸年份数字不收（2026-09-30 拍板）：两种语境均静默，须带 dx 前缀。"""
    from nonebot_plugin_awmc_helper.core.combo import parse_combo

    assert parse_combo("2024") is None
    assert parse_combo("2024", numeric_level=True) is None
    assert parse_combo("2024进度", numeric_level=True) is None
    # 「2024b50」尾缀形态同样静默（条件串「2024b」无前缀不成回到过去）
    assert parse_combo("2024b") is None
    assert parse_combo("2027", numeric_level=True) is None
    assert parse_combo("1350", numeric_level=True) is None  # 非年份非等级
    assert parse_combo("13", numeric_level=True)[0].ctype.name == "LEVEL"


@pytest.mark.asyncio
async def test_run_combo_era_boundary_split(db, songs, monkeypatch):
    """dx2022：分界移到 22000——v20000 入 b35、v22000 入 b15（当年「现行」）。"""
    from mocks import make_diff, make_song
    from maimai_py import SongType, LevelIndex

    from nonebot_plugin_awmc_helper.core import combo as combo_mod
    from nonebot_plugin_awmc_helper.core.combo import ComboResult, parse_combo
    from nonebot_plugin_awmc_helper.core.songs import song_service

    await seed_service(
        song_service,
        [
            make_song(
                300,
                "边界旧曲",
                version=20000,
                genre=__import__("maimai_py", fromlist=["Genre"]).Genre.maimai,
                diffs=[
                    make_diff(
                        type=SongType.STANDARD,
                        level_index=LevelIndex.MASTER,
                        version=20000,
                        level_value=12.0,
                    )
                ],
            ),
            make_song(
                301,
                "边界新曲",
                version=22000,
                genre=__import__("maimai_py", fromlist=["Genre"]).Genre.maimai,
                diffs=[
                    make_diff(
                        type=SongType.STANDARD,
                        level_index=LevelIndex.MASTER,
                        version=22000,
                        level_value=13.0,
                    )
                ],
            ),
        ],
    )
    scores = [
        _score(300, type_=SongType.STANDARD, version=20000, achievements=100.0),
        _score(301, type_=SongType.STANDARD, version=22000, achievements=99.0),
    ]
    monkeypatch.setattr(combo_mod, "score_service", _FakeScoreService(scores))
    result = await combo_mod.run_combo(parse_combo("dx2022"), _binding())
    assert isinstance(result, ComboResult)
    assert result.flat is False  # 回到过去 → 恒拆分（覆盖谱面类性质）
    assert [s.id for s in result.bests.scores_b35] == [300]
    assert [s.id for s in result.bests.scores_b15] == [301]


@pytest.mark.asyncio
async def test_run_combo_era_forces_split_with_chart_cond(db, songs, monkeypatch):
    """东方dx2024：谱面类（genre）在场但 era 覆写 → 拆分而非平铺。"""
    from nonebot_plugin_awmc_helper.core import combo as combo_mod
    from nonebot_plugin_awmc_helper.core.combo import ComboResult, parse_combo

    scores = [_score(199, version=12000, achievements=99.0)]
    monkeypatch.setattr(combo_mod, "score_service", _FakeScoreService(scores))
    result = await combo_mod.run_combo(parse_combo("东方dx2024"), _binding())
    assert isinstance(result, ComboResult)
    assert result.flat is False


def test_era_year_applicability():
    """§9.7 D 行：era_year 只进 b50/b40，分数列表/完成表/定数表拒绝。"""
    from nonebot_plugin_awmc_helper.core.combo import (
        OutputKind,
        parse_combo,
        inapplicable,
    )

    assert inapplicable(parse_combo("dx2024"), OutputKind.B50) == []
    assert inapplicable(parse_combo("dx2024"), OutputKind.B40) == []
    assert [
        c.ctype.name for c in inapplicable(parse_combo("dx2024"), OutputKind.SCORE_LIST)
    ] == ["ERA_YEAR"]
    assert [
        c.ctype.name for c in inapplicable(parse_combo("dx2024"), OutputKind.TABLE)
    ] == ["ERA_YEAR"]
    assert [
        c.ctype.name for c in inapplicable(parse_combo("dx2024"), OutputKind.DS_TABLE)
    ] == ["ERA_YEAR"]


class _FakeState:
    def __init__(self, hist):
        self.hist = hist  # {(song_id, kind, level_id): [(version, ds), ...]}

    def resolve_chart_level(self, song_id, kind, level_id, version=None):
        pts = self.hist.get((song_id, kind, level_id), [])
        if version is not None:
            pts = [p for p in pts if p[0] <= version]
        return pts[-1][1] if pts else None


@pytest.mark.asyncio
async def test_run_combo_era_timepoint_ds(db, songs, monkeypatch):
    """回到过去 × 定数条件：DS 对比**时点定数**（现行 14.0、2020 时点 13.0 →
    「13定数」命中）；无历史（首变化点晚于分界）→ 视为未实装不命中。"""
    from mocks import make_diff, make_song
    from maimai_py import SongType

    from nonebot_plugin_awmc_helper.core import combo as combo_mod
    from nonebot_plugin_awmc_helper.core import songdb
    from nonebot_plugin_awmc_helper.core.combo import ComboResult, parse_combo
    from nonebot_plugin_awmc_helper.core.songs import song_service

    await seed_service(
        song_service,
        [
            make_song(
                300,
                "时点曲",
                version=20000,
                diffs=[
                    make_diff(
                        type=SongType.STANDARD,
                        level_index=__import__(
                            "maimai_py", fromlist=["LevelIndex"]
                        ).LevelIndex.MASTER,
                        version=20000,
                        level_value=14.0,  # 现行定数
                    )
                ],
            ),
        ],
    )
    fake = _FakeState(
        {(300, "sd", 3): [(20000, 12.8)]}  # 2020(20500) 时点为 12.8
    )

    async def fake_load():
        return fake

    monkeypatch.setattr(songdb.State, "load", fake_load)
    scores = [_score(300, type_=SongType.STANDARD, version=20000, achievements=97.0)]
    monkeypatch.setattr(combo_mod, "score_service", _FakeScoreService(scores))

    result = await combo_mod.run_combo(parse_combo("dx2020"), _binding())
    assert isinstance(result, ComboResult)
    assert [s.id for s in result.scores] == [300]  # era 只看版本，谱面在键集

    # 12.8定数 + dx2020：现行 14.0 不匹配，但**时点 12.8** 匹配 → 命中
    result = await combo_mod.run_combo(
        parse_combo("12.8定数dx2020", numeric_level=True), _binding()
    )
    assert isinstance(result, ComboResult)
    assert [s.id for s in result.scores] == [300]
    (hit,) = result.scores
    # modifier：定数与时点值替换 + 现行系数重算 RA（副本，原成绩不动）
    assert hit.level_value == 12.8
    from nonebot_plugin_awmc_helper.core.calc import compute_rating

    assert hit.dx_rating == compute_rating(12.8, 97.0)
    # 原成绩（ds 13.0）分毫未动，且时点定数 12.8 的 RA 更低
    assert scores[0].level_value == 13.0
    assert scores[0].dx_rating > hit.dx_rating

    # 14.5定数 + dx2020：时点 12.8 不匹配（现行 14.0 亦不匹配）→ 空键集
    from nonebot_plugin_awmc_helper.core.combo import ComboEmpty

    r2 = await combo_mod.run_combo(
        parse_combo("14.5定数dx2020", numeric_level=True), _binding()
    )
    assert isinstance(r2, ComboEmpty)

    # 无历史谱面（fake 表空）+ DS：视为未实装 → 键集空
    async def fake_load_empty():
        return _FakeState({})

    monkeypatch.setattr(songdb.State, "load", fake_load_empty)
    r3 = await combo_mod.run_combo(
        parse_combo("12.8定数dx2020", numeric_level=True), _binding()
    )
    assert isinstance(r3, ComboEmpty)


@pytest.mark.asyncio
async def test_run_combo_era_modifier_no_history_keeps_current(db, songs, monkeypatch):
    """无定数历史的谱面：时点 modifier 保持现行值（不替换不重算）。"""
    from mocks import make_diff, make_song
    from maimai_py import SongType

    from nonebot_plugin_awmc_helper.core import combo as combo_mod
    from nonebot_plugin_awmc_helper.core import songdb
    from nonebot_plugin_awmc_helper.core.combo import ComboResult, parse_combo
    from nonebot_plugin_awmc_helper.core.songs import song_service

    await seed_service(
        song_service,
        [
            make_song(
                300,
                "无历史曲",
                version=20000,
                diffs=[
                    make_diff(
                        type=SongType.STANDARD,
                        level_index=__import__(
                            "maimai_py", fromlist=["LevelIndex"]
                        ).LevelIndex.MASTER,
                        version=20000,
                        level_value=12.5,
                    )
                ],
            )
        ],
    )

    async def fake_load():
        return _FakeState({})  # 空历史表

    monkeypatch.setattr(songdb.State, "load", fake_load)
    scores = [_score(300, type_=SongType.STANDARD, version=20000, achievements=97.0)]
    monkeypatch.setattr(combo_mod, "score_service", _FakeScoreService(scores))
    result = await combo_mod.run_combo(parse_combo("dx2020"), _binding())
    assert isinstance(result, ComboResult)
    (s,) = result.scores
    assert s.level_value == 13.0  # 成绩原值保持（State 空历史 → 不替换）
    assert s.dx_rating == scores[0].dx_rating


# ---------------------------------------------------------------- P3：b40 旧系数


def test_old_ra_math():
    """FiNALE 旧系数精确值（KarenBot calcOld 同式）：floor(ds × 档位系数 ×
    min(100.5, 达成率) / 100)。"""
    from nonebot_plugin_awmc_helper.core.combo import _old_ra

    assert _old_ra(13.0, 100.5) == 182  # SSSP 14.0：13*14*1.005=182.91
    assert _old_ra(13.0, 100.7) == 182  # 万分位封顶 100.5
    assert _old_ra(10.0, 97.0) == 121  # S 12.5：10*12.5*0.97=121.25
    assert _old_ra(11.0, 60.0) == 39  # B 6.0：11*6*0.6=39.6
    assert _old_ra(14.0, 80.0) == 95  # A 8.5：14*8.5*0.8=95.2
    assert _old_ra(13.0, 49.9) == 0  # D 档系数 0
    assert _old_ra(13.5, 99.0) == 173  # SS 13.0：13.5*13*0.99=173.745


# ---------------------------------------------------------------- P3：b40 执行器


@pytest.mark.asyncio
async def test_run_combo_b40_split_caps(db, songs, monkeypatch):
    """b40：恒拆分 25/15（30 旧 + 20 新 → b35 槽 25、b15 槽 15）。"""
    from nonebot_plugin_awmc_helper.core import combo as combo_mod
    from nonebot_plugin_awmc_helper.core.combo import (
        OutputKind,
        ComboResult,
        parse_combo,
    )

    scores = [
        _score(1000 + i, version=12000, achievements=100.8 + i * 0.001)
        for i in range(30)
    ] + [
        _score(2000 + i, version=25500, achievements=100.8 + i * 0.001)
        for i in range(20)
    ]
    monkeypatch.setattr(combo_mod, "score_service", _FakeScoreService(scores))
    result = await combo_mod.run_combo(
        parse_combo("牛逼"), _binding(), output=OutputKind.B40
    )
    assert isinstance(result, ComboResult)
    assert result.flat is False  # b40 恒拆分（即便纯成绩类条件也是 25/15）
    assert len(result.bests.scores_b35) == 25
    assert len(result.bests.scores_b15) == 15
    # 旧系数重算：dx_rating 全为 FiNALE 口径（SSSP 系数 14.0，远小于现行 22.4）
    assert all(s.dx_rating < 200 for s in result.scores)
    assert result.total_ra == sum(s.dx_rating for s in result.scores)


@pytest.mark.asyncio
async def test_run_combo_b40_forces_split_with_chart_cond(db, songs, monkeypatch):
    """东方b40：谱面类条件在场仍恒拆分（b40 无平铺形态）。"""
    from nonebot_plugin_awmc_helper.core import combo as combo_mod
    from nonebot_plugin_awmc_helper.core.combo import (
        OutputKind,
        ComboResult,
        parse_combo,
    )

    scores = [_score(199, version=12000, achievements=99.9)]
    monkeypatch.setattr(combo_mod, "score_service", _FakeScoreService(scores))
    result = await combo_mod.run_combo(
        parse_combo("东方"), _binding(), output=OutputKind.B40
    )
    assert isinstance(result, ComboResult)
    assert result.flat is False
    assert [s.id for s in result.scores] == [199]


@pytest.mark.asyncio
async def test_run_combo_b40_era_combined(db, songs, monkeypatch):
    """dx2022b40：分界覆写 + 25/15 + 旧系数三重叠加。"""
    from mocks import make_diff, make_song
    from maimai_py import SongType, LevelIndex

    from nonebot_plugin_awmc_helper.core import combo as combo_mod
    from nonebot_plugin_awmc_helper.core.combo import (
        OutputKind,
        ComboResult,
        parse_combo,
    )
    from nonebot_plugin_awmc_helper.core.songs import song_service

    await seed_service(
        song_service,
        [
            make_song(
                300,
                "旧曲",
                version=20000,
                diffs=[
                    make_diff(
                        type=SongType.STANDARD,
                        level_index=LevelIndex.MASTER,
                        version=20000,
                        level_value=12.0,
                    )
                ],
            ),
            make_song(
                301,
                "当年新曲",
                version=22000,
                diffs=[
                    make_diff(
                        type=SongType.STANDARD,
                        level_index=LevelIndex.MASTER,
                        version=22000,
                        level_value=13.0,
                    )
                ],
            ),
        ],
    )
    scores = [
        _score(
            300,
            type_=SongType.STANDARD,
            version=20000,
            achievements=100.5,
            level_value=12.0,
        ),
        _score(
            301,
            type_=SongType.STANDARD,
            version=22000,
            achievements=100.5,
            level_value=13.0,
        ),
    ]
    monkeypatch.setattr(combo_mod, "score_service", _FakeScoreService(scores))
    result = await combo_mod.run_combo(
        parse_combo("dx2022b"), _binding(), output=OutputKind.B40
    )
    assert isinstance(result, ComboResult)
    assert result.flat is False
    assert [s.id for s in result.bests.scores_b35] == [300]
    assert [s.id for s in result.bests.scores_b15] == [301]
    # 全部旧系数（SSSP@100.5 → ds×14.0×1.005，floor）
    assert result.bests.scores_b35[0].dx_rating == int(12.0 * 14.0 * 1.005) == 168
    assert result.bests.scores_b15[0].dx_rating == int(13.0 * 14.0 * 1.005) == 182


@pytest.mark.asyncio
async def test_run_combo_b40_ideal_chain(db, songs, monkeypatch):
    """理想b40：理想升档后按旧系数重算（副本链）。"""
    from maimai_py import RateType

    from nonebot_plugin_awmc_helper.core import combo as combo_mod
    from nonebot_plugin_awmc_helper.core.combo import OutputKind, parse_combo

    s = _score(199, version=12000, achievements=99.0, level_value=13.0)  # SS
    monkeypatch.setattr(combo_mod, "score_service", _FakeScoreService([s]))
    result = await combo_mod.run_combo(
        parse_combo("理想"), _binding(), output=OutputKind.B40
    )
    (hit,) = result.scores
    assert hit.rate == RateType.SSP  # 理想升档
    assert hit.achievements == 99.5
    assert hit.dx_rating == int(13.0 * 13.2 * 0.995)  # SSP 系数 13.2 旧口径
    assert s.dx_rating != hit.dx_rating


def test_b40_applicability_mirrors_b50():
    """§9.7 b50/40 同列：B1/B2/C 类条件对 b40 全部适用。"""
    from nonebot_plugin_awmc_helper.core.combo import (
        OutputKind,
        parse_combo,
        inapplicable,
    )

    assert inapplicable(parse_combo("fc寸理想拟合"), OutputKind.B40) == []
    assert inapplicable(parse_combo("五星"), OutputKind.B40) == []


# --------------------------------------------- P3：同型 OR（§9.0 修正回归锁）


@pytest.mark.asyncio
async def test_same_type_record_conds_or(db, songs, monkeypatch):
    """牛逼越级：同 CondType → OR（≥100.8 ∪ <95；98 双不中排除）。"""
    from nonebot_plugin_awmc_helper.core import combo as combo_mod
    from nonebot_plugin_awmc_helper.core.combo import ComboResult, parse_combo

    scores = [
        _score(1, version=12000, achievements=101.0),  # 牛逼
        _score(2, version=12000, achievements=90.0),  # 越级
        _score(3, version=12000, achievements=98.0),  # 双不中
    ]
    monkeypatch.setattr(combo_mod, "score_service", _FakeScoreService(scores))
    result = await combo_mod.run_combo(parse_combo("牛逼越级"), _binding())
    assert isinstance(result, ComboResult)
    assert {s.id for s in result.scores} == {1, 2}


@pytest.mark.asyncio
async def test_same_type_chart_conds_or(db, songs, monkeypatch):
    """祝x雪（残片分隔双版本段）：同 CondType → OR（祝∪雪 并集切片）。"""
    from mocks import make_diff, make_song
    from maimai_py import SongType, LevelIndex

    from nonebot_plugin_awmc_helper.core import combo as combo_mod
    from nonebot_plugin_awmc_helper.core.combo import ComboResult, parse_combo
    from nonebot_plugin_awmc_helper.core.songs import song_service

    await seed_service(
        song_service,
        [
            make_song(
                300,
                "祝曲",
                version=23000,
                diffs=[
                    make_diff(
                        type=SongType.STANDARD,
                        level_index=LevelIndex.MASTER,
                        version=23000,
                        level_value=13.0,
                    )
                ],
            ),
            make_song(
                301,
                "雪曲",
                version=19500,
                diffs=[
                    make_diff(
                        type=SongType.STANDARD,
                        level_index=LevelIndex.MASTER,
                        version=19500,
                        level_value=12.0,
                    )
                ],
            ),
            make_song(
                302,
                "双曲",
                version=24000,
                diffs=[
                    make_diff(
                        type=SongType.STANDARD,
                        level_index=LevelIndex.MASTER,
                        version=24000,
                        level_value=13.5,
                    )
                ],
            ),
        ],
    )
    scores = [
        _score(300, type_=SongType.STANDARD, version=23000, achievements=99.0),
        _score(301, type_=SongType.STANDARD, version=19500, achievements=99.0),
        _score(302, type_=SongType.STANDARD, version=24000, achievements=99.0),
    ]
    monkeypatch.setattr(combo_mod, "score_service", _FakeScoreService(scores))
    result = await combo_mod.run_combo(parse_combo("祝x雪"), _binding())
    assert isinstance(result, ComboResult)
    # 祝(23000) ∪ 雪(19900)；24000 不属于任何一侧（AND 语义会得空集）
    assert {s.id for s in result.scores} == {300, 301}
    assert result.flat is True


@pytest.mark.asyncio
async def test_cross_type_still_and(db, songs, monkeypatch):
    """跨型 AND 不受分组影响：东方神 = 东方曲 ∩ AP 族。"""
    from maimai_py import FCType

    from nonebot_plugin_awmc_helper.core import combo as combo_mod
    from nonebot_plugin_awmc_helper.core.combo import ComboResult, parse_combo

    scores = [
        _score(199, version=12000, achievements=100.5, fc=FCType.AP),  # 东方 ∩ AP ✓
        _score(624, version=18500, achievements=100.5, fc=FCType.AP),  # AP 但非东方
        _score(8, version=10000, achievements=100.5, fc=FCType.AP),  # maimai 非东方
    ]
    monkeypatch.setattr(combo_mod, "score_service", _FakeScoreService(scores))
    result = await combo_mod.run_combo(parse_combo("东方神"), _binding())
    assert isinstance(result, ComboResult)
    assert {s.id for s in result.scores} == {199}


@pytest.mark.asyncio
async def test_same_type_star_conds_or(db, songs, monkeypatch):
    """三星五星：STAR 同型 OR（3 或 5 星命中，4 星排除）。"""
    from nonebot_plugin_awmc_helper.core import combo as combo_mod
    from nonebot_plugin_awmc_helper.core.combo import ComboResult, parse_combo

    scores = [
        _score(1, version=12000, achievements=99.0, dx_star=3),
        _score(2, version=12000, achievements=99.0, dx_star=4),
        _score(3, version=12000, achievements=99.0, dx_star=5),
    ]
    monkeypatch.setattr(combo_mod, "score_service", _FakeScoreService(scores))
    result = await combo_mod.run_combo(parse_combo("三星五星"), _binding())
    assert isinstance(result, ComboResult)
    assert {s.id for s in result.scores} == {1, 3}


# ---------------------------------------------------------------- 边界补强


def test_rate_boundary_semantics():
    """评级档边界：ge 包含式按 _from_achievement 阈值；纯/仅 严格等于。"""
    from nonebot_plugin_awmc_helper.core.combo import parse_combo

    sss = parse_combo("sss")[0]
    assert sss.record(_score(1, achievements=100.0))  # SSS 阈值含
    assert not sss.record(_score(1, achievements=99.9999))  # SSP 不算
    pure_s = parse_combo("纯s")[0]
    assert pure_s.record(_score(1, achievements=97.5))
    assert not pure_s.record(_score(1, achievements=98.0))  # SP 不算「纯S」
    assert not pure_s.record(_score(1, achievements=96.5))


def test_cun_kill_boundaries():
    """寸/名刀万分位边界（§9 S-17/18 定稿区间，含半开衔接）。"""
    from nonebot_plugin_awmc_helper.core.combo import parse_combo

    cun = parse_combo("寸")[0].record
    kill = parse_combo("锁")[0].record
    # 寸 [99.9,100) ∪ [100.45,100.5)
    assert cun(_score(1, achievements=99.9))
    assert not cun(_score(1, achievements=99.8999))
    assert cun(_score(1, achievements=99.9999))
    assert not cun(_score(1, achievements=100.0))  # 里程碑归锁不归寸
    assert not cun(_score(1, achievements=100.4499))
    assert cun(_score(1, achievements=100.45))
    assert not cun(_score(1, achievements=100.5))
    # 名刀 [100,100.1) ∪ [100.5,100.55)
    assert kill(_score(1, achievements=100.0))
    assert kill(_score(1, achievements=100.0999))
    assert not kill(_score(1, achievements=100.1))
    assert kill(_score(1, achievements=100.5))
    assert kill(_score(1, achievements=100.5499))
    assert not kill(_score(1, achievements=100.55))


@pytest.mark.asyncio
async def test_cun_sort_by_distance(db, songs, monkeypatch):
    """寸排序覆盖：距目标线近者在前（100.49 距 0.01 < 99.95 距 0.05）。"""
    from nonebot_plugin_awmc_helper.core import combo as combo_mod
    from nonebot_plugin_awmc_helper.core.combo import parse_combo

    scores = [
        _score(1, version=12000, achievements=99.95),  # 距 100.0 = 0.05
        _score(2, version=12000, achievements=100.49),  # 距 100.5 = 0.01
        _score(3, version=12000, achievements=99.91),  # 距 100.0 = 0.09
    ]
    monkeypatch.setattr(combo_mod, "score_service", _FakeScoreService(scores))
    result = await combo_mod.run_combo(parse_combo("寸"), _binding())
    assert [s.id for s in result.scores] == [2, 1, 3]


def test_version_run_purple_white_context():
    """段长 ≥2 的紫白 = 版本成员（上下文消歧，非歧义中止）。"""
    from maimai_py import Version

    from nonebot_plugin_awmc_helper.core.combo import parse_combo

    conds = parse_combo("紫白")
    assert len(conds) == 1
    assert conds[0].ctype.name == "VERSION"
    assert conds[0].value == {Version.MAIMAI_MURASAKI.value, Version.MAIMAI_MILK.value}


def test_ideal_sssp_cap():
    """理想 SSSP 封顶 = 理论值（101/AP+）。"""
    from maimai_py import FCType, RateType

    from nonebot_plugin_awmc_helper.core.combo import _ideal_of, parse_combo

    s = _score(1, achievements=100.5)  # 已 SSSP
    ideal = _ideal_of(s)
    assert ideal.achievements == 101.0
    assert ideal.fc == FCType.APP
    assert ideal.rate == RateType.SSSP
    # 记录行为：理想条件与 modifier 直调同源
    assert parse_combo("理想")[0].modifier is _ideal_of


def test_tokenizer_longest_match():
    """层内最长命中：sssp 单条件不被 sss+s 拆分；鸟加不被鸟拆。"""
    from nonebot_plugin_awmc_helper.core.combo import parse_combo

    assert [c.label for c in parse_combo("sssp")] == ["SSS+"]
    assert [c.label for c in parse_combo("鸟加")] == ["SSS+"]
    # fcap：fc + ap 两个连击族条件（同型 OR，fc_all ⊃ ap_all → 等价 fc）
    conds = parse_combo("fcap")
    assert [c.key for c in conds] == ["fc_all", "ap_all"]


def test_version_run_dedup_chars():
    """版本段内重复字去重：辉辉 = 辉 单码集。"""
    from maimai_py import Version

    from nonebot_plugin_awmc_helper.core.combo import parse_combo

    conds = parse_combo("辉辉")
    assert conds[0].value == {Version.MAIMAI_FINALE.value}


@pytest.mark.asyncio
async def test_run_combo_b40_excludes_utage(db, songs, monkeypatch):
    """b40 默认排除宴谱成绩（与 b50 同口径）。"""
    from maimai_py import SongType, LevelIndex

    from nonebot_plugin_awmc_helper.core import combo as combo_mod
    from nonebot_plugin_awmc_helper.core.combo import OutputKind, parse_combo

    utage = _score(
        100199,
        type_=SongType.UTAGE,
        level_index=LevelIndex.BASIC,
        version=24000,
        achievements=100.85,
    )
    normal = _score(199, version=12000, achievements=100.85)
    monkeypatch.setattr(combo_mod, "score_service", _FakeScoreService([utage, normal]))
    result = await combo_mod.run_combo(
        parse_combo("牛逼"), _binding(), output=OutputKind.B40
    )
    assert [s.id for s in result.scores] == [199]


# ---------------------------------------------------------------- at 段位置


def test_parse_combo_strips_at_segments():
    """at 夹在条件词中间：昵称里的条件字不污染解析（雪/神/将等单字）。"""
    from nonebot_plugin_awmc_helper.core.combo import parse_combo

    # 中间 at：条件串会捕获 CQ 码全文，剥除后仅剩东方
    conds = parse_combo("东方[CQ:at,qq=99999999,name=雪] ")
    assert [(c.ctype.value, c.label) for c in conds] == [("genre", "东方")]
    # 昵称含多字条件词同样不污染
    conds = parse_combo("东方[CQ:at,qq=99999999,name=大将]50")
    assert [(c.ctype.value, c.label) for c in conds] == [("genre", "东方")]
    # 纯 at 串 → 零条件静默
    assert parse_combo("[CQ:at,qq=99999999,name=雪]") is None
    # 无 at 的输入不受影响
    assert [(c.ctype.value, c.label) for c in parse_combo("东方50")] == [
        ("genre", "东方")
    ]


def test_numeric_level_progress_context():
    """进度/完成表语境裸数字等级：14+sss+ 双条件（等级 14+ ∩ SSS+）。

    回归锁：进度语境曾把 numeric_level 写成 False——「14+」被丢弃，整表
    退化为纯 sss+ 过滤（用户实测 14.0~14.5 曲混入）。
    """
    from nonebot_plugin_awmc_helper.core.combo import parse_combo

    conds = parse_combo("14+sss+", numeric_level=True)
    assert [(c.ctype.name, c.label) for c in conds] == [
        ("LEVEL", "14+级"),
        ("RATE", "SSS+"),
    ]
    # 与完成表语境同解析（「完成表」尾缀由 matcher 剥离后传入）
    assert [(c.ctype, c.key) for c in parse_combo("14+sss+", numeric_level=True)] == [
        (c.ctype, c.key) for c in conds
    ]


def test_parse_exact_match_only():
    """精确匹配口径（2026-10-01 拍板）：条件串出现第一个未匹配汉字即静默
    ——闲聊长句偶然含条件字不触发（英文/数字残渣不拒）。"""
    from nonebot_plugin_awmc_helper.core.combo import parse_combo

    # 用户实测案例：长句尾缀 50 + 句中「彩代」条件字
    assert parse_combo("你把所有版本名除彩代打一遍+") is None
    # 首汉字 junk 即静默（「来点」「帮我看看」类动词前缀）
    assert parse_combo("来点东方") is None
    assert parse_combo("帮我看看东方完成表", numeric_level=True) is None
    # 「更新」不再含单字新条件词（单字新/旧已收窄）
    assert parse_combo("更新") is None
    # 合法形态不受影响
    assert parse_combo("东方") is not None
    assert parse_combo("雪辉dx") is not None
    # 英文/数字残渣不拒：dx2024b50 尾缀 b 的吸收机制
    assert parse_combo("dx2024b") is not None
