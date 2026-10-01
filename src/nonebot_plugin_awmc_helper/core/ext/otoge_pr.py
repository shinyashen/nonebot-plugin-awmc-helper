"""otoge-db 自动化 PR 分支预读：day-0 新曲标题/封面哈希（munet-alias-notes §六）。

上游 cron 推分支 ``maimai/update-<日期>`` 开 PR 且物量/定数脚本在分支上跑完，
PR 分支 = 完整数据快照，可能尚未 merge。分支文件偶发损坏（工作流
``git stash pop || true`` 吞冲突后照常提交，#1206 实测带 33 行冲突标记），
按分层修复解析：L1 严格解析 → L2 git 标记分段（承重层，保留分支侧）→
L3 段级抢救兜底；任一层不可解析即放弃该分支（宁缺毋滥，merge 后由 main
常规管线补上）。消费语义：只取 main 没有的条目，已存在条目一律以 main 为准。
"""

import re
import json

import httpx
from nonebot import logger

from . import ExtError, ext_request
from . import otoge_db as ext_otoge

_GITHUB_API = "https://api.github.com/repos/zvuc/otoge-db"
_RAW_BASE = "https://raw.githubusercontent.com/zvuc/otoge-db"
_BRANCH_PREFIX = "maimai/update-"

# git 标准冲突标记：ours（Updated upstream）/ theirs（Stashed changes）。
# 保留 theirs（分支侧）——bot 新数据在分支上；两侧实测都完整，取舍只影响
# 个别字段新旧，消费端只取 main 没有的条目故不受影响。
_MARKER_BLOCK = re.compile(
    r"<<<<<<<[^\n]*\n(.*?)\n?=======\n(.*?)\n?>>>>>>>[^\n]*\n?", re.S
)
# 标记行本身（不含内容）：stash pop 多重撞车时产生连续/嵌套/空侧标记
# （2026-09-30 PR #1206 实测 105 行：`<<<<<<<` 三连同挂、`<<<<<<<` 直连
# `>>>>>>>` 无 ours 侧、`=======` 与 `>>>>>>>` 交替续段），此时三段式无法
# 匹配，直接剥掉所有标记行即可还原——数据内容全部还留在两侧之间。
_MARKER_LINE = re.compile(r"^(?:<{7}|={7}|>{7}).*$", re.M)


def _balanced_end(text: str, start: int) -> int | None:
    """``start`` 指向 ``{`` 时返回配对 ``}`` 下标；括号不平衡 → None。"""
    depth = 0
    in_str = False
    esc = False
    for j in range(start, len(text)):
        ch = text[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return j
    return None


def parse_music_ex(text: str) -> tuple[list[dict], str] | None:
    """music-ex.json 文本 → (条目列表, 解析层级)；完全不可解析 → None。

    层级语义：strict（原样合法）；markers（冲突标记可三段式分段或整行剥离后
    合法）；salvage（段级抢救——只保留能独立解析且带合法 title 的对象，被冲突
    彻底切碎的对象会丢失，只作最后兜底，消费端以「条数 ≥ main」门校验）。
    """
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if isinstance(data, list):
        return [e for e in data if isinstance(e, dict)], "strict"
    repaired = _MARKER_BLOCK.sub(lambda m: m.group(2) + "\n", text)
    if repaired != text:
        try:
            data = json.loads(repaired)
        except ValueError:
            data = None
        if isinstance(data, list):
            return [e for e in data if isinstance(e, dict)], "markers"
    # 多重撞车形态（连续/嵌套/空侧标记）：三段式无能为力，剥所有标记行后
    # 两侧数据内容都还在，实测可完整解析（PR #1206 head 1599 条含三新曲）
    stripped = _MARKER_LINE.sub("", text)
    if stripped != text:
        try:
            data = json.loads(stripped)
        except ValueError:
            data = None
        if isinstance(data, list):
            return [e for e in data if isinstance(e, dict)], "markers"
    entries = []
    i, n = 0, len(text)
    while i < n:
        start = text.find("{", i)
        if start == -1:
            break
        end = _balanced_end(text, start)
        if end is None:
            i = start + 1
            continue
        try:
            obj = json.loads(text[start : end + 1])
        except ValueError:
            i = start + 1  # 该跨度不可解析：前移一位重扫，内层对象仍可救回
            continue
        if isinstance(obj, dict) and obj.get("title"):
            entries.append(obj)
        i = end + 1
    if entries:
        return entries, "salvage"
    return None


async def list_open_song_prs() -> list[tuple[int, str, str]]:
    """open 且 head 分支为 ``maimai/update-*`` 的 PR：[(number, branch, title)]。

    配置了 ``AWMC_GITHUB_TOKEN`` 时携带 Bearer 认证（限额 5000 次/小时，
    匿名 60 次/小时在共享出口 IP 上易触顶 403）。
    """
    from ...config import plugin_config

    headers = {"accept": "application/vnd.github+json"}
    if plugin_config.awmc_github_token:
        headers["Authorization"] = f"Bearer {plugin_config.awmc_github_token}"
    resp = await ext_request(
        "GET",
        f"{_GITHUB_API}/pulls",
        name="otoge PR 列表",
        network_message="otoge PR 列表网络异常，请稍后再试",
        params={"state": "open", "per_page": "50"},
        headers=headers,
        timeout=30,
    )
    if resp.status_code != 200:
        raise ExtError(f"otoge PR 列表拉取失败（HTTP {resp.status_code}）")
    out: list[tuple[int, str, str]] = []
    for pr in resp.json():
        ref = (pr.get("head") or {}).get("ref") or ""
        if ref.startswith(_BRANCH_PREFIX):
            out.append((int(pr["number"]), ref, pr.get("title") or ""))
    return out


async def fetch_branch_music_ex(branch: str) -> str:
    """PR 分支上的 music-ex.json 原文（raw.githubusercontent，智能代理路由）。"""
    resp = await ext_request(
        "GET",
        f"{_RAW_BASE}/{branch}/maimai/data/music-ex.json",
        name=f"otoge PR 分支 {branch}",
        network_message="otoge PR 分支数据网络异常，请稍后再试",
        timeout=120,
    )
    if resp.status_code == 404:
        raise ExtError(f"otoge PR 分支 {branch} 无 music-ex.json（404）")
    if resp.status_code != 200:
        raise ExtError(f"otoge PR 分支 {branch} 拉取失败（HTTP {resp.status_code}）")
    return resp.text


async def load_open_pr_entries() -> list[dict]:
    """全部 open 批次 PR 的解析条目（含已在 main 的；调用方按规范表过滤）。

    单 PR 损坏/分支消失记日志跳过；条目为 otoge 原始 dict（含 ``title``/
    ``image_url``/``version`` 等），跨 PR 按标题去重。无 open PR → 空列表。
    """
    prs = await list_open_song_prs()
    if not prs:
        return []
    main = await ext_otoge.fetch_music_ex()
    main_count = len(main)
    out: list[dict] = []
    seen: set[str] = set()
    for number, branch, _title in prs:
        try:
            text = await fetch_branch_music_ex(branch)
        except (ExtError, httpx.HTTPStatusError, httpx.RequestError) as e:
            logger.warning(f"otoge PR #{number}（{branch}）分支拉取失败，跳过：{e}")
            continue
        parsed = parse_music_ex(text)
        if parsed is None:
            logger.warning(f"otoge PR #{number}（{branch}）分支完全不可解析，跳过")
            continue
        entries, layer = parsed
        # 合理性门：分支由 main 生长而来，条数不应少于 main（否则视为损坏）
        if len(entries) < main_count:
            logger.warning(
                f"otoge PR #{number}（{branch}）条数 {len(entries)} < main "
                f"{main_count}，疑似损坏，跳过"
            )
            continue
        logger.info(f"otoge PR #{number}（{branch}）：{layer} 层解析 {len(entries)} 条")
        for entry in entries:
            title = entry.get("title")
            if title and title not in seen:
                seen.add(title)
                out.append(entry)
    return out
