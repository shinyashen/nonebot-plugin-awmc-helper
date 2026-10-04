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
from typing import Any, cast

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
_walk_running = False
"""别名走查在跑标志（互斥单源）：check 与置位之间无 await，事件循环内
原子——并发触发（每日 cron + 手动 force）后到者立即 running，不排队重走。"""

_WALK_KV = "munet_alias_walk"
_WALK_BUDGET_SECONDS = 3600.0
"""单次走查时间预算（60 分钟；规范表实测 1763 请求 ≈ 30 分钟，预算留足余量
保证单晚走完），超时记游标跨日续走。"""

_PENDING_MAX_ATTEMPTS = 10
"""批次候选的 pending 重试阈值：``SongPending.attempts``（每轮归并失败/upsert
自增）达到该值的标题不再作为批次候选——连续多轮都未能在 MuNET 归并的条目
暂缓重试，防每晚空转；用户可见的 pending 查歌路径不受影响。"""

_REQUEST_TIMEOUT = 20
"""单请求超时（秒）；MuNET 响应轻且直连快（§七实测 0.24s），超时按整机容忍取 20s。"""
_DAY_SECONDS = 86400
"""整轮间隔换算系数：``awmc_munet_alias_days``（天）→ 秒。"""
_WALK_CHECKPOINT = 50
"""走查游标落盘步长（每 N 条记一次断点，异常中断最多重走 N 条）。"""
_BATCH_TITLE_CAP = 300
"""批次候选标题安全阀：正常批次 ≤ 数十，超常（上游异常批量）截断防拖垮整轮。"""

# genre 数字 → 规范表流派名（落雪/国服值域）。2026-09-28 与规范表全量分组对账
# 推导（抽样 46 曲六档全一致），非官方映射表；107=宴会場 为推断（未实测到条目）。
# 与 constants.GENRE_TO_ZH（Genre 枚举 → 中文显示名）、songdb 规范表 canonical
# 流派名映射 OTOGE_CATCODE_TO_GENRE（songdb.py ~391，otoge catcode → maimai_py
# Genre 值域）是三个各自语义正确的平行表（本表=规范表 canonical，constants=
# 显示名），独立维护；规范表流派名变更时须同步核对本表。
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
                timeout=_REQUEST_TIMEOUT,
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
    # 全主机网络失败 → ExtNetworkError；全主机 418（WAF 风控语义）→ ExtError，
    # 两者语义不同，不把风控误报成网络错误
    if isinstance(last_error, ExtError):
        raise last_error
    raise ExtNetworkError(f"MuNET {path} 请求失败：{last_error}") from last_error


async def fetch_browse_filters() -> dict[str, list[int]]:
    """歌曲浏览过滤维度：``{"genres": [101..107], "versions": [0..27]}``。

    ``versions`` 即 addVersion 全集——出现新值 = MuNET 已收录新版本批次（版本级
    信号，每日 1 请求）。
    """
    resp = await _request("GET", "/api/v3/mai2/Mai2Music/BrowseFilters")
    if resp is None:
        raise ExtError("MuNET BrowseFilters 不存在（404）")
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
    if resp is None:
        return []  # 404 = 无命中（对齐 fetch_music_by_id 的 404 语义）
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
    entry: dict, *, image_url: str | None = None, otoge_fact: dict | None = None
) -> tuple[int, dict] | None:
    """MuNET 条目 → 01 标准 JSON 片段（(规范表曲 id, doc)）；不可转换 → None。

    - 只产日侧字段（version_cn 由合并层拒写保证）；定数取 constants[] 末值
      （单元素 =「当前定数」快照语义，§7.5-C）；
    - 组版本/日期只在条目确认日服在役（谱面 ``optJapan`` 非空）时由 addVersion
      推导——addVersion 跟随条目所在区域的当期版本批次，国际服先行曲拿到的是
      国际服码（OV3RCLOCK 实测 addVersion 26 → CiRCLE PLUS，日服实为 MAGiCAL），
      误写日侧组版本且 fill/基础源都不覆盖会永久滞留；otoge 事实（PR 预读/
      现役表的日服权威 version/release）优先于 MuNET 推导值；
    - 宴谱内容不转换：MuNET utageId 无法映射规范表 level_id，宴字段由 otoge-db
      提供（独立宴谱条目仍建曲与组版本，别名照常收割）；宴为日服独占，其组
      版本不做在役确认；
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
    genre_id = entry.get("genre")
    genre = _GENRE_NAMES.get(genre_id) if isinstance(genre_id, int) else None
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
        jp_confirmed = any(
            not c.get("utageId") and c.get("optJapan")
            for c in entry.get("charts") or []
        )
        fact = otoge_fact or {}
        version = fact.get("version")
        if version is None and code is not None and jp_confirmed:
            version = code
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
            if version is not None:
                sheet["version"] = version
            date = fact.get("date")
            if date is None and earliest is not None and jp_confirmed:
                date = earliest  # 国际服先行条目的 releaseTime 是国际服日期，不采信
            if date is not None:
                sheet["date"] = date
            sheet["contents"] = contents
            sheets[kind] = sheet
    elif code is not None:
        sheets["utage"] = {"version": code}
    if not sheets:
        return None
    doc["sheets"] = sheets
    return music_id % DX_ID_OFFSET, doc


def _otoge_fact(item: dict) -> dict | None:
    """otoge 条目 → 日服权威版本/日期事实（值域校验，供批次文档与既有曲校正）。

    - version 裸批次码直接采信（与 songdb._otoge_version 同口径，如 MAGiCAL
      期中 27002）；DX 轴范围外（老框 <10000 或 FUTURE 占位 30000）不产事实；
    - date 按 dx 规则 ``release ‖ date_updated ‖ date_added``（YYMMDD 六位原样，
      song-db-design §3；release 语义即追加/复活日，SD 组同样适用）；
    - otoge title join 全程按标题对齐，上游字段缺失/脏值宁可放弃（宁缺毋滥）。
    """
    try:
        version = int(cast("int | str", item.get("version")))
    except (TypeError, ValueError):
        return None
    if not 10000 <= version < 30000:
        return None
    fact: dict[str, int] = {"version": version}
    for key in ("release", "date_updated", "date_added"):
        try:
            date = int(cast("int | str", item.get(key)))
        except (TypeError, ValueError):
            continue
        if date:
            fact["date"] = date
            break
    return fact


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

    - 落库走增量 upsert + 目标集外清理（source=munet，原始形态，归一化在
      provider 合并层）；中断记 kv 游标、本轮成果先行落库，下次任务从断点
      继续（单条失败游标照常前进，下个刷新周期自然重试）；
    - ``awmc_munet_alias_days`` 控制整轮间隔（0=禁用）；时间预算默认 60 分钟，
      单晚走完（实测 1763 请求 ≈ 30 分钟）；
    - ``force=True``（手动触发，重载补充数据）：跳过间隔/禁用检查，仍受
      在跑标志互斥（进行中直接返回 running）。
    """
    global _walk_running
    from ...config import plugin_config

    if _walk_running:
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
            and time.time() - finished_at < interval_days * _DAY_SECONDS
        ):
            return {"status": "fresh", "finished_at": finished_at}
    if _walk_running:
        return {"status": "running"}
    _walk_running = True
    try:
        return await _walk_targets(budget_seconds)
    finally:
        _walk_running = False


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
            # 预算耗尽：本轮成果增量落库后再断点（整源替换会裁剪此前各晚数据）
            # source 名与 provider.ALIAS_SOURCES 对齐（munet 在列）
            await store.upsert_song_aliases("munet", collected)
            await store.kv_set(_WALK_KV, {**state, "cursor": index})
            logger.info(
                f"MuNET 别名走查：预算耗尽，断点 {index}/{len(targets)}"
                f"（本轮增量 {sum(len(v) for v in collected.values())} 条已落库）"
            )
            return {"status": "partial", "cursor": index, "total": len(targets)}
        entry = None
        try:
            entry = await fetch_music_by_id(targets[index])
        except ExtError as e:
            logger.warning(f"MuNET 别名走查：id={targets[index]} 失败（{e}）")
        if entry is None:
            misses += 1
        else:
            hits += 1
            for root, aliases in harvest_aliases([entry]).items():
                collected.setdefault(root, []).extend(aliases)
        index += 1
        if index % _WALK_CHECKPOINT == 0:
            await store.kv_set(_WALK_KV, {**state, "cursor": index})
    # 完成：增量 upsert + 目标集外陈旧行清理（= 整源替换的对齐语义，且不裁剪
    # 断点续走时此前各晚已落库的成果；新增/删除别名以 MuNET 现态为准的强同步
    # 仅在单晚走完全程时成立，多晚拼接对存活曲只增不删——别名列表近似只增）
    # source 名与 provider.ALIAS_SOURCES 对齐（munet 在列）
    await store.upsert_song_aliases("munet", collected)
    pruned = await store.prune_song_aliases(
        "munet", {t % DX_ID_OFFSET for t in targets}
    )
    await store.kv_set(
        _WALK_KV, {"cursor": None, "finished_at": time.time(), "count": len(collected)}
    )
    total = sum(len(v) for v in collected.values())
    logger.info(
        f"MuNET 别名走查完成：命中 {hits} / 空 {misses}，本轮别名 {total} 条，"
        f"清理陈旧 {pruned} 行"
    )
    return {"status": "done", "hits": hits, "misses": misses, "aliases": total}


async def _pending_titles() -> list[str]:
    """song_pending 暂存条目的标题（无 id 曲目，同样走 MuNET 解析 id）。

    attempts 达 :data:`_PENDING_MAX_ATTEMPTS` 的条目跳过（降频，见常量注）。
    """
    titles: list[str] = []
    skipped = 0
    async with store.session() as db:
        pending_rows = (await db.exec(select(store.SongPending))).all()
    for row in pending_rows:
        if row.attempts >= _PENDING_MAX_ATTEMPTS:
            skipped += 1
            continue
        try:
            payload = json.loads(row.payload)
        except ValueError:
            continue
        if isinstance(payload, dict) and payload.get("title"):
            titles.append(payload["title"])
    if skipped:
        logger.info(f"MuNET 批次：{skipped} 条 pending 超重试阈值，本轮跳过")
    return titles


async def _correct_existing_versions(facts: dict[str, dict]) -> list[int]:
    """既有曲版本/日期校正：MuNET 曾入库的曲按 otoge 日服权威值覆写。

    批次建曲时写入的组版本可能是 MuNET 侧口径（国际服先行曲拿到国际服批次
    码，2026-10-03 OV3RCLOCK 实测 26500≠日服 27002），而合并层 fill 与日侧
    基础源（apply_jp/_fill_row_from_otoge）对既有组版本都只填空不覆盖，错误
    值会永久滞留——此处对 ``munet_batch_ids`` 在列且 otoge 事实不同的曲走
    override 文档显式校正（经 apply_external_sources，指纹/底图联动一致）。
    只动 sd/dx 组：宴为日服独占，MuNET addVersion 无国际服先行问题。
    """
    if not facts:
        return []
    from .. import songdb

    munet_ids = set(await store.kv_get("munet_batch_ids") or [])
    if not munet_ids:
        return []
    rows = await store.song_group_facts(sorted(munet_ids))

    def _join_key(title: str) -> str:
        # facts 键在 run_batch_supplement 已按 normalize_text 归一；行标题侧
        # 同口径后再叠 norm_title（去空白）对齐——任一侧只做 norm_title 会在
        # 简繁/全半角差异上错配（normalize_text 相等保证 NFKC 相等，反向不然）
        return songdb.norm_title(normalize_text(title))

    by_title: dict[str, int] = {}
    for sid, info in rows.items():
        if info["title"]:
            by_title.setdefault(_join_key(info["title"]), sid)
    corrections: dict[str, dict] = {}
    for title, fact in facts.items():
        sid = by_title.get(_join_key(title))
        if sid is None:
            continue
        patch = {k: v for k in ("version", "date") if (v := fact.get(k)) is not None}
        if not patch:
            continue
        for kind in ("sd", "dx"):
            group = rows[sid]["groups"].get(kind)
            # 有差异才写：override 文档只在真实偏差时产生，稳态零合并
            if group and any(group.get(k) != v for k, v in patch.items()):
                sheet = corrections.setdefault(str(sid), {"sheets": {}})["sheets"]
                sheet[kind] = dict(patch)
    if not corrections:
        return []
    await songdb.apply_external_sources(
        preloaded=[("munet", "override", corrections)], force=True
    )
    corrected_ids = [int(sid) for sid in corrections]
    logger.info(
        f"MuNET 批次：既有曲版本校正 {len(corrected_ids)} 首"
        f"（{corrected_ids}，otoge 权威值覆写）"
    )
    return corrected_ids


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
    # 成员判断走 set（全库曲名 × 候选集的 `not in` 否则是数百万次线性比较）
    canonical_titles = set(await store.list_song_titles())
    images: dict[str, str] = {}
    facts: dict[str, dict] = {}
    # 两表键与下方等值门同口径（normalize_text）：搜索放行按归一相等，而
    # payload.name 与 otoge 标题原文可能全半角/简繁不等，原名查表会静默丢
    # 封面与版本/日期事实
    try:
        pr_entries = await otoge_pr.load_open_pr_entries()
    except Exception as e:
        logger.warning(f"MuNET 批次：otoge PR 预读失败（不影响流程）：{e}")
        pr_entries = []
    for entry in pr_entries:
        if entry.get("image_url") and entry.get("title"):
            images[normalize_text(entry["title"])] = entry["image_url"]
        # PR 分支领先 main（日服当期批次），先到先得
        if (fact := _otoge_fact(entry)) and entry.get("title"):
            facts.setdefault(normalize_text(entry["title"]), fact)
    live_entries = await ext_otoge.fetch_music_ex()
    for item in live_entries:
        if (fact := _otoge_fact(item)) and item.get("title"):
            facts.setdefault(normalize_text(item["title"]), fact)
    titles = [e["title"] for e in pr_entries if e["title"] not in canonical_titles]
    titles.extend(
        e["title"]
        for e in live_entries
        if e.get("title") and e["title"] not in canonical_titles
    )
    titles.extend(await _pending_titles())
    titles = [t for t in dict.fromkeys(titles) if t not in canonical_titles]
    if len(titles) > _BATCH_TITLE_CAP:  # 安全阀：异常批量候选截断（正常批次 ≤ 数十）
        logger.warning(
            f"MuNET 批次：候选标题 {len(titles)} 超常，截断至 {_BATCH_TITLE_CAP}"
        )
        titles = titles[:_BATCH_TITLE_CAP]
    logger.info(f"MuNET 批次补充：规范表外候选标题 {len(titles)} 个")
    docs_by_base: dict[int, dict] = {}
    alias_items: dict[int, list[str]] = {}
    processed = 0
    for title in titles:
        try:
            hits = await search_music(title)
        except ExtError as e:
            logger.warning(f"MuNET 批次：搜索「{title}」失败（{e}）")
            continue
        want = normalize_text(title)
        for entry in hits:
            # 搜索按标题/别名模糊命中：只拉名称与候选一致的条目，防泛化拉取
            if normalize_text(entry.get("name") or "") != want:
                continue
            try:
                payload = await fetch_music_by_id(int(entry["id"]))
            except ExtError as e:
                logger.warning(f"MuNET 批次：id={entry['id']} 拉取失败（{e}）")
                continue
            if payload is None:
                continue
            processed += 1
            for root, aliases in harvest_aliases([payload]).items():
                alias_items.setdefault(root, []).extend(aliases)
            converted = entry_to_doc(
                payload,
                image_url=images.get(normalize_text(payload.get("name") or "")),
                otoge_fact=facts.get(normalize_text(payload.get("name") or "")),
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
        # 累计留痕已入库曲 id：重建删除判定的「MuNET 在列」信号
        # （munet-alias-notes §七——otoge 侧回滚不应导致已入库曲被误删）
        known = await store.kv_get("munet_batch_ids")
        merged_ids = set(known) if isinstance(known, list) else set()
        merged_ids |= set(docs_by_base)
        await store.kv_set("munet_batch_ids", sorted(merged_ids))
    corrected = await _correct_existing_versions(facts)
    if corrected:
        result["corrected"] = corrected
    if alias_items:
        # source 名与 provider.ALIAS_SOURCES 对齐（munet 在列）
        result["aliases"] = await store.upsert_song_aliases("munet", alias_items)
    try:
        filters = await fetch_browse_filters()
        logger.info(f"MuNET 版本状态：addVersion {filters.get('versions')}")
    except ExtError as e:
        logger.debug(f"MuNET BrowseFilters 状态获取失败（信息性）：{e}")
    logger.info(f"MuNET 批次补充完成：{result}")
    return result
