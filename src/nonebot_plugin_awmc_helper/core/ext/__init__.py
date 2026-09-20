"""ext 直连层：maimai-py 未覆盖的外部 API。

- :mod:`.yuzu`：柚子别名申请/投票/进行中投票 + SSE 常驻推送；
- :mod:`.divingfish`：水鱼 RA 排行；
- :mod:`.lxns`：落雪 OAuth 换 token / AP50；
- :mod:`.wahlap`：华立机厅 location。

每个模块独立客户端、独立 respx 测试；统一超时与异常语义。
"""

import httpx


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
        )
    return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
