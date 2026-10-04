"""段位認定数据层：gallery.yaml 解析 / 入库 / 查询（段位表查询的域模块）。

数据源：zetaraku arcade-songs 线上 gallery.yaml（CloudFront 直链，随数据管线
日级更新）。格式细则、三 URL 关系与「底分」语义定案见
``local/reference/arcade-songs-gallery-notes.md``。

表结构（store）：``dan_course`` / ``dan_grade`` / ``dan_sheet`` / ``dan_random``
四表整包重写（单事务），原文 yaml 进 kv_cache 溯源；解析层国服/海外版/朋友
对战/活动歌单一律不收（段位表查询只面向日服普通段位＋随机段位）。

底分（每曲「底分 X (+N)」）：底分 = 玩家该谱当前单曲 RA；(+N) = 打到
100.5000% 时按 ``nb_chart.new_best_score`` 计的 B50 净提升，best_list 必须
传完整 B50（b35+b15，段位课题曲常为新曲）。成绩数据源不可用时降级：达成率
全 0.0000%、底分照算（无成绩即 0）且不显示括号，与歌曲卡无数据时一致。
"""

from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING

import yaml
from nonebot import logger
from maimai_py import current_version
from maimai_py.enums import Version

from . import store
from .http import create_smart_client

if TYPE_CHECKING:
    from maimai_py.models import ScoreExtend

    from .render.dan import DanCardData

GALLERY_URL = "https://dp4p6x0xfi5o9.cloudfront.net/maimai/gallery.yaml"
KV_GALLERY_YAML = "dan_gallery_yaml"
KV_GALLERY_TIME = "dan_gallery_time"

# gallery 列表 title 前缀（版本代目名）→ maimai_py Version；长前缀优先匹配
_VERSION_BY_NAME: list[tuple[str, Version]] = [
    ("Splash PLUS", Version.MAIMAI_DX_SPLASH_PLUS),
    ("UNiVERSE PLUS", Version.MAIMAI_DX_UNIVERSE_PLUS),
    ("UNiVERSE", Version.MAIMAI_DX_UNIVERSE),
    ("BUDDiES PLUS", Version.MAIMAI_DX_BUDDIES_PLUS),
    ("BUDDiES", Version.MAIMAI_DX_BUDDIES),
    ("FESTiVAL PLUS", Version.MAIMAI_DX_FESTIVAL_PLUS),
    ("FESTiVAL", Version.MAIMAI_DX_FESTIVAL),
    ("PRiSM PLUS", Version.MAIMAI_DX_PRISM_PLUS),
    ("PRiSM", Version.MAIMAI_DX_PRISM),
    ("CiRCLE PLUS", Version.MAIMAI_DX_CIRCLE_PLUS),
    ("CiRCLE", Version.MAIMAI_DX_CIRCLE),
    ("MAGiCAL", Version.MAIMAI_DX_MAGICAL),
]

# 段位种名表：dan_id → 日文正名，顺序即段位表内官方顺序（21 段版本无裏皆伝）
DAN_KINDS: list[tuple[str, str]] = [
    *[
        (f"{i}dan", f"{name}段")
        for i, name in enumerate(
            ["初", "二", "三", "四", "五", "六", "七", "八", "九", "十"], 1
        )
    ],
    *[
        (f"shin_{i}dan", f"真{name}段")
        for i, name in enumerate(
            ["初", "二", "三", "四", "五", "六", "七", "八", "九", "十"], 1
        )
    ],
    ("shin_kaiden", "真皆伝"),
    ("ura_kaiden", "裏皆伝"),
]
_DAN_ID_BY_NAME = {name: dan_id for dan_id, name in DAN_KINDS}

# 随机段位档名（档序 1..4 = 初級..超上級）；定数区间取 BUDDiES 时代社区实测
# （gamerch 评论区，非官方；EXPERT 各档与 MASTER 初級无实测 → 标级跨度推导，
# 实证 Lv N = [N.0, N.5]、Lv N+ = [N.6, N.9]，详见 gallery 调研笔记 §6）
RANDOM_TIERS = ("初級", "中級", "上級", "超上級")
_RANDOM_DS_MEASURED: dict[tuple[str, int], tuple[float, float]] = {
    ("master", 2): (12.0, 13.2),
    ("master", 3): (13.3, 14.4),
    ("master", 4): (14.5, 14.9),
}

_RULE_RE = re.compile(r"^❤ ?(\d+)｜-(\d+)/-(\d+)/-(\d+)｜\+(\d+)$")
_DAN_SECTION_RE = re.compile(r"^【(.+)】$")
_RANDOM_SECTION_RE = re.compile(r"^【(EXPERT|MASTER) (初級|中級|上級|超上級)】$")

# gallery 谱面类型/难度 → 规范表口径
KIND_TO_SONG_TYPE = {"std": "sd", "dx": "dx"}
DIFF_TO_LEVEL_INDEX = {
    "basic": 0,
    "advanced": 1,
    "expert": 2,
    "master": 3,
    "remaster": 4,
}


# ---------------------------------------------------------------- 解析模型


class ParsedSheet:
    """段位课题曲解析结果（gallery sheetExpr 三段键）+ 规范表 join 回填。"""

    __slots__ = ("difficulty", "kind", "song_id", "title")

    def __init__(self, title: str, kind: str, difficulty: str) -> None:
        self.title = title
        self.kind = kind
        self.difficulty = difficulty
        self.song_id: int | None = None


class ParsedGrade:
    """单段位解析结果：段位种名 id + 日文正名 + 血量规则 + 课题曲。"""

    __slots__ = (
        "clear_bonus",
        "damage_good",
        "damage_great",
        "damage_miss",
        "dan_id",
        "life",
        "name_ja",
        "sheets",
    )

    def __init__(
        self,
        dan_id: str,
        name_ja: str,
        life: int,
        damage_great: int,
        damage_good: int,
        damage_miss: int,
        clear_bonus: int,
    ) -> None:
        self.dan_id = dan_id
        self.name_ja = name_ja
        self.life = life
        self.damage_great = damage_great
        self.damage_good = damage_good
        self.damage_miss = damage_miss
        self.clear_bonus = clear_bonus
        self.sheets: list[ParsedSheet] = []


class ParsedCourse:
    """一个版本的段位表解析结果。"""

    __slots__ = ("gallery_id", "grades", "kind", "version")

    def __init__(self, gallery_id: str, kind: str, version: int) -> None:
        self.gallery_id = gallery_id
        self.kind = kind
        self.version = version
        self.grades: list[ParsedGrade] = []


class ParsedRandom:
    """随机段位档位解析结果（全版本同值）。"""

    __slots__ = (
        "clear_bonus",
        "damage_good",
        "damage_great",
        "damage_miss",
        "dan_id",
        "difficulty",
        "ds_hi",
        "ds_lo",
        "ds_source",
        "level_range",
        "life",
        "name_ja",
    )

    def __init__(
        self,
        dan_id: str,
        difficulty: str,
        name_ja: str,
        level_range: str,
        ds_range: tuple[float, float],
        ds_source: str,
        life: int,
        damage_great: int,
        damage_good: int,
        damage_miss: int,
        clear_bonus: int,
    ) -> None:
        self.dan_id = dan_id
        self.difficulty = difficulty
        self.name_ja = name_ja
        self.level_range = level_range
        self.ds_lo, self.ds_hi = ds_range
        self.ds_source = ds_source
        self.life = life
        self.damage_great = damage_great
        self.damage_good = damage_good
        self.damage_miss = damage_miss
        self.clear_bonus = clear_bonus


# ---------------------------------------------------------------- 解析


def _parse_rule(description: str | None) -> dict:
    """血量规则 description → 五元组 dict（❤初始｜GREAT/GOOD/MISS扣血｜每曲回复）。"""
    m = _RULE_RE.match(description or "")
    if not m:
        raise ValueError(f"血量规则不匹配: {description!r}")
    life, great, good, miss, bonus = map(int, m.groups())
    return {
        "life": life,
        "damage_great": great,
        "damage_good": good,
        "damage_miss": miss,
        "clear_bonus": bonus,
    }


def _level_str_bounds(level: str) -> tuple[float, float]:
    """标级串 → 定数跨度（实证：Lv N = [N.0, N.5]，Lv N+ = [N.6, N.9]）。"""
    m = re.fullmatch(r"(\d+)(\+?)", level)
    if not m:
        raise ValueError(f"标级串异常: {level!r}")
    base = int(m.group(1))
    return (base + 0.6, base + 0.9) if m.group(2) else (float(base), base + 0.5)


def _derive_ds_range(level_range: str) -> tuple[float, float]:
    """显示等级区间 → 左标级最低定数 ~ 右标级最高定数。"""
    left, right = level_range.split("~")
    return _level_str_bounds(left)[0], _level_str_bounds(right)[1]


def _version_of_gallery_title(title: str) -> Version:
    for name, version in _VERSION_BY_NAME:
        if title.startswith(name + " "):
            return version
    raise ValueError(f"无法识别段位表版本: {title!r}")


def parse_gallery(text: str) -> tuple[list[ParsedCourse], list[ParsedRandom]]:
    """gallery.yaml 文本 → （普通/真段位表列表, 随机段位档位列表）。

    纯函数零 IO。只收日服 ``*-dan`` 列表与 ``random-dan``；海外版（``-intl``）、
    朋友对战、活动歌单一律跳过。YAML 锚点/别名（海外版复用日服定义）经
    ``safe_load`` 自然展开。血量规则与段名不匹配即抛 ValueError（上游格式
    变化必须显式失败，禁止静默吞掉）。
    """
    data = yaml.safe_load(text)
    courses: list[ParsedCourse] = []
    randoms: list[ParsedRandom] = []
    for lst in data:
        gid = str(lst.get("id") or "")
        title = lst.get("title") or ""
        if gid == "random-dan":
            for sec in lst["sections"]:
                m = _RANDOM_SECTION_RE.match(sec["title"])
                if not m:
                    raise ValueError(f"随机段位段名不匹配: {sec['title']!r}")
                diff = m.group(1).lower()
                tier = RANDOM_TIERS.index(m.group(2)) + 1
                ranges = {sh.split("|")[3] for sh in sec["sheets"]}
                if len(ranges) != 1:
                    raise ValueError(f"随机段位 {sec['title']} 区间不一致: {ranges}")
                level_range = ranges.pop()
                measured = _RANDOM_DS_MEASURED.get((diff, tier))
                ds_range = measured or _derive_ds_range(level_range)
                randoms.append(
                    ParsedRandom(
                        dan_id=f"random_{diff}_{tier}",
                        difficulty=diff,
                        name_ja=m.group(2),
                        level_range=level_range,
                        ds_range=ds_range,
                        ds_source="measured" if measured else "derived",
                        **_parse_rule(sec.get("description")),
                    )
                )
            continue
        if not gid.endswith("-dan") or gid.endswith("-dan-intl"):
            continue
        version = _version_of_gallery_title(title)
        # gallery 每版本一张段位表（普通+真段位同表）；kind 恒 normal，
        # 渲染的 normal/shin 底图由段位种名 id 推导
        course = ParsedCourse(gid, "normal", version.value)
        for sec in lst["sections"]:
            m = _DAN_SECTION_RE.match(sec["title"])
            if m is None:
                raise ValueError(f"段位段名不匹配: {sec['title']!r}")
            name_ja = m.group(1)
            dan_id = _DAN_ID_BY_NAME.get(name_ja)
            if dan_id is None:
                raise ValueError(f"未知段位名: {sec['title']!r}")
            grade = ParsedGrade(dan_id, name_ja, **_parse_rule(sec.get("description")))
            for expr in sec["sheets"]:
                parts = expr.split("|")
                if len(parts) != 3 or parts[1] not in ("std", "dx"):
                    raise ValueError(f"谱面行格式异常: {expr!r}")
                if parts[2] not in DIFF_TO_LEVEL_INDEX:
                    raise ValueError(f"未知难度: {expr!r}")
                grade.sheets.append(ParsedSheet(*parts))
            course.grades.append(grade)
        courses.append(course)
    if not courses and not randoms:
        raise ValueError("gallery.yaml 未解析出任何段位数据")
    return courses, randoms


# ---------------------------------------------------------------- 入库


def _norm_title(text: str) -> str:
    """标题归一（与曲库 otoge join 同口径）：NFKC + 去空白 + 小写。"""
    return "".join(unicodedata.normalize("NFKC", text).split()).lower()


async def _song_index() -> dict:
    """规范表 JP 视图索引：标题精确/归一两级查 id + 谱面存在性集合。"""
    async with store.session() as db:
        from sqlmodel import select

        songs = {
            row.id: row.title for row in (await db.exec(select(store.SongRow))).all()
        }
        jp_ids = {
            row.song_id
            for row in (await db.exec(select(store.SongSheetGroup))).all()
            if row.version is not None
        }
        charts = {
            (row.song_id, row.kind, row.level_id)
            for row in (await db.exec(select(store.SongChart))).all()
            if row.kind in ("sd", "dx")
        }
    exact: dict[str, int] = {}
    norm: dict[str, int] = {}
    for sid, title in songs.items():
        if sid not in jp_ids:
            continue
        exact.setdefault(title, sid)
        norm.setdefault(_norm_title(title), sid)
    return {"exact": exact, "norm": norm, "charts": charts}


def _join_sheets(courses: list[ParsedCourse], index: dict) -> None:
    """回填各课题曲的规范表 song_id（就地修改 ParsedSheet.song_id）。

    三段键精确匹配：标题（精确→归一）× 谱面类型 × 难度；标题未命中（削除曲
    等）song_id 置 None，谱面缺失同样 None（渲染层统一降级为仅标题展示）。
    """
    for course in courses:
        for grade in course.grades:
            for sheet in grade.sheets:
                sid = index["exact"].get(sheet.title)
                matched_kind = KIND_TO_SONG_TYPE.get(sheet.kind)
                level_index = DIFF_TO_LEVEL_INDEX[sheet.difficulty]
                if sid is None:
                    sid = index["norm"].get(_norm_title(sheet.title))
                if (
                    sid is not None
                    and (sid, matched_kind, level_index) not in index["charts"]
                ):
                    sid = None  # 标题命中但规范表无此谱面（跨版本补谱等）→ 未收录
                sheet.song_id = sid


async def refresh(*, text: str | None = None) -> dict:
    """拉取（或注入）gallery.yaml → 解析 → 规范表 join → 四表整包重写。

    ``text`` 供测试与离线注入；返回统计 dict。幂等可重复执行。
    """
    if text is None:
        async with create_smart_client(timeout=30) as http:
            resp = await http.get(GALLERY_URL)
            resp.raise_for_status()
            text = resp.text
    courses, randoms = parse_gallery(text)
    _join_sheets(courses, await _song_index())

    sheets = [
        (course.gallery_id, grade.dan_id, idx, sheet)
        for course in courses
        for grade in course.grades
        for idx, sheet in enumerate(grade.sheets)
    ]
    async with store.session() as db:
        from sqlmodel import delete

        await db.exec(delete(store.DanSheet))
        await db.exec(delete(store.DanGrade))
        await db.exec(delete(store.DanCourse))
        await db.exec(delete(store.DanRandom))
        for course in courses:
            db.add(
                store.DanCourse(
                    gallery_id=course.gallery_id,
                    kind=course.kind,
                    version=course.version,
                )
            )
            for sort, grade in enumerate(course.grades):
                db.add(
                    store.DanGrade(
                        gallery_id=course.gallery_id,
                        dan_id=grade.dan_id,
                        sort=sort,
                        name_ja=grade.name_ja,
                        life=grade.life,
                        damage_great=grade.damage_great,
                        damage_good=grade.damage_good,
                        damage_miss=grade.damage_miss,
                        clear_bonus=grade.clear_bonus,
                    )
                )
        for gallery_id, dan_id, idx, sheet in sheets:
            db.add(
                store.DanSheet(
                    gallery_id=gallery_id,
                    dan_id=dan_id,
                    idx=idx,
                    title=sheet.title,
                    kind=sheet.kind,
                    difficulty=sheet.difficulty,
                    song_id=sheet.song_id,
                )
            )
        for r in randoms:
            db.add(
                store.DanRandom(
                    dan_id=r.dan_id,
                    difficulty=r.difficulty,
                    name_ja=r.name_ja,
                    level_range=r.level_range,
                    ds_lo=r.ds_lo,
                    ds_hi=r.ds_hi,
                    ds_source=r.ds_source,
                    life=r.life,
                    damage_great=r.damage_great,
                    damage_good=r.damage_good,
                    damage_miss=r.damage_miss,
                    clear_bonus=r.clear_bonus,
                )
            )
        await db.commit()
    from datetime import datetime

    await store.kv_set(KV_GALLERY_YAML, text)
    await store.kv_set(KV_GALLERY_TIME, datetime.now().isoformat())
    logger.info(
        f"dan: gallery 入库完成（段位表 {len(courses)} 张，随机段位 {len(randoms)} 档，"
        f"课题曲 {len(sheets)} 行）"
    )
    return {"courses": len(courses), "randoms": len(randoms), "sheets": len(sheets)}


async def ensure_loaded(max_age_hours: float = 24.0) -> bool:
    """段位数据懒加载：库为空或快照超龄时拉取刷新，返回是否实际刷新。

    失败（网络等）仅记日志不抛——段位查询在无库数据时给出引导文案。
    """
    from datetime import datetime, timedelta

    raw = await store.kv_get(KV_GALLERY_TIME)
    if raw and await latest_gallery_id() is not None:
        try:
            age = datetime.now() - datetime.fromisoformat(raw)
            if age < timedelta(hours=max_age_hours):
                return False
        except ValueError:
            pass
    try:
        await refresh()
        return True
    except Exception as e:
        logger.warning(f"dan: gallery 拉取失败（段位查询不可用）：{e}")
        return False


# ---------------------------------------------------------------- 查询


# ---------------------------------------------------------------- 版本前缀

# 版本词根（小写）：（词根, 无 PLUS 码, PLUS 码；None=无 PLUS）。
# dx 初代/PLUS 特例 2 字符缩写，其余按用户口径取前 n≥3 字符
_VERSION_BASES: tuple[tuple[str, int, int | None], ...] = (
    ("dx", Version.MAIMAI_DX.value, Version.MAIMAI_DX_PLUS.value),
    ("splash", Version.MAIMAI_DX_SPLASH.value, Version.MAIMAI_DX_SPLASH_PLUS.value),
    (
        "universe",
        Version.MAIMAI_DX_UNIVERSE.value,
        Version.MAIMAI_DX_UNIVERSE_PLUS.value,
    ),
    (
        "festival",
        Version.MAIMAI_DX_FESTIVAL.value,
        Version.MAIMAI_DX_FESTIVAL_PLUS.value,
    ),
    ("buddies", Version.MAIMAI_DX_BUDDIES.value, Version.MAIMAI_DX_BUDDIES_PLUS.value),
    ("prism", Version.MAIMAI_DX_PRISM.value, Version.MAIMAI_DX_PRISM_PLUS.value),
    ("circle", Version.MAIMAI_DX_CIRCLE.value, Version.MAIMAI_DX_CIRCLE_PLUS.value),
    ("magical", Version.MAIMAI_DX_MAGICAL.value, None),
)
# 国服形式年→码（随心配 S-2 口径）：码 = 20000 + (年 - 2019) × 500
_CN_YEAR_BASE, _CN_YEAR_STEP = 2019, 500


def parse_version_prefix(arg: str) -> tuple[int | None, str]:
    """解析段位名开头的版本前缀 → (版本码或 None, 余下段位名)。

    - 日服：完整版本名或前 n≥3 字符（dx 系 2 字符）+ 可选「+」表 PLUS，
      大小写不敏感（如 dx/dx+/uni/uni+/bud+/mag/magical）；
    - 国服（随心配 S-2 口径）：舞萌dx无印 / 舞萌dxYYYY / 舞萌YYYY / dxYYYY /
      YYYY，年→码 = 20000+(年-2019)×500；
    - 无前缀返回 (None, arg)。MAGiCAL 无 PLUS，「mag+」等显式报错。
    """
    lowered = arg.lower()
    if m := re.match(r"^(?:舞萌)?dx无印", lowered):
        return Version.MAIMAI_DX.value, arg[m.end() :]
    if m := re.match(r"^(?:舞萌)?dx(20[12]\d)", lowered):
        year = int(m.group(1))
        code = 20000 + (year - _CN_YEAR_BASE) * _CN_YEAR_STEP
        return code, arg[m.end() :]
    if m := re.match(r"^(?:舞萌)?dx\+?", lowered):
        # 舞萌dx（无年份）= DX 无印；dx+ = PLUS
        code = (
            Version.MAIMAI_DX_PLUS.value
            if m.group().endswith("+")
            else (Version.MAIMAI_DX.value)
        )
        return code, arg[m.end() :]
    if m := re.match(r"^(?:舞萌)?(20[12]\d)", lowered):
        year = int(m.group(1))
        code = 20000 + (year - _CN_YEAR_BASE) * _CN_YEAR_STEP
        return code, arg[m.end() :]
    for base, code, plus_code in _VERSION_BASES:
        min_len = len(base) if base == "dx" else 3
        for n in range(len(base), min_len - 1, -1):
            if lowered.startswith(base[:n]):
                rest = arg[n:]
                if rest.startswith("+"):
                    if plus_code is None:
                        raise ValueError(f"该版本无 PLUS：{base[:n]}+")
                    return plus_code, rest[1:]
                return code, rest
    return None, arg


async def course_id_by_version(version: int) -> str | None:
    """按版本码查段位表 id（库无该版本数据返回 None）。"""
    async with store.session() as db:
        from sqlmodel import select

        row = (
            await db.exec(
                select(store.DanCourse).where(
                    store.DanCourse.kind == "normal",
                    store.DanCourse.version == version,
                )
            )
        ).first()
        return row.gallery_id if row else None


async def latest_gallery_id(
    kind: str = "normal", *, version_limit: int | None = None
) -> str | None:
    """最新段位表 gallery_id：``version_limit`` 给定时取 ≤ 该版本的最新
    （数据源 current_version 口径）；库内无满足行返回 None。"""
    async with store.session() as db:
        from sqlmodel import select

        query = select(store.DanCourse).where(store.DanCourse.kind == kind)
        if version_limit is not None:
            query = query.where(store.DanCourse.version <= version_limit)
        courses = (
            await db.exec(select(store.DanCourse).where(store.DanCourse.kind == kind))
        ).all()
    if version_limit is not None:
        courses = [c for c in courses if c.version <= version_limit]
    if not courses:
        return None
    return max(courses, key=lambda c: c.version).gallery_id


async def cn_current_version() -> int:
    """国服当前版本：规范表 max(version_cn)，回落 maimai_py current_version
    （口径对齐 songdb.State.cn_current_version）。"""
    from sqlmodel import select
    from sqlalchemy import func

    async with store.session() as db:
        value = (
            await db.exec(select(func.max(store.SongSheetGroup.version_cn)))
        ).first()
    return int(value) if value else current_version.value


async def grades_of(
    gallery_id: str,
) -> list[tuple[store.DanGrade, list[store.DanSheet]]]:
    """段位表内全部段位（按官方序）及其课题曲。"""
    async with store.session() as db:
        from sqlmodel import select

        grades = (
            await db.exec(
                select(store.DanGrade).where(store.DanGrade.gallery_id == gallery_id)
            )
        ).all()
        sheets = (
            await db.exec(
                select(store.DanSheet).where(store.DanSheet.gallery_id == gallery_id)
            )
        ).all()
    by_grade: dict[str, list[store.DanSheet]] = {}
    for s in sheets:
        by_grade.setdefault(s.dan_id, []).append(s)
    grades = sorted(grades, key=lambda g: g.sort)
    return [
        (g, sorted(by_grade.get(g.dan_id, []), key=lambda s: s.idx)) for g in grades
    ]


async def find_grade_row(gallery_id: str | None, dan_id: str) -> store.DanGrade | None:
    async with store.session() as db:
        from sqlmodel import select

        return (
            await db.exec(
                select(store.DanGrade).where(
                    store.DanGrade.gallery_id == gallery_id,
                    store.DanGrade.dan_id == dan_id,
                )
            )
        ).first()


async def random_tiers() -> list[store.DanRandom]:
    """随机段位全部档位（按 EXPERT→MASTER、初級→超上級 排序）。"""
    async with store.session() as db:
        from sqlmodel import select

        return list((await db.exec(select(store.DanRandom))).all())


def format_base_score(current_ra: int, gain: int | None) -> str:
    """底分展示串：有加分预测时「RA (+N)」，无（数据源降级/负提升）仅 RA。"""
    if gain is None or gain <= 0:
        return str(current_ra)
    return f"{current_ra} (+{gain})"


# ---------------------------------------------------------------- 段位卡组装


def _level_str(ds: float) -> str:
    """定数 → 显示等级串（实证映射：N.0-N.5 → N，N.6-N.9 → N+）。

    用十分位整数比较：浮点直接减会踩 14.6-14=0.5999… 的坑（14.6 误判 14）。
    """
    base = int(ds)
    return f"{base}+" if round(ds * 10) - base * 10 >= 6 else str(base)


async def _chart_info(
    song_id: int, kind: str, level_index: int, version_code: int | None = None
) -> tuple[str, str, str, float]:
    """规范表单谱面展示信息：（显示等级, 谱师, BPM, 定数）。缺位以 - / 0 兜底。

    定数取 carry-forward：``version_code`` 给定时取该版本时点值（跨版本段位
    卡显示/加分预测统一时点口径，随心配 S-2 改进先例），None 取最新。
    """
    from sqlmodel import select

    db_kind = "sd" if kind == "std" else "dx"
    level_clauses = [
        store.SongChartLevel.song_id == song_id,
        store.SongChartLevel.kind == db_kind,
        store.SongChartLevel.level_id == level_index,
    ]
    if version_code is not None:
        level_clauses.append(store.SongChartLevel.version <= version_code)
    async with store.session() as db:
        song = (
            await db.exec(select(store.SongRow).where(store.SongRow.id == song_id))
        ).first()
        chart = (
            await db.exec(
                select(store.SongChart).where(
                    store.SongChart.song_id == song_id,
                    store.SongChart.kind == db_kind,
                    store.SongChart.level_id == level_index,
                )
            )
        ).first()
        level_rows = (
            await db.exec(select(store.SongChartLevel).where(*level_clauses))
        ).all()
    if song is None or chart is None or not level_rows:
        return "-", "-", "-", 0.0
    latest = max(level_rows, key=lambda r: r.version)
    ds = latest.level_value or 0.0
    return _level_str(ds), chart.designer or "-", str(song.bpm or "-"), ds


async def _sample_random_sheets(
    random_row: store.DanRandom,
    *,
    version_code: int | None = None,
    jp_region: bool = False,
) -> list[tuple[int, str, str]]:
    """按档位规则随机抽四首课题曲（独立抽取、可重复）。

    候选 = sd/dx 谱面，定数（最新 carry-forward）落在档位区间内；MASTER 档
    按游戏规则含 Re:MASTER（gamerch：FESTiVAL 起 MASTER 随机段位也会选出
    Re:MASTER 谱面），EXPERT 档仅 EXPERT。

    区域跟随玩家数据源（``jp_region``：net=日服视图，其余/未绑定=国服视图），
    国服数据源不会抽出日服限定曲；``version_code`` 给定时只抽「到该版本为止」
    的曲库（JP 看 group.version、CN 看 version_cn，均含该版本）。候选为空抛
    ValueError。
    """
    import random as _random

    from sqlmodel import select

    level_ids = (
        (2,) if random_row.difficulty == "expert" else (3, 4)
    )  # master 含 Re:MASTER
    version_col = (
        store.SongSheetGroup.version if jp_region else (store.SongSheetGroup.version_cn)
    )
    scope_clauses = [version_col.is_not(None)]  # type: ignore[attr-defined]
    if version_code is not None:
        scope_clauses.append(version_col <= version_code)  # type: ignore[attr-defined]
    async with store.session() as db:
        rows = (
            await db.exec(
                select(store.SongChart, store.SongChartLevel)
                .join(
                    store.SongSheetGroup,
                    (store.SongSheetGroup.song_id == store.SongChart.song_id)
                    & (store.SongSheetGroup.kind == store.SongChart.kind),  # type: ignore[reportArgumentType]
                )
                .join(
                    store.SongChartLevel,
                    (store.SongChartLevel.song_id == store.SongChart.song_id)
                    & (store.SongChartLevel.kind == store.SongChart.kind)
                    & (store.SongChartLevel.level_id == store.SongChart.level_id),  # type: ignore[reportArgumentType]
                )
                .where(
                    *scope_clauses,
                    store.SongChart.kind.in_(("sd", "dx")),  # type: ignore[attr-defined]
                    store.SongChart.level_id.in_(level_ids),  # type: ignore[attr-defined]
                ),
            )
        ).all()
    # 同谱面多版本定数行取「生效版本最新」的一行（非最大值——被改订降定数
    # 的谱面应取新值）；scope 时行集已限定 version <= code，即该版本时点定数
    latest: dict[tuple[int, str, int], tuple[int, float]] = {}
    for chart, level in rows:
        if level.level_value is None:
            continue
        if version_code is not None and level.version > version_code:
            continue
        key = (chart.song_id, chart.kind, chart.level_id)
        if key not in latest or level.version > latest[key][0]:
            latest[key] = (level.version, level.level_value)
    candidates = [
        (
            song_id,
            "std" if kind == "sd" else "dx",
            {0: "basic", 1: "advanced", 2: "expert", 3: "master", 4: "remaster"}[
                level_id
            ],
        )
        for (song_id, kind, level_id), (_ver, ds) in latest.items()
        if random_row.ds_lo <= ds <= random_row.ds_hi
    ]
    if not candidates:
        raise ValueError(
            f"随机段位 {random_row.dan_id} 无候选谱面"
            f"（定数 {random_row.ds_lo}~{random_row.ds_hi}）"
        )
    return _random.choices(candidates, k=4)


async def card_data(
    gallery_id: str | None,
    dan_id: str,
    binding=None,
    *,
    version_code: int | None = None,
) -> "DanCardData | None":
    """组装段位卡渲染输入：课题曲规范表信息 + 玩家成绩（可降级）。

    ``dan_id`` 为 ``random_*`` 时走随机档位（DanRandom 表；按规则真实抽四首，
    独立抽取可重复，不需要 ``gallery_id``）——抽曲区域随 ``binding`` 的数据源
    （net=日服，其余/未绑定=国服），``version_code`` 限定「到该版本为止」的
    曲库；普通/真段位从 DanGrade/DanSheet 取数（削除曲等 join 不到的行保留
    标题、其余 fallback），``gallery_id`` 为该版本段位表 id；展示/加分预测
    定数取段位表所属版本的时点值（版本时效性——默认表即数据源现行版本）。
    ``binding`` 为空或成绩数据源不可用（UserScoreError）时走降级：达成率全
    0.0000%、底分「0」不带括号（与歌曲卡无数据一致）。返回 None = 库内无此
    段位数据（未刷新）。
    """
    from sqlmodel import select
    from maimai_py.enums import SongType
    from maimai_py.utils import ScoreCoefficient

    from .score import UserScoreError, score_service
    from .render.dan import DanCardData, DanSongCard
    from .render.assets import assets as render_assets
    from .render.nb_chart import new_best_score

    grade_row: store.DanGrade | store.DanRandom | None
    course: store.DanCourse | None = None
    if dan_id.startswith("random_"):
        async with store.session() as db:
            grade_row = (
                await db.exec(
                    select(store.DanRandom).where(store.DanRandom.dan_id == dan_id)
                )
            ).first()
        if grade_row is None:
            return None
        # 抽曲：区域随数据源（net=日服，其余/未绑定=国服），候选为空属数据
        # 异常显式失败
        jp_region = binding is not None and binding.service == "net"
        picks = [
            (song_id, kind, difficulty)
            for song_id, kind, difficulty in await _sample_random_sheets(
                grade_row, version_code=version_code, jp_region=jp_region
            )
        ]
    else:
        grade_row = await find_grade_row(gallery_id, dan_id)
        if grade_row is None:
            return None
        async with store.session() as db:
            course = (
                await db.exec(
                    select(store.DanCourse).where(
                        store.DanCourse.gallery_id == gallery_id
                    )
                )
            ).first()
            sheets = (
                await db.exec(
                    select(store.DanSheet).where(
                        store.DanSheet.gallery_id == gallery_id,
                        store.DanSheet.dan_id == dan_id,
                    )
                )
            ).all()
        picks = [
            (s.song_id, s.kind, s.difficulty, s.title)
            for s in sorted(sheets, key=lambda s: s.idx)
        ]

    # 玩家成绩：未绑定/数据源不可用统一降级（与歌曲卡无数据一致）
    score_map: dict[tuple, ScoreExtend] = {}
    best_list: list | None = None
    if binding is not None:
        try:
            all_scores = (await score_service.get_scores_all(binding)).scores
            score_map = {
                (s.id, s.type.value, s.level_index.value): s for s in all_scores
            }
            bests = await score_service.get_b50(binding)
            best_list = list(bests.scores)  # 完整 B50（b35+b15，入线线才不失真）
        except UserScoreError as e:
            logger.info(f"dan: 玩家成绩拉取失败，按无数据降级（{e}）")

    cards: list[DanSongCard] = []
    for entry in picks:
        song_id, kind, difficulty, *rest = entry
        title = rest[0] if rest else None
        song_type = SongType.STANDARD if kind == "std" else SongType.DX
        level_index = DIFF_TO_LEVEL_INDEX[difficulty]
        achievement, ra = 0.0, 0
        score = (
            score_map.get((song_id, song_type.value, level_index))
            if song_id is not None
            else None
        )
        if score is not None:
            achievement = score.achievements or 0.0
            ra = int(score.dx_rating or 0)
        gain = None
        level, charter, bpm, ds = "-", "-", "-", 0.0
        if song_id is not None:
            # 版本时效性：普通/真段位用段位表所属版本的时点定数（默认表=
            # 数据源现行版本）；随机档位无版本归属，无前缀即现行
            level, charter, bpm, ds = await _chart_info(
                song_id,
                kind,
                level_index,
                course.version if course else version_code,
            )
            if best_list is not None:
                # 加分预测：目标 100.5%（ScoreCoefficient 内部封顶）的 B50 净提升
                target_ra = int(ScoreCoefficient(100.5).ra(ds))
                gain = max(
                    new_best_score(
                        song_id, level_index, target_ra, best_list, song_type
                    ),
                    0,
                )
        if title is None and song_id is not None:
            # 随机抽取行：标题取规范表原题
            async with store.session() as db:
                row = (
                    await db.exec(
                        select(store.SongRow).where(store.SongRow.id == song_id)
                    )
                ).first()
            title = row.title if row else "-"
        cards.append(
            DanSongCard(
                title=title or "-",
                kind=kind,
                level=level,
                level_index=level_index,
                ds=f"{ds:.1f}" if song_id is not None else "-",
                charter=charter,
                bpm=bpm,
                base_score=format_base_score(ra, gain) if song_id is not None else "-",
                achievement=achievement,
                song_id=song_id,
            )
        )

    # 奖励区版本 logo（国服区素材，缺失 None 由渲染跳过）
    logo_path = render_assets.static_path() / "mai" / "pic" / "dan" / "DX_2026_Logo.png"
    logo = render_assets.get(logo_path) if logo_path.exists() else None
    return DanCardData(
        dan_id=dan_id,
        life=grade_row.life,
        damage_great=grade_row.damage_great,
        damage_good=grade_row.damage_good,
        damage_miss=grade_row.damage_miss,
        clear_bonus=grade_row.clear_bonus,
        songs=cards,
        logo=logo,
    )
