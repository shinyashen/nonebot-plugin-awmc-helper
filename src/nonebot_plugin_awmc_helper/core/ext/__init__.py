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

每个模块独立客户端、独立 respx 测试；统一超时与异常语义。
"""

from typing import Any
from collections.abc import Callable, Awaitable

import httpx
from tenacity import retry, stop_after_attempt, retry_if_exception_type

from ..http import create_smart_client


class ExtError(Exception):
    """外部接口业务错误（message 面向用户可读）。"""


class ExtNetworkError(ExtError):
    """外部接口网络错误。"""


_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    """ext 层共享的 httpx 客户端（懒创建）。"""
    global _client
    if _client is None:
        _client = create_smart_client(
            timeout=httpx.Timeout(connect=10, read=30, write=10, pool=10)
        )
    return _client


@retry(
    stop=stop_after_attempt(3),
    retry=retry_if_exception_type(httpx.RequestError),
    reraise=True,
)
async def _fetch_json_attempt(url: str, timeout: float) -> httpx.Response:
    """单次 GET：连接类失败重试 3 次（对齐 maimai_py provider 的 retry(3)），
    耗尽后原样 reraise 由 fetch_json 统一包装。"""
    return await get_client().get(url, timeout=timeout)


async def fetch_json(url: str, *, name: str, timeout: float = 60) -> Any:
    """GET JSON 公共封装（错误三态样板见 wahlap）：网络异常重试 3 次后包装
    ExtNetworkError，非 200 或 JSON 解析失败抛 ExtError。

    ``name`` 用于错误文案（如 ``"maimaiinfo all_data.json"``）。
    """
    try:
        resp = await _fetch_json_attempt(url, timeout)
    except httpx.RequestError as e:
        raise ExtNetworkError(f"{name} 网络异常") from e
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
