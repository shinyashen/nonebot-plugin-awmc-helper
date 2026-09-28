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
from typing import NamedTuple
from dataclasses import dataclass
from urllib.parse import urljoin

import httpx
from bs4 import Tag, BeautifulSoup

from . import ExtError, ExtNetworkError
from ..http import create_smart_client, maimaidx_ssl_context

BASE = "https://maimaidx.jp/maimai-mobile"

# 错误页判定（dxrating URLS.CHECKLIST.ERROR：jp/eng 两域，本模块仅 JP）
_ERROR_PATH_MARK = "maimai-mobile/error/"
# 维护文案（dxrating URLS.CHECKLIST.MAINTENANCE）
_MAINTENANCE_MARKS = ("定期メンテナンス中です",)

# 记录页块标识（实测 2026-09-26）：未游玩的难度页仍返回全曲块，
# 只是块内无成绩字段；页面缺失该标识即改版或登录态丢失
_RECORD_BLOCK_MARK = "w_450 m_15 p_r f_0"

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

# デフォルト头像文件名（img/Icon/ 哈希）：两实测账号（2026-09-27 联调账号、
# 2026-09-28 作者账号 shinya，均从未更换头像）同哈希，据此判定为全服初始头像；
# 版本更新若整体换哈希即失配——失配只是回到「贴官方默认图」的现状，无副作用
_DEFAULT_ICON_FILE = "34f0363f4ce86d07.png"


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


@dataclass
class NetPlayer:
    """NET 首页身份区（home/ 页 .basic_block 玩家名片）。

    供 B50 卡头部对齐落雪名片显示（头像/称号/段位认定/でらっクラス/名字）；
    course/class 徽章页面上是哈希文件名图片，无数字可解析——采集官方图 URL
    由渲染层直接贴图。姓名框经收藏品页顺带采集（可获取性差，net_score 层
    kv_cache 兜底）；边框（frame）收藏品页有但落雪卡版式不渲染，不采集。
    """

    name: str  # 游戏内玩家名（全角字符原样）
    rating: int  # 官方 rating（NET 首页展示值）
    icon_url: str | None = (
        None  # 头像图 URL（img/Icon/ 哈希）；デフォルト头像为 None（渲染层落 QQ 头像）
    )
    trophy_name: str | None = None  # 称号（名牌条）文本
    trophy_color: str | None = None  # 称号稀有度（trophy_{Color} class 尾段）
    course_url: str | None = None  # 段位认定徽章图 URL（img/course/）
    class_url: str | None = None  # でらっクラス徽章图 URL（img/class/）
    nameplate_url: str | None = (
        None  # 装备中自定义姓名框图 URL（收藏品页，可获取性差）；默认框/未知为 None
    )
    # 收藏品页确认装备的是「デフォルト」框（与抓取失败区分：前者清兜底缓存，后者回填）
    nameplate_is_default: bool = False
    # star（icon_star ×N）NET 有展示但落雪卡版式无槽位，不采集；
    # 边框（frame）NET 收藏品页有，但落雪卡版式不渲染，不采集


class EquippedNameplate(NamedTuple):
    """收藏品页「設定中のネームプレート」块解析结果（三态）。"""

    url: str | None  # 装备中自定义姓名框图 URL；默认框/未知态为 None
    is_default: bool  # True = 页面确认装备的是「デフォルト」框（≠抓取失败的未知态）


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
        self._http = create_smart_client(
            timeout=httpx.Timeout(connect=10, read=30, write=10, pool=10),
            headers=COMMON_HEADERS,
            follow_redirects=False,
            verify=maimaidx_ssl_context(),
        )
        self.player: NetPlayer | None = None
        """登录时从首页身份区解析的玩家信息（登录失败/页面无身份块为 None）。"""

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
        maintenance_check: bool = False,
    ) -> httpx.Response:
        """单请求：手动跟随语义 + 错误重定向判定（dxrating fetch 同构）。

        302 且 Location 命中错误页 → 抛 ``error_code``（登录 POST 处传
        ``invalid_credentials``）；其余跳转不跟随（NET 内部各页均直接 200，
        与 dxrating redirect: manual 行为一致）。

        维护页检测仅 ``maintenance_check=True`` 时做（导航/登录链小页）：
        记录页 ~1MB×5 全文扫描代价不值——维护时导航页必然先命中。
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
        if maintenance_check and any(m in resp.text for m in _MAINTENANCE_MARKS):
            raise NetError("maintenance")
        return resp

    async def login(self, creds: NetCredentials) -> None:
        """SEGA ID 登录并选定第一张 Aime 卡（dxrating MaimaiNETJpClient.login）。"""
        login_page = await self._request("GET", f"{BASE}/", maintenance_check=True)
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
            maintenance_check=True,
        )
        # 选 Aime 卡（idx=0 = 第一张）→ 进 home 领全会话 cookie；顺带解析身份区
        await self._request("GET", f"{BASE}/aimeList/", maintenance_check=True)
        await self._request(
            "GET",
            f"{BASE}/aimeList/submit/",
            params={"idx": "0"},
            maintenance_check=True,
        )
        home = await self._request("GET", f"{BASE}/home/", maintenance_check=True)
        self.player = _parse_player(home.text)
        if self.player is not None:
            equipped = await self._fetch_equipped_nameplate()
            if equipped is not None:
                self.player.nameplate_url = equipped.url
                self.player.nameplate_is_default = equipped.is_default

    async def _fetch_equipped_nameplate(self) -> EquippedNameplate | None:
        """收藏品姓名框页 → 装备中项解析（弹回/改版返回 None＝未知态）。

        NET 收藏品区**可获取性差**（间歇性 302 回登录页，实测成功率低且与
        账号/路径无关），失败不重试不报错——上层 net_score 有 kv_cache 持久
        缓存兜底，抓到一次即长期可用；确认装备「デフォルト」框时返回
        is_default 态，供上层清掉兜底缓存（旧自定义名牌不该在默认框时代复活）。
        """
        try:
            resp = await self._request("GET", f"{BASE}/collection/nameplate")
        except (NetError, ExtNetworkError):
            return None
        if resp.status_code != 200:
            return None
        return _parse_equipped_nameplate(resp.text)

    async def fetch_music_records(self) -> list[NetRecord]:
        """逐难度抓全曲记录页并解析（B50 参与：standard/dx 全难度）。

        实测（2026-09-26，Q37）：未游玩该难度的账号页面仍返回全曲块，
        只是块内无成绩字段 → 单页解析 0 条是**合法状态**，不报错；
        页面连记录块都缺失（改版/登录态丢失回登录页）才判 parse_error。
        """
        records: list[NetRecord] = []
        for diff in B50_DIFF_PARAMS:
            resp = await self._request(
                "GET",
                f"{BASE}/record/musicGenre/search/",
                params={"genre": "99", "diff": str(diff)},
            )
            page_records = _parse_music_records(resp.text)
            if not page_records and _RECORD_BLOCK_MARK not in resp.text:
                raise NetError("parse_error")
            records.extend(page_records)
        return records


def _extract_login_token(html: str) -> str | None:
    """登录页 hidden token：``<input name="token" value="...">``。"""
    soup = BeautifulSoup(html, "html.parser")
    node = soup.find("input", attrs={"name": "token"})
    value = node.get("value") if isinstance(node, Tag) else None
    return str(value) if value else None


def _parse_equipped_nameplate(html: str) -> EquippedNameplate:
    """收藏品姓名框页 → 装备中项解析（无装备块/改版 → 未知态）。

    页面结构（2026-09-28 真实页面实测；betterDXnet/maifetcher 同口径）：页首
    「設定中のネームプレート」块即装备块（``.see_through_block`` 附加
    ``collection_setting_block`` class），块内依次为 分组名（``.block_info``）、
    项名（``.p_5.f_14.break``）、装饰分隔线（``img.w_396``，line_01.png，实测
    踩坑）、备注、名牌预览图（``img.w_396.m_r_10``）。注意分组名在デフォルト
    分组下恒为「デフォルト」，判定默认框只能看**项名**。

    装备的是游戏初始「デフォルト」框（项名「デフォルト」+ 备注「はじめから
    所持」）→ is_default 态：卡面落水鱼缺省牌 550101，不贴官方素色默认框。
    """
    soup = BeautifulSoup(html, "html.parser")
    block = soup.select_one(".collection_setting_block")
    if block is None:
        return EquippedNameplate(None, False)
    name_el = block.select_one(".p_5.f_14.break")
    if name_el is not None and name_el.get_text(strip=True) == "デフォルト":
        return EquippedNameplate(None, True)
    img = block.select_one("img.w_396.m_r_10")
    src = _attr_text(img, "src") if img is not None else ""
    return EquippedNameplate(urljoin(f"{BASE}/", src) if src else None, False)


def _parse_player(html: str) -> NetPlayer | None:
    """首页身份块 → :class:`NetPlayer`（无身份块/关键字段缺失返回 None）。

    DOM 结构（2026-09-27 服务器实测，dxrating 未解析此页）：home/ 页
    ``.basic_block`` 内依次为 头像（``img.w_112``，img/Icon/ 哈希文件名）、
    称号条（``.trophy_block``，稀有度是 ``trophy_{Color}`` 附加 class）、
    玩家名（``.name_block``，全角字符）、官方 rating（``.rating_block``）。
    aimeList 页也有同构身份块，本函数只用于 home/ 页。
    """
    soup = BeautifulSoup(html, "html.parser")
    block = soup.select_one(".basic_block")
    if block is None:
        return None
    name_el = block.select_one(".name_block")
    rating_el = block.select_one(".rating_block")
    name = name_el.get_text(strip=True) if name_el else ""
    rating_text = rating_el.get_text(strip=True) if rating_el else ""
    if not name or not rating_text.isdigit():
        return None
    icon = block.select_one("img.w_112")
    icon_src = _attr_text(icon, "src") if icon is not None else ""
    trophy_el = block.select_one(".trophy_block")
    trophy_name = trophy_color = None
    if trophy_el is not None:
        trophy_name = trophy_el.get_text(strip=True) or None
        # 稀有度 = trophy_{Color} 附加 class；trophy_block 是块标识本身，排除
        trophy_color = next(
            (
                c.removeprefix("trophy_")
                for c in trophy_el.get("class") or []
                if c.startswith("trophy_") and c != "trophy_block"
            ),
            None,
        )
    # 段位认定/でらっクラス徽章：按 src 路径定位（哈希文件名，数字不可解析）
    course = block.select_one('img[src*="/img/course/"]')
    class_ = block.select_one('img[src*="/img/class/"]')
    # デフォルト头像 → icon_url 置 None：渲染层跳过官方素色头像直接落 QQ 头像
    icon_url = urljoin(f"{BASE}/", icon_src) if icon_src else None
    if (
        icon_url is not None
        and icon_src.rsplit("/", 1)[-1].split("?")[0] == _DEFAULT_ICON_FILE
    ):
        icon_url = None
    return NetPlayer(
        name=name,
        rating=int(rating_text),
        icon_url=icon_url,
        trophy_name=trophy_name,
        trophy_color=trophy_color,
        course_url=urljoin(f"{BASE}/", _attr_text(course, "src"))
        if course is not None
        else None,
        class_url=urljoin(f"{BASE}/", _attr_text(class_, "src"))
        if class_ is not None
        else None,
    )


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


def _attr_text(tag: Tag, name: str) -> str:
    """bs4 属性值展平为 str：stub 类型是 ``str | AttributeValueList | None``，
    而 ``re.search`` 只收 str；src 等单值属性实际恒为 str，list 分支仅为类型完备。"""
    value = tag.get(name)
    if isinstance(value, list):
        return " ".join(value)
    return value or ""


def _parse_type(block: Tag) -> str | None:
    """谱面类型：图标 src 优先，toggle 按钮 ``_btn_on`` 状态覆盖。"""
    type_ = None
    icon = block.select_one(".music_kind_icon")
    if icon is not None:
        m = re.search(r"music_(standard|dx)\.png", _attr_text(icon, "src"))
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
        re.search(r"diff_(\w+)\.png", _attr_text(icon, "src"))
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
