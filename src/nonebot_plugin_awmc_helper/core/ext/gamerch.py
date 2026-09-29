"""gamerch（maimai 攻略wiki）直连：日服谱面数据运行时补充（fill 语义）。

数据来源：gamerch maimai 攻略wiki（社区人工维护，新版本曲目数小时内即有数据）。
置信度最次：只补规范表空字段（fill），绝不覆盖机台/maimaiinfo/otoge 已有值。

- **页面编号自发现**：从配信順リスト页互链爬取历代リスト（MAGiCAL 页链接全部
  前代），得到 标题→页面编号 全量清单，入库 kv ``gamerch_page_inventory``；
- **磁盘缓存**：localstore cache 目录 gzip 存储，TTL ``awmc_gamerch_max_age``
  小时，网络失败回退过期缓存；
- **按需抓取**：只抓规范表存在缺口的曲（sd/dx 谱面物量全零），稳态零抓取；
- **解析与版式解耦**（gamerch 2025+ 版式）：表头含 Touch 列为 DX/宴表；谱面行
  按 Lv 单元格形状识别，「定数 -」的旧框移植行剔除；宴谱行按 kanji 匹配已存在
  的谱面，同 kanji 多张（轮换快照）的官方 level_id 无法从 wiki 获知，不创建、
  等机台包/maimaiinfo。
"""

import re
import gzip
import json
import time
import logging
import unicodedata
from pathlib import Path
from dataclasses import field, dataclass

import httpx
from bs4 import BeautifulSoup

from . import get_client
from .. import store
from ..songdb import norm_title as _norm_title

logger = logging.getLogger("nonebot_plugin_awmc_helper.songdb")

WIKI_BASE = "https://gamerch.com/maimai/"
# gamerch 有 UA 反爬：非浏览器 UA 返回 202 空页；必须带浏览器 UA，
# 且首请求的 Set-Cookie 入 jar 后再访问即稳定 200
_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) "
        "Gecko/20100101 Firefox/121.0"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
# 配信順リスト根页（MAGiCAL；页内互链全部前代リスト，可自发现全曲页面编号）
LIST_ROOT_IDS = [1011740]
INVENTORY_KV = "gamerch_page_inventory"
_MAX_LIST_PAGES = 30
_FETCH_DELAY = 0.5
# 难度行 Lv 单元格形状：3 / 13+ / 14?（宴谱行带 kanji 前缀，不会 fullmatch）
_LV_RE = re.compile(r"\d{1,2}\+?\??")
_HEADER_WORDS = {"Lv", "Tap", "Hold", "Slide", "Touch", "Break", "総数", "内訳"}


def strip_variants(title: str) -> str:
    """基础标题：去宴前缀后归一化（与リスト页锚文本对齐）。"""
    return _norm_title(re.sub(r"^\[[^\]]+\]", "", title))


def _config():
    from ...config import plugin_config

    return plugin_config


def _cache_dir() -> Path:
    from nonebot_plugin_localstore import get_cache_dir

    return get_cache_dir("nonebot_plugin_awmc_helper") / "gamerch"


@dataclass
class ParsedTable:
    """单张谱面形表格。"""

    touch: bool  # 表头含 Touch 列 → DX/宴表
    head: list[str]
    diff_rows: list[list[str]] = field(default_factory=list)
    # 宴谱候选行（带 rowspan 信息）
    label_rows: list[dict] = field(default_factory=list)


@dataclass
class ParsedPage:
    title: str
    tables: list[ParsedTable]


async def fetch_page_text(http, url: str, *, max_age: int) -> str:
    """带磁盘缓存的页面抓取；网络失败回退过期缓存。

    gamerch 不支持 ETag/Last-Modified 条件请求，只能本地按时间缓存；
    缓存命中不发网络请求也不加礼貌延时。
    """
    m = re.search(r"/maimai/(\d+)", url)
    page_id = m.group(1) if m else None
    # 调用方 URL 恒为 /maimai/{id} 形态：page_id 缺失直接不缓存（仅内存路径）
    cache_dir = _cache_dir()
    meta_path = cache_dir / "meta.json"
    meta: dict = {}
    if page_id and meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}
    entry = meta.get(page_id) if page_id else None
    fetched_at = float(entry.get("fetched_at", 0)) if entry else 0.0
    fresh = bool(fetched_at) and time.time() - fetched_at < max_age * 3600
    cache_gz = cache_dir / f"{page_id}.html.gz" if page_id else None
    if cache_gz is not None and max_age > 0 and fresh:
        try:
            with gzip.open(cache_gz, "rt", encoding="utf-8") as f:
                return f.read()
        except (OSError, EOFError):
            pass  # 缓存损坏则走网络

    import asyncio

    await asyncio.sleep(_FETCH_DELAY)
    # 首请求可能被 202 反爬（空页、无 Cookie），Cookie 入共享客户端 jar 后
    # 重试；只重试 202/403（反爬信号），404/500 等真实失败不重试。
    # 202 属 2xx、raise_for_status 不抛，重试耗尽仍 202 时必须显式失败——
    # 反爬空页入库会把缺口曲标记为「已抓取」，TTL 内补充静默 no-op
    resp = await http.get(url, headers=_REQUEST_HEADERS)
    for _ in range(2):
        if resp.status_code == 200 or resp.status_code not in (202, 403):
            break
        await asyncio.sleep(1.0)
        resp = await http.get(url, headers=_REQUEST_HEADERS)
    if resp.status_code == 202:
        raise httpx.HTTPStatusError(
            "gamerch 反爬拦截（202 空页）", request=resp.request, response=resp
        )
    resp.raise_for_status()
    text = resp.text
    if cache_gz is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = cache_gz.with_suffix(".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            f.write(text)
        tmp.replace(cache_gz)
        meta[page_id] = {"url": url, "fetched_at": time.time()}
        tmp_meta = meta_path.with_suffix(".tmp")
        tmp_meta.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        tmp_meta.replace(meta_path)
    return text


async def build_page_inventory(http, *, max_age: int) -> dict[str, int]:
    """配信順リスト爬取：归一化标题 → 页面编号（kv 持久化，TTL 同页面缓存）。"""
    prev_raw = await store.kv_get(INVENTORY_KV)
    prev: dict = {}
    if prev_raw:
        try:
            prev = json.loads(prev_raw)
        except ValueError:
            prev = {}
    mapping: dict[str, int] = {t: v for t, v in prev.items() if not t.startswith("__")}
    fetched_at = float(prev.get("__fetched_at__", 0))
    if fetched_at and max_age > 0 and time.time() - fetched_at < max_age * 3600:
        return mapping

    queue = list(LIST_ROOT_IDS)
    visited: set[int] = set()
    while queue:
        page_id = queue.pop(0)
        if page_id in visited or len(visited) >= _MAX_LIST_PAGES:
            continue
        visited.add(page_id)
        try:
            html = await fetch_page_text(http, f"{WIKI_BASE}{page_id}", max_age=max_age)
        except Exception as e:
            logger.warning(f"songdb: gamerch リスト页 {page_id} 拉取失败（{e}）")
            continue
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            m = re.match(r"^(?:https://gamerch\.com)?/maimai/(\d+)/?$", str(a["href"]))
            if not m:
                continue
            target = int(m.group(1))
            text = a.get_text(strip=True)
            if not text:
                continue
            if "配信順楽曲リスト" in text:
                if target not in visited and len(visited) < _MAX_LIST_PAGES:
                    queue.append(target)
            else:
                mapping.setdefault(_norm_title(text), target)
                stripped = _norm_title(strip_variants(text))
                if stripped != _norm_title(text):
                    mapping.setdefault(stripped, target)
    payload: dict[str, int | float] = dict(mapping)
    payload["__fetched_at__"] = time.time()
    await store.kv_set(INVENTORY_KV, json.dumps(payload, ensure_ascii=False))
    n_pages = len(visited)
    logger.info(f"songdb: gamerch 清单 {len(mapping)} 条（リスト页 {n_pages}）")
    return mapping


def parse_page(html: str) -> ParsedPage | None:
    """gamerch 页面 → 结构化谱面表（版式解耦：不依赖颜色/列位置）。"""
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1", class_="content-head")
    if h1 is None:
        return None
    # 清理脚注引用（物量数字旁的 * 上标）
    for a in soup.find_all("a", id=re.compile(r"^notes_")):
        if "*" in a.get_text(strip=True):
            a.decompose()

    page = ParsedPage(title=h1.get_text(strip=True), tables=[])
    for table in soup.select("body .main table"):
        head = [
            c.get_text(strip=True)
            for c in table.select("thead th:not([colspan]), thead td:not([colspan])")
            if c.get_text(strip=True) and c.get_text(strip=True) != "スコア"
        ]
        if not head or "Lv" not in head[0]:
            continue
        pt = ParsedTable(touch="Touch" in head, head=head)
        trs = table.select("tr")
        for idx, tr in enumerate(trs):
            cells = tr.find_all(["th", "td"], recursive=False)
            if len(cells) < 4:
                continue
            texts = [c.get_text(strip=True) for c in cells]
            if texts[0] in _HEADER_WORDS:
                continue
            if _LV_RE.fullmatch(unicodedata.normalize("NFKC", texts[0])):
                pt.diff_rows.append(texts)
                continue
            entry = {
                "label": unicodedata.normalize("NFKC", texts[0]),
                "cells": texts,
                "spans": [c.has_attr("rowspan") for c in cells],
                "right": None,
            }
            # buddy 右行紧随左行：单元格更少且以 右/(右) 开头
            if idx + 1 < len(trs):
                nxt = trs[idx + 1].find_all(["th", "td"], recursive=False)
                if nxt:
                    nxt_label = nxt[0].get_text(strip=True)
                    if nxt_label and "右" in nxt_label:
                        entry["right"] = [c.get_text(strip=True) for c in nxt]
            pt.label_rows.append(entry)
        page.tables.append(pt)
    return page if page.tables else None


def _pick_table(page: ParsedPage, kind: str) -> ParsedTable | None:
    """按 kind 选表：Touch 列 → DX/宴表（含难度行的优先占 DX 槽，纯宴表不抢占）；
    否则 STD 表（首个）。"""
    dx: ParsedTable | None = None
    std: ParsedTable | None = None
    for pt in page.tables:
        if pt.touch:
            if dx is None or pt.diff_rows:
                dx = pt
        elif std is None:
            std = pt
    return std if kind == "sd" else dx


def _legend_free_rows(pt: ParsedTable) -> list[list[str]]:
    """剔除「定数 -」的旧框移植行（历代机台值，以带定数行为准）；全被剔除则原样返回。"""
    if "定数" not in pt.head:
        return pt.diff_rows
    idx = pt.head.index("定数")

    def has_const(cells: list[str]) -> bool:
        if idx >= len(cells):
            return True
        return cells[idx].strip() not in ("-", "", "?", "？")

    kept = [r for r in pt.diff_rows if has_const(r)]
    return kept or pt.diff_rows


def _parts(head: list[str], cells: list[str], *, with_touch: bool) -> list[int] | None:
    """内訳 → 五元组 [Tap, Hold, Slide, Touch, Break]；STD 表无 Touch 列，补 0。"""
    row = dict(zip(head, cells))
    names = ["Tap", "Hold", "Slide", "Break"]
    ints: list[int] = []
    for name in names:
        v = (row.get(name) or "").replace(",", "")
        if not v.isdigit():
            return None
        ints.append(int(v))
    ints.insert(3, 0)  # Slide 与 Break 之间为 Touch
    if with_touch:
        touch = (row.get("Touch") or "").replace(",", "")
        if not touch.isdigit():
            return None
        ints[3] = int(touch)
    return ints


def _build_song_entry(state, song_id: int, page: ParsedPage) -> dict | None:
    """单曲 → fill 文档条目（只发该曲在规范表中已存在的谱面）。"""
    charts = state.charts_of_song(song_id)
    entry: dict = {"title": state.songs[song_id].title or page.title, "sheets": {}}
    for kind in ("sd", "dx"):
        existing = sorted(lid for (k, lid) in charts if k == kind and lid <= 4)
        if not existing:
            continue
        pt = _pick_table(page, kind)
        if pt is None or not pt.diff_rows:
            continue
        rows = _legend_free_rows(pt)
        # 底部锚定：多余行（旧框移植/未剔除）在顶部，取末尾 n 行对位
        take = rows[-len(existing) :] if len(rows) >= len(existing) else rows
        if len(take) < len(existing):
            continue  # 行数不足无法可靠对位
        contents = []
        for level_id, cells in zip(existing, take):
            content: dict = {"level_id": level_id}
            parts = _parts(pt.head, cells, with_touch=kind == "dx")
            if parts is not None:
                content["notes"] = parts
            row_map = dict(zip(pt.head, cells))
            designer = row_map.get("譜面作者") or ""
            if designer and designer != "-":
                content["designer"] = designer
            if len(content) > 1:
                contents.append(content)
        if contents:
            entry["sheets"][kind] = {
                "version": None,
                "version_cn": None,
                "date": None,
                "contents": contents,
            }

    # 宴谱：按 kanji 匹配已存在的谱面（轮换新谱官方 level_id 未知，不创建）
    utage_charts = {lid: c for (k, lid), c in charts.items() if k == "utage"}
    if utage_charts:
        contents = []
        for lid, chart in utage_charts.items():
            kanji = chart.kanji
            if not kanji:
                continue
            for pt in page.tables:
                matched = None
                for row_entry in pt.label_rows:
                    label = row_entry["label"]
                    if kanji in label and not _LV_RE.fullmatch(label):
                        matched = (pt, row_entry)
                        break
                if matched is None:
                    continue
                pt_hit, row_entry = matched
                cells = row_entry["cells"]
                parts = _parts(pt_hit.head, cells, with_touch=True)
                if parts is None:
                    continue
                # 首个 content 声明已确立 dict 形状，此处不再重复注解
                content = {"level_id": lid, "notes": parts}
                right = row_entry.get("right")
                if right is not None:
                    right_insert = list(right)
                    right_insert.insert(0, cells[0])
                    shared = len(cells) > 2 and row_entry["spans"][2]
                    if shared:
                        right_insert.insert(2, cells[2])
                    rparts = _parts(pt_hit.head, right_insert, with_touch=True)
                    if rparts is not None:
                        content["notes_right"] = rparts
                contents.append(content)
                break
        if contents:
            entry["sheets"]["utage"] = {
                "version": None,
                "version_cn": None,
                "date": None,
                "contents": contents,
            }

    return entry if entry["sheets"] else None


async def _build_fill_doc() -> tuple[dict, int]:
    """缺口曲（sd/dx 物量全零）→ fill 文档。返回 (文档, 抓取了页面的曲数)。"""
    from .. import songdb

    state = await songdb.State.load()
    cfg = _config()
    http = get_client()
    max_age = cfg.awmc_gamerch_max_age
    inventory = await build_page_inventory(http, max_age=max_age)

    gap_song_ids = []
    for song_id in state.songs:
        for (kind, _lid), chart in state.charts_of_song(song_id).items():
            if kind in ("sd", "dx") and (
                chart.notes_tap,
                chart.notes_hold,
                chart.notes_slide,
                chart.notes_break,
            ) == (0, 0, 0, 0):
                gap_song_ids.append(song_id)
                break

    doc: dict[str, dict] = {}
    fetched = 0
    for song_id in gap_song_ids:
        row = state.songs[song_id]
        page_id = inventory.get(strip_variants(row.title or "")) or inventory.get(
            _norm_title(row.title or "")
        )
        if page_id is None:
            continue
        try:
            html = await fetch_page_text(http, f"{WIKI_BASE}{page_id}", max_age=max_age)
        except Exception as e:
            logger.warning(
                "songdb: gamerch 页面 %s「%s」拉取失败（%s）", page_id, row.title, e
            )
            continue
        page = parse_page(html)
        if page is None:
            continue
        fetched += 1
        entry = _build_song_entry(state, song_id, page)
        if entry is not None:
            doc[str(song_id)] = entry
    return doc, fetched


async def apply_fill() -> tuple[int, bool]:
    """抓取缺口曲页面并按 fill 合并（复用 _merge_extra_docs 全部入库/留档机制）。

    返回 (applied, changed)。
    """
    from .. import songdb

    doc, fetched = await _build_fill_doc()
    if not doc:
        logger.info("songdb: gamerch 补充无缺口谱面，跳过")
        return 0, False
    applied, created, changed = await songdb._merge_extra_docs(
        [("gamerch", "fill", doc)]
    )
    logger.info(
        f"songdb: gamerch 补充完成（抓取 {fetched} 曲、{applied} 处字段、"
        f"新增 {created} 曲、{changed} 处真实值变化）"
    )
    return applied, bool(changed or created)
