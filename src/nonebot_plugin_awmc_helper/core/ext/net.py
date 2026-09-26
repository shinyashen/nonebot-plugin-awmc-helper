"""日服 maimai でらっくす NET 直连抓取（maimai-py 不含 NET 数据源）。

登录流与 HTML 解析对齐 gekichumai/dxrating 的 MaimaiNETJpClient（本地克隆
local/repos/dxrating，调研笔记 local/reference/dxrating-net-notes.md）：
登录页取 token → 表单提交 → 选 Aime 卡 → 逐难度抓记录页并按 CSS 选择器解析。
INTL（国际服 am-all.net 网关）暂不实现，端点差异见调研笔记 §4。

- 客户端每次查询新建（登录 cookie 会话级持有，用完即弃，不落库）；
  follow_redirects=False：登录成功与否靠 302 Location 判定，其余跳转不跟随；
- maimaidx.jp TLS 只下发叶子证书，用 core.http.maimaidx_ssl_context() 补链；
  智能代理层对该域已按「国外站代理优先」路由；
- 官方无公开 API，页面改版会导致解析失效：解析不到任何记录时抛
  NetError("parse_error")，由上层转用户文案（提醒联系管理员）。
"""

import re
from dataclasses import dataclass

import httpx
from bs4 import Tag, BeautifulSoup

from . import ExtError, ExtNetworkError
from ..http import maimaidx_ssl_context, build_smart_transport

BASE = "https://maimaidx.jp/maimai-mobile"

# 错误页判定（dxrating URLS.CHECKLIST.ERROR：jp/eng 两域，本模块仅 JP）
_ERROR_PATH_MARK = "maimai-mobile/error/"
# 维护文案（dxrating URLS.CHECKLIST.MAINTENANCE）
_MAINTENANCE_MARKS = ("定期メンテナンス中です",)

# 浏览器指纹头（dxrating COMMON_HEADERS 全量照抄；NET 对非浏览器 UA 有风控）
COMMON_HEADERS = {
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
        "image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
    ),
    "Accept-Language": "ja;q=0.9,en;q=0.8",
    "DNT": "1",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    ),
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "sec-ch-ua": '"Chromium";v="121", "Not A(Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
}

# B50 参与谱面的 diff 参数（0-4 = basic..remaster；10 = 宴谱页，不参与 rating 暂不抓）
B50_DIFF_PARAMS: tuple[int, ...] = (0, 1, 2, 3, 4)


class NetError(ExtError):
    """NET 业务错误（dxrating NetImportError 的 code 语义）。"""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


# code → 用户文案（core 层统一转 UserScoreError）
NET_ERROR_MESSAGES = {
    "maintenance": "maimai NET 正在定期维护中，请稍后再试",
    "invalid_credentials": "SEGA ID 或密码错误，请重新绑定",
    "aime_unavailable": "SEGA ID 登录成功，但 maimai NET 没有可用的 Aime 卡片",
    "token_error": "NET 登录页校验异常（官方页面可能已改版），请联系管理员",
    "parse_error": "NET 记录页解析失败（官方页面可能已改版），请联系管理员",
    "unknown_error": "NET 查询失败，请稍后再试",
}


@dataclass
class NetCredentials:
    """NET 登录凭据（SEGA ID + 密码）。"""

    sega_id: str
    password: str


@dataclass
class NetRecord:
    """NET 记录页单谱面成绩（dxrating MusicRecord 对应）。"""

    title: str
    type: str  # standard / dx / utage
    difficulty: str  # basic / advanced / expert / master / remaster / utage
    achievement: float  # 达成率百分制（万分位精度，如 100.5）
    dx_score: int | None = None
    dx_score_total: int | None = None
    fc: str | None = None  # fc / fcp / ap / app
    fs: str | None = None  # sync / fs / fsp / fsd / fsdp


# FC/FS 徽章文件名 → 语义（dxrating MUSIC_RECORD_FLAG_MATCHERS）
_FLAG_MATCHERS: tuple[tuple[str, str], ...] = (
    ("applus.png", "app"),
    ("ap.png", "ap"),
    ("fcplus.png", "fcp"),
    ("fc.png", "fc"),
    ("fsdplus.png", "fsdp"),
    ("fsd.png", "fsd"),
    ("fsplus.png", "fsp"),
    ("fs.png", "fs"),
    ("sync.png", "sync"),
)
# ap/fc 前缀互撞（ap.png ∈ applus.png 子串）：先长后短保证精确匹配


class MaimaiNetClient:
    """单次查询会话：登录 + 记录抓取（cookie 会话级，用完 aclose）。"""

    def __init__(self) -> None:
        transport = build_smart_transport(verify=maimaidx_ssl_context())
        kwargs: dict = {
            "follow_redirects": False,
            "headers": COMMON_HEADERS,
            "timeout": httpx.Timeout(connect=10, read=30, write=10, pool=10),
            # Referer 由逐请求设置（dxrating fetch() 同语义）
        }
        if transport is not None:
            kwargs["transport"] = transport
        else:
            kwargs["verify"] = maimaidx_ssl_context()
        self._http = httpx.AsyncClient(**kwargs)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(
        self,
        method: str,
        url: str,
        *,
        data: dict | None = None,
        params: dict | None = None,
        error_code: str = "unknown_error",
    ) -> httpx.Response:
        """单请求：手动跟随语义 + 错误重定向/维护判定（dxrating fetch 同构）。

        302 且 Location 命中错误页 → 抛 ``error_code``（登录 POST 处传
        ``invalid_credentials``）；其余跳转不跟随（NET 内部各页均直接 200，
        与 dxrating redirect: manual 行为一致）。
        """
        try:
            resp = await self._http.request(
                method, url, data=data, params=params, headers={"Referer": BASE + "/"}
            )
        except httpx.RequestError as e:
            raise ExtNetworkError("maimai NET 网络异常") from e
        location = resp.headers.get("location", "")
        if resp.is_redirect and _ERROR_PATH_MARK in location:
            raise NetError(error_code, "maimai NET 把请求重定向到了错误页")
        if any(m in resp.text for m in _MAINTENANCE_MARKS):
            raise NetError("maintenance")
        return resp

    async def login(self, creds: NetCredentials) -> None:
        """SEGA ID 登录并选定第一张 Aime 卡（dxrating MaimaiNETJpClient.login）。"""
        login_page = await self._request("GET", f"{BASE}/")
        token = _extract_login_token(login_page.text)
        if not token:
            raise NetError("token_error")
        await self._request(
            "POST",
            f"{BASE}/submit/",
            data={
                "segaId": creds.sega_id,
                "password": creds.password,
                "save_cookie": "on",
                "token": token,
            },
            error_code="invalid_credentials",
        )
        # 选 Aime 卡（idx=0 = 第一张）→ 进 home 领全会话 cookie
        await self._request("GET", f"{BASE}/aimeList/")
        await self._request("GET", f"{BASE}/aimeList/submit/", params={"idx": "0"})
        await self._request("GET", f"{BASE}/home/")

    async def fetch_music_records(self) -> list[NetRecord]:
        """逐难度抓全曲记录页并解析（B50 参与：standard/dx 全难度）。"""
        records: list[NetRecord] = []
        for diff in B50_DIFF_PARAMS:
            resp = await self._request(
                "GET",
                f"{BASE}/record/musicGenre/search/",
                params={"genre": "99", "diff": str(diff)},
            )
            page_records = _parse_music_records(resp.text)
            if not page_records:
                # 有登录会话的记录页不可能为空（未登录会被重定向到错误页）：
                # 空结果即页面结构变化，宁可报错不给空 B50
                raise NetError("parse_error")
            records.extend(page_records)
        return records


def _extract_login_token(html: str) -> str | None:
    """登录页 hidden token：``<input name="token" value="...">``。"""
    soup = BeautifulSoup(html, "html.parser")
    node = soup.find("input", attrs={"name": "token"})
    value = node.get("value") if isinstance(node, Tag) else None
    return str(value) if value else None


def _parse_music_records(html: str) -> list[NetRecord]:
    """记录页 HTML → 谱面成绩列表（dxrating parseMusicRecordNode 同构）。

    解析失败的块静默跳过；块内必要字段缺失（曲名/类型/难度/达成率）时丢弃。
    """
    soup = BeautifulSoup(html, "html.parser")
    records: list[NetRecord] = []
    for block in soup.select(".w_450.m_15.p_r.f_0"):
        record = _parse_record_block(block)
        if record is not None:
            records.append(record)
    return records


def _parse_record_block(block: Tag) -> NetRecord | None:
    title_el = block.select_one(".music_name_block")
    title = title_el.get_text(strip=True) if title_el else ""
    if not title:
        return None

    rate_el = block.select_one(".music_score_block.w_112")
    achievement = _parse_achievement(rate_el.get_text(strip=True) if rate_el else "")
    if achievement is None:
        return None

    type_ = _parse_type(block)
    difficulty = _parse_difficulty(block)
    if not type_ or not difficulty:
        return None

    dx_score, dx_total = _parse_dx_score(block)

    fc: str | None = None
    fs: str | None = None
    for img in block.select("form img.f_r"):
        src = img.get("src") or ""
        for suffix, flag in _FLAG_MATCHERS:
            if suffix in src:
                if flag.startswith(("ap", "fc")):
                    fc = flag
                else:
                    fs = flag
                break

    return NetRecord(
        title=title,
        type=type_,
        difficulty=difficulty,
        achievement=achievement,
        dx_score=dx_score,
        dx_score_total=dx_total,
        fc=fc,
        fs=fs,
    )


def _parse_achievement(text: str) -> float | None:
    """达成率 ``100.5000%`` → 百分制 100.5（dxrating 万分位还原）。"""
    if not text:
        return None
    raw = text.replace("%", "").replace(".", "")
    try:
        rate = int(raw) / 10000
    except ValueError:
        return None
    return rate if 0 <= rate <= 101 else None


def _parse_type(block: Tag) -> str | None:
    """谱面类型：图标 src 优先，toggle 按钮 ``_btn_on`` 状态覆盖。"""
    type_ = None
    icon = block.select_one(".music_kind_icon")
    if icon is not None:
        m = re.search(r"music_(standard|dx)\.png", icon.get("src") or "")
        type_ = m.group(1) if m else None
    for selector, value in (
        (".music_kind_icon_dx", "dx"),
        (".music_kind_icon_standard", "standard"),
    ):
        btn = block.select_one(selector)
        classes = " ".join(btn.get("class") or []) if btn is not None else ""
        if "_btn_on" in classes:
            return value
    return type_


def _parse_difficulty(block: Tag) -> str | None:
    icon = block.select_one(".h_20.f_l")
    m = (
        re.search(r"diff_(\w+)\.png", icon.get("src") or "")
        if icon is not None
        else None
    )
    return m.group(1) if m else None


def _parse_dx_score(block: Tag) -> tuple[int | None, int | None]:
    """DX 分 ``1,234,567 / 2,000,000`` → (achieved, total)；失败返回 (None, None)。"""
    el = block.select_one(".music_score_block.w_190")
    text = el.get_text(strip=True) if el else ""
    if " / " not in text:
        return None, None
    parts = text.split(" / ", 1)
    try:
        return int(parts[0].replace(",", "")), int(parts[1].replace(",", ""))
    except ValueError:
        return None, None
