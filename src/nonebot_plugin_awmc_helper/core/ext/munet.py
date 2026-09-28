"""MuNET 直连：门户公开 API（版本批次补充 + 别名全量走查）。

- 读取侧公开免登录（门户 v3 后端 apidashboard3）；WAF 校验 Origin 头（缺失一律
  418），所有请求必须带 ``Origin: https://portal.mumur.net``；
- 直连 :42081 最快（CN 托管，§七实测 0.24s），``-cf`` 回源为备用主机；
- 范围定案（2026-09-28）：歌曲数据只作 current_jp 版本批次补充（历史回填/全库
  走查不做，由 maimaiinfo/otoge-db/机台外部源覆盖）；**别名做全量走查**；
- 限速：请求最小间隔 1s（实测 2s 稳，1s 为走查吞吐折中），418/网络错误换备用
  主机重试一轮。细节与调研数据见 ``local/reference/munet-alias-notes.md``。
"""

import json
import time
import asyncio
from typing import Any

import httpx
from nonebot import logger
from sqlmodel import select

from . import ExtError, ExtNetworkError, get_client
from .. import store
from ...constants import DX_ID_OFFSET, normalize_text

_PORTAL_ORIGIN = "https://portal.mumur.net"
_HOSTS = (
    "https://apidashboard3.mumur.net:42081",
    "https://apidashboard3-cf.mumur.net",
)
_MIN_INTERVAL = 1.0
"""请求最小间隔（秒）；走查吞吐与对私服礼貌的折中（实测 2s 完全稳）。"""

_throttle_lock = asyncio.Lock()
_last_request_at = 0.0
_walk_lock = asyncio.Lock()
"""别名走查互斥锁：每日任务与手动触发（重载补充数据）不会并发走查。"""

_WALK_KV = "munet_alias_walk"
_BATCH_KV = "munet_batch"
_WALK_BUDGET_SECONDS = 3600.0
"""单次走查时间预算（60 分钟；规范表实测 1763 请求 ≈ 30 分钟，预算留足余量
保证单晚走完），超时记游标跨日续走。"""

# genre 数字 → 规范表流派名（落雪/国服值域）。2026-09-28 与规范表全量分组对账
# 推导（抽样 46 曲六档全一致），非官方映射表；107=宴会場 为推断（未实测到条目）。
_GENRE_NAMES = {
    101: "流行&动漫",
    102: "niconico & VOCALOID",
    103: "东方Project",
    104: "其他游戏",
    105: "舞萌",
    106: "音击&中二节奏",
    107: "宴会場",
}


async def _throttle() -> None:
    global _last_request_at
    async with _throttle_lock:
        wait = _MIN_INTERVAL - (time.monotonic() - _last_request_at)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request_at = time.monotonic()


async def _request(
    method: str, path: str, *, json_body: dict | None = None
) -> httpx.Response | None:
    """带 Origin 头的主机回退请求；404（条目不存在）返回 None。

    418（WAF）/网络错误换备用主机各试一轮；其余非 2xx 抛 ExtError。
    """
    last_error: Exception | None = None
    for host in _HOSTS:
        await _throttle()
        try:
            resp = await get_client().request(
                method,
                host + path,
                json=json_body,
                headers={"Origin": _PORTAL_ORIGIN, "accept": "application/json"},
                timeout=20,
            )
        except httpx.RequestError as e:
            last_error = e
            continue
        if resp.status_code == 404:
            return None
        if resp.status_code == 418:
            last_error = ExtError("MuNET WAF 拦截（418，Origin 头缺失或被风控）")
            continue
        if resp.status_code != 200:
            raise ExtError(f"MuNET {path} 拉取失败（HTTP {resp.status_code}）")
        return resp
    raise ExtNetworkError(f"MuNET {path} 请求失败：{last_error}") from last_error


async def fetch_browse_filters() -> dict[str, list[int]]:
    """歌曲浏览过滤维度：``{"genres": [101..107], "versions": [0..27]}``。

    ``versions`` 即 addVersion 全集——出现新值 = MuNET 已收录新版本批次（版本级
    信号，每日 1 请求）。
    """
    resp = await _request("GET", "/api/v3/mai2/Mai2Music/BrowseFilters")
    data = _parse_json(resp, "MuNET BrowseFilters")
    if not isinstance(data, dict) or not isinstance(data.get("versions"), list):
        raise ExtError("MuNET BrowseFilters 返回了无效数据")
    return data


async def search_music(query: str) -> list[dict]:
    """按标题/别名搜索，返回 ``musicData``（条目自带 addVersion/aliases/charts）。"""
    if len(query.strip()) < 2:
        return []  # 实测单字符 0 命中（服务端最小长度），省一次请求
    resp = await _request(
        "POST",
        "/api/v3/mai2/Mai2Music/Search",
        json_body={"query": query, "cachedIds": []},
    )
    data = _parse_json(resp, "MuNET Search")
    return (data or {}).get("musicData") or []


async def fetch_music_by_id(music_id: int) -> dict | None:
    """单曲全量（aliases/charts/constants）；条目不存在（404 或空壳）→ None。"""
    resp = await _request("GET", f"/api/v3/mai2/Mai2Music/GetById/{music_id}")
    if resp is None:
        return None
    data = _parse_json(resp, f"MuNET GetById/{music_id}")
    if not isinstance(data, dict) or not data.get("name"):
        return None
    return data


def _parse_json(resp: httpx.Response, name: str) -> Any:
    try:
        return resp.json()
    except ValueError as e:
        raise ExtError(f"{name} 返回了无效数据") from e


def add_version_to_code(add_version: int) -> int | None:
    """MuNET addVersion 序号 → 日服版本码：13→DX 20000、每档 +500（实测 35/35）。

    老框（0–12）语义与本项目范围无关（§七：只做 current_jp 批次），返回 None。
    """
    if add_version >= 13:
        return 20000 + (add_version - 13) * 500
    return None


def _entry_kind(entry: dict) -> str | None:
    """MuNET 条目 → 规范表组类别（sd/dx/utage）。

    id≥10000 恒为 DX 组；id<10000 看谱面 kind（0=SD、1=DX——DX-only 曲直接用
    曲 id，如パズルリボン=1449）；只剩宴谱的独立条目为 utage。
    """
    if int(entry["id"]) >= DX_ID_OFFSET:
        return "dx"
    kinds = {c.get("kind") for c in entry.get("charts") or [] if not c.get("utageId")}
    if 0 in kinds:
        return "sd"
    if 1 in kinds:
        return "dx"
    if entry.get("charts"):
        return "utage"
    return None


def entry_to_doc(
    entry: dict, *, image_url: str | None = None
) -> tuple[int, dict] | None:
    """MuNET 条目 → 01 标准 JSON 片段（(规范表曲 id, doc)）；不可转换 → None。

    - 只产日侧字段（version_cn 由合并层拒写保证）；定数取 constants[] 末值
      （单元素 =「当前定数」快照语义，§7.5-C）；
    - 宴谱内容不转换：MuNET utageId 无法映射规范表 level_id，宴字段由 otoge-db
      提供（独立宴谱条目仍建曲与组版本，别名照常收割）；
    - buddy（協）谱物量恒 0（无左右分工），不产 notes 留给 otoge-db。
    """
    music_id = int(entry["id"])
    kind = _entry_kind(entry)
    if kind is None:
        return None
    doc: dict[str, Any] = {
        "title": entry.get("name") or "",
        "artist": entry.get("artist") or "",
    }
    if not doc["title"]:
        return None
    genre = _GENRE_NAMES.get(entry.get("genre"))
    if genre:
        doc["genre"] = genre
    if entry.get("bpm"):
        doc["bpm"] = str(entry["bpm"])
    if image_url:
        doc["image_url"] = image_url
    add_version = entry.get("addVersion")
    code = add_version_to_code(int(add_version)) if add_version is not None else None
    sheets: dict[str, Any] = {}
    if kind != "utage":
        contents: list[dict] = []
        earliest: int | None = None
        for chart in entry.get("charts") or []:
            if chart.get("utageId"):
                continue
            content: dict[str, Any] = {"level_id": int(chart.get("difficulty") or 0)}
            if chart.get("designer"):
                content["designer"] = chart["designer"]
            notes = [
                int(chart.get(k) or 0)
                for k in (
                    "tapCount",
                    "holdCount",
                    "slideCount",
                    "touchCount",
                    "breakCount",
                )
            ]
            if any(notes):
                content["notes"] = notes
            consts = sorted(
                (int(c.get("version") or 0), float(c["constant"]))
                for c in chart.get("constants") or []
                if c.get("constant") is not None
            )
            if consts:
                content["level"] = [consts[-1][1]]
            rel = chart.get("releaseTime")
            if rel:
                ymd = int(str(rel)[:10].replace("-", ""))
                earliest = ymd if earliest is None else min(earliest, ymd)
            contents.append(content)
        if contents:
            sheet: dict[str, Any] = {}
            if code is not None:
                sheet["version"] = code
            if earliest is not None:
                sheet["date"] = earliest
            sheet["contents"] = contents
            sheets[kind] = sheet
    elif code is not None:
        sheets["utage"] = {"version": code}
    if not sheets:
        return None
    doc["sheets"] = sheets
    return music_id % DX_ID_OFFSET, doc


def harvest_aliases(entries: list[dict]) -> dict[int, list[str]]:
    """条目别名收割（根 id 折叠 → 原始别名；归一化在 provider 合并层做）。"""
    out: dict[int, list[str]] = {}
    for entry in entries:
        root = int(entry["id"]) % DX_ID_OFFSET
        aliases = [a["alias"] for a in entry.get("aliases") or [] if a.get("alias")]
        if aliases:
            out.setdefault(root, []).extend(aliases)
    return out


async def refresh_aliases_full(
    *, budget_seconds: float = _WALK_BUDGET_SECONDS, force: bool = False
) -> dict:
    """别名全量走查（断点续走）：规范表派生组级 id 集 → 逐条 GetById 收割。

    - 完成后整源替换 ``song_alias``（source=munet，原始形态，归一化在 provider
      合并层）；中断记 kv 游标，下次任务从断点继续（单条失败游标照常前进，
      下个刷新周期自然重试）；
    - ``awmc_munet_alias_days`` 控制整轮间隔（0=禁用）；时间预算默认 60 分钟，
      单晚走完（实测 1763 请求 ≈ 30 分钟）；
    - ``force=True``（手动触发，重载补充数据）：跳过间隔/禁用检查，仍受互斥锁
      保护（进行中直接返回 running）。
    """
    from ...config import plugin_config

    if _walk_lock.locked():
        return {"status": "running"}
    interval_days = plugin_config.awmc_munet_alias_days
    if not force:
        if interval_days <= 0:
            return {"status": "disabled"}
        walk_state = await store.kv_get(_WALK_KV) or {}
        finished_at = walk_state.get("finished_at")
        if (
            walk_state.get("cursor") is None
            and finished_at
            and time.time() - finished_at < interval_days * 86400
        ):
            return {"status": "fresh", "finished_at": finished_at}
    async with _walk_lock:
        return await _walk_targets(budget_seconds)


async def _walk_targets(budget_seconds: float) -> dict:
    state = await store.kv_get(_WALK_KV) or {}
    cursor = state.get("cursor")
    targets = await store.list_alias_walk_targets()
    index = int(cursor or 0)
    logger.info(
        f"MuNET 别名走查：从 {index}/{len(targets)} 继续（预算 {budget_seconds:.0f}s）"
    )
    started = time.monotonic()
    collected: dict[int, list[str]] = {}
    hits = misses = 0
    while index < len(targets):
        if time.monotonic() - started > budget_seconds:
            await store.kv_set(_WALK_KV, {**state, "cursor": index})
            logger.info(f"MuNET 别名走查：预算耗尽，断点 {index}/{len(targets)}")
            return {"status": "partial", "cursor": index, "total": len(targets)}
        entry = None
        try:
            entry = await fetch_music_by_id(targets[index])
        except (ExtError, ExtNetworkError) as e:
            logger.warning(f"MuNET 别名走查：id={targets[index]} 失败（{e}）")
        if entry is None:
            misses += 1
        else:
            hits += 1
            for root, aliases in harvest_aliases([entry]).items():
                collected.setdefault(root, []).extend(aliases)
        index += 1
        if index % 50 == 0:
            await store.kv_set(_WALK_KV, {**state, "cursor": index})
    await store.save_song_aliases("munet", collected)
    await store.kv_set(
        _WALK_KV, {"cursor": None, "finished_at": time.time(), "count": len(collected)}
    )
    total = sum(len(v) for v in collected.values())
    logger.info(f"MuNET 别名走查完成：命中 {hits} / 空 {misses}，别名 {total} 条")
    return {"status": "done", "hits": hits, "misses": misses, "aliases": total}


async def _pending_titles() -> list[str]:
    """song_pending 暂存条目的标题（无 id 曲目，同样走 MuNET 解析 id）。"""
    titles: list[str] = []
    async with store.session() as db:
        pending_rows = (await db.exec(select(store.SongPending))).all()
    for row in pending_rows:
        try:
            payload = json.loads(row.payload)
        except ValueError:
            continue
        if isinstance(payload, dict) and payload.get("title"):
            titles.append(payload["title"])
    return titles


async def run_batch_supplement() -> dict:
    """current_jp 批次内新增歌曲补充：otoge 视角 title-diff → 拉取合并。

    - 候选 = otoge-db 视角下规范表没有的标题：自动化 PR 分支（day-0 标题+
      封面哈希，§六）∪ merged main 现役表 ∪ ``song_pending`` 暂存标题——
      **版本内期中新增**（如 MAGiCAL 期的 27001 追加曲）与新版本批次同样覆盖；
    - 每个候选标题 ``Search`` 解析 id → ``GetById`` 全量 → 01 标准 JSON
      （fill 模式 + 创建缺失曲，经 :func:`songdb.apply_external_sources`）；
      搜索按标题/别名模糊命中，仅对名称与候选一致的条目拉取全量；
    - 别名随拉取收割进 ``song_alias``（增量 upsert）；
    - 每日管线触发，稳态无新增时零歌曲请求；BrowseFilters 仅作版本状态日志。
    """
    from . import otoge_db as ext_otoge
    from . import otoge_pr
    from .. import songdb
    from ...config import plugin_config

    if not plugin_config.awmc_munet_batch:
        return {"status": "disabled"}
    canonical_titles = await store.list_song_titles()
    images: dict[str, str] = {}
    try:
        pr_entries = await otoge_pr.load_open_pr_entries()
    except Exception as e:
        logger.warning(f"MuNET 批次：otoge PR 预读失败（不影响流程）：{e}")
        pr_entries = []
    for entry in pr_entries:
        if entry.get("image_url"):
            images[entry["title"]] = entry["image_url"]
    titles = [e["title"] for e in pr_entries if e["title"] not in canonical_titles]
    titles.extend(
        e["title"]
        for e in await ext_otoge.fetch_music_ex()
        if e.get("title") and e["title"] not in canonical_titles
    )
    titles.extend(await _pending_titles())
    titles = [t for t in dict.fromkeys(titles) if t not in canonical_titles]
    if len(titles) > 300:  # 安全阀：异常批量候选截断（正常批次 ≤ 数十）
        logger.warning(f"MuNET 批次：候选标题 {len(titles)} 超常，截断至 300")
        titles = titles[:300]
    logger.info(f"MuNET 批次补充：规范表外候选标题 {len(titles)} 个")
    docs_by_base: dict[int, dict] = {}
    alias_items: dict[int, list[str]] = {}
    processed = 0
    for title in titles:
        try:
            hits = await search_music(title)
        except (ExtError, ExtNetworkError) as e:
            logger.warning(f"MuNET 批次：搜索「{title}」失败（{e}）")
            continue
        want = normalize_text(title)
        for entry in hits:
            # 搜索按标题/别名模糊命中：只拉名称与候选一致的条目，防泛化拉取
            if normalize_text(entry.get("name") or "") != want:
                continue
            try:
                payload = await fetch_music_by_id(int(entry["id"]))
            except (ExtError, ExtNetworkError) as e:
                logger.warning(f"MuNET 批次：id={entry['id']} 拉取失败（{e}）")
                continue
            if payload is None:
                continue
            processed += 1
            for root, aliases in harvest_aliases([payload]).items():
                alias_items.setdefault(root, []).extend(aliases)
            converted = entry_to_doc(
                payload, image_url=images.get(str(payload.get("name")))
            )
            if converted is None:
                continue
            base, song_doc = converted
            merged = docs_by_base.setdefault(base, {"sheets": {}})
            for field in ("title", "artist", "genre", "bpm", "image_url"):
                if song_doc.get(field) and not merged.get(field):
                    merged[field] = song_doc[field]
            merged["sheets"].update(song_doc["sheets"])
    result: dict[str, Any] = {
        "status": "batch",
        "candidates": len(titles),
        "entries": processed,
    }
    if docs_by_base:
        doc = {str(base): song_doc for base, song_doc in docs_by_base.items()}
        summary = await songdb.apply_external_sources(
            preloaded=[("munet", "fill", doc)], force=True
        )
        result["merge"] = {
            k: summary.get(k) for k in ("applied", "changed") if k in summary
        }
    if alias_items:
        result["aliases"] = await store.upsert_song_aliases("munet", alias_items)
    try:
        filters = await fetch_browse_filters()
        logger.info(f"MuNET 版本状态：addVersion {filters.get('versions')}")
    except (ExtError, ExtNetworkError) as e:
        logger.debug(f"MuNET BrowseFilters 状态获取失败（信息性）：{e}")
    await store.kv_set(_BATCH_KV, {"ran_at": time.time(), **result})
    logger.info(f"MuNET 批次补充完成：{result}")
    return result
