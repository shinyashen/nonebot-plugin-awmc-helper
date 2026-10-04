"""落雪直连：OAuth 授权码换个人 token（maimai-py 不含 OAuth 流程）。

端点与字段对齐原版 maimaiDX 的 LXNS OAuth2 实现。
"""

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse, urlencode

from . import (
    ExtError,
    ext_request,
    jwt_payload_unverified,
)
from ...config import plugin_config

LXNS_BASE = "https://maimai.lxns.net"
# 落雪 access token 有效期秒（oauth-guide ``expires_in`` 口径；exp 缺失时按
# iat + 该值回退推算）
LXNS_ACCESS_TOKEN_TTL = 900
# 授权码形态：裸码（XXXX-XXXX-XXXX / 长 base64 串），提取时三处复用
_CODE_PATTERN = re.compile(
    r"^(?:[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}|[A-Za-z0-9_-]{16,256})$"
)
# write_player（2026-09-26 应作者要求追加）：绑定 token 兼作第三方传分插件的
# 写凭据（落雪成绩上传），读权限之外多申请一项；存量绑定需重新 lxbind 授权
# 才会升级到新 scope。
SCOPE = "read_player read_user_profile write_player"


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
    resp = await ext_request(
        "GET",
        f"{LXNS_BASE}/api/v0/maimai/song/list",
        name="落雪曲库列表",
        network_message="落雪曲库列表网络异常",
        params={"notes": "true" if notes else "false"},
        headers=headers,
        timeout=60,
    )
    if resp.status_code != 200:
        raise ExtError(f"落雪曲库列表拉取失败（HTTP {resp.status_code}）")
    try:
        data = resp.json()
    except ValueError as e:
        raise ExtError("落雪曲库列表返回了无效数据") from e
    if not data.get("success", True):
        raise ExtError(str(data.get("message", "落雪曲库列表拉取失败")))
    return data.get("data", data)


@dataclass
class LxnsToken:
    """落雪 OAuth token 端点响应（``data`` 段；friend_code 可能缺省）。"""

    access_token: str
    refresh_token: str | None = None
    friend_code: int | None = None

    @classmethod
    def from_payload(cls, data: dict) -> "LxnsToken":
        return cls(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            friend_code=data.get("friend_code"),
        )


class LxnsGrantError(ExtError):
    """OAuth token 端点的错误响应体。

    落雪失败响应为 OAuth 风格 ``{"error", "error_description"}``（无 message
    字段）；``invalid_grant`` = 授权码/refresh_token 无效或已过期（区别于
    网络等暂时性失败）。见 https://maimai.lxns.net/docs/oauth-guide。
    """

    def __init__(self, error: str, description: str) -> None:
        self.error = error
        self.description = description
        friendly = {"invalid_grant": "落雪授权已失效或过期"}.get(error)
        super().__init__(friendly or f"落雪授权失败（{error}）")


def oauth_configured() -> bool:
    return all(
        (
            plugin_config.awmc_lxns_client_id,
            plugin_config.awmc_lxns_client_secret,
            plugin_config.awmc_lxns_redirect_uri,
        )
    )


def build_authorize_url() -> str:
    """构造落雪 OAuth 授权页链接（scope：读玩家/成绩 + 上传成绩，见 SCOPE 注）。"""
    query = urlencode(
        {
            "response_type": "code",
            "client_id": plugin_config.awmc_lxns_client_id,
            "redirect_uri": plugin_config.awmc_lxns_redirect_uri,
            "scope": SCOPE,
        }
    )
    return f"{LXNS_BASE}/oauth/authorize?{query}"


async def _token_grant(payload: dict, error_default: str) -> LxnsToken:
    """POST oauth/token 公共封装（两 grant 同端点同响应解析）。"""
    resp = await ext_request(
        "POST",
        f"{LXNS_BASE}/api/v0/oauth/token",
        name="落雪授权接口",
        network_message="落雪授权接口网络异常，请稍后再试",
        json=payload,
    )
    data = (
        resp.json()
        if resp.headers.get("content-type", "").startswith("application/json")
        else {}
    )
    if "error" in data:  # OAuth 风格错误体（无 message 字段）
        raise LxnsGrantError(str(data["error"]), str(data.get("error_description", "")))
    if resp.status_code != 200 or not data.get("success", True):
        raise ExtError(str(data.get("message", error_default)))
    grant = data.get("data", data)
    # 200 但缺 access_token（上游异常响应）：显式失败，不让 KeyError 逃逸到
    # 绑定入口的宽捕处变成裸 'access_token' 提示
    if "access_token" not in grant:
        raise ExtError("落雪授权接口返回了无效数据")
    return LxnsToken.from_payload(grant)


def token_expiry(access_token: str) -> float | None:
    """读出 access token 的过期时刻（JWT ``exp``，缺则 ``iat + TTL``）。

    只解不验：令牌是落雪签发、仅库存自用，本地预检判过期无需验签，验签由
    落雪资源服务器做（同水鱼 ``token_subject`` 先例）。有效期取
    :data:`LXNS_ACCESS_TOKEN_TTL`（oauth-guide ``expires_in=900``）。非 JWT /
    payload 非对象 / 两字段皆缺时返回 None，调用方回退 401 驱动的既有续期链路
    （落雪 OAuth 仍在 beta，不对令牌内部结构做硬依赖）。
    """
    payload = jwt_payload_unverified(access_token)
    if payload is None:
        return None
    exp = payload.get("exp")
    if isinstance(exp, (int, float)):
        return float(exp)
    iat = payload.get("iat")
    if isinstance(iat, (int, float)):
        return float(iat) + LXNS_ACCESS_TOKEN_TTL
    return None


def token_writable(access_token: str) -> bool | None:
    """读出 access token 是否含写 scope（JWT ``scope`` 含 ``write_player``）。

    **score-updater 跨仓消费的外部契约，签名勿动**（第三方传分插件据此判
    「可上传」而不必复刻 scope 知识，单源）；存量旧授权无写 scope 返回 False，
    需重新 lxbind。非 JWT / payload 非对象返回 None，调用方按未知态处理
    （同 :func:`token_expiry` 的回退口径）。
    """
    payload = jwt_payload_unverified(access_token)
    if payload is None:
        return None
    scope = payload.get("scope")
    return isinstance(scope, str) and "write_player" in scope.split()


async def fetch_token(code: str) -> LxnsToken:
    """授权码 → 个人 token（含 friend_code）。"""
    return await _token_grant(
        {
            "client_id": plugin_config.awmc_lxns_client_id,
            "client_secret": plugin_config.awmc_lxns_client_secret,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": plugin_config.awmc_lxns_redirect_uri,
        },
        "落雪授权失败，请确认授权码是否有效",
    )


async def refresh_token(refresh_token: str) -> LxnsToken:
    """refresh_token → 新个人 token（落雪 refresh grant，可能轮换 refresh_token）。

    对齐原版 maimaiDX 的自动续期：access_token 过期（401）时调用，
    成功后由调用方落库，用户无感。
    """
    return await _token_grant(
        {
            "client_id": plugin_config.awmc_lxns_client_id,
            "client_secret": plugin_config.awmc_lxns_client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        "落雪授权已过期，请重新「绑定落雪」",
    )


def extract_authorization_code(text: str) -> str | None:
    """从用户输入提取授权码：裸码 / `授权码：xxx` / 回调链接 query。"""
    value = text.strip()
    if _CODE_PATTERN.fullmatch(value):
        return value
    prefixed = re.fullmatch(r"授权码\s*[:：]?\s*(\S+)", value)
    if prefixed and _CODE_PATTERN.fullmatch(prefixed.group(1)):
        return prefixed.group(1)
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https"}:
        code = parse_qs(parsed.query).get("code", [None])[0]
        if code and _CODE_PATTERN.fullmatch(code):
            return code
    return None
