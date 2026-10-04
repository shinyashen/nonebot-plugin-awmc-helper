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
import time
import asyncio
import hashlib
import unicodedata
from typing import Any, Literal
from dataclasses import field, dataclass

from nonebot import logger
from sqlmodel import col, delete, select
from maimai_py import Version, current_version
from maimai_py.enums import (
    Genre,
    SongType,
    LevelIndex,
    name_to_genre,
    divingfish_to_version,
)
from maimai_py.models import (
    Song,
    BuddyNotes,
    SongDifficulty,
    SongDifficulties,
    SongDifficultyUtage,
)

from . import store
from .http import create_smart_client
from ..constants import (
    GENRE_TO_ZH,
    DX_ID_OFFSET,
    UTAGE_ID_BASE,
    DX_VERSION_CODES,
    UTAGE_LEVEL_STRIDE,
    SOURCE_NAME_TO_VERSION,
    normalize_text,
    level_from_value,
)

Scope = Literal["cn", "jp"]

CURRENT_FINGERPRINT: str | None = None
"""规范表内容指纹（进程内缓存，rebuild 末尾刷新；供 provider._hash 同步读取）。"""

# ---------------------------------------------------------------------------
# 解析辅助
# ---------------------------------------------------------------------------

_NOTE_KEYS = ("tap", "hold", "slide", "touch", "break")

# 定数变化点去重容差：与相邻变化点差值 ≤ 容差视作同值不另立变化点
# （dschange 连续同值合并、与 all_data 末点互校共用）
_DS_DEDUP_TOL = 0.001
# §5.3 国服定数校验容差：推导定数与落雪/水鱼实测偏差超过记警告
# （宴定数是标级推导的代理值，必然有差，不参与该校验）
_CN_DS_WARN_TOL = 0.05
# 01 文档扁平 level 展开循环的理论上限保护（正常到轴末即 break，防异常
# 历史把展开推入死循环）
_FLAT_AXIS_GUARD = 4096
# 浮点同值判定容差：差值在 1e-9 内视作不变（外部源幂等合并/扁平列合并用）
_EPS = 1e-9
# Version 枚举 DX 段相邻成员步进（与 maimai_py enums 对齐；版本码超出
# 枚举已知值时的 +500 递推依据）
_DX_VERSION_STRIDE = 500


def norm_title(title: str) -> str:
    """标题归一（otoge-db title join 用）：NFKC + 去空白 + 小写。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", title)).lower()


# ---------------------------------------------------------------------------
# 中二/音击侧别（「オンゲキ＆CHUNITHM」大类出身判定，song-db-design §2.22 /
# karenbot-combo-notes §12.3/§12.4）：norm_title ∈ ongeki 原创栏标题集 → 音击，
# 其余（大类内）默认中二。集合随 refresh_all 抓取入库（kv_cache 单行）。
# ---------------------------------------------------------------------------

_ongeki_origin_titles: "frozenset[str] | None" = None
"""音击原创栏归一标题集；None = 进程未加载（消费侧经 :func:`ongeki_titles`）。"""


def ongeki_titles() -> frozenset[str]:
    """音击原创栏标题集。

    未加载（冷启动且 kv 无值）视作空集——「音击」条件得谱面集空文案、
    「中二」=大类全部，与断网降级语义一致。
    """
    return _ongeki_origin_titles if _ongeki_origin_titles is not None else frozenset()


async def ensure_ongeki_titles() -> None:
    """进程内集合惰性加载（kv_cache；幂等，combo 消费入口在谓词求值前调用）。"""
    global _ongeki_origin_titles
    if _ongeki_origin_titles is not None:
        return
    stored = await store.kv_get("ongeki_origin_titles")
    _ongeki_origin_titles = frozenset(stored or ())


def _ongeki_origin_titles_of(entries: "list[dict]") -> frozenset[str]:
    """otoge-db ongeki 条目 → 原创栏（``category=オンゲキ``）归一标题集。"""
    return frozenset(
        norm_title(x["title"]) for x in entries if x.get("category") == "オンゲキ"
    )


async def store_ongeki_titles(payload: "list[dict] | None") -> bool:
    """refresh_all 抓取产物入库（kv_cache 单行替换 + 进程缓存更新）。

    ``payload=None``（一体抓取失败）→ 保留旧集合（断网降级现成语义），返回
    False。全量替换语义：集合定义 = ongeki 原创栏标题集，现役与下架一体
    抓取保证不缺下架档。
    """
    global _ongeki_origin_titles
    if payload is None:
        return False
    titles = _ongeki_origin_titles_of(payload)
    if not titles:
        return False
    await store.kv_set("ongeki_origin_titles", sorted(titles))
    _ongeki_origin_titles = titles
    return True


def utage_ids(diff_id: int) -> tuple[int, int]:
    """宴谱 6 位机台内部 id → (song_id, level_id)。

    已实测：100018→(18, 0)、161852→(1852, 6)。
    """
    return diff_id % UTAGE_LEVEL_STRIDE, (diff_id // UTAGE_LEVEL_STRIDE) % 10


def utage_diff_id(song_id: int, level_id: int) -> int:
    """(song_id, level_id) → 宴谱 6 位机台内部 id（maimai_py diff_id 同规则）。"""
    return UTAGE_ID_BASE + level_id * UTAGE_LEVEL_STRIDE + song_id


def parse_level_float(level: str) -> float | None:
    """宴标级串 → 标级浮点：有 + 一律 .7、无 + 一律 .0（作者口径，勿猜其他分布）。"""
    text = (level or "").strip().rstrip("?")
    match = re.fullmatch(r"(\d+)(\+?)", text)
    if not match:
        return None
    return int(match.group(1)) + (0.7 if match.group(2) else 0.0)


def _source_version(name: str | None) -> int | None:
    """数据源版本名 → 版本码（本地表优先、库表兜底；未知返回 None）。"""
    if not name:
        return None
    ver = SOURCE_NAME_TO_VERSION.get(name)
    if ver is None:
        ver = divingfish_to_version.get(name)
    return ver.value if ver is not None else None


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


def _utage_notes(raw: list) -> tuple[int, int, int, int, int]:
    """宴谱 notes 数组 → 五元组：宽度定语义——4 元组末位是 break（SD 约定，
    otoge 同谱面交叉验证 21:0），5 元组才含 touch（DX 约定）。"""
    return _notes_tuple(raw, len(raw) >= 5)


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
    """曲级中间结构；``versions`` 按组类型分开（SD/DX key 的 from 可不同）。

    ``disabled`` 仅落雪侧使用：落雪对删除/禁用曲**打标留在列表**（官方文档：
    disabled 为 true 不计入 Best 50），并非从列表移除——它是 CN 缺失的信号之一。
    """

    song_id: int
    title: str = ""
    artist: str = ""
    genre: str = ""
    bpm: str = ""
    disabled: bool = False
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
            song_id, level_id = int(key) % DX_ID_OFFSET, 0
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
            # 「未知」等：定数历史的首版本可补（dschange 自该曲登场版本起记录）。
            # 仅 sd/dx 生效：宴 key 恒 6 位、_dschange_kind 返回 None，
            # histories 从无宴键（宴版本由 otoge 的 release 补，见下）
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
            # 宴谱物量：charts 1 张 = 普通谱，2 张 = buddy（[0]=左、[1]=右，
            # 与 maimai_py 自带 provider 的 charts 形态约定一致）。主物量列
            # 入库即存合计（buddy = 左右之和）：dx 星按主物量算 max DX，留 0
            # 会除零（2026-09-26 线上实测）；左右明细另存 notes_left/right
            utage_charts = item.get("charts") or []
            if len(utage_charts) == 2:
                chart.is_buddy = True
                chart.left = list(_utage_notes(utage_charts[0].get("notes") or []))
                chart.right = list(_utage_notes(utage_charts[1].get("notes") or []))
                chart.notes = (
                    chart.left[0] + chart.right[0],
                    chart.left[1] + chart.right[1],
                    chart.left[2] + chart.right[2],
                    chart.left[3] + chart.right[3],
                    chart.left[4] + chart.right[4],
                )
            elif len(utage_charts) == 1:
                chart.notes = _utage_notes(utage_charts[0].get("notes") or [])
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
                if (
                    chart.history
                    and abs(chart.history[-1][1] - current) > _DS_DEDUP_TOL
                ):
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
        if not points or abs(points[-1][1] - value) > _DS_DEDUP_TOL:
            points.append((version, value))
    return points


@dataclass
class OtogeData:
    """otoge-db 解析结果：现役条目按归一标题索引 + 下架标题集。"""

    by_title: dict[str, list[dict]] = field(default_factory=dict)
    live_titles: set[str] = field(default_factory=set)
    deleted_titles: set[str] = field(default_factory=set)  # 下架记录 ∖ 现役列表


# otoge-db catcode → maimai_py Genre 值（＆/连写差异归一）
OTOGE_CATCODE_TO_GENRE: dict[str, str] = {
    "maimai": "maimai",
    "POPS＆アニメ": "POPSアニメ",
    "ゲーム＆バラエティ": "ゲームバラエティ",
    "niconico＆ボーカロイド": "niconicoボーカロイド",
    "東方Project": "東方Project",
    "オンゲキ＆CHUNITHM": "オンゲキCHUNITHM",
    "宴会場": "宴会場",
}


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
        # 落雪 raw_id 三命名空间（SD/DX/宴）统一取根：DX_OFFSET 与宴步进同为 10000
        song_id = raw_id % DX_ID_OFFSET
        entry = songs.setdefault(song_id, Entry(song_id=song_id))
        entry.title = entry.title or item.get("title", "")
        entry.artist = entry.artist or item.get("artist", "")
        entry.genre = entry.genre or item.get("genre", "")
        entry.bpm = entry.bpm or str(item.get("bpm", "") or "")
        entry.disabled = entry.disabled or bool(item.get("disabled"))
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


def _df_known_kinds(music_data: dict[str, dict]) -> set[tuple[int, str]]:
    """水鱼在列集（(song_id, kind)；id 形状定 kind：≤4 位 sd、5 位 dx、6 位宴）。

    id 形状约定引用 constants 的 UTAGE_ID_BASE/DX_ID_OFFSET（不双写裸数字）；
    与 maimai_py ``SongType._from_id`` 差一个边界等号（上游为严格 ``>``，
    id 恰为 10000/100000 时本函数判 dx/宴、上游判 STANDARD，现实数据到不了
    边界值）。
    """
    known: set[tuple[int, str]] = set()
    for raw_id in music_data:
        i = int(raw_id)
        if i >= UTAGE_ID_BASE:
            known.add((i % DX_ID_OFFSET, "utage"))
        elif i >= DX_ID_OFFSET:
            known.add((i % DX_ID_OFFSET, "dx"))
        else:
            known.add((i, "sd"))
    return known


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
        # 单曲级懒建索引：逐曲读取 O(1)（原先每曲全表线性扫描，整库物化 O(n²)）
        self._charts_by_song: (
            dict[int, dict[tuple[str, int], store.SongChart]] | None
        ) = None
        self._groups_by_song: dict[int, list[store.SongSheetGroup]] | None = None

    # -- 行构造 --

    def song(self, song_id: int) -> store.SongRow:
        if song_id not in self.songs:
            self.songs[song_id] = store.SongRow(id=song_id, title="")
        return self.songs[song_id]

    def group(self, song_id: int, kind: str) -> store.SongSheetGroup:
        key = (song_id, kind)
        if key not in self.groups:
            self.groups[key] = store.SongSheetGroup(song_id=song_id, kind=kind)
            self._groups_by_song = None
        return self.groups[key]

    def chart(self, song_id: int, kind: str, level_id: int) -> store.SongChart:
        key = (song_id, kind, level_id)
        if key not in self.charts:
            self.charts[key] = store.SongChart(
                song_id=song_id, kind=kind, level_id=level_id
            )
            self._charts_by_song = None
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
        """单曲的全部谱面行（懒建索引，O(1) 定位）。"""
        if self._charts_by_song is None:
            by_song: dict[int, dict[tuple[str, int], store.SongChart]] = {}
            for (sid, kind, level_id), row in self.charts.items():
                by_song.setdefault(sid, {})[(kind, level_id)] = row
            self._charts_by_song = by_song
        return self._charts_by_song.get(song_id, {})

    def groups_of_song(self, song_id: int) -> list[store.SongSheetGroup]:
        if self._groups_by_song is None:
            by_song: dict[int, list[store.SongSheetGroup]] = {}
            for (sid, _kind), row in self.groups.items():
                by_song.setdefault(sid, []).append(row)
            self._groups_by_song = by_song
        return self._groups_by_song.get(song_id, [])

    # -- 加载/写回 --

    @classmethod
    async def load(cls) -> "State":
        """从 DB 载入现有行（合并基线：单源失败时已有数据不丢）。"""
        state = cls()
        async with store.session() as session:
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
        _normalize_utage_notes(self)
        async with store.session() as session:
            # SQLModel 已弃用 session.execute，delete/insert 一律走 exec
            await session.exec(delete(store.SongChartLevel))
            await session.exec(delete(store.SongChart))
            await session.exec(delete(store.SongSheetGroup))
            await session.exec(delete(store.SongRow))
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
        """国服当前版本：数据 max(version_cn)，回落 maimai_py current_version。

        与 ``dan.cn_current_version`` 同口径双实现（这里走内存态、那里走
        库查询），改一侧须同步另一侧。
        """
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


def _fill_song_basics(state: State, song_id: int, entry) -> store.SongRow:
    """行级字段「列从空变满」回填（apply_jp/apply_cn 共用）。"""
    row = state.song(song_id)
    row.title = row.title or entry.title
    row.artist = row.artist or entry.artist
    row.genre = row.genre or entry.genre
    row.bpm = row.bpm or entry.bpm
    return row


def _notes_left_empty(raw: str | None) -> bool:
    """buddy 左右物量 JSON 是否「未填」：None 或全零（全零是历史写入的
    垃圾值——真实 buddy 谱两侧物量不可能同时为 0，按空对待允许修复）。"""
    if raw is None:
        return True
    return not any(json.loads(raw))


def _chart_notes_of(row) -> "tuple[int, int, int, int, int]":
    """谱面行物量五元组读取（[Tap, Hold, Slide, Touch, Break]，01 文档线格式）。"""
    return (
        row.notes_tap,
        row.notes_hold,
        row.notes_slide,
        row.notes_touch,
        row.notes_break,
    )


def _set_notes(row, notes: "tuple[int, int, int, int, int]") -> None:
    """谱面行物量五元组整体写入（原先是五行拆包赋值在四五个调用点各写一份）。"""
    row.notes_tap, row.notes_hold, row.notes_slide, row.notes_touch, row.notes_break = (
        notes
    )


def _fill_utage_fields(target: store.SongChart, chart) -> None:
    """宴谱 kanji/is_buddy/左右物量「列从空变满」回填（apply_jp/apply_cn 共用）。"""
    target.kanji = target.kanji or chart.kanji
    target.is_buddy = target.is_buddy or chart.is_buddy
    if chart.left is not None and _notes_left_empty(target.notes_left):
        target.notes_left = json.dumps(chart.left)
        target.notes_right = json.dumps(chart.right or [])


def _normalize_utage_notes(state: State) -> None:
    """buddy 宴谱主物量不变式：主列 ≡ 左右两组之和（入库即正确）。

    dx 星按主物量算 max DX，buddy 行主列留 0 会在 maimai_py `_get_extended`
    除零（2026-09-26 线上实测）；各来源只保证左右明细与主列其一，主列在此
    统一归一，旧库行随下次写回一并修正。挂载点为 State.save，重建/外部源
    补充全部经过。左右全零（垃圾值）时不动主列——主列可能已由其他源填对，
    左右待 `_fill_utage_fields` 的全零修复补齐后下轮归一。
    """
    for (_song_id, kind, _level_id), row in state.charts.items():
        if kind != "utage" or not row.is_buddy or row.notes_left is None:
            continue
        left = json.loads(row.notes_left)
        right = json.loads(row.notes_right) if row.notes_right else [0] * 5
        combined = (
            left[0] + right[0],
            left[1] + right[1],
            left[2] + right[2],
            left[3] + right[3],
            left[4] + right[4],
        )
        if any(combined):
            _set_notes(row, combined)


def apply_jp(state: State, jp: dict[int, Entry], otoge: OtogeData | None) -> None:
    """日侧骨架与充实：maimaiinfo（骨架/历史）+ otoge-db（title join 充实）。"""
    unmatched = 0
    for song_id, entry in jp.items():
        row = _fill_song_basics(state, song_id, entry)
        otoge_items = _otoge_match(entry, otoge) if otoge else []
        if not row.genre:
            # maimaiinfo 不含分类：日服限定曲的分类从 otoge-db catcode 映射补齐
            for item in otoge_items:
                mapped = OTOGE_CATCODE_TO_GENRE.get(item.get("catcode") or "")
                if mapped:
                    row.genre = mapped
                    break
        for kind, charts in entry.charts.items():
            group = state.group(song_id, kind)
            if group.version is None:
                group.version = entry.versions.get(kind)
            for level_id, chart in charts.items():
                target = state.chart(song_id, kind, level_id)
                target.designer = target.designer or chart.designer
                if chart.notes != (0, 0, 0, 0, 0):
                    _set_notes(target, chart.notes)
                if kind == "utage":
                    _fill_utage_fields(target, chart)
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
                elif otoge_items and kind != "utage":
                    group.date = group.date or _otoge_date(kind, otoge_items)
                    if group.version is None:
                        group.version = _otoge_version(otoge_items)
        # otoge 歌级充实：封面/BPM（join 失败置空 + 汇总告警）
        if otoge_items:
            row.image_url = row.image_url or (otoge_items[0].get("image_url") or None)
            row.bpm = row.bpm or str(otoge_items[0].get("bpm") or "")
        elif otoge is not None and norm_title(entry.title) not in otoge.deleted_titles:
            unmatched += 1
    if unmatched:
        state.warn(f"otoge-db 未收录 {unmatched} 首（封面/日期/宴字段留空，不阻塞）")


def _otoge_match_title(title: str, otoge: OtogeData) -> list[dict]:
    """按给定标题做归一 join（不消歧，返回全部同名条目）。"""
    if otoge is None:
        return []
    return otoge.by_title.get(norm_title(title)) or []


_OTOLEVEL_IDS = {"bas": 0, "adv": 1, "exp": 2, "mas": 3, "rem": 4}


def _fill_row_from_otoge(state: State, song_id: int, item: dict) -> None:
    """otoge-db 直接充实规范表已有行（maimaiinfo 未收录、外部源先建行的曲）。

    仅填空不覆盖（与 apply_jp 的 join 充实同口径）；谱面物量按
    ``dx_lev_<难度>_notes_*``（SD 为 ``lev_<难度>_notes_*``）字段对位，
    设计者仅 exp/mas 字段可得，rem 仅 SD 存在。
    """
    row = state.songs.get(song_id)
    if row is None:
        return
    row.image_url = row.image_url or (item.get("image_url") or None)
    row.bpm = row.bpm or str(item.get("bpm") or "")
    if not row.genre:
        row.genre = OTOGE_CATCODE_TO_GENRE.get(item.get("catcode") or "") or ""
    kind = "dx" if any(k.startswith("dx_lev") for k in item) else "sd"
    prefix = "dx_lev" if kind == "dx" else "lev"
    group = state.group(song_id, kind)
    if group.date is None:
        group.date = _otoge_date(kind, [item])
    for suffix, level_id in _OTOLEVEL_IDS.items():
        if kind == "dx" and suffix == "rem":
            continue
        target = state.chart(song_id, kind, level_id)
        designer = item.get(f"{prefix}_{suffix}_designer")
        if designer and not target.designer:
            target.designer = designer
        tap = _safe_int(item.get(f"{prefix}_{suffix}_notes_tap"))
        if tap is None:
            continue
        if _chart_notes_of(target) != (0, 0, 0, 0, 0):
            continue  # 已有物量（maimaiinfo 权威）不覆盖
        _set_notes(
            target,
            (
                tap,
                _safe_int(item.get(f"{prefix}_{suffix}_notes_hold")) or 0,
                _safe_int(item.get(f"{prefix}_{suffix}_notes_slide")) or 0,
                _safe_int(item.get(f"{prefix}_{suffix}_notes_touch")) or 0,
                _safe_int(item.get(f"{prefix}_{suffix}_notes_break")) or 0,
            ),
        )


def _apply_otoge_utage(
    state: State, song_id: int, level_id: int, otoge_items: list[dict]
) -> None:
    """otoge 宴字段充实（单谱面）：kanji/comment/buddy 物量 + 标级推导（仅填空）。"""
    item = next((x for x in otoge_items if x.get("kanji")), None)
    if item is None or (song_id, "utage", level_id) not in state.charts:
        return
    chart = state.charts[(song_id, "utage", level_id)]
    chart.kanji = chart.kanji or item.get("kanji")
    chart.comment = chart.comment or (item.get("comment") or None)
    buddy = item.get("buddy") == "○"
    chart.is_buddy = chart.is_buddy or buddy
    main_empty = not any(_chart_notes_of(chart))
    if buddy and _notes_left_empty(chart.notes_left):
        chart.notes_left = json.dumps(
            [_safe_int(item.get(f"lev_utage_left_notes_{k}")) or 0 for k in _NOTE_KEYS]
        )
        chart.notes_right = json.dumps(
            [_safe_int(item.get(f"lev_utage_right_notes_{k}")) or 0 for k in _NOTE_KEYS]
        )
    elif not buddy and not chart.is_buddy and main_empty:
        # 非 buddy 平铺物量（otoge 时效支柱：新宴谱常先于机台源更新）
        tap, hold, slide, touch, brk = (
            _safe_int(item.get(f"lev_utage_notes_{k}")) or 0 for k in _NOTE_KEYS
        )
        if any((tap, hold, slide, touch, brk)):
            _set_notes(chart, (tap, hold, slide, touch, brk))
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
        _fill_song_basics(state, song_id, entry)
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
                if not any(_chart_notes_of(target)):
                    _set_notes(target, chart.notes)
                if kind == "utage":
                    _fill_utage_fields(target, chart)
                # §5.3 校验：推导国服定数 vs 落雪实测（超容差记警告）；
                # 宴定数是标级推导的代理值（§3），与实测必然有差，不参与校验
                if chart.cn_level_value and chart.history and kind != "utage":
                    derived = cn_level_value(chart.history, cn_current)
                    if (
                        derived is not None
                        and abs(derived - chart.cn_level_value) > _CN_DS_WARN_TOL
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
    for kind, raw_id in (
        ("sd", str(song_id)),
        ("dx", str(song_id + DX_ID_OFFSET)),
    ):
        item = df.get(raw_id)
        if not item:
            continue
        group = state.groups.get((song_id, kind))
        from_name = (item.get("basic_info") or {}).get("from")
        df_version = _source_version(from_name)
        if group and group.version_cn and df_version and group.version_cn != df_version:
            state.warn(
                f"「{entry.title}」{kind} version_cn 两源不一致："
                f"落雪 {group.version_cn} / 水鱼 {df_version}"
            )
        for idx, value in enumerate(item.get("ds") or []):
            history = state.history_of(song_id, kind, idx)
            cn_value = cn_level_value(history, cn_current) if history else None
            if cn_value and abs(cn_value - float(value)) > _CN_DS_WARN_TOL:
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
    cn_known: set[tuple[int, str]] | None,
    jp_known_songs: set[int] | None,
    jp_known_titles: set[str] | None,
    jp_deleted_titles: set[str] | None = None,
    extra_known_ids: set[int] | None = None,
) -> int:
    """缺失/删除统一规则（§7.5-B）：一侧缺置该侧版本列 NULL，两侧皆无整曲删除。

    - ``cn_known``：国服在列 (song_id, kind) 集（禁用/消失不算在列）；
      None = 双源未齐，跳过 CN 缺省同步；
    - ``jp_known_songs``：maimaiinfo 解出的 song_id 集；None = 跳过 JP 缺省同步；
    - ``jp_known_titles``：otoge-db 现役归一标题集（None = 不强求）；
    - ``jp_deleted_titles``：otoge 下架标题集（权威下架信号）；
    - ``extra_known_ids``：外部补充源（机台快照等）给出的 id 集——在列信号最强
      （日服当前机台数据），优先于 otoge 下架记录（§7.5-C）。
    整曲删除以**源在列信号**判定（任一信号源认为在列即保留；信号源全缺时回落
    版本列全空），避免「版本未知」与「该服未上线」混淆造成误删。
    返回整曲删除数。
    """
    if cn_known is not None:
        for (song_id, kind), group in state.groups.items():
            if group.version_cn is not None and (song_id, kind) not in cn_known:
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
            if extra_known_ids is not None and song_id in extra_known_ids:
                continue  # 机台快照在列（maimaiinfo/otoge 均滞后），版本保留
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
            if extra_known_ids is not None and song_id in extra_known_ids:
                jp_present = True  # 机台当前数据在列，覆盖一切滞后信号
        else:
            jp_present = any(g.version is not None for g in groups)
        if cn_known is not None:
            cn_present = any((song_id, g.kind) in cn_known for g in groups)
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
    # 懒索引按 song_id 键控：删除只影响被删曲自己的条目，循环内查其他曲仍准确
    # （循环里 groups_of_song 会命中 stale 索引而非触发全表重建）；整轮结束统一置空
    state._charts_by_song = None
    state._groups_by_song = None
    return removed


async def rebuild(
    payloads: dict[str, Any], *, extra_jp_ids: set[int] | None = None
) -> dict[str, Any]:
    """规范表重建入口：接收各源 payload（None = 该源本次未拉取成功，跳过），写回 DB。

    payloads 键：``maimaiinfo`` / ``dschange`` / ``otoge_db`` / ``otoge_deleted`` /
    ``lxns`` / ``divingfish``。``extra_jp_ids`` 为外部补充源的 id 集（JP 在列信号，
    §7.5-C）。返回统计与告警（已写日志，供通知拼接）。
    """
    state = await State.load()
    jp: dict[int, Entry] = {}
    if payloads.get("maimaiinfo") is not None and payloads.get("dschange") is not None:
        jp = parse_maimaiinfo(payloads["maimaiinfo"], payloads["dschange"])
    otoge: OtogeData | None = None
    if payloads.get("otoge_db") is not None and jp:
        otoge = parse_otoge(payloads["otoge_db"], payloads.get("otoge_deleted") or [])
        # otoge 在场只跑这一遍：apply_jp(jp, otoge) 完全包含 apply_jp(jp, None)
        # 的工作（otoge 匹配均有守卫），先跑无 otoge 版是纯重复
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
        # 外部源已建行但 maimaiinfo 未收录（先发数据）：title join 直接充实，
        # 不进暂存——暂存只收规范表尚无行的条目（§7.5-A 宴快照为主）
        state_titles: dict[str, int] = {}
        for row in state.songs.values():
            if row.title:
                state_titles.setdefault(norm_title(row.title), row.id)
        for item in payloads["otoge_db"]:
            title = item.get("title", "")
            if not title:
                continue
            key = norm_title(title)
            if key in known_titles:
                continue
            song_id = state_titles.get(key)
            if song_id is None:
                await upsert_pending("otoge-db", f"title:{title}", "missing_id", item)
            else:
                _fill_row_from_otoge(state, song_id, item)
    elif jp:
        apply_jp(state, jp, None)
    cn_known: set[tuple[int, str]] | None = None
    if payloads.get("lxns") is not None:
        cn = parse_lxns(payloads["lxns"])
        df = (
            parse_divingfish(payloads["divingfish"])
            if payloads.get("divingfish") is not None
            else None
        )
        if df is not None:
            # CN 缺省同步需双源确认（§7.5-B）：单源抓取失败则不做缺省判定。
            # 粒度 (song_id, kind)：落雪对禁用（删除/宴轮换下架）曲打 disabled
            # 留在列表而非移除，禁用条目不算在列；宴条目独立、可精确到宴组
            cn_known = {
                (sid, kind)
                for sid, entry in cn.items()
                if not entry.disabled
                for kind in entry.charts
            }
            cn_known |= _df_known_kinds(df)
        apply_cn(state, cn, df)
    removed = apply_missing(
        state,
        cn_known=cn_known,
        jp_known_songs=set(jp) if jp else None,
        jp_known_titles=set(otoge.live_titles) if otoge else None,
        jp_deleted_titles=set(otoge.deleted_titles) if otoge else set(),
        extra_known_ids=extra_jp_ids,
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
    # 重建统计快照，供人工查库（kv_cache 表）核对历次重建规模，无程序内消费方
    await store.kv_set("songdb_stat", result)
    await _refresh_standard_json(state)
    return result


async def _archive_raw(payloads: dict[str, Any]) -> None:
    """源始留档：成功拉取的源逐条重写 ``song_source_raw``（otoge-db 以 title 为键）。"""
    rows: list[store.SongSourceRaw] = []

    def _dump(source: str, song_id: str, payload: Any) -> store.SongSourceRaw:
        return store.SongSourceRaw(
            source=source,
            song_id=song_id,
            payload=json.dumps(payload, ensure_ascii=False),
        )

    if (lxns_doc := payloads.get("lxns")) is not None:
        rows += [
            _dump("lxns", str(item["id"]), item) for item in lxns_doc.get("songs", [])
        ]
    if (df_doc := payloads.get("divingfish")) is not None:
        rows += [_dump("divingfish", str(item["id"]), item) for item in df_doc]
    if (info_doc := payloads.get("maimaiinfo")) is not None:
        rows += [_dump("maimaiinfo", str(key), item) for key, item in info_doc.items()]
    if (ds_doc := payloads.get("dschange")) is not None:
        rows.append(_dump("dschange", "__all__", ds_doc))
    if (otoge_doc := payloads.get("otoge_db")) is not None:
        rows += [
            _dump("otoge-db", f"title:{item.get('title', '')}", item)
            for item in otoge_doc
        ]
    if (deleted_doc := payloads.get("otoge_deleted")) is not None:
        rows.append(_dump("otoge-db", "__deleted__", deleted_doc))
    async with store.session() as session:
        await session.exec(delete(store.SongSourceRaw))
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
    """分类名 → Genre：国服叫法经 maimai_py ``name_to_genre`` 归一
    （库表与日文名双向齐全），未知值回落 maimai。"""
    return name_to_genre.get(name) or Genre.maimai


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
    cn_current: int | None = None,
) -> Song | None:
    """规范表行 → maimai_py ``Song``（scope 决定用国服列还是日服列，§5.1/§5.2）。

    ``index`` 为 ``(kind, level_id) → SongChart`` 索引；``cn_current`` 为国服当前
    版本码——整库物化时由 :func:`all_songs` 计算一次传入（cn_current_version
    内部全组扫描，原先每张谱面调一次，物化上万次）。
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
    if cn_current is not None:
        cn_cur = cn_current
    elif scope == "cn":
        cn_cur = state.cn_current_version()
    else:
        # JP 定数不走 cn 推导，0 仅占位（cn_level_value 只在 cn 分支消费）
        cn_cur = 0
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
                    state.history_of(song_id, kind, level_id), cn_cur
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
    cn_current = state.cn_current_version() if scope == "cn" else None
    result = []
    for song_id in sorted(state.songs):
        if song := build_song(
            state, song_id, scope, index.get(song_id, {}), cn_current
        ):
            result.append(song)
    return result


# ---------------------------------------------------------------------------
# 标准 JSON（01 文档结构）与指纹
# ---------------------------------------------------------------------------


def _level_flat(history: list[tuple[int, float]]) -> list[float]:
    """变化点序列 → 01 文档的扁平 ``level`` 列表（sd/dx 用）。

    文档语义（Data-structure.md §1）：列表从**该谱面登场版本**起、到日服最新版本，
    逐版本一个定数值；旧框谱不含旧框值、从 DX 初代起统计。变化点序列的首个版本即
    登场版本（旧框谱的 dschange 自 DX 初代起记录），据此对位展开；
    末尾用 DX 版本轴之后的已知/递推码补齐（新版本超出枚举时的兜底）。
    """
    if not history:
        return []
    # 导出轴固定为 14 个 DX 版本（与文档示例一致）：数据源（dschange）未覆盖
    # 更新版本时不虚构第 15 个值；导入侧才用扩展轴兼容新版本文档
    axis = DX_VERSION_CODES
    start = history[0][0]
    # 对位：优先用首变化点版本在轴上的位置；早于 DX 初代（旧框）从轴首开始
    if start in axis:
        start_idx = axis.index(start)
    elif start < axis[0]:
        start_idx = 0
    else:
        # 超出已知轴的新版本：按 _DX_VERSION_STRIDE 递推对位（DX 时代惯例）
        start_idx = len(axis) - 1 + (start - axis[-1]) // _DX_VERSION_STRIDE
    out: list[float] = []
    for idx in range(start_idx, start_idx + _FLAT_AXIS_GUARD):  # 理论上限保护
        code = (
            axis[idx]
            if idx < len(axis)
            else axis[-1] + (idx - len(axis) + 1) * _DX_VERSION_STRIDE
        )
        value = None
        for v, val in history:
            if v <= code:
                value = val
        if value is None:
            break  # 登场之前的空档不该出现在展开里，出现即为止
        out.append(value)
        if code >= history[-1][0] and idx >= len(axis) - 1:
            break  # 已覆盖到轴末（=当前日服最新版本）
    return out


def _points_from_flat(
    values: list[float], debut: int | None
) -> list[tuple[int, float]]:
    """01 文档的扁平 ``level`` 列表 → 变化点序列（外部源导入用，§7.5-C）。

    对位规则：登场版本（组 version，旧框取 DX 初代）在轴上有位则从该位起；
    长度与登场版本不一致或未知时，按「列表末位 = 轴末位（DX 14 版）」端对齐。
    列表长于 14（文档收录了 MAGiCAL 等新版本）时轴先补枚举已知码、再按
    _DX_VERSION_STRIDE 递推。连续相同值合并为变化点。
    """
    n = len(values)
    if n == 0:
        return []
    axis = list(DX_VERSION_CODES)
    # 枚举已收录、DX 轴尚未收录的新版本码（如 MAGiCAL 27000；FUTURE 占位不计）
    top = max(DX_VERSION_CODES)
    extras = [
        v.value for v in Version if top < v.value < Version.MAIMAI_DX_FUTURE.value
    ]
    while len(axis) < n:  # 扩展轴：先已知新版本码，再步进递推
        axis.append(
            extras[len(axis) - len(DX_VERSION_CODES)]
            if len(axis) - len(DX_VERSION_CODES) < len(extras)
            else axis[-1] + _DX_VERSION_STRIDE
        )
    start_idx = 0
    if debut is not None:
        anchor = max(debut, axis[0])
        if anchor in axis:
            start_idx = axis.index(anchor)
    if start_idx + n > len(axis) or debut is None:
        start_idx = max(0, len(axis) - n)  # 端对齐
    points: list[tuple[int, float]] = []
    for offset, value in enumerate(values):
        idx = start_idx + offset
        if idx < len(axis):
            code = axis[idx]
        else:  # 超出已知轴：步进递推（新版本尚未进枚举/EXTRA 表）
            code = axis[-1] + (idx - len(axis) + 1) * _DX_VERSION_STRIDE
        if not points or abs(points[-1][1] - value) > _EPS:
            points.append((code, value))
    return points


def song_standard_json(state: State, song_id: int) -> dict[str, Any] | None:
    """单曲 → 01 文档结构（Data-structure.md §1 的线格式）。

    - sd/dx ``level``：从登场版本（旧框谱从 DX 初代）到日服最新的扁平定数列表；
    - utage ``level``：单元素列表，值为标级推导浮点（如 ``12.7`` 表示 ``12+?``）；
    - ``notes``：``[Tap, Hold, Slide, Touch, Break]`` 五元数组；
    - buddy 以外 ``notes_left``/``notes_right`` 为 null。
    """
    row = state.songs.get(song_id)
    if row is None:
        return None
    by_group: dict[str, list[tuple[int, store.SongChart]]] = {}
    for (kind, level_id), chart in state.charts_of_song(song_id).items():
        by_group.setdefault(kind, []).append((level_id, chart))
    sheets: dict[str, Any] = {}
    for group in state.groups_of_song(song_id):
        kind = group.kind
        contents = []
        for level_id, chart in sorted(by_group.get(kind, [])):
            history = state.history_of(song_id, kind, level_id)
            level = [history[-1][1]] if kind == "utage" else _level_flat(history)
            content: dict[str, Any] = {
                "level_id": level_id,
                "level": level,
                "designer": chart.designer,
                "notes": [
                    chart.notes_tap,
                    chart.notes_hold,
                    chart.notes_slide,
                    chart.notes_touch,
                    chart.notes_break,
                ],
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


async def _refresh_standard_json(state: State) -> str:
    """重算全库标准 JSON 并同步指纹。

    任何规范表直写（重建、外部源合并）之后都必须调用：日服视图等缓存以
    ``CURRENT_FINGERPRINT`` 为失效键，写库不更新指纹会让缓存读不到新数据。
    """
    doc = standard_json(state)
    await store.kv_set("songdb_json", doc)
    global CURRENT_FINGERPRINT
    CURRENT_FINGERPRINT = hashlib.md5(
        json.dumps(doc, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    return CURRENT_FINGERPRINT


async def is_empty() -> bool:
    """规范表是否为空（冷启动判定：空表时运行时加载前须先全量重建）。"""
    async with store.session() as session:
        return (await session.exec(select(store.SongRow))).first() is None


# ---------------------------------------------------------------------------
# 取数编排（ext 直连，不碰 MaimaiClient）与国服更新检测
# ---------------------------------------------------------------------------


DetectKey = tuple[int, str]


def detect_cn_update(
    known: set[DetectKey], lx: set[DetectKey], df: set[DetectKey]
) -> tuple[set[DetectKey], set[DetectKey]] | None:
    """国服更新判定（§7.2）：两源新增集交集非空（新歌）或两源消失集交集非空（下架）。

    三个集合为同粒度的在列键（建议 ``(song_id, kind)``，调用方排除宴与禁用）；
    ``known`` 空集视为首次运行（仅建基线，不触发）。返回 ``(added, removed)``，
    无更新返回 None。
    """
    if not known:
        return None
    added = (lx - known) & (df - known)
    removed = (known - lx) & (known - df)
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

    total_started = time.monotonic()
    logger.info(
        "songdb：开始全量重建（"
        + ("国服+日服" if include_cn and include_jp else "仅国服")
        + "）……"
    )
    payloads: dict[str, Any] = {}

    async def _fetch(name: str, fetch) -> None:
        started = time.monotonic()
        try:
            payloads[name] = await fetch()
            logger.info(f"songdb：{name} 拉取成功（{time.monotonic() - started:.1f}s）")
        except Exception as e:
            logger.warning(
                f"songdb: {name} 拉取失败，本次跳过"
                f"（{time.monotonic() - started:.1f}s：{e}）"
            )

    jobs = []
    if include_jp:
        jobs += [
            _fetch("maimaiinfo", ext_info.fetch_all_data),
            _fetch("dschange", ext_info.fetch_dschange),
            _fetch("otoge_db", ext_otoge.fetch_music_ex),
            _fetch("otoge_deleted", ext_otoge.fetch_music_ex_deleted),
            _fetch("ongeki_origin", ext_otoge.fetch_ongeki_origin),
        ]
    if include_cn:
        jobs += [
            _fetch("lxns", lambda: ext_lxns.fetch_song_list(notes=True)),
            _fetch("divingfish", ext_df.fetch_music_data),
        ]
    await asyncio.gather(*jobs)  # 单源容错在 _fetch 内，互不阻塞
    # 外部补充源：读取一次，id 集作 JP 在列信号参与删除判定，重建后合并（§7.5-C）
    try:
        extra_docs = await _load_extra_docs()
    except Exception:
        logger.exception("songdb: 外部补充源读取失败（不影响规范表重建）")
        extra_docs = []
    # MuNET 已知曲目 id 并入 JP 在列信号（§7.5-C'）：MuNET 在列 = 该曲真实在
    # 日服运营——otoge 侧若发生数据回滚（2026-10-01 实测 PR 分支被重写致
    # 三首 MAGiCAL 新曲从候选与在列信号同时消失、次日重建遭误删），已入库
    # 曲目不随上游回滚丢失；重建后批次补充再从 MuNET 拉回字段。
    munet_ids = await _munet_known_ids()
    result = await rebuild(payloads, extra_jp_ids=extra_jp_ids(extra_docs) | munet_ids)
    logger.info(
        f"songdb：重建完成——曲 {result['songs']}、谱面组 {result['groups']}、"
        f"谱面 {result['charts']}（总耗时 {time.monotonic() - total_started:.1f}s）"
    )
    # 中二/音击侧别标题集（song-db-design §2.22）：抓取失败保留旧集合，
    # 失败期 maimai 新增大类曲按默认中二、下次刷新自愈
    try:
        if await store_ongeki_titles(payloads.get("ongeki_origin")):
            logger.info("songdb：音击原创栏标题集已更新（中二/音击侧别判定）")
        else:
            logger.warning("songdb: 音击原创栏标题集未更新（本轮抓取失败，保留旧集合）")
    except Exception:
        logger.exception("songdb: 音击标题集存储失败（不影响规范表）")
    if extra_docs:
        try:
            # force：重建已用基础源覆写外部字段（maimaiinfo 物量/定数历史无条件
            # 写回），必须强制重放外部源，否则机台校正活不过下一次重建
            result["extra"] = await apply_external_sources(
                preloaded=extra_docs, force=True
            )
        except Exception:
            logger.exception("songdb: 外部补充源应用失败（不影响规范表）")
    # gamerch wiki 运行时补充（fill 语义）：只填缺口谱面，稳态零抓取
    from ..config import plugin_config

    if plugin_config.awmc_gamerch_fill:
        try:
            from .ext import gamerch

            g_applied, g_changed = await gamerch.apply_fill()
            if g_changed:
                extra_summary = result.setdefault("extra", {})
                extra_summary["changed"] = True
                extra_summary["gamerch_applied"] = g_applied
        except Exception:
            logger.exception("songdb: gamerch 补充失败（不影响规范表）")
    # 谱师別名義图（L1）：随刷新路径顺带抓取（唯一抓取时机；词表/匹配纯读）
    try:
        from . import designer

        await designer.get_alias_graph(fetch=True)
    except Exception:
        logger.exception("songdb: gamerch 別名義图抓取失败（不影响规范表）")
    # 归并在外部源之后：本轮由外部源创建的曲即可清理对应 pending 行
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

    async with store.session() as session:
        rows = list((await session.exec(select(store.SongPending))).all())
        # 只取标题一列：整库 State.load() 数千行全表物化只为对标题集，杀鸡用牛刀
        db_titles = (await session.exec(select(store.SongRow.title))).all()
        titles = {norm_title(t) for t in db_titles if t}
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

    async with store.session() as session:
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


# ---------------------------------------------------------------------------
# 待归并曲目可查（§7.4-7 / Q33）：id 未到位的 pending 曲以「仅展示」形式参与
# 查歌兜底——不进运行时视图/别名索引/id 键路径。搜索侧用轻量结构（PendingSong，
# 携带封面 URL 等模型没有的字段），渲染时经 :func:`pending_to_song` 物化为
# maimai_py Song 走既有查歌卡（用户拍板「0 即 -」约定：bpm/物量的 0 在卡面
# 画 -，真实数据中 0 只代表未收录或旧框移植谱本就无该类物量）。
# ---------------------------------------------------------------------------


@dataclass
class PendingChart:
    """pending 曲的谱面行：只收**有具体定数**的 sd/dx 谱面，缺失字段为 None。

    物量按「0 即 -」约定填 0（渲染层画 -）；tap 缺整行视为无物量 → None。
    """

    kind: str  # "sd" / "dx"
    level_id: int  # 0-4（rem 仅 sd）
    level: str | None  # 标级串（如 "13+"），缺 → None
    level_value: float  # 定数（可查 gate 保证可得）
    designer: str | None  # 谱师，缺 → None
    notes: tuple[int, int, int, int, int] | None  # (tap, hold, slide, touch, break)

    @property
    def is_dx(self) -> bool:
        return self.kind == "dx"


@dataclass
class PendingSong:
    """``song_pending`` 曲目的可查表示（仅展示，不参与以 id 为键的功能）。"""

    source: str
    key: str
    title: str
    artist: str | None
    bpm: str | None
    genre: str | None  # Genre 枚举值域（catcode 映射），映射不到 → None
    version: int | None  # 组级日服版本码
    image_url: str | None  # 官方封面哈希名（maimaidx.jp 直取）
    charts: list[PendingChart]

    @property
    def cover_key(self) -> str:
        """封面缓存键：标题摘要（pending 曲无 id，不能按 id 缓存）。"""
        return hashlib.md5(norm_title(self.title).encode()).hexdigest()[:16]

    @property
    def genre_display(self) -> str | None:
        """分类中文展示名；未知分类返回 None（卡面画 -，不猜 Genre 枚举）。"""
        if not self.genre:
            return None
        try:
            return GENRE_TO_ZH.get(Genre(self.genre), self.genre)
        except ValueError:
            return self.genre

    def major_charts(self) -> list[PendingChart]:
        """卡片难度行：有 DX 用 DX（major_diffs 同口径），按难度序。"""
        charts = [c for c in self.charts if c.is_dx] or [
            c for c in self.charts if not c.is_dx
        ]
        return sorted(charts, key=lambda c: c.level_id)


def pending_to_song(pending: PendingSong) -> Song:
    """PendingSong → 临时 maimai_py ``Song``（走既有查歌卡渲染，仅展示）。

    - id 恒 0：不进任何运行时缓存/id 键路径，卡面 ID 行由调用方以「ID —」覆写；
    - 缺失字段按「0 即 -」约定：bpm/物量缺 → 0、曲师/标级缺 → "-"；
    - version 未知填 0（``Version.from_value(0)`` 为 None，版本 logo 自然缺席）。
    """
    standard, dx = [], []
    for chart in pending.charts:
        tap, hold, slide, touch, brk = chart.notes or (0, 0, 0, 0, 0)
        diff = SongDifficulty(
            type=SongType.DX if chart.is_dx else SongType.STANDARD,
            level_index=LevelIndex(chart.level_id),
            level=chart.level or "-",
            level_value=chart.level_value,
            note_designer=chart.designer or "-",
            version=pending.version or 0,
            tap_num=tap,
            hold_num=hold,
            slide_num=slide,
            touch_num=touch,
            break_num=brk,
            curve=None,
        )
        (dx if chart.is_dx else standard).append(diff)
    try:
        bpm = int(float(pending.bpm)) if pending.bpm else 0
    except ValueError:
        bpm = 0
    return Song(
        id=0,
        title=pending.title,
        artist=pending.artist or "-",
        genre=_genre_of(pending.genre or ""),
        bpm=bpm,
        map=None,
        version=pending.version or 0,
        rights=None,
        aliases=None,
        disabled=False,
        difficulties=SongDifficulties(standard=standard, dx=dx, utage=[]),
    )


def parse_pending_item(payload: dict) -> PendingSong | None:
    """otoge 条目 → :class:`PendingSong`；**可查 gate** 不满足返回 None。

    gate（Q33 用户口径）：标题非空且至少一张 sd/dx 谱面的定数（``*_i``）
    可解析为浮点——``?``/空/缺都不算；不满足说明该曲还没有可展示的核心
    信息，继续留在 pending 等 otoge/官方补数。
    """
    title = (payload.get("title") or "").strip()
    if not title:
        return None
    charts: list[PendingChart] = []
    for prefix, kind in (("lev", "sd"), ("dx_lev", "dx")):
        for suffix, level_id in _OTOLEVEL_IDS.items():
            if kind == "dx" and suffix == "rem":
                continue  # rem 仅 SD 存在（与 _fill_row_from_otoge 同口径）
            raw_value = payload.get(f"{prefix}_{suffix}_i")
            if raw_value is None:
                continue  # 无定数（缺）：该谱面暂不展示
            try:
                level_value = float(raw_value)
            except (TypeError, ValueError):
                continue  # 无定数（? / 空）：该谱面暂不展示
            # 标级串接受 13 / 13+ / 13? / 13+? 形态，其余（空、?）视为未知
            raw_level = str(payload.get(f"{prefix}_{suffix}") or "").strip()
            level = raw_level if re.fullmatch(r"\d+\+?\??", raw_level) else None
            tap = _safe_int(payload.get(f"{prefix}_{suffix}_notes_tap"))
            if tap is None:
                notes = None  # tap 是物量锚点（与 _fill_row_from_otoge 同口径）
            else:
                # 「0 即 -」约定：缺失列填 0，渲染层画 -
                notes = (
                    tap,
                    _safe_int(payload.get(f"{prefix}_{suffix}_notes_hold")) or 0,
                    _safe_int(payload.get(f"{prefix}_{suffix}_notes_slide")) or 0,
                    _safe_int(payload.get(f"{prefix}_{suffix}_notes_touch")) or 0,
                    _safe_int(payload.get(f"{prefix}_{suffix}_notes_break")) or 0,
                )
            charts.append(
                PendingChart(
                    kind=kind,
                    level_id=level_id,
                    level=level,
                    level_value=level_value,
                    designer=payload.get(f"{prefix}_{suffix}_designer") or None,
                    notes=notes,
                )
            )
    if not charts:
        return None
    version_raw = str(payload.get("version") or "").strip()
    genre_name = OTOGE_CATCODE_TO_GENRE.get(payload.get("catcode") or "")
    return PendingSong(
        source="otoge-db",
        key=f"title:{title}",
        title=title,
        artist=(payload.get("artist") or "").strip() or None,
        bpm=str(payload.get("bpm") or "").strip() or None,
        genre=genre_name or None,
        version=int(version_raw) if version_raw.isdigit() else None,
        image_url=payload.get("image_url") or None,
        charts=charts,
    )


async def pending_search(
    *,
    title: str | None = None,
    ds_range: tuple[float, float] | None = None,
) -> list[PendingSong]:
    """查 ``song_pending``（reason=missing_id）中满足可查 gate 的曲目。

    ``title``：归一化子串匹配；``ds_range``：任一谱面定数落在闭区间。
    均不传则返回全部可查 pending 曲。按版本降序（新曲在前）。
    """
    keyword = normalize_text(title) if title else None
    async with store.session() as session:
        rows = (
            await session.exec(
                select(store.SongPending).where(
                    store.SongPending.reason == "missing_id"
                )
            )
        ).all()
    result: list[PendingSong] = []
    for row in rows:
        try:
            payload = json.loads(row.payload)
        except json.JSONDecodeError:
            continue
        pending = parse_pending_item(payload)
        if pending is None:
            continue
        pending.source, pending.key = row.source, row.key
        if keyword and keyword not in normalize_text(pending.title):
            continue
        if ds_range and not any(
            ds_range[0] <= c.level_value <= ds_range[1] for c in pending.charts
        ):
            continue
        result.append(pending)
    result.sort(key=lambda p: (-(p.version or 0), p.title))
    return result


async def _load_extra_docs() -> list[tuple[str, str, dict]]:
    """读取外部补充源配置（§7.5-C），返回 (来源名, mode, 文档) 列表。

    配置 ``awmc_extra_song_sources`` 每项为路径/URL，可带 ``::fill``/``::override``
    后缀指定该源合并模式（默认 override，人工即权威）。读取失败记 error 跳过。
    """
    from pathlib import Path

    from ..config import plugin_config

    docs: list[tuple[str, str, dict]] = []
    for spec in plugin_config.awmc_extra_song_sources:
        source, _, mode = spec.partition("::")
        mode = mode if mode in ("fill", "override") else "override"
        try:
            if source.startswith(("http://", "https://")):
                async with create_smart_client(timeout=30) as http:
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
    return docs


def extra_jp_ids(docs: list[tuple[str, str, dict]]) -> set[int]:
    """外部源文档给出的 id 集——JP 在列信号（apply_missing 删除判定用，§7.5-C）。"""
    return {int(key) for _n, _m, doc in docs for key in doc if str(key).isdigit()}


async def _munet_known_ids() -> set[int]:
    """MuNET 视角「日服真实在列」的规范表曲 id 集（批次补充成功入库过的曲）。

    实现取巧：MuNET 入库曲的 `image_url` 由批次补充写入（otoge/机台源之外唯一
    会给新曲写封面的信号），但 image_url 也可能来自 otoge——改用 kv 留痕：批次
    补充每次合并的 id 集记 `munet_batch_ids`（累计并集），此处直接读。
    """
    raw = await store.kv_get("munet_batch_ids")
    if not isinstance(raw, list):
        return set()
    return {int(x) for x in raw if isinstance(x, (int, float))}


async def apply_external_sources(
    preloaded: list[tuple[str, str, dict]] | None = None,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """读取并应用外部补充源（§7.5-C）：仅日服侧，标准 JSON，override/fill 字段级合并。

    返回 {sources, applied, changed}；内容哈希记 kv_cache。
    ``force=True``：跳过哈希门控强制重合并——rebuild 会以基础源（maimaiinfo/
    otoge）覆写外部源已合并的字段，refresh_all 重建后必须强制重放，否则机台
    校正活不过下一次重建；此时 ``changed`` 只反映真实值变化（避免无谓底图重建）。
    ``preloaded`` 传入已读取的文档（refresh_all 管线复用，避免重复读取）。
    """
    from ..config import plugin_config

    specs = plugin_config.awmc_extra_song_sources
    summary: dict[str, Any] = {
        "sources": len(specs),
        "applied": 0,
        "created": 0,
        "changed": False,
    }
    if not specs and preloaded is None:
        return summary
    docs = preloaded if preloaded is not None else await _load_extra_docs()
    digest = hashlib.md5(
        json.dumps(
            [(m, d) for _, m, d in docs], ensure_ascii=False, sort_keys=True
        ).encode()
    ).hexdigest()
    prev = await store.kv_get("songdb_extra_hash")
    summary["hash"] = digest
    if docs and (force or digest != prev):
        applied, created, changed_fields = await _merge_extra_docs(docs)
        summary["applied"] = applied
        summary["created"] = created
        summary["changed"] = bool(changed_fields or created)
        await store.kv_set("songdb_extra_hash", digest)
    return summary


def _jp_current_version(state: State) -> int | None:
    """日服当前版本：数据 max(组 version)，无任何值时 None。"""
    values = [g.version for g in state.groups.values() if g.version is not None]
    return max(values) if values else None


def _merge_current_level(
    state: State,
    song_id: int,
    kind: str,
    level_id: int,
    value: float,
    debut: int | None,
    anchor: int | None,
) -> bool:
    """单元素 level 列表（快照源的「当前定数」）合并进定数历史（§7.5-C）。

    - 无历史：退化为登场版本单行（§6 口径，登场地板到 DX 初代）；
    - 有历史且末值一致：幂等 no-op；
    - 有历史且末值不同：锚定到日服当前版本追加变化点（末点已在锚点则原位校正）
      ——快照源只知道「现在值」，变化点版本取当前版本是新鲜快照的最优近似，
      绝不改写历史版本上的值。
    返回是否真实改动（供上游统计 changed）。
    """
    history = state.history_of(song_id, kind, level_id)
    if not history:
        base = debut if debut is not None else anchor
        if base is None:
            logger.debug(
                f"songdb: 外部源当前定数无从锚定"
                f"（id={song_id} {kind}{level_id}={value}），跳过"
            )
            return False
        state.set_history(
            song_id, kind, level_id, [(max(base, Version.MAIMAI_DX.value), value)]
        )
        return True
    last_version, last_value = history[-1]
    if abs(last_value - value) <= _EPS:
        return False
    if anchor is None:
        anchor = last_version
    if last_version == anchor:
        history[-1] = (anchor, value)
        logger.info(
            f"songdb: 外部源校正当前定数（id={song_id} {kind}{level_id}）"
            f"{last_value} → {value}"
        )
    else:
        history.append((anchor, value))
        logger.info(
            f"songdb: 外部源追加定数更新（id={song_id} {kind}{level_id}）"
            f"自版本 {anchor}：{last_value} → {value}"
        )
    state.set_history(song_id, kind, level_id, history)
    return True


def _doc_anchor_version(state: State, doc: dict) -> int | None:
    """锚定版本取「状态已有 ∨ 本文档最大」——与曲处理顺序无关。"""
    return max(
        (
            v
            for v in [
                _jp_current_version(state),
                *[
                    sheet.get("version")
                    for _k2, s2 in doc.items()
                    if isinstance(s2, dict)
                    for sheet in (s2.get("sheets") or {}).values()
                    if isinstance(sheet, dict) and sheet.get("version")
                ],
            ]
            if v is not None
        ),
        default=None,
    )


def _merge_chart_content(
    state: State,
    song_id: int,
    kind: str,
    level_id: int,
    content: dict,
    group: store.SongSheetGroup,
    *,
    mode: str,
    anchor: int | None,
) -> int:
    """单个谱面条目合并：designer/notes/buddy/宴字段/定数历史。

    返回真实值变化字段数（override 重放同值不计，供上游判断是否需要重建底图）。
    """
    changed = 0
    target = state.chart(song_id, kind, level_id)
    if content.get("designer") and (mode == "override" or not target.designer):
        if target.designer != content["designer"]:
            changed += 1
        target.designer = content["designer"]
    # 01 文档线格式：notes 为 [Tap, Hold, Slide, Touch, Break]
    notes = content.get("notes") or []
    if (
        isinstance(notes, list)
        and len(notes) == 5
        and (mode == "override" or not target.notes_tap)
    ):
        new_notes: tuple[int, int, int, int, int] = (
            int(notes[0]),
            int(notes[1]),
            int(notes[2]),
            int(notes[3]),
            int(notes[4]),
        )
        if _chart_notes_of(target) != new_notes:
            changed += 1
        _set_notes(target, new_notes)
    # 01 文档双人谱口径：宴 buddy 左右手物量（JSON 存 notes_left/right）；
    # 全零视为未填（历史垃圾值，override 除外——人工即权威）
    for key in ("notes_left", "notes_right"):
        val = content.get(key)
        if (
            isinstance(val, list)
            and len(val) == 5
            and (mode == "override" or _notes_left_empty(getattr(target, key)))
        ):
            new_json = json.dumps([int(v) for v in val])
            if getattr(target, key) != new_json:
                changed += 1
            setattr(target, key, new_json)
    if kind == "utage":
        if content.get("is_buddy") is not None and (
            mode == "override" or not target.is_buddy
        ):
            new_buddy = bool(content["is_buddy"])
            if target.is_buddy != new_buddy:
                changed += 1
            target.is_buddy = new_buddy
        for field in ("kanji", "comment"):
            if content.get(field) and (
                mode == "override" or not getattr(target, field)
            ):
                if getattr(target, field) != content[field]:
                    changed += 1
                setattr(target, field, content[field])
    # 01 文档线格式：level 为扁平列表（sd/dx 逐版本；宴单元素标级浮点）
    flat = content.get("level") or []
    if not flat:
        return changed
    debut = group.version
    if kind == "utage":
        points = [(debut, float(flat[0]))] if debut is not None else []
        if points and (
            mode == "override" or not state.history_of(song_id, kind, level_id)
        ):
            if state.history_of(song_id, kind, level_id) != points:
                changed += 1
            state.set_history(song_id, kind, level_id, points)
    elif len(flat) == 1:
        # 单元素 = 快照源的「当前定数」（§7.5-C 语义）
        if mode == "fill" and state.history_of(song_id, kind, level_id):
            return changed  # fill 不动已有历史
        if _merge_current_level(
            state,
            song_id,
            kind,
            level_id,
            float(flat[0]),
            debut,
            anchor,
        ):
            changed += 1
    else:
        points = _points_from_flat([float(v) for v in flat], debut)
        if points and (
            mode == "override" or not state.history_of(song_id, kind, level_id)
        ):
            if state.history_of(song_id, kind, level_id) != points:
                changed += 1
            state.set_history(song_id, kind, level_id, points)
    return changed


def _merge_song_doc(
    state: State,
    name: str,
    mode: str,
    song_id: int,
    song_doc: dict,
    anchor: int | None,
) -> tuple[int, int, int]:
    """单曲外部文档合并（返回 (applied, created, changed 真实值变化数)）。"""
    applied = 0
    created = 0
    changed = 0
    row = state.songs.get(song_id)
    if row is None:
        title = song_doc.get("title") or ""
        if not title:
            return 0, 0, 0  # 连标题都缺失的条目无法建曲
        row = state.song(song_id)
        created = 1
        logger.info(
            f"songdb: 外部源 {name} 新增曲 id={song_id}「{title}」"
            "（日侧行，version_cn=NULL，骨架待常规管线补全）"
        )
    for field_name in ("title", "artist", "genre", "bpm", "image_url"):
        if field_name not in song_doc:
            continue
        new_value = song_doc[field_name]
        if new_value in (None, ""):
            # 文档未提供该字段（如机台源无官方封面哈希名）：跳过而非清空，
            # 否则 override 重放会把既有值整体抹掉（2026-09-25 封面全挂事故）
            continue
        if mode == "override" or not getattr(row, field_name):
            if getattr(row, field_name) != new_value:
                changed += 1
            setattr(row, field_name, new_value)
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
            if group.version != sheet["version"]:
                changed += 1
            group.version = sheet["version"]
        if sheet.get("date") is not None and (mode == "override" or group.date is None):
            if group.date != sheet["date"]:
                changed += 1
            group.date = sheet["date"]
        for content in sheet.get("contents") or []:
            level_id = content.get("level_id")
            if level_id is None:
                continue
            applied += 1
            changed += _merge_chart_content(
                state,
                song_id,
                kind,
                level_id,
                content,
                group,
                mode=mode,
                anchor=anchor,
            )
    return applied, created, changed


async def _archive_extra_docs(docs: list[tuple[str, str, dict]]) -> None:
    """外部片段归档（source=extra:<名称>；同键多 mode 以 mode 后缀区分）。

    重放前先清同 origin 旧行。
    """
    from pathlib import Path

    origins = {
        f"extra:{Path(name).name if not name.startswith('http') else name}"
        for name, _m, _d in docs
    }
    async with store.session() as session:
        for origin in origins:
            await session.exec(
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


async def _merge_extra_docs(docs: list[tuple[str, str, dict]]) -> tuple[int, int, int]:
    """外部标准 JSON 合并进主表：**只允许日服侧**，写 ``version_cn`` 忽略并告警。

    主表缺失的曲（骨架外新曲、maimaiinfo 滞后的新版曲等，文档自带 id）直接
    创建（仅日侧行，version_cn 恒 NULL）；单元素 sd/dx ``level`` 按「当前定数」
    语义合并（:func:`_merge_current_level`），多元素列表按 01 文档线格式对位。
    返回 (applied 处理条目数, created 新增曲数, changed 真实值变化字段数)。
    """

    state = await State.load()
    applied = 0
    created = 0
    changed = 0
    for name, mode, doc in docs:
        anchor = _doc_anchor_version(state, doc)
        for song_id_str, song_doc in doc.items():
            if not song_id_str.isdigit():
                logger.warning(f"songdb: 外部源 {name} 非法 id {song_id_str!r}，跳过")
                continue
            song_id = int(song_id_str)
            if not isinstance(song_doc, dict):
                logger.warning(f"songdb: 外部源 {name} id={song_id} 条目非对象，跳过")
                continue
            song_applied, song_created, song_changed = _merge_song_doc(
                state, name, mode, song_id, song_doc, anchor
            )
            applied += song_applied
            created += song_created
            changed += song_changed
    await _archive_extra_docs(docs)
    if applied or created or changed:
        await state.save()
    # 外部源直写主表不经过 rebuild，指纹必须在此同步，否则日服视图缓存失效键不变
    await _refresh_standard_json(state)
    logger.info(
        f"songdb: 外部补充源应用完成（{applied} 处谱面级字段、新增 {created} 曲、"
        f"{changed} 处真实值变化）"
    )
    return applied, created, changed
