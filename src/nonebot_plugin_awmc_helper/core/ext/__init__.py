"""ext 直连层：maimai-py 未覆盖的外部 API。

- :mod:`.yuzu`：柚子别名申请/投票/进行中投票 + SSE 常驻推送；
- :mod:`.divingfish`：水鱼 RA 排行；
- :mod:`.lxns`：落雪 OAuth 换 token / AP50；
- :mod:`.wahlap`：华立机厅 location。

每个模块独立客户端、独立 respx 测试；统一超时与异常语义。
"""

from typing import Any

import httpx

from ..http import build_smart_transport


class ExtError(Exception):
    """外部接口业务错误（message 面向用户可读）。"""


class ExtNetworkError(ExtError):
    """外部接口网络错误。"""


_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    """ext 层共享的 httpx 客户端（懒创建）。"""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10, read=30, write=10, pool=10),
            follow_redirects=True,
            transport=build_smart_transport(),
        )
    return _client


async def fetch_json(url: str, *, name: str, timeout: float = 60) -> Any:
    """GET JSON 公共封装（错误三态样板见 wahlap）：网络异常包装 ExtNetworkError，
    非 200 或 JSON 解析失败抛 ExtError。

    ``name`` 用于错误文案（如 ``"maimaiinfo all_data.json"``）。
    """
    try:
        resp = await get_client().get(url, timeout=timeout)
    except httpx.RequestError as e:
        raise ExtNetworkError(f"{name} 网络异常") from e
    if resp.status_code != 200:
        raise ExtError(f"{name} 拉取失败（HTTP {resp.status_code}）")
    try:
        return resp.json()
    except ValueError as e:
        raise ExtError(f"{name} 返回了无效数据") from e
