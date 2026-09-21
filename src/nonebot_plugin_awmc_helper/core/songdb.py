"""歌曲规范表：多源解析 → 并集合并 → 定数历史与推导 → 双视图 → 标准 JSON。

设计契约见 ``local/reference/song-db-design.md``（r5）。要点：

- 表集存**并集**：日服全集（maimaiinfo 骨架）∪ 国服信息（落雪/水鱼，唯二国服源）；
- 表结构 = 作者 01 文档 1:1，外加 ``song_source_raw``/``song_pending`` 两个基建表；
- 定数历史只存变化点（``version`` = 自此版本起生效），读取方 carry-forward；
- 国服当前定数 = 日服历史中 ``version ≤ 国服当前版本`` 的最新值，
  区间无值取首值（已全量验证 5502/5502，§5.3）；
- 每次合并**只让列从空变满**：单源失败跳过该源贡献、不回填已有数据；
  缺失/删除统一规则——一侧缺置该侧版本列 NULL、两侧皆无才整曲删除（§7.5-B）。

本模块全部函数无网络副作用（payload 由 :mod:`core.ext` 拉取后传入），便于测试。
"""

import re
import json
import asyncio
import hashlib
import unicodedata
from typing import Any, Literal
from dataclasses import field, dataclass

from nonebot import logger
from sqlmodel import col, delete, select
from maimai_py import current_version
from maimai_py.enums import Genre, SongType, LevelIndex, divingfish_to_version
from maimai_py.models import (
    Song,
    BuddyNotes,
    SongDifficulty,
    SongDifficulties,
    SongDifficultyUtage,
)

from . import store
from ..constants import SOURCE_NAME_TO_VERSION, level_from_value

Scope = Literal["cn", "jp"]

CURRENT_FINGERPRINT: str | None = None
"""规范表内容指纹（进程内缓存，rebuild 末尾刷新；供 provider._hash 同步读取）。"""

# ---------------------------------------------------------------------------
# 解析辅助
# ---------------------------------------------------------------------------

_NOTE_KEYS = ("tap", "hold", "slide", "touch", "break")


def norm_title(title: str) -> str:
    """标题归一（otoge-db title join 用）：NFKC + 去空白 + 小写。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", title)).lower()


def utage_ids(diff_id: int) -> tuple[int, int]:
    """宴谱 6 位机台内部 id → (song_id, level_id)。

    已实测：100018→(18, 0)、161852→(1852, 6)。
    """
    return diff_id % 10000, (diff_id // 10000) % 10


def utage_diff_id(song_id: int, level_id: int) -> int:
    """(song_id, level_id) → 宴谱 6 位机台内部 id（maimai_py diff_id 同规则）。"""
    return 100000 + level_id * 10000 + song_id


def parse_level_float(level: str) -> float | None:
    """宴标级串 → 标级浮点：有 + 一律 .7、无 + 一律 .0（作者口径，勿猜其他分布）。"""
    text = (level or "").strip().rstrip("?")
    match = re.fullmatch(r"(\d+)(\+?)", text)
    if not match:
        return None
    return int(match.group(1)) + (0.7 if match.group(2) else 0.0)


def _source_version(name: str | None) -> int | None:
    """数据源版本名 → 版本码（穷举映射；「未知」等返回 None）。"""
    if not name:
        return None
    if name in SOURCE_NAME_TO_VERSION:
        return SOURCE_NAME_TO_VERSION[name]
    ver = divingfish_to_version.get(name)
    return ver.value if ver else None


def _notes_tuple(raw: list, is_dx: bool) -> tuple[int, int, int, int, int]:
    """maimaiinfo notes 数组 → 五元组 (tap, hold, slide, touch, break)。

    SD 为 4 元组（touch=0）；个别 DX 谱只有 4 元（旧谱无 touch，实测 5 例）。
    """
    vals = list(raw) + [0] * (5 - len(raw))
    return (
        int(vals[0]),
        int(vals[1]),
        int(vals[2]),
        int(vals[3] if is_dx else 0),
        int(vals[4] if is_dx else vals[3]),
    )


def _utage_kanji(title: str) -> str | None:
    """宴标题 ``[X]…`` 前缀 → kanji（两源标题同构）。"""
    return title[1] if title.startswith("[") and len(title) > 1 else None


# ---------------------------------------------------------------------------
# 源解析（payload → 中间结构，纯函数）
# ---------------------------------------------------------------------------


@dataclass
class Chart:
    """谱面中间结构（日/国侧共用；各字段按来源填充）。

    ``title`` 为谱面所属条目的标题：宴条目带 ``[協]`` 等前缀、与基曲标题不同，
    otoge title join 与待归并判定都必须用它。
    """

    level_id: int
    title: str = ""
    designer: str | None = None
    notes: tuple[int, int, int, int, int] = (0, 0, 0, 0, 0)
    # 日侧：定数变化点 [(version, value)]（宴为标级推导单行）
    history: list[tuple[int, float]] = field(default_factory=list)
    # 宴
    kanji: str | None = None
    is_buddy: bool = False
    left: list[int] | None = None
    right: list[int] | None = None
    # 国侧（落雪实测：version_cn 组级来源与 §5.3 推导校验源）
    cn_version: int | None = None
    cn_level_value: float | None = None


@dataclass
class Entry:
    """曲级中间结构；``versions`` 按组类型分开（SD/DX key 的 from 可不同）。"""

    song_id: int
    title: str = ""
    artist: str = ""
    genre: str = ""
    bpm: str = ""
    versions: dict[str, int | None] = field(default_factory=dict)
    charts: dict[str, dict[int, Chart]] = field(default_factory=dict)

    def group(self, kind: str) -> dict[int, Chart]:
        return self.charts.setdefault(kind, {})


def parse_maimaiinfo(all_data: dict[str, dict], dschange: dict) -> dict[int, Entry]:
    """日服骨架：all_data（组/谱面）+ dschange（定数历史，含 ``__increments__`` 合并）。

    - 宴以 6 位 key 识别（``type`` 字段不可靠，实测 UTAGE 仅 12/1939）；
    - 宴 ds/level 尾部垃圾不采信：定数用标级串推导，且只取数组首元素；
    - dschange 末值旧于 all_data 时以 all_data 校正末变化点（§2.13）。
    """
    histories = _parse_dschange(dschange)

    songs: dict[int, Entry] = {}
    for key, item in all_data.items():
        # 宴 = 6 位 key（曲 id 0-9999 为 ≤5 位；100000+level_id*10000+song_id 恒 6 位，
        # level_id≥1 时以 "1x" 开头而非 "10"，如实测 111852——勿用 startswith("10")）
        is_utage = len(key) == 6 and key.isdigit()
        if is_utage:
            kind = "utage"
            song_id, level_id = utage_ids(int(key))
        else:
            kind = "sd" if item.get("type") == "SD" else "dx"
            song_id, level_id = int(key) % 10000, 0
        info = item.get("basic_info", {})
        entry = songs.setdefault(song_id, Entry(song_id=song_id))
        entry.title = entry.title or info.get("title", "")
        entry.artist = entry.artist or info.get("artist", "")
        entry.genre = entry.genre or info.get("genre", "")
        entry.bpm = entry.bpm or str(info.get("bpm", "") or "")

        entry_title = info.get("title", "") or entry.title
        group = entry.group(kind)
        version = _source_version(info.get("from"))
        if version is None:
            # 「未知」等：定数历史的首版本可补（dschange 自该曲登场版本起记录）
            history_by_diff = histories.get(key, {}).get(kind, {})
            for points in history_by_diff.values():
                version = points[0][0]
                break
        entry.versions[kind] = version

        if is_utage:
            # 宴：一个 key = 一张谱面；ds/level 只信首元素，定数由标级串推导
            chart = Chart(level_id=level_id, title=entry_title)
            chart.kanji = _utage_kanji(entry_title)
            derived = parse_level_float((item.get("level") or [""])[0])
            if derived is not None and version is not None:
                chart.history = [(version, derived)]
            group[level_id] = chart
            continue

        charts = item.get("charts", [])
        ds_values = item.get("ds", [])
        history_by_diff = histories.get(key, {}).get(kind, {})
        for idx in range(len(charts)):
            chart = Chart(level_id=idx, title=entry_title)
            charter = charts[idx].get("charter")
            chart.designer = charter if charter not in (None, "", "-") else None
            chart.notes = _notes_tuple(charts[idx].get("notes", []), kind == "dx")
            if idx < len(ds_values) and ds_values[idx] is not None:
                chart.history = list(history_by_diff.get(idx, []))
                current = float(ds_values[idx])
                if chart.history and abs(chart.history[-1][1] - current) > 0.001:
                    # dschange 快照较旧（14 处）：末变化点值以 all_data 当前值兜底
                    chart.history[-1] = (chart.history[-1][0], current)
                elif not chart.history and version is not None:
                    chart.history = [(version, current)]
            group[idx] = chart
    return songs


def _parse_dschange(
    dschange: dict,
) -> dict[str, dict[str, dict[int, list[tuple[int, float]]]]]:
    """dschange → 内部 id → kind → 难度 → 变化点序列（含 ``__increments__`` 合并）。"""
    histories: dict[str, dict[str, dict[int, list[tuple[int, float]]]]] = {}
    for key, item in dschange.items():
        if key == "__increments__":
            continue
        kind = _dschange_kind(key)
        if kind is None:  # 宴定数不采信
            continue
        per_diff = histories.setdefault(key, {}).setdefault(kind, {})
        for idx, seq in enumerate(item["ds"]):
            points = _dedup_points(seq)
            if points:
                per_diff[idx] = points
    for seg in dschange.get("__increments__", []):
        version = _source_version(seg.get("version"))
        if version is None:
            continue
        for key, item in seg.get("songs", {}).items():
            kind = _dschange_kind(key)
            if kind is None:
                continue
            per_diff = histories.setdefault(key, {}).setdefault(kind, {})
            for idx, value in enumerate(item.get("ds", [])):
                per_diff.setdefault(idx, [(version, float(value))])  # 主段优先
    return histories


def _dschange_kind(key: str) -> str | None:
    """dschange 键 → kind；宴（6 位）定数不采信，返回 None 跳过。"""
    if len(key) == 6 and key.isdigit():
        return None
    return "dx" if len(key) == 5 else "sd"


def _dedup_points(seq: dict[str, Any]) -> list[tuple[int, float]]:
    """全版本值序列（版本名→值）→ 按版本码升序、连续去重的变化点序列。"""
    ordered: list[tuple[int, float]] = []
    for name, value in seq.items():
        version = _source_version(name)
        if version is not None:
            ordered.append((version, float(value)))
    ordered.sort(key=lambda x: x[0])
    points: list[tuple[int, float]] = []
    for version, value in ordered:
        if not points or abs(points[-1][1] - value) > 0.001:
            points.append((version, value))
    return points


@dataclass
class OtogeData:
    """otoge-db 解析结果：现役条目按归一标题索引 + 下架标题集。"""

    by_title: dict[str, list[dict]] = field(default_factory=dict)
    live_titles: set[str] = field(default_factory=set)
    deleted_titles: set[str] = field(default_factory=set)  # 下架记录 ∖ 现役列表


def parse_otoge(music_ex: list[dict], deleted: list[dict]) -> OtogeData:
    """otoge-db → 标题索引；「当前下架集」= 下架记录 ∖ 现役列表（§2.17）。"""
    data = OtogeData()
    for item in music_ex:
        key = norm_title(item.get("title", ""))
        data.by_title.setdefault(key, []).append(item)
        data.live_titles.add(key)
    data.deleted_titles = {
        norm_title(x.get("title", "")) for x in deleted
    } - data.live_titles
    return data


def parse_lxns(song_list: dict) -> dict[int, Entry]:
    """落雪列表 → 国侧中间结构（version_cn / 国服谱面 / 宴 buddy，§3）。"""
    songs: dict[int, Entry] = {}
    for item in song_list.get("songs", []):
        raw_id = int(item["id"])
        song_id = raw_id % 10000
        entry = songs.setdefault(song_id, Entry(song_id=song_id))
        entry.title = entry.title or item.get("title", "")
        entry.artist = entry.artist or item.get("artist", "")
        entry.genre = entry.genre or item.get("genre", "")
        entry.bpm = entry.bpm or str(item.get("bpm", "") or "")
        diffs = item.get("difficulties", {})
        for diff in diffs.get("standard", []):
            entry.group("sd")[int(diff["difficulty"])] = _cn_chart(
                diff, int(diff["difficulty"])
            )
        for diff in diffs.get("dx", []):
            entry.group("dx")[int(diff["difficulty"])] = _cn_chart(
                diff, int(diff["difficulty"])
            )
        for diff in diffs.get("utage", []):
            level_id = utage_ids(raw_id)[1]
            chart = _cn_chart(diff, level_id)
            chart.kanji = diff.get("kanji") or _utage_kanji(entry.title)
            # 落雪 description 是固定文案，不作为 comment 来源（§3）
            chart.is_buddy = bool(diff.get("is_buddy"))
            notes = diff.get("notes") or {}
            if chart.is_buddy and "left" in notes and "right" in notes:
                chart.left = [int(notes["left"].get(k, 0) or 0) for k in _NOTE_KEYS]
                chart.right = [int(notes["right"].get(k, 0) or 0) for k in _NOTE_KEYS]
            entry.group("utage")[level_id] = chart
    return songs


def _cn_chart(diff: dict, level_id: int) -> Chart:
    notes = diff.get("notes") or {}
    chart = Chart(level_id=level_id)
    designer = diff.get("note_designer")
    chart.designer = designer if designer not in (None, "", "-") else None
    chart.notes = tuple(int(notes.get(k, 0) or 0) for k in _NOTE_KEYS)  # type: ignore[assignment]
    chart.cn_version = diff.get("version")
    chart.cn_level_value = diff.get("level_value")
    return chart


def parse_divingfish(music_data: list[dict]) -> dict[str, dict]:
    """水鱼曲库 → 对账索引（raw id 字符串 → 条目；version_cn/定数互证 + 在列判定）。"""
    return {str(item["id"]): item for item in music_data}


# ---------------------------------------------------------------------------
# 合并状态（内存 → 一次性写库）
# ---------------------------------------------------------------------------


class State:
    """规范表内存态：四张主表整库重写（单事务），合并规则「列从空变满」。"""

    def __init__(self) -> None:
        self.songs: dict[int, store.SongRow] = {}
        self.groups: dict[tuple[int, str], store.SongSheetGroup] = {}
        self.charts: dict[tuple[int, str, int], store.SongChart] = {}
        self.levels: dict[tuple[int, str, int], list[tuple[int, float]]] = {}
        self.warnings: list[str] = []

    # -- 行构造 --

    def song(self, song_id: int) -> store.SongRow:
        if song_id not in self.songs:
            self.songs[song_id] = store.SongRow(id=song_id, title="")
        return self.songs[song_id]

    def group(self, song_id: int, kind: str) -> store.SongSheetGroup:
        key = (song_id, kind)
        if key not in self.groups:
            self.groups[key] = store.SongSheetGroup(song_id=song_id, kind=kind)
        return self.groups[key]

    def chart(self, song_id: int, kind: str, level_id: int) -> store.SongChart:
        key = (song_id, kind, level_id)
        if key not in self.charts:
            self.charts[key] = store.SongChart(
                song_id=song_id, kind=kind, level_id=level_id
            )
        return self.charts[key]

    def set_history(
        self, song_id: int, kind: str, level_id: int, history: list[tuple[int, float]]
    ) -> None:
        if history:
            self.levels[(song_id, kind, level_id)] = list(history)

    def warn(self, message: str) -> None:
        self.warnings.append(message)
        logger.warning(f"songdb: {message}")

    def charts_of_song(self, song_id: int) -> dict[tuple[str, int], store.SongChart]:
        """单曲的全部谱面行（整库物化场景请用 :func:`_index_charts` 避免重复扫描）。"""
        return {
            (kind, level_id): row
            for (sid, kind, level_id), row in self.charts.items()
            if sid == song_id
        }

    def groups_of_song(self, song_id: int) -> list[store.SongSheetGroup]:
        return [row for (sid, _), row in self.groups.items() if sid == song_id]

    # -- 加载/写回 --

    @classmethod
    async def load(cls) -> "State":
        """从 DB 载入现有行（合并基线：单源失败时已有数据不丢）。"""
        state = cls()
        async with store._open_session() as session:
            for row in (await session.exec(select(store.SongRow))).all():
                state.songs[row.id] = row
            for row in (await session.exec(select(store.SongSheetGroup))).all():
                state.groups[(row.song_id, row.kind)] = row
            for row in (await session.exec(select(store.SongChart))).all():
                state.charts[(row.song_id, row.kind, row.level_id)] = row
            for row in (await session.exec(select(store.SongChartLevel))).all():
                state.levels.setdefault(
                    (row.song_id, row.kind, row.level_id), []
                ).append((row.version, row.level_value or 0.0))
        for points in state.levels.values():
            points.sort(key=lambda x: x[0])
        return state

    async def save(self) -> None:
        """整库重写四张主表（单事务）；「两侧皆无数据的整曲删除」在此自然发生。

        行对象按值重建（load 出的 ORM 实例附着在旧 session 上，跨 session 复用
        会被当作 persistent 走 UPDATE 而撞上刚 DELETE 掉的空表）。
        """
        async with store._open_session() as session:
            await session.execute(delete(store.SongChartLevel))
            await session.execute(delete(store.SongChart))
            await session.execute(delete(store.SongSheetGroup))
            await session.execute(delete(store.SongRow))
            for row in self.songs.values():
                session.add(store.SongRow(**row.model_dump()))
            for row in self.groups.values():
                session.add(store.SongSheetGroup(**row.model_dump()))
            for row in self.charts.values():
                session.add(store.SongChart(**row.model_dump()))
            for (song_id, kind, level_id), points in self.levels.items():
                for version, value in points:
                    session.add(
                        store.SongChartLevel(
                            song_id=song_id,
                            kind=kind,
                            level_id=level_id,
                            version=version,
                            level_value=value,
                        )
                    )
            await session.commit()

    # -- 定数 --

    def history_of(
        self, song_id: int, kind: str, level_id: int
    ) -> list[tuple[int, float]]:
        return self.levels.get((song_id, kind, level_id), [])

    def resolve_chart_level(
        self, song_id: int, kind: str, level_id: int, version: int | None = None
    ) -> float | None:
        """carry-forward 取定数：``version`` 为取值版本（None=最新）；无值返回 None。"""
        points = self.history_of(song_id, kind, level_id)
        if not points:
            return None
        if version is None:
            return points[-1][1]
        eligible = [p for p in points if p[0] <= version]
        return eligible[-1][1] if eligible else None

    def cn_current_version(self) -> int:
        """国服当前版本：数据 max(version_cn)，回落 maimai_py current_version。"""
        values = [
            g.version_cn for g in self.groups.values() if g.version_cn is not None
        ]
        return max(values) if values else current_version.value


def cn_level_value(history: list[tuple[int, float]], cn_current: int) -> float | None:
    """国服定数推导（§5.3）：≤ 国服当前版本最新值；区间无值取首值（同步上线的曲）。"""
    eligible = [p for p in history if p[0] <= cn_current]
    if eligible:
        return eligible[-1][1]
    return history[0][1] if history else None


# ---------------------------------------------------------------------------
# 合并管线
# ---------------------------------------------------------------------------


def apply_jp(state: State, jp: dict[int, Entry], otoge: OtogeData | None) -> None:
    """日侧骨架与充实：maimaiinfo（骨架/历史）+ otoge-db（title join 充实）。"""
    unmatched = 0
    for song_id, entry in jp.items():
        row = state.song(song_id)
        row.title = row.title or entry.title
        row.artist = row.artist or entry.artist
        row.genre = row.genre or entry.genre
        row.bpm = row.bpm or entry.bpm
        ot_items = _otoge_match(entry, otoge) if otoge else []
        for kind, charts in entry.charts.items():
            group = state.group(song_id, kind)
            if group.version is None:
                group.version = entry.versions.get(kind)
            for level_id, chart in charts.items():
                target = state.chart(song_id, kind, level_id)
                target.designer = target.designer or chart.designer
                if chart.notes != (0, 0, 0, 0, 0):
                    target.notes_tap, target.notes_hold = (
                        chart.notes[0],
                        chart.notes[1],
                    )
                    target.notes_slide, target.notes_touch = (
                        chart.notes[2],
                        chart.notes[3],
                    )
                    target.notes_break = chart.notes[4]
                if kind == "utage":
                    target.kanji = target.kanji or chart.kanji
                    target.is_buddy = target.is_buddy or chart.is_buddy
                state.set_history(song_id, kind, level_id, chart.history)
                # otoge 逐谱面 join：宴标题带前缀、与基曲不同，不能用歌级匹配结果
                chart_items = (
                    _otoge_match_title(chart.title or entry.title, otoge)
                    if otoge
                    else []
                )
                if chart_items:
                    group.date = group.date or (
                        _otoge_utage_date(chart_items)
                        if kind == "utage"
                        else _otoge_date(kind, chart_items)
                    )
                    if group.version is None:
                        group.version = _otoge_version(chart_items)
                    if kind == "utage":
                        _apply_otoge_utage(state, song_id, level_id, chart_items)
                elif ot_items and kind != "utage":
                    group.date = group.date or _otoge_date(kind, ot_items)
                    if group.version is None:
                        group.version = _otoge_version(ot_items)
        # otoge 歌级充实：封面/BPM（join 失败置空 + 汇总告警）
        if ot_items:
            row.image_url = row.image_url or (ot_items[0].get("image_url") or None)
            row.bpm = row.bpm or str(ot_items[0].get("bpm") or "")
        elif otoge is not None and norm_title(entry.title) not in otoge.deleted_titles:
            unmatched += 1
    if unmatched:
        state.warn(f"otoge-db 未收录 {unmatched} 首（封面/日期/宴字段留空，不阻塞）")


def _otoge_match_title(title: str, otoge: OtogeData) -> list[dict]:
    """按给定标题做归一 join（不消歧，返回全部同名条目）。"""
    if otoge is None:
        return []
    return otoge.by_title.get(norm_title(title)) or []


def _apply_otoge_utage(
    state: State, song_id: int, level_id: int, ot_items: list[dict]
) -> None:
    """otoge 宴字段充实（单谱面）：kanji/comment/buddy 物量 + 标级推导（仅填空）。"""
    item = next((x for x in ot_items if x.get("kanji")), None)
    if item is None or (song_id, "utage", level_id) not in state.charts:
        return
    chart = state.charts[(song_id, "utage", level_id)]
    chart.kanji = chart.kanji or item.get("kanji")
    chart.comment = chart.comment or (item.get("comment") or None)
    buddy = item.get("buddy") == "○"
    chart.is_buddy = chart.is_buddy or buddy
    if buddy and chart.notes_left is None:
        chart.notes_left = json.dumps(
            [_safe_int(item.get(f"lev_utage_left_notes_{k}")) or 0 for k in _NOTE_KEYS]
        )
        chart.notes_right = json.dumps(
            [_safe_int(item.get(f"lev_utage_right_notes_{k}")) or 0 for k in _NOTE_KEYS]
        )
    # 无历史源的宴谱退化为登场版本单行（§6）；标级推导值
    if not state.history_of(song_id, "utage", level_id):
        derived = parse_level_float(item.get("lev_utage", "") or "")
        group = state.groups.get((song_id, "utage"))
        if derived is not None and group and group.version is not None:
            state.set_history(song_id, "utage", level_id, [(group.version, derived)])


def _otoge_match(entry: Entry, otoge: OtogeData) -> list[dict]:
    """归一标题 join（歌级）；同名多义（实测 2 组）按组类型字段消歧。"""
    items = _otoge_match_title(entry.title, otoge)
    if not items:
        return []
    if len(items) == 1:
        return items
    # 'Link'：SD 条目 vs DX 条目，按本条目的组类型判别；
    # '[宴]Wonderland…'×5：轮换重复，取最新 version
    kind = next(iter(entry.charts), "sd")
    need_dx = kind == "dx"
    prefer = [x for x in items if bool(x.get("dx_lev_bas")) == need_dx] or items
    return sorted(prefer, key=lambda x: _safe_int(x.get("version")) or 0, reverse=True)


def _otoge_version(items: list[dict]) -> int | None:
    for item in items:
        version = _safe_int(item.get("version"))
        if version:
            return version
    return None


def _otoge_date(kind: str, items: list[dict]) -> int | None:
    """日期规则（§3）：sd=date_added；dx=release ‖ date_updated ‖ date_added。"""
    item = items[0]
    added = _safe_int(item.get("date_added"))
    if kind == "sd":
        return added
    return (
        _safe_int(item.get("release")) or _safe_int(item.get("date_updated")) or added
    )


def _otoge_utage_date(items: list[dict]) -> int | None:
    """宴日期：release（复活日期）直接用 ‖ date_added（§3）。"""
    for item in items:
        release = _safe_int(item.get("release"))
        if release:
            return release
    return _safe_int(items[0].get("date_added")) if items else None


def _safe_int(value: Any) -> int | None:
    try:
        return int(value) if value else None
    except (TypeError, ValueError):
        return None


def apply_cn(state: State, cn: dict[int, Entry], df: dict[str, dict] | None) -> None:
    """国侧回填：落雪（version_cn/国服谱面/宴 buddy）+ 水鱼（对账告警，不写入）。"""
    # 国服当前版本必须取**整批**载荷的最大值（合并过程中 version_cn 尚未写全，
    # 逐曲读 state 会因处理顺序得到偏小的截点，导致 §5.3 校验误报）
    all_versions = [
        c.cn_version
        for entry in cn.values()
        for charts in entry.charts.values()
        for c in charts.values()
        if c.cn_version is not None
    ]
    cn_current = max(all_versions) if all_versions else state.cn_current_version()
    for song_id, entry in cn.items():
        row = state.song(song_id)
        row.title = row.title or entry.title
        row.artist = row.artist or entry.artist
        row.genre = row.genre or entry.genre
        row.bpm = row.bpm or entry.bpm
        for kind, charts in entry.charts.items():
            if not charts:
                continue
            versions = {
                c.cn_version for c in charts.values() if c.cn_version is not None
            }
            group = state.group(song_id, kind)
            if versions:
                if len(versions) > 1:
                    state.warn(
                        f"「{entry.title}」{kind} 组内 version_cn 不一致 {versions}"
                        f"，取最小值"
                    )
                if group.version_cn is None:
                    group.version_cn = min(versions)
            for level_id, chart in charts.items():
                target = state.chart(song_id, kind, level_id)
                if target.designer is None:
                    target.designer = chart.designer
                elif (
                    chart.designer
                    and target.designer != chart.designer
                    and kind != "utage"
                ):
                    state.warn(
                        f"「{entry.title}」{kind}{level_id} 谱师两源不一致："
                        f"{target.designer} / {chart.designer}"
                    )
                if not any(
                    (
                        target.notes_tap,
                        target.notes_hold,
                        target.notes_slide,
                        target.notes_touch,
                        target.notes_break,
                    )
                ):
                    target.notes_tap, target.notes_hold = chart.notes[0], chart.notes[1]
                    target.notes_slide, target.notes_touch = (
                        chart.notes[2],
                        chart.notes[3],
                    )
                    target.notes_break = chart.notes[4]
                if kind == "utage":
                    target.kanji = target.kanji or chart.kanji
                    target.is_buddy = target.is_buddy or chart.is_buddy
                    if chart.left is not None and target.notes_left is None:
                        target.notes_left = json.dumps(chart.left)
                        target.notes_right = json.dumps(chart.right or [])
                # §5.3 校验：推导国服定数 vs 落雪实测（偏差 > 0.05 记警告）；
                # 宴定数是标级推导的代理值（§3），与实测必然有差，不参与校验
                if chart.cn_level_value and chart.history and kind != "utage":
                    derived = cn_level_value(chart.history, cn_current)
                    if (
                        derived is not None
                        and abs(derived - chart.cn_level_value) > 0.05
                    ):
                        state.warn(
                            f"「{entry.title}」{kind}{level_id} 国服定数推导 "
                            f"{derived} ≠ 落雪 {chart.cn_level_value}"
                        )
        _crosscheck_df(state, song_id, entry, df, cn_current)


def _crosscheck_df(
    state: State,
    song_id: int,
    entry: Entry,
    df: dict[str, dict] | None,
    cn_current: int,
) -> None:
    """水鱼对账：version_cn（组级 from）与定数；仅告警，写入侧唯一来源仍是落雪。"""
    if not df:
        return
    for kind, raw_id in (("sd", str(song_id)), ("dx", str(song_id + 10000))):
        item = df.get(raw_id)
        if not item:
            continue
        group = state.groups.get((song_id, kind))
        from_name = (item.get("basic_info") or {}).get("from")
        df_version = (
            divingfish_to_version[from_name].value
            if from_name in divingfish_to_version
            else None
        )
        if group and group.version_cn and df_version and group.version_cn != df_version:
            state.warn(
                f"「{entry.title}」{kind} version_cn 两源不一致："
                f"落雪 {group.version_cn} / 水鱼 {df_version}"
            )
        for idx, value in enumerate(item.get("ds") or []):
            history = state.history_of(song_id, kind, idx)
            cn_value = cn_level_value(history, cn_current) if history else None
            if cn_value and abs(cn_value - float(value)) > 0.05:
                state.warn(
                    f"「{entry.title}」{kind}{idx} 定数两源不一致："
                    f"落雪 {cn_value} / 水鱼 {value}"
                )


# ---------------------------------------------------------------------------
# 缺失同步与重建入口
# ---------------------------------------------------------------------------


def apply_missing(
    state: State,
    *,
    cn_known: set[int] | None,
    jp_known_songs: set[int] | None,
    jp_known_titles: set[str] | None,
    jp_deleted_titles: set[str] | None = None,
) -> int:
    """缺失/删除统一规则（§7.5-B）：一侧缺置该侧版本列 NULL，两侧皆无整曲删除。

    - ``cn_known``：国服在列 song_id 集；None = 双源未齐，跳过 CN 缺省同步；
    - ``jp_known_songs``：maimaiinfo 解出的 song_id 集；None = 跳过 JP 缺省同步；
    - ``jp_known_titles``：otoge-db 现役归一标题集（None = 不强求）；
    - ``jp_deleted_titles``：otoge 下架标题集（权威下架信号）。
    整曲删除以**源在列信号**判定（任一信号源认为在列即保留；信号源全缺时回落
    版本列全空），避免「版本未知」与「该服未上线」混淆造成误删。
    返回整曲删除数。
    """
    if cn_known is not None:
        for (song_id, _kind), group in state.groups.items():
            if group.version_cn is not None and song_id not in cn_known:
                group.version_cn = None
    if jp_known_songs is not None:
        for (song_id, _kind), group in state.groups.items():
            if group.version is None:
                continue
            row = state.songs.get(song_id)
            title_key = norm_title(row.title) if row else ""
            if song_id in jp_known_songs:
                if title_key and title_key in (jp_deleted_titles or set()):
                    group.version = None  # maimaiinfo 可能滞后，以 otoge 下架记录为准
                continue
            if jp_known_titles is not None and title_key in jp_known_titles:
                continue
            group.version = None
    # 两侧皆无（按源在列信号判定）→ 整曲删除；无信号源时回落版本列
    removed = 0
    for song_id in list(state.songs):
        row = state.songs[song_id]
        title_key = norm_title(row.title) if row.title else ""
        groups = state.groups_of_song(song_id)
        if jp_known_songs is not None or jp_known_titles is not None:
            jp_present = (jp_known_songs is not None and song_id in jp_known_songs) or (
                jp_known_titles is not None and title_key in jp_known_titles
            )
            if title_key and title_key in (jp_deleted_titles or set()):
                jp_present = (
                    False  # otoge 下架记录为权威 JP 缺失信号（maimaiinfo 可能滞后）
                )
        else:
            jp_present = any(g.version is not None for g in groups)
        if cn_known is not None:
            cn_present = song_id in cn_known
        else:
            cn_present = any(g.version_cn is not None for g in groups)
        if not groups or (not jp_present and not cn_present):
            state.songs.pop(song_id, None)
            for key in [k for k in state.groups if k[0] == song_id]:
                del state.groups[key]
            for key in [k for k in state.charts if k[0] == song_id]:
                del state.charts[key]
            for key in [k for k in state.levels if k[0] == song_id]:
                del state.levels[key]
            removed += 1
    return removed


async def rebuild(payloads: dict[str, Any]) -> dict[str, Any]:
    """规范表重建入口：接收各源 payload（None = 该源本次未拉取成功，跳过），写回 DB。

    payloads 键：``maimaiinfo`` / ``dschange`` / ``otoge_db`` / ``otoge_deleted`` /
    ``lxns`` / ``divingfish``。返回统计与告警（已写日志，供通知拼接）。
    """
    state = await State.load()
    jp: dict[int, Entry] = {}
    if payloads.get("maimaiinfo") is not None and payloads.get("dschange") is not None:
        jp = parse_maimaiinfo(payloads["maimaiinfo"], payloads["dschange"])
        apply_jp(state, jp, None)
    otoge: OtogeData | None = None
    if payloads.get("otoge_db") is not None and jp:
        otoge = parse_otoge(payloads["otoge_db"], payloads.get("otoge_deleted") or [])
        apply_jp(state, jp, otoge)
        # otoge 独有的无 id 条目（宴轮换快照为主）→ 暂存待归并（§7.5-A）；
        # 已知标题须含谱面自身标题（宴条目标题 ≠ 基曲标题）
        known_titles = set()
        for e in jp.values():
            if e.title:
                known_titles.add(norm_title(e.title))
            for charts in e.charts.values():
                for c in charts.values():
                    if c.title:
                        known_titles.add(norm_title(c.title))
        for item in payloads["otoge_db"]:
            title = item.get("title", "")
            if title and norm_title(title) not in known_titles:
                await upsert_pending("otoge-db", f"title:{title}", "missing_id", item)
    cn_known: set[int] | None = None
    if payloads.get("lxns") is not None:
        cn = parse_lxns(payloads["lxns"])
        df = (
            parse_divingfish(payloads["divingfish"])
            if payloads.get("divingfish") is not None
            else None
        )
        if df is not None:
            # CN 缺省同步需双源确认（§7.5-B）：单源抓取失败则不做缺省判定
            cn_known = set(cn) | {int(k) % 10000 for k in df}
        apply_cn(state, cn, df)
    removed = apply_missing(
        state,
        cn_known=cn_known,
        jp_known_songs=set(jp) if jp else None,
        jp_known_titles=set(otoge.live_titles) if otoge else None,
        jp_deleted_titles=set(otoge.deleted_titles) if otoge else set(),
    )
    await state.save()
    await _archive_raw(payloads)
    result = {
        "songs": len(state.songs),
        "groups": len(state.groups),
        "charts": len(state.charts),
        "level_points": sum(len(v) for v in state.levels.values()),
        "removed": removed,
        "warnings": state.warnings,
        "cn_current_version": state.cn_current_version(),
    }
    await store.kv_set("songdb_stat", result)
    doc = standard_json(state)
    await store.kv_set("songdb_json", doc)
    global CURRENT_FINGERPRINT
    CURRENT_FINGERPRINT = hashlib.md5(
        json.dumps(doc, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    return result


async def _archive_raw(payloads: dict[str, Any]) -> None:
    """源始留档：成功拉取的源逐条重写 ``song_source_raw``（otoge-db 以 title 为键）。"""
    rows: list[store.SongSourceRaw] = []
    if payloads.get("lxns") is not None:
        rows += [
            store.SongSourceRaw(
                source="lxns",
                song_id=str(item["id"]),
                payload=json.dumps(item, ensure_ascii=False),
            )
            for item in payloads["lxns"].get("songs", [])
        ]
    if payloads.get("divingfish") is not None:
        rows += [
            store.SongSourceRaw(
                source="divingfish",
                song_id=str(item["id"]),
                payload=json.dumps(item, ensure_ascii=False),
            )
            for item in payloads["divingfish"]
        ]
    if payloads.get("maimaiinfo") is not None:
        rows += [
            store.SongSourceRaw(
                source="maimaiinfo",
                song_id=str(key),
                payload=json.dumps(item, ensure_ascii=False),
            )
            for key, item in payloads["maimaiinfo"].items()
        ]
    if payloads.get("dschange") is not None:
        rows.append(
            store.SongSourceRaw(
                source="dschange",
                song_id="__all__",
                payload=json.dumps(payloads["dschange"], ensure_ascii=False),
            )
        )
    if payloads.get("otoge_db") is not None:
        rows += [
            store.SongSourceRaw(
                source="otoge-db",
                song_id=f"title:{item.get('title', '')}",
                payload=json.dumps(item, ensure_ascii=False),
            )
            for item in payloads["otoge_db"]
        ]
    if payloads.get("otoge_deleted") is not None:
        rows.append(
            store.SongSourceRaw(
                source="otoge-db",
                song_id="__deleted__",
                payload=json.dumps(payloads["otoge_deleted"], ensure_ascii=False),
            )
        )
    async with store._open_session() as session:
        await session.execute(delete(store.SongSourceRaw))
        # 同名多义（如 otoge-db 的 'Link'×2）会生成重复键：追加序号去重
        seen: dict[tuple[str, str], int] = {}
        for row in rows:
            count = seen.setdefault((row.source, row.song_id), 0)
            seen[(row.source, row.song_id)] = count + 1
            if count:
                row.song_id = f"{row.song_id}#{count + 1}"
            session.add(row)
        await session.commit()


# ---------------------------------------------------------------------------
# 双视图构造（§5：maimai_py 模型物化）
# ---------------------------------------------------------------------------


def _genre_of(name: str) -> Genre:
    try:
        return Genre(name)
    except ValueError:
        return Genre.maimai


def _song_version(groups: list[store.SongSheetGroup], key: str) -> int:
    values = [getattr(g, key) for g in groups if getattr(g, key) is not None]
    return min(values) if values else 0


def _chart_from_row(
    row: store.SongChart,
    kind: str,
    version: int | None,
    level_value: float | None,
) -> SongDifficulty | SongDifficultyUtage:
    """song_chart 行 → maimai_py 谱面对象（§5.5 映射；curve 由曲线缓存附加）。"""
    song_type = (
        SongType.UTAGE
        if kind == "utage"
        else (SongType.DX if kind == "dx" else SongType.STANDARD)
    )
    kwargs: dict[str, Any] = {
        "type": song_type,
        "level": level_from_value(level_value) if level_value else "?",
        "level_value": level_value or 0.0,
        "level_index": LevelIndex(0) if kind == "utage" else LevelIndex(row.level_id),
        "note_designer": row.designer or "-",
        "version": version or 0,
        "tap_num": row.notes_tap,
        "hold_num": row.notes_hold,
        "slide_num": row.notes_slide,
        "touch_num": row.notes_touch,
        "break_num": row.notes_break,
        "curve": None,
    }
    if kind == "utage":
        left = json.loads(row.notes_left) if row.notes_left else None
        right = json.loads(row.notes_right) if row.notes_right else None
        buddy = (
            BuddyNotes(
                left_tap_num=left[0],
                left_hold_num=left[1],
                left_slide_num=left[2],
                left_touch_num=left[3],
                left_break_num=left[4],
                right_tap_num=right[0],
                right_hold_num=right[1],
                right_slide_num=right[2],
                right_touch_num=right[3],
                right_break_num=right[4],
            )
            if row.is_buddy and left and right
            else None
        )
        return SongDifficultyUtage(
            **kwargs,  # type: ignore[arg-type]
            kanji=row.kanji or "",
            description=row.comment or "",  # 无 comment 用空串，不臆造固定文案（§5.5）
            diff_id=utage_diff_id(row.song_id, row.level_id),
            is_buddy=row.is_buddy,
            buddy_notes=buddy,
        )
    return SongDifficulty(**kwargs)


def build_song(
    state: State,
    song_id: int,
    scope: Scope,
    index: dict[tuple[str, int], store.SongChart] | None = None,
) -> Song | None:
    """规范表行 → maimai_py ``Song``（scope 决定用国服列还是日服列，§5.1/§5.2）。

    ``index`` 为 ``(kind, level_id) → SongChart`` 索引；整库物化时由 :func:`all_songs`
    传入以避免逐曲扫描。
    """
    row = state.songs.get(song_id)
    if row is None:
        return None
    version_key = "version_cn" if scope == "cn" else "version"
    groups = state.groups_of_song(song_id)
    if not any(getattr(g, version_key) is not None for g in groups):
        return None  # 该 scope 下无任何谱面组（如 JP-only 曲的 CN 视图）
    if index is None:
        index = state.charts_of_song(song_id)
    standard, dx, utage = [], [], []
    for group in groups:
        group_version = getattr(group, version_key)
        if group_version is None:
            continue  # 该服当前无此谱面组
        kind = group.kind
        for (kind_, level_id), chart_row in index.items():
            if kind_ != kind:
                continue
            if kind == "utage":
                level_value = state.resolve_chart_level(song_id, kind, level_id)
            elif scope == "cn":
                level_value = cn_level_value(
                    state.history_of(song_id, kind, level_id),
                    state.cn_current_version(),
                )
            else:
                level_value = state.resolve_chart_level(song_id, kind, level_id)
            diff = _chart_from_row(chart_row, kind, group_version, level_value)
            if kind == "utage":
                utage.append(diff)
            elif kind == "sd":
                standard.append(diff)
            else:
                dx.append(diff)
    try:
        bpm = int(float(row.bpm)) if row.bpm else 0
    except (TypeError, ValueError):
        bpm = 0
    return Song(
        id=song_id,
        title=row.title,
        artist=row.artist,
        genre=_genre_of(row.genre),
        bpm=bpm,
        map=None,
        version=_song_version(groups, version_key),
        rights=None,
        aliases=None,
        disabled=False,
        difficulties=SongDifficulties(standard=standard, dx=dx, utage=utage),
    )


def all_songs(state: State, scope: Scope) -> list[Song]:
    """整库物化（scope 内至少一个组有数据的曲），按 id 升序。"""
    index: dict[int, dict[tuple[str, int], store.SongChart]] = {}
    for (song_id, kind, level_id), row in state.charts.items():
        index.setdefault(song_id, {})[(kind, level_id)] = row
    result = []
    for song_id in sorted(state.songs):
        if song := build_song(state, song_id, scope, index.get(song_id, {})):
            result.append(song)
    return result


# ---------------------------------------------------------------------------
# 标准 JSON（01 文档结构）与指纹
# ---------------------------------------------------------------------------


def song_standard_json(state: State, song_id: int) -> dict[str, Any] | None:
    """单曲 → 01 文档结构（``level`` 为变化点序列 ``[[version, value], …]``）。"""
    row = state.songs.get(song_id)
    if row is None:
        return None
    by_group: dict[str, list[tuple[int, store.SongChart]]] = {}
    for (sid, kind, level_id), chart in state.charts.items():
        if sid == song_id:
            by_group.setdefault(kind, []).append((level_id, chart))
    sheets: dict[str, Any] = {}
    for (sid, kind), group in state.groups.items():
        if sid != song_id:
            continue
        contents = []
        for level_id, chart in sorted(by_group.get(kind, [])):
            content: dict[str, Any] = {
                "level_id": level_id,
                "level": [
                    [v, val] for v, val in state.history_of(song_id, kind, level_id)
                ],
                "designer": chart.designer,
                "notes": {
                    "tap": chart.notes_tap,
                    "hold": chart.notes_hold,
                    "slide": chart.notes_slide,
                    "touch": chart.notes_touch,
                    "break": chart.notes_break,
                },
            }
            if kind == "utage":
                content["kanji"] = chart.kanji
                content["comment"] = chart.comment
                content["is_buddy"] = chart.is_buddy
                content["notes_left"] = (
                    json.loads(chart.notes_left) if chart.notes_left else None
                )
                content["notes_right"] = (
                    json.loads(chart.notes_right) if chart.notes_right else None
                )
            contents.append(content)
        sheets[kind] = {
            "version": group.version,
            "version_cn": group.version_cn,
            "date": group.date,
            "contents": contents,
        }
    return {
        "id": song_id,
        "title": row.title,
        "artist": row.artist,
        "genre": row.genre,
        "bpm": row.bpm,
        "image_url": row.image_url,
        "sheets": sheets,
    }


def standard_json(state: State) -> dict[str, Any]:
    """全库标准 JSON（kv_cache ``songdb_json``；外部源合并与指纹的契约格式）。"""
    result = {}
    for song_id in sorted(state.songs):
        if song := song_standard_json(state, song_id):
            result[str(song_id)] = song
    return result


def fingerprint(state: State) -> str:
    """规范表内容指纹（自定义 provider ``_hash`` 用；数据变更即自动重建缓存）。"""
    raw = json.dumps(standard_json(state), ensure_ascii=False, sort_keys=True)
    return hashlib.md5(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# 取数编排（ext 直连，不碰 MaimaiClient）与国服更新检测
# ---------------------------------------------------------------------------


def detect_cn_update(
    known: set[int], lx_ids: set[int], df_ids: set[int]
) -> tuple[set[int], set[int]] | None:
    """国服更新判定（§7.2）：两源新增集交集非空（新歌）或两源消失集交集非空（下架）。

    ``known`` 为上一轮两源并集（song_id 级）；空集视为首次运行（仅建基线，不触发）。
    返回 ``(added, removed)``；无更新返回 None。
    """
    if not known:
        return None
    lx, df = {i % 10000 for i in lx_ids}, {i % 10000 for i in df_ids}
    known_norm = {i % 10000 for i in known}
    added = (lx - known_norm) & (df - known_norm)
    removed = (known_norm - lx) & (known_norm - df)
    if added or removed:
        return added, removed
    return None


async def refresh_all(
    *,
    include_cn: bool = True,
    include_jp: bool = True,
) -> dict[str, Any]:
    """拉取全部可用源并重建规范表（单源失败跳过不阻塞），返回合并统计。

    - ``include_cn``：落雪（notes 全量）+ 水鱼；CN 检测用轻载荷由轮询层另行拉取；
    - ``include_jp``：maimaiinfo all_data+dschange + otoge-db 现役/下架。
    """
    from .ext import lxns as ext_lxns
    from .ext import otoge_db as ext_otoge
    from .ext import divingfish as ext_df
    from .ext import maimaiinfo as ext_info

    payloads: dict[str, Any] = {}
    if include_jp:
        for name, fetch in (
            ("maimaiinfo", ext_info.fetch_all_data),
            ("dschange", ext_info.fetch_dschange),
            ("otoge_db", ext_otoge.fetch_music_ex),
            ("otoge_deleted", ext_otoge.fetch_music_ex_deleted),
        ):
            try:
                payloads[name] = await fetch()
            except Exception as e:
                logger.warning(f"songdb: {name} 拉取失败，本次跳过（{e}）")
    if include_cn:
        try:
            payloads["lxns"] = await ext_lxns.fetch_song_list(notes=True)
        except Exception as e:
            logger.warning(f"songdb: 落雪曲库拉取失败，本次跳过（{e}）")
        try:
            payloads["divingfish"] = await ext_df.fetch_music_data()
        except Exception as e:
            logger.warning(f"songdb: 水鱼曲库拉取失败，本次跳过（{e}）")
    result = await rebuild(payloads)
    try:
        await flush_pending()
    except Exception:
        logger.exception("songdb: 待归并清理失败（不影响规范表）")
    return result


# ---------------------------------------------------------------------------
# 待归并（song_pending）与外部补充源（§7.5-A/C）
# ---------------------------------------------------------------------------


async def flush_pending() -> int:
    """批量归并 ``song_pending``：id 已到位（标题 join 命中）即删行，否则计数重试。

    入表本身由常规合并完成（id 到位后 maimaiinfo/otoge join 自然生效），此处只做
    收尾清理与重试计数（超阈值由调用方降频）。返回本次归并条数。
    """
    from datetime import datetime

    async with store._open_session() as session:
        rows = list((await session.exec(select(store.SongPending))).all())
        state = await State.load()
        titles = {norm_title(row.title) for row in state.songs.values() if row.title}
        merged = 0
        for row in rows:
            key = row.key.removeprefix("title:")
            if row.reason == "missing_id" and norm_title(key) in titles:
                await session.delete(row)
                merged += 1
                continue
            row.attempts += 1
            row.last_seen = datetime.now()
            session.add(row)
        await session.commit()
        if merged:
            logger.info(f"songdb: 待归并 {merged} 条已入主表并清理")
        return merged


async def upsert_pending(source: str, key: str, reason: str, payload: dict) -> None:
    """构造器遇到主键不可得的曲目时 upsert 暂存（幂等）。"""
    from datetime import datetime

    async with store._open_session() as session:
        row = (
            await session.exec(
                select(store.SongPending).where(
                    store.SongPending.source == source, store.SongPending.key == key
                )
            )
        ).first()
        if row is None:
            row = store.SongPending(source=source, key=key, reason=reason, payload="{}")
        row.payload = json.dumps(payload, ensure_ascii=False)
        row.last_seen = datetime.now()
        row.attempts += 1
        session.add(row)
        await session.commit()


async def apply_external_sources() -> dict[str, Any]:
    """读取并应用外部补充源（§7.5-C）：仅日服侧，标准 JSON，override/fill 字段级合并。

    配置 ``awmc_extra_song_sources`` 每项为路径/URL，可带 ``::fill``/``::override``
    后缀指定该源合并模式（默认 override，人工即权威）。返回
    {sources, applied, changed}；内容哈希记 kv_cache，变化才写库。
    """
    import hashlib as _hashlib
    from pathlib import Path

    import httpx

    from ..config import plugin_config

    specs = plugin_config.awmc_extra_song_sources
    summary: dict[str, Any] = {"sources": len(specs), "applied": 0, "changed": False}
    if not specs:
        return summary
    docs: list[tuple[str, str, dict]] = []  # (来源名, mode, 文档)
    for spec in specs:
        source, _, mode = spec.partition("::")
        mode = mode if mode in ("fill", "override") else "override"
        try:
            if source.startswith(("http://", "https://")):
                async with httpx.AsyncClient(timeout=30) as http:
                    resp = await http.get(source)  # 匿名读取，不加鉴权头（作者拍板）
                    resp.raise_for_status()
                    data = resp.json()
            else:
                data = json.loads(
                    await asyncio.to_thread(Path(source).read_text, encoding="utf-8")
                )
            docs.append((source, mode, data))
        except Exception as e:
            logger.error(f"songdb: 外部补充源 {source} 读取失败，跳过（{e}）")
    digest = _hashlib.md5(
        json.dumps(
            [(m, d) for _, m, d in docs], ensure_ascii=False, sort_keys=True
        ).encode()
    ).hexdigest()
    prev = await store.kv_get("songdb_extra_hash")
    summary["hash"] = digest
    if digest != prev and docs:
        summary["applied"] = await _merge_extra_docs(docs)
        await store.kv_set("songdb_extra_hash", digest)
        summary["changed"] = True
    return summary


async def _merge_extra_docs(docs: list[tuple[str, str, dict]]) -> int:
    """外部标准 JSON 合并进主表：**只允许日服侧**，写 ``version_cn`` 忽略并告警。"""
    from pathlib import Path

    state = await State.load()
    applied = 0
    for name, mode, doc in docs:
        for song_id_str, song_doc in doc.items():
            if not song_id_str.isdigit():
                logger.warning(f"songdb: 外部源 {name} 非法 id {song_id_str!r}，跳过")
                continue
            song_id = int(song_id_str)
            row = state.songs.get(song_id)
            if row is None:
                continue  # 只补充已存在的曲（骨架外的新曲等 id 到位由常规管线处理）
            for field_name in ("title", "artist", "genre", "bpm", "image_url"):
                if field_name in song_doc and (
                    mode == "override" or not getattr(row, field_name)
                ):
                    setattr(row, field_name, song_doc[field_name])
            for kind, sheet in (song_doc.get("sheets") or {}).items():
                if sheet.get("version_cn") is not None:
                    logger.warning(
                        f"songdb: 外部源 {name} 试图写 version_cn（id={song_id}），"
                        "已忽略（国服唯二源）"
                    )
                group = state.group(song_id, kind)
                if sheet.get("version") is not None and (
                    mode == "override" or group.version is None
                ):
                    group.version = sheet["version"]
                if sheet.get("date") is not None and (
                    mode == "override" or group.date is None
                ):
                    group.date = sheet["date"]
                for content in sheet.get("contents") or []:
                    level_id = content.get("level_id")
                    if level_id is None:
                        continue
                    target = state.chart(song_id, kind, level_id)
                    if content.get("designer") and (
                        mode == "override" or not target.designer
                    ):
                        target.designer = content["designer"]
                    notes = content.get("notes") or {}
                    if notes and (mode == "override" or not target.notes_tap):
                        target.notes_tap = int(notes.get("tap", 0) or 0)
                        target.notes_hold = int(notes.get("hold", 0) or 0)
                        target.notes_slide = int(notes.get("slide", 0) or 0)
                        target.notes_touch = int(notes.get("touch", 0) or 0)
                        target.notes_break = int(notes.get("break", 0) or 0)
                    if kind == "utage":
                        if content.get("comment") and (
                            mode == "override" or not target.comment
                        ):
                            target.comment = content["comment"]
                    history = content.get("level") or []
                    if history:
                        points = [(int(v), float(val)) for v, val in history]
                        if mode == "override" or not state.history_of(
                            song_id, kind, level_id
                        ):
                            state.set_history(song_id, kind, level_id, points)
                    applied += 1
    # 外部片段归档（source=extra:<名称>；同键多 mode 以 mode 后缀区分，重放先清旧行）
    origins = {
        f"extra:{Path(name).name if not name.startswith('http') else name}"
        for name, _m, _d in docs
    }
    async with store._open_session() as session:
        for origin in origins:
            await session.execute(
                delete(store.SongSourceRaw).where(
                    col(store.SongSourceRaw.source) == origin
                )
            )
        for idx, (name, mode, doc) in enumerate(docs):
            origin = f"extra:{Path(name).name if not name.startswith('http') else name}"
            session.add(
                store.SongSourceRaw(
                    source=origin,
                    song_id="__doc__" if idx == 0 else f"__doc__#{mode}#{idx + 1}",
                    payload=json.dumps(doc, ensure_ascii=False),
                )
            )
        await session.commit()
    if applied:
        await state.save()
    logger.info(f"songdb: 外部补充源应用完成（{applied} 处谱面级字段）")
    return applied
