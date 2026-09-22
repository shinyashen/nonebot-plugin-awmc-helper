"""HTTP 智能代理层：国外站代理优先、国内站直连优先，连接失败互为回退。

- 分类按 host 后缀：内置 GitHub 系（素材在线获取/日服数据源），可用
  ``AWMC_FOREIGN_HOSTS`` 追加；未命中的站一律按国内站处理；
- 回退只针对连接阶段错误（ConnectError/ConnectTimeout/ProxyError），
  读超时不回退——请求可能已到达对端，换通道重放有重复副作用风险；
- :func:`build_smart_transport` 在未配置 ``AWMC_PROXY`` 时返回 None，
  各客户端行为与不启用时完全一致（测试/CI 零影响）。
"""

import httpx
from nonebot import logger

from ..config import plugin_config

_BUILTIN_FOREIGN_HOSTS: tuple[str, ...] = (
    "github.com",
    "github.io",
    "githubusercontent.com",
    "githubassets.com",
)

_FALLBACK_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.ProxyError)


class SmartProxyTransport(httpx.AsyncBaseTransport):
    """按 host 双通道路由的 transport（两通道连接池独立，互不影响）。"""

    def __init__(
        self,
        proxy_url: str,
        foreign_hosts: tuple[str, ...] = (),
        direct: httpx.AsyncBaseTransport | None = None,
        proxied: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._direct = direct or httpx.AsyncHTTPTransport()
        self._proxied = proxied or httpx.AsyncHTTPTransport(
            proxy=httpx.Proxy(proxy_url)
        )
        self._foreign_hosts = foreign_hosts

    def _route(
        self, host: str
    ) -> tuple[httpx.AsyncBaseTransport, httpx.AsyncBaseTransport]:
        """返回（主通道，回退通道）：国外站代理优先，其余直连优先。"""
        if host.endswith(self._foreign_hosts):
            return self._proxied, self._direct
        return self._direct, self._proxied

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        primary, fallback = self._route(request.url.host or "")
        try:
            return await primary.handle_async_request(request)
        except _FALLBACK_ERRORS as primary_error:
            logger.debug(
                f"smart-proxy：{request.url.host} 主通道连接失败，"
                f"走备用通道（{primary_error!r}）"
            )
            try:
                return await fallback.handle_async_request(request)
            except _FALLBACK_ERRORS as fallback_error:
                logger.error(f"smart-proxy：{request.url.host} 双通道均连接失败")
                raise primary_error from fallback_error


def build_smart_transport() -> httpx.AsyncBaseTransport | None:
    """按当前配置构造智能 transport；未配置代理时返回 None（保持默认行为）。"""
    proxy_url = plugin_config.awmc_proxy
    if not proxy_url:
        return None
    foreign = _BUILTIN_FOREIGN_HOSTS + tuple(plugin_config.awmc_foreign_hosts)
    logger.info(
        f"HTTP 智能代理已启用：{proxy_url}（国外站代理优先，命中后缀 {foreign}）"
    )
    return SmartProxyTransport(proxy_url, foreign_hosts=foreign)
