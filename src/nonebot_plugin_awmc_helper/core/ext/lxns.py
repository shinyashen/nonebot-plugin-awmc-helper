"""落雪直连：OAuth 授权码换个人 token（maimai-py 不含 OAuth 流程）。

端点与字段对齐原版 maimaiDX 的 LXNS OAuth2 实现。
"""

from . import ExtError, get_client
from ...config import plugin_config

LXNS_BASE = "https://maimai.lxns.net"
SCOPE = "read_player read_user_profile"


async def fetch_song_list(notes: bool = True) -> dict:
    """直连拉取落雪曲库列表（国服规范表的唯一国服谱面源，song-db-design §7.2）。

    - **不得走 MaimaiClient**：``client.songs()`` 会写运行时缓存命名空间，污染 CN 视图；
    - ``notes=false`` 的轻载荷（≈109KB）仅用于每小时轮询的更新检测；
    - 必须带开发者 token（未配置时落雪公开列表也可匿名读，但保持与 provider 一致）。
    """
    headers = (
        {"Authorization": plugin_config.awmc_lxns_developer_token}
        if plugin_config.awmc_lxns_developer_token
        else {}
    )
    resp = await get_client().get(
        f"{LXNS_BASE}/api/v0/maimai/song/list?notes={'true' if notes else 'false'}",
        headers=headers,
        timeout=60,
    )
    if resp.status_code != 200:
        raise ExtError(f"落雪曲库列表拉取失败（HTTP {resp.status_code}）")
    data = resp.json()
    if not data.get("success", True):
        raise ExtError(str(data.get("message", "落雪曲库列表拉取失败")))
    return data.get("data", data)


class LxnsToken:
    access_token: str
    refresh_token: str | None
    friend_code: int | None

    def __init__(self, data: dict) -> None:
        self.access_token = data["access_token"]
        self.refresh_token = data.get("refresh_token")
        self.friend_code = data.get("friend_code")


def oauth_configured() -> bool:
    return all(
        (
            plugin_config.awmc_lxns_client_id,
            plugin_config.awmc_lxns_client_secret,
            plugin_config.awmc_lxns_redirect_uri,
        )
    )


def build_authorize_url() -> str:
    """构造落雪 OAuth 授权页链接（scope：读取玩家信息与成绩）。"""
    from urllib.parse import urlencode

    query = urlencode(
        {
            "response_type": "code",
            "client_id": plugin_config.awmc_lxns_client_id,
            "redirect_uri": plugin_config.awmc_lxns_redirect_uri,
            "scope": SCOPE,
        }
    )
    return f"{LXNS_BASE}/oauth/authorize?{query}"


async def fetch_token(code: str) -> LxnsToken:
    """授权码 → 个人 token（含 friend_code）。"""
    resp = await get_client().post(
        f"{LXNS_BASE}/api/v0/oauth/token",
        json={
            "client_id": plugin_config.awmc_lxns_client_id,
            "client_secret": plugin_config.awmc_lxns_client_secret,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": plugin_config.awmc_lxns_redirect_uri,
        },
    )
    data = (
        resp.json()
        if resp.headers.get("content-type", "").startswith("application/json")
        else {}
    )
    if resp.status_code != 200 or not data.get("success", True):
        raise ExtError(str(data.get("message", "落雪授权失败，请确认授权码是否有效")))
    return LxnsToken(data.get("data", data))


async def refresh_token(refresh_token: str) -> LxnsToken:
    """refresh_token → 新个人 token（落雪 refresh grant，可能轮换 refresh_token）。

    对齐原版 maimaiDX 的自动续期：access_token 过期（401）时调用，
    成功后由调用方落库，用户无感。
    """
    resp = await get_client().post(
        f"{LXNS_BASE}/api/v0/oauth/token",
        json={
            "client_id": plugin_config.awmc_lxns_client_id,
            "client_secret": plugin_config.awmc_lxns_client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
    )
    data = (
        resp.json()
        if resp.headers.get("content-type", "").startswith("application/json")
        else {}
    )
    if resp.status_code != 200 or not data.get("success", True):
        raise ExtError(str(data.get("message", "落雪授权已过期，请重新「绑定落雪」")))
    return LxnsToken(data.get("data", data))


def extract_authorization_code(text: str) -> str | None:
    """从用户输入提取授权码：裸码 / `授权码：xxx` / 回调链接 query。"""
    import re
    from urllib.parse import parse_qs, urlparse

    value = text.strip()
    pattern = re.compile(
        r"^(?:[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}|[A-Za-z0-9_-]{16,256})$"
    )
    if pattern.fullmatch(value):
        return value
    prefixed = re.fullmatch(r"授权码\s*[:：]?\s*(\S+)", value)
    if prefixed and pattern.fullmatch(prefixed.group(1)):
        return prefixed.group(1)
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https"}:
        code = parse_qs(parsed.query).get("code", [None])[0]
        if code and pattern.fullmatch(code):
            return code
    return None
