"""ext 直连层：maimai-py 未覆盖的外部 API。

- :mod:`.yuzu`：柚子别名申请/投票/进行中投票 + SSE 常驻推送；
- :mod:`.divingfish`：水鱼 RA 排行；
- :mod:`.lxns`：落雪 OAuth 换 token / 曲库列表 / AP50；
- :mod:`.wahlap`：华立机厅 location；
- :mod:`.maimaiinfo`：日服曲库骨架/定数历史（GitHub raw）；
- :mod:`.otoge_db`：otoge-db 日服谱面/版本/宴谱数据（GitHub raw）；
- :mod:`.otoge_pr`：otoge-db 自动化 PR 分支预读（day-0 新曲标题/封面，分层修复）；
- :mod:`.munet`：MuNET 门户公开 API（current_jp 版本批次补充 + 别名全量走查）；
- :mod:`.gamerch`：gamerch wiki 运行时补充源（宴谱物量等）；
- :mod:`.net`：日服 maimai でらっくす NET 官方直连。

每个模块独立客户端、独立 respx 测试；网络异常与重试口径经
:func:`ext_request` 单源，状态码检查与 JSON 解析按各源协议自持。
"""

import json
import base64
from typing import Any
from collections.abc import Callable, Awaitable

import httpx

from ..http import create_smart_client


class ExtError(Exception):
    """外部接口业务错误（message 面向用户可读）。"""


class ExtNetworkError(ExtError):
    """外部接口网络错误。"""


def jwt_payload_unverified(token: str) -> dict | None:
    """JWT payload「只解不验」解码（生态内同构解码的单一来源）。

    水鱼 ``token_subject`` / 落雪 ``token_expiry`` 等消费方拿到的令牌都是刚从
    对应服务取回、仅做自洽性比对，验签由签发方资源服务器负责。非 JWT /
    payload 非对象返回 None，调用方回退既有错误链路（不硬依赖令牌结构）。
    """
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError):
        # binascii.Error / JSONDecodeError 均为 ValueError 子类
        return None
    return data if isinstance(data, dict) else None


_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    """ext 层共享的 httpx 客户端（懒创建）。"""
    global _client
    if _client is None:
        _client = create_smart_client(
            timeout=httpx.Timeout(connect=10, read=30, write=10, pool=10)
        )
    return _client


async def ext_request(
    method: str,
    url: str,
    *,
    name: str,
    retries: int = 0,
    network_message: str | None = None,
    **kwargs,
) -> httpx.Response:
    """ext 层请求底座（D1）：网络异常重试 ``retries`` 次后统一包装
    ExtNetworkError。

    状态码检查与 JSON 解析留在各源模块——协议形态不同（柚子 4xx 带
    message 错误体、落雪 OAuth error 体、水鱼 device 流按状态码分支、
    MuNET 双主机自管节流），底座只收口网络异常语义；``retries`` 是首次
    之外的额外尝试次数（幂等 GET 列表端点共 3 次尝试即 retries=2，
    令牌/提交类端点 0 次直抛）。
    """
    last_error: httpx.RequestError | None = None
    for _ in range(1 + max(0, retries)):
        try:
            return await get_client().request(method, url, **kwargs)
        except httpx.RequestError as e:
            last_error = e
    raise ExtNetworkError(network_message or f"{name} 网络异常") from last_error


async def fetch_json(url: str, *, name: str, timeout: float = 60) -> Any:
    """GET JSON 公共封装：幂等列表口径共 3 次尝试（retries=2）后包装
    ExtNetworkError，非 200 抛 ExtError，JSON 解析失败抛 ExtError。

    ``name`` 用于错误文案（如 ``"maimaiinfo all_data.json"``）。
    """
    resp = await ext_request("GET", url, name=name, retries=2, timeout=timeout)
    if resp.status_code != 200:
        raise ExtError(f"{name} 拉取失败（HTTP {resp.status_code}）")
    try:
        return resp.json()
    except ValueError as e:
        raise ExtError(f"{name} 返回了无效数据") from e


def fetch_github_raw(
    base_url: str, *, source: str, timeout: float = 120
) -> "Callable[[str], Awaitable[Any]]":
    """GitHub raw JSON 拉取工厂（maimaiinfo/otoge_db 同构 _fetch_json 收口）。

    返回 ``async def (name) -> Any``：按文件名拼 base_url 拉取。
    """

    async def _fetch(name: str) -> Any:
        return await fetch_json(
            f"{base_url}/{name}", name=f"{source} {name}", timeout=timeout
        )

    return _fetch
