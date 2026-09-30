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
    """规则表逐条正例：kind 序列即词法形态（同层最长、层小优先）。"""
    from nonebot_plugin_awmc_helper.core.combo import tokenize

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
    """裸紫/白 → 歧义中止；紫代/白谱/紫将（牌绑定）豁免。"""
    from nonebot_plugin_awmc_helper.core.combo import ComboAmbiguity, parse_combo

    assert isinstance(parse_combo("紫"), ComboAmbiguity)
    assert isinstance(parse_combo("白"), ComboAmbiguity)
    assert not isinstance(parse_combo("紫代"), ComboAmbiguity)
    assert not isinstance(parse_combo("白谱"), ComboAmbiguity)
    assert not isinstance(parse_combo("紫将"), ComboAmbiguity)


def test_parse_version_segment():
    """版本字连续段：段内多版本 OR 成一个 Cond；真=初+真两码。"""
    from maimai_py import Version

    from nonebot_plugin_awmc_helper.core.combo import parse_combo

    conds = parse_combo("雪辉")
    assert len(conds) == 1
    codes = conds[0].value
    assert codes == {Version.MAIMAI_MILK_PLUS.value, Version.MAIMAI_FINALE.value}
    assert Version.MAIMAI.value in parse_combo("真")[0].value
    assert Version.MAIMAI_PLUS.value in parse_combo("真")[0].value


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

    # fc 族：包含式（FC/FCP/AP/APP 全算），与旧 _plan_checker("fc") 同
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


def test_plate_shape():
    """牌子形状检测：牌组合文本 → (版本, 牌种)；非牌形状 → None。

    形状与合法性两段式——「真将」形状成立但牌单无此牌，由调用方拒绝
    （保持旧「没有找到牌子」文案），不落入条件分解。
    """
    from nonebot_plugin_awmc_helper.core.plates import norm_plate, is_valid_plate
    from nonebot_plugin_awmc_helper.plugins.tables.sheet import _plate_shape

    assert _plate_shape("祝将") == ("祝", "将")
    assert _plate_shape("舞神") == ("舞", "神")
    assert _plate_shape("樱舞舞") == ("樱", "舞舞")
    assert _plate_shape("暁極") == ("暁", "極")  # 形状层不归一
    assert _plate_shape("辉") is None  # 无牌种
    assert _plate_shape("东方") is None  # 非牌文本
    # 合法性校验（归一后按牌单例外表）
    ver, kind = _plate_shape("暁極")
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
