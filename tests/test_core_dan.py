"""core/dan：gallery 解析 / 入库 / join / 查询 API 测试。

yaml 片段内联（真实快照口径构造：段名/规则/谱面行均与线上格式同构）；
入库走 tmp 库；玩家成绩路径不触网（未绑定降级口径断言）。
"""

from pathlib import Path

import pytest
from mocks import requires_assets

# 内联 fixture：MAGiCAL 段位表（初段/十段/裏皆伝三段即可）+ 随机段位一档 +
# 海外版（应跳过）+ 朋友对战（应跳过）。谱面行取真实快照曲（PANDORA PARADOXXX）。
FIXTURE = """
- title: MAGiCAL 段位認定
  id: magical-dan
  sections:
    - &magical-1-dan
      title: 【初段】
      description: ❤ 350｜-0/-2/-5｜+20
      sheets:
        - 魂のルフラン|dx|basic
        - WARNING×WARNING×WARNING|dx|basic
        - オーバーライド|dx|basic
        - その群青が愛しかったようだった|std|basic
    - &magical-10-dan
      title: 【十段】
      description: ❤ 900｜-2/-2/-5｜+30
      sheets:
        - Nyan Cat EX|std|master
        - LiftOff|dx|master
        - Destiny Runner|dx|master
        - るろうらんる|dx|master
    - &magical-ura
      title: 【裏皆伝】
      description: ❤ 10｜-1/-3/-10｜+0
      sheets:
        - PANDORA PARADOXXX|std|remaster
        - 存在しない曲名|dx|master
- title: MAGiCAL オトモダチ対戦
  id: magical-otomodachi
  sections:
    - title: 【A5~A1】
      sheets:
        - 明星ロケット|std|expert
- title: MAGiCAL 段位認定（海外版）
  id: magical-dan-intl
  sections:
    - *magical-1-dan
- title: ランダム段位認定
  id: random-dan
  sections:
    - title: 【MASTER 超上級】
      description: ❤ 100｜-2/-3/-5｜+10
      sheets:
        - ランダムで選曲されます|rnd|master|14~14+
        - ランダムで選曲されます|rnd|master|14~14+
        - ランダムで選曲されます|rnd|master|14~14+
        - ランダムで選曲されます|rnd|master|14~14+
"""


@pytest.fixture
async def tmp_db(tmp_path: Path):
    from nonebot_plugin_awmc_helper.core import store

    store.set_db_file(tmp_path / "awmc.db")
    await store.init_db()
    yield store
    store.set_db_file(None)


@pytest.mark.asyncio
async def test_parse_gallery_filters_and_values():
    """解析：日服段位表/随机段位收入，海外版/朋友对战跳过；数值逐项断言。"""
    from nonebot_plugin_awmc_helper.core import dan

    courses, randoms = dan.parse_gallery(FIXTURE)
    assert [c.gallery_id for c in courses] == ["magical-dan"]
    course = courses[0]
    assert (course.kind, course.version) == ("normal", 27000)
    assert [g.dan_id for g in course.grades] == ["1dan", "10dan", "ura_kaiden"]
    ura = course.grades[2]
    assert (ura.life, ura.damage_great, ura.damage_good, ura.damage_miss) == (
        10,
        1,
        3,
        10,
    )
    assert ura.clear_bonus == 0
    assert ura.sheets[0].title == "PANDORA PARADOXXX"
    assert (ura.sheets[0].kind, ura.sheets[0].difficulty) == ("std", "remaster")

    assert len(randoms) == 1
    r = randoms[0]
    assert (r.dan_id, r.difficulty, r.name_ja) == (
        "random_master_4",
        "master",
        "超上級",
    )
    assert (r.level_range, r.ds_lo, r.ds_hi, r.ds_source) == (
        "14~14+",
        14.5,
        14.9,
        "measured",
    )
    assert (r.life, r.damage_miss, r.clear_bonus) == (100, 5, 10)


@pytest.mark.asyncio
async def test_parse_gallery_rejects_bad_format():
    """规则行/段名/谱面行格式异常必须显式失败（上游格式变化禁止静默）。"""
    from nonebot_plugin_awmc_helper.core import dan

    with pytest.raises(ValueError, match="血量规则不匹配"):
        dan.parse_gallery(FIXTURE.replace("❤ 10｜-1/-3/-10｜+0", "LIFE 10"))
    with pytest.raises(ValueError, match="未知段位名"):
        dan.parse_gallery(FIXTURE.replace("【裏皆伝】", "【裏皆传递】"))
    with pytest.raises(ValueError, match="谱面行格式异常"):
        dan.parse_gallery(
            FIXTURE.replace("PANDORA PARADOXXX|std|remaster", "P|utage|master")
        )
    with pytest.raises(ValueError, match="无法识别段位表版本"):
        dan.parse_gallery(
            "- title: 未知版本 段位認定\n  id: x-dan\n  sections:\n"
            "    - title: 【初段】\n      description: ❤ 1｜-0/-0/-0｜+0\n"
            "      sheets:\n        - A|dx|basic\n"
        )


@pytest.mark.asyncio
async def test_refresh_roundtrip_and_join(tmp_db):
    """入库 + join：命中曲回填 song_id；削除曲（无此曲名）置 None 不抛。"""
    from nonebot_plugin_awmc_helper.core import dan
    from nonebot_plugin_awmc_helper.core.store import (
        SongRow,
        SongChart,
        SongSheetGroup,
    )

    async with tmp_db.session() as db:
        db.add(SongRow(id=834, title="PANDORA PARADOXXX", bpm="150"))
        db.add(SongSheetGroup(song_id=834, kind="sd", version=27000))
        db.add(SongChart(song_id=834, kind="sd", level_id=4, notes_tap=1))
        await db.commit()

    stats = await dan.refresh(text=FIXTURE)
    assert stats == {"courses": 1, "randoms": 1, "sheets": 10}

    assert await dan.latest_gallery_id() == "magical-dan"
    grades = await dan.grades_of("magical-dan")
    assert [g.dan_id for g, _ in grades] == ["1dan", "10dan", "ura_kaiden"]
    ura_grade, ura_sheets = grades[2]
    assert ura_grade.life == 10
    # PANDORA PARADOXXX std/remaster 已种 → 命中 834；「存在しない曲名」→ None
    assert ura_sheets[0].song_id == 834
    assert ura_sheets[1].song_id is None
    # 海外版/朋友对战不入库
    assert await dan.find_grade_row("magical-dan-intl", "1dan") is None
    tiers = await dan.random_tiers()
    assert len(tiers) == 1
    assert tiers[0].dan_id == "random_master_4"


@pytest.mark.asyncio
async def test_card_data_degrades_without_binding(tmp_db):
    """未绑定（无成绩数据）降级：达成率 0.0、底分「0」无括号、削除曲全 fallback。"""
    from nonebot_plugin_awmc_helper.core import dan
    from nonebot_plugin_awmc_helper.core.store import (
        SongRow,
        SongChart,
        SongChartLevel,
        SongSheetGroup,
    )

    async with tmp_db.session() as db:
        db.add(SongRow(id=834, title="PANDORA PARADOXXX", bpm="150"))
        db.add(SongSheetGroup(song_id=834, kind="sd", version=27000))
        db.add(
            SongChart(
                song_id=834,
                kind="sd",
                level_id=4,
                designer="PANDORA PARADOXXX",
                notes_tap=1,
            )
        )
        db.add(
            SongChartLevel(
                song_id=834,
                kind="sd",
                level_id=4,
                version=27000,
                level_value=14.9,
            )
        )
        await db.commit()

    await dan.refresh(text=FIXTURE)
    data = await dan.card_data("magical-dan", "ura_kaiden", binding=None)
    assert data is not None
    assert (data.life, data.damage_miss, data.clear_bonus) == (10, 10, 0)
    hit, miss = data.songs[0], data.songs[1]
    assert (hit.song_id, hit.achievement, hit.base_score) == (834, 0.0, "0")
    assert hit.level == "14+"
    assert hit.ds == "14.9"
    assert hit.charter == "PANDORA PARADOXXX"
    assert miss.song_id is None
    assert miss.level == "-"
    assert miss.ds == "-"


@pytest.mark.asyncio
@requires_assets
async def test_render_smoke_from_card_data(tmp_db):
    """card_data → render_dan_card 冒烟：渲染可执行（需本地素材包 static/）。"""
    from nonebot_plugin_awmc_helper.core import dan
    from nonebot_plugin_awmc_helper.core.store import (
        SongRow,
        SongChart,
        SongChartLevel,
        SongSheetGroup,
    )
    from nonebot_plugin_awmc_helper.core.render.dan import render_dan_card

    async with tmp_db.session() as db:
        db.add(SongRow(id=834, title="PANDORA PARADOXXX", bpm="150"))
        db.add(SongSheetGroup(song_id=834, kind="sd", version=27000))
        db.add(
            SongChart(
                song_id=834,
                kind="sd",
                level_id=4,
                designer="PANDORA PARADOXXX",
                notes_tap=1,
            )
        )
        db.add(
            SongChartLevel(
                song_id=834,
                kind="sd",
                level_id=4,
                version=27000,
                level_value=14.9,
            )
        )
        await db.commit()

    await dan.refresh(text=FIXTURE)
    data = await dan.card_data("magical-dan", "ura_kaiden", binding=None)
    assert render_dan_card(data).startswith(b"\x89PNG")


@pytest.mark.asyncio
async def test_card_data_random_sampling(tmp_db):
    """随机档位真实抽曲：候选限定档位定数区间（master 含 Re:MASTER），
    独立抽取可重复、恒四行；区间外谱面不会出现。"""
    from nonebot_plugin_awmc_helper.core import dan
    from nonebot_plugin_awmc_helper.core.store import (
        SongRow,
        SongChart,
        SongChartLevel,
        SongSheetGroup,
    )

    async with tmp_db.session() as db:
        # 候选：master 14.9 / remaster 14.8（区间内），master 14.0（区间外）
        for sid, title, kind, lid, ds in (
            (1818, "World's end BLACKBOX", "dx", 3, 14.9),
            (834, "PANDORA PARADOXXX", "sd", 4, 14.8),
            (1400, "区间外曲", "dx", 3, 14.0),
        ):
            db.add(SongRow(id=sid, title=title, bpm="150"))
            db.add(
                SongSheetGroup(
                    song_id=sid,
                    kind=kind,
                    version=27000,
                    version_cn=27000,
                )
            )
            db.add(SongChart(song_id=sid, kind=kind, level_id=lid, notes_tap=1))
            db.add(
                SongChartLevel(
                    song_id=sid, kind=kind, level_id=lid, version=27000, level_value=ds
                )
            )
        await db.commit()

    await dan.refresh(text=FIXTURE)
    data = await dan.card_data(None, "random_master_4")
    assert data is not None
    assert (data.life, data.damage_good, data.clear_bonus) == (100, 3, 10)
    assert len(data.songs) == 4
    allowed = {1818, 834}
    assert all(s.song_id in allowed for s in data.songs)
    assert all(s.ds in ("14.9", "14.8") for s in data.songs)
    # 重复抽取消融：全部行同曲也合法（独立抽取），这里只断言数量与域
    assert all(s.kind in ("std", "dx") for s in data.songs)


def test_level_str_boundaries():
    """定数→标级映射边界：N.5→N、N.6→N+（浮点 14.6-14 坑回归）。"""
    from nonebot_plugin_awmc_helper.core.dan import _level_str

    assert _level_str(14.5) == "14"
    assert _level_str(14.6) == "14+"
    assert _level_str(12.6) == "12+"
    assert _level_str(13.9) == "13+"
    assert _level_str(15.0) == "15"
    assert _level_str(7.0) == "7"


def test_resolve_dan_id_aliases():
    """段位别名解析：普通/真段位、随机档位（难度颜色替代/省略级/空格/大小写）。"""
    from nonebot_plugin_awmc_helper.plugins.dan.matchers import _resolve_dan_id

    assert _resolve_dan_id("十段") == "10dan"
    assert _resolve_dan_id("裏皆伝") == "ura_kaiden"
    assert _resolve_dan_id("里皆传") == "ura_kaiden"
    assert _resolve_dan_id("MASTER 超上级") == "random_master_4"
    assert _resolve_dan_id("紫超上") == "random_master_4"
    assert _resolve_dan_id("master超上級") == "random_master_4"
    assert _resolve_dan_id("红中级") == "random_expert_2"
    assert _resolve_dan_id("EXPERT 上级") == "random_expert_3"
    assert _resolve_dan_id("随机") == "random"
    assert _resolve_dan_id("紫") is None  # 缺档名
    assert _resolve_dan_id("超上级") is None  # 缺难度
    assert _resolve_dan_id("不存在的段位") is None


def test_parse_version_prefix():
    """版本前缀：完整名/前 n≥3 字符 + 可选 +、dx 系 2 字符、大小写、国服形式。"""
    from nonebot_plugin_awmc_helper.core.dan import parse_version_prefix

    cases = {
        "dx": (20000, ""),
        "dx+": (20500, ""),
        "DX十段": (20000, "十段"),
        "bud+": (24500, ""),
        "bud十段": (24000, "十段"),
        "BUD+裏皆传": (24500, "裏皆传"),
        "uni+": (22500, ""),
        "universe十段": (22000, "十段"),
        "mag十段": (27000, "十段"),
        "MAGiCAL 段位": (27000, " 段位"),
        "spl+": (21500, ""),
        "cir十段": (26000, "十段"),
        "fes+十段": (23500, "十段"),
        "舞萌dx初段": (20000, "初段"),
        "舞萌dx2022十段": (21500, "十段"),
        "舞萌2022十段": (21500, "十段"),
        "dx2022十段": (21500, "十段"),
        "2022十段": (21500, "十段"),
        "dx无印十段": (20000, "十段"),
        "十段": (None, "十段"),
    }
    for arg, (code, rest) in cases.items():
        assert parse_version_prefix(arg) == (code, rest), arg
    with pytest.raises(ValueError, match="无 PLUS"):
        parse_version_prefix("mag+十段")


@pytest.mark.asyncio
async def test_cross_version_course_lookup(tmp_db):
    """跨版本：按版本码查段位表；无数据版本返回 None（渲染层给引导文案）。"""
    from nonebot_plugin_awmc_helper.core import dan
    from nonebot_plugin_awmc_helper.core.store import DanGrade, DanSheet, DanCourse

    await dan.refresh(text=FIXTURE)  # magical-dan 27000（先刷新，后种旧版本）
    async with tmp_db.session() as db:
        db.add(DanCourse(gallery_id="circle-dan", kind="normal", version=26000))
        db.add(
            DanGrade(
                gallery_id="circle-dan",
                dan_id="10dan",
                sort=9,
                name_ja="十段",
                life=900,
                damage_great=2,
                damage_good=2,
                damage_miss=5,
                clear_bonus=30,
            )
        )
        db.add(
            DanSheet(
                gallery_id="circle-dan",
                dan_id="10dan",
                idx=0,
                title="X",
                kind="dx",
                difficulty="master",
            )
        )
        await db.commit()

    assert await dan.course_id_by_version(26000) == "circle-dan"
    assert await dan.course_id_by_version(20000) is None

    data = await dan.card_data("circle-dan", "10dan")
    assert data is not None
    assert data.songs[0].title == "X"
    # 版本前缀解析 + 当前版本混用不串
    code, rest = dan.parse_version_prefix("cir十段")
    assert rest == "十段"
    assert await dan.course_id_by_version(code) == "circle-dan"


@pytest.mark.asyncio
async def test_random_version_and_region_scope(tmp_db, monkeypatch):
    """随机抽曲：版本范围（到该版本为止）与数据源区域（CN/JP 视图）过滤。"""
    from types import SimpleNamespace

    from nonebot_plugin_awmc_helper.core import dan
    from nonebot_plugin_awmc_helper.core.score import UserScoreError
    from nonebot_plugin_awmc_helper.core.store import (
        SongRow,
        SongChart,
        SongChartLevel,
        SongSheetGroup,
    )

    async with tmp_db.session() as db:
        # JP-only 新曲（26000，CN 无）：CN 区域任何范围都不应抽出
        for sid, title, kind, lid, ds, ver, ver_cn in (
            (1, "JP新曲", "dx", 3, 14.7, 26000, None),
            (2, "老曲双域", "dx", 3, 14.6, 24500, 24500),
            (3, "更老曲双域", "sd", 4, 14.5, 24000, 24000),
        ):
            db.add(SongRow(id=sid, title=title, bpm="150"))
            db.add(
                SongSheetGroup(
                    song_id=sid,
                    kind=kind,
                    version=ver,
                    version_cn=ver_cn,
                )
            )
            db.add(SongChart(song_id=sid, kind=kind, level_id=lid, notes_tap=1))
            db.add(
                SongChartLevel(
                    song_id=sid,
                    kind=kind,
                    level_id=lid,
                    version=ver,
                    level_value=ds,
                )
            )
        await db.commit()

    await dan.refresh(text=FIXTURE)

    from nonebot_plugin_awmc_helper.core.score import score_service

    async def _boom(*a, **k):
        raise UserScoreError("offline")

    monkeypatch.setattr(score_service, "get_scores_all", _boom)
    monkeypatch.setattr(score_service, "get_b50", _boom)

    cn = SimpleNamespace(service="divingfish")
    jp = SimpleNamespace(service="net")

    def sampled_union(binding, **kw) -> set[int]:
        """多次抽卡取并集（抽样随机，单次可能漏候选；30 轮漏率 <1e-20）。"""

    async def union_of(binding, **kw) -> set[int]:
        union: set[int] = set()
        for _ in range(30):
            data = await dan.card_data(None, "random_master_4", binding, **kw)
            assert len(data.songs) == 4
            union |= {s.song_id for s in data.songs}
        return union

    # CN 区域（默认/水鱼）：只见双域曲（1 为 JP 限定被排除）
    assert await union_of(cn) == {2, 3}
    # 未绑定默认 CN
    assert await union_of(None) == {2, 3}
    # JP 区域：JP 新曲在内
    assert await union_of(jp) == {1, 2, 3}
    # 版本范围：bud+（24500）为止 → 26000 的 JP 新曲被排除
    assert await union_of(jp, version_code=24500) == {2, 3}
    # CN 区域 + 版本范围：24000 为止 → 只剩 3
    assert await union_of(cn, version_code=24000) == {3}
