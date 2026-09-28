"""HTTP 智能代理层：国外站代理优先、国内站直连优先，连接失败互为回退。

- 分类按 host 后缀：内置 GitHub 系（素材在线获取/日服数据源），可用
  ``AWMC_FOREIGN_HOSTS`` 追加；未命中的站一律按国内站处理；
- 回退只针对连接阶段错误（ConnectError/ConnectTimeout/ProxyError），
  读超时不回退——请求可能已到达对端，换通道重放有重复副作用风险；
- :func:`build_smart_transport` 在未配置 ``AWMC_PROXY`` 时返回 None，
  各客户端行为与不启用时完全一致（测试/CI 零影响）。
"""

import ssl
from functools import lru_cache

import httpx
from nonebot import logger

from ..config import plugin_config

_BUILTIN_FOREIGN_HOSTS: tuple[str, ...] = (
    "github.com",
    "github.io",
    "githubusercontent.com",
    "githubassets.com",
    # 日服官方站：封面（maimai-mobile/img/Music）等资源，国内直连基本不可达
    "maimaidx.jp",
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
        verify: ssl.SSLContext | bool = True,
    ) -> None:
        self._direct = direct or httpx.AsyncHTTPTransport(verify=verify)
        self._proxied = proxied or httpx.AsyncHTTPTransport(
            proxy=httpx.Proxy(proxy_url), verify=verify
        )
        self._foreign_hosts = foreign_hosts

    def _route(
        self, host: str
    ) -> tuple[httpx.AsyncBaseTransport, httpx.AsyncBaseTransport]:
        """返回（主通道，回退通道）：国外站代理优先，其余直连优先。"""
        # 精确域名或子域命中（后缀子串匹配会把 notgithub.com 误判国外站）
        if host in self._foreign_hosts or any(
            host.endswith("." + h) for h in self._foreign_hosts
        ):
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


def build_smart_transport(
    verify: ssl.SSLContext | bool = True,
) -> httpx.AsyncBaseTransport | None:
    """按当前配置构造智能 transport；未配置代理时返回 None（保持默认行为）。

    ``verify`` 透传给底层通道：特定站点需自带补充 CA（如省略中间证书的站）。
    底层 transport 按 ``verify`` 键进程级缓存（连接池跨客户端复用，INFO 仅
    首次），但每次调用返回独立薄壳——httpx 会在 client.aclose() 时关闭其持有
    的 transport，短命客户端（每次图片下载、NET 查询、SSE 重连）关闭的是壳，
    共享连接池不受单个客户端生命周期影响。
    """
    proxy_url = plugin_config.awmc_proxy
    if not proxy_url:
        return None
    inner = _transport_cache.get(verify)
    if inner is None:
        foreign = _BUILTIN_FOREIGN_HOSTS + tuple(plugin_config.awmc_foreign_hosts)
        inner = SmartProxyTransport(proxy_url, foreign_hosts=foreign, verify=verify)
        _transport_cache[verify] = inner
        logger.info(
            f"HTTP 智能代理已启用：{proxy_url}（国外站代理优先，命中后缀 {foreign}）"
        )
    return _SharedTransport(inner)


class _SharedTransport(httpx.AsyncBaseTransport):
    """共享 transport 的每客户端薄壳：请求透传，关闭不穿透（基类 aclose 为 no-op）。"""

    def __init__(self, inner: SmartProxyTransport) -> None:
        self._inner = inner

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self._inner.handle_async_request(request)


_transport_cache: dict[ssl.SSLContext | bool, SmartProxyTransport] = {}
"""按 verify 键进程级持有的共享 transport（模块级引用，防 GC/被关）。"""


def create_smart_client(
    *,
    timeout: httpx.Timeout | float,
    headers: dict[str, str] | None = None,
    follow_redirects: bool = True,
    verify: ssl.SSLContext | bool = True,
) -> httpx.AsyncClient:
    """智能代理 httpx 客户端工厂（awmc 生态三仓懒创建统一入口）。

    配置了 ``AWMC_PROXY`` 时挂共享智能 transport（连接池跨客户端复用，见
    :func:`build_smart_transport`）；未配置时不挂 transport、把 ``verify``
    交给 client 本身——两种模式下自定义 CA 语义一致（直建写法在无代理时
    会静默丢掉 verify，本工厂顺带修掉这一坑）。
    """
    transport = build_smart_transport(verify=verify)
    kwargs: dict = {
        "timeout": timeout,
        "headers": headers,
        "follow_redirects": follow_redirects,
    }
    if transport is not None:
        kwargs["transport"] = transport
    else:
        kwargs["verify"] = verify
    return httpx.AsyncClient(**kwargs)


# maimaidx.jp 官方站 TLS 只下发叶子证书（缺 GlobalSign 中间件），httpx 严格校验
# 会失败：下述中间证书并入信任上下文补齐链（CA 名含年份，官方轮换后需更新）。
# GlobalSign GCC R46 OV TLS CA 2025 中间证书（AIA: secure.globalsign.com/cacert/
# gsgccr46ovtlsca2025.crt；有效期至 2029-06，链向系统内置的 GlobalSign Root R46）
MAIMAIDX_INTERMEDIATE_PEM = """\
-----BEGIN CERTIFICATE-----
MIIFfDCCA2SgAwIBAgIRAIRDWJCDb2c5QYLLnJpdyZ8wDQYJKoZIhvcNAQELBQAw
RjELMAkGA1UEBhMCQkUxGTAXBgNVBAoTEEdsb2JhbFNpZ24gbnYtc2ExHDAaBgNV
BAMTE0dsb2JhbFNpZ24gUm9vdCBSNDYwHhcNMjUwOTE3MDI1NTU2WhcNMjkwNjIz
MDAwMDAwWjBUMQswCQYDVQQGEwJCRTEZMBcGA1UEChMQR2xvYmFsU2lnbiBudi1z
YTEqMCgGA1UEAxMhR2xvYmFsU2lnbiBHQ0MgUjQ2IE9WIFRMUyBDQSAyMDI1MIIB
IjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA1JyrGiv+210Lw4LTp9qxx9WC
o6w8HnxcTKr5XwR6WwtKidGXriLqGXtBINGTi4HUZ1Vl3FUIvscLwNcq2DRLwjWs
cYFNClVnuSw4CtwAcfa7Iltz+0FmFeh/KOWv5BfgCxAo9FaeXRG725b2eedo/7fb
0zBc6M/XcfQREVteZ6GovnLE96+T8RzRImvX38Y8vZoulp/XWv3p09C1pgp/53+1
itDl7xbrM4sglGNkeJ5LBN2dOR1sqWCMZ/V4a4cPQwopBtZis1vVh7/k4S6Ysgk0
CTi5vei0RSEIhxoFk48BHSXzTA4FJxqjfauYCZ4M5tmZ/R5VgXOZ4Ck/PifnXQID
AQABo4IBVTCCAVEwDgYDVR0PAQH/BAQDAgGGMBMGA1UdJQQMMAoGCCsGAQUFBwMB
MBIGA1UdEwEB/wQIMAYBAf8CAQAwHQYDVR0OBBYEFGl0Pq/DWwGVSe4UQVqT+rEw
mNqiMB8GA1UdIwQYMBaAFANcq3OBh6jMsKbVlOI2lkn/BZksMHsGCCsGAQUFBwEB
BG8wbTAuBggrBgEFBQcwAYYiaHR0cDovL29jc3AuZ2xvYmFsc2lnbi5jb20vcm9v
dHI0NjA7BggrBgEFBQcwAoYvaHR0cDovL3NlY3VyZS5nbG9iYWxzaWduLmNvbS9j
YWNlcnQvcm9vdHI0Ni5jcnQwNgYDVR0fBC8wLTAroCmgJ4YlaHR0cDovL2NybC5n
bG9iYWxzaWduLmNvbS9yb290cjQ2LmNybDAhBgNVHSAEGjAYMAgGBmeBDAECAjAM
BgorBgEEAaAyCgECMA0GCSqGSIb3DQEBCwUAA4ICAQBEUTiKxe5jEintARUvLBm9
qWZtGiOSV9E+3bntbFFBDBAroqwB6Cj53Zp/W08HwgxaPXdkVaRNYHB/eAatEtSm
1ldtoorfPc+mVlzbwCwfbpIs2uqW5rF78ne37qy2o+iVnJptq9AzPnlC03+zhhB9
JwmjUXVtPuqQZ96tFl0fAT77xGSLzCO8yfEDrxCqdWz2wneShSbCCsC15JB07OgO
StE+MsVBkwe5+PNzAlAr8NZ6f8mzeY/FzaBzlhYw5+c1yyzXJqp+gjRXWrLpD3Ho
hGOvIXIvCBnyVrYI/HPe6DR5w7oteui9Rt0xfUUudaTkt0iz7fc23eGboZ+bpvgT
gbd/kYK6JOrxawMyfBYxrR5zDHIJX0Mws99DNgACKBUfFadKAfwFw0+0airY5WAI
Xs8yhCb5XGwyzVpcB30BrQbWtqdI0PoE9usNvNbH3YFGfuS8oRmAJEgUUQnwOoGK
jMWtHacw0n8QESdRM274LJvLd9nwawYU4svJpf06FtKPqGH3nXefL741NO9KzDAG
PM11YScyJVfYdBDXFM86HU1fBGTKlkLcG/qMJxOqppY4wydRI3koSH6A78nO2QaJ
yqjTOQyCNHaSlmGjdiOvhJ8y1PiazHnuvWBx6z+7JJF2ukqqfjlSARwyfkfnRUIY
la7ZYEqcc56eoPAiElhvrg==
-----END CERTIFICATE-----
"""


@lru_cache(maxsize=1)
def maimaidx_ssl_context() -> ssl.SSLContext:
    """系统 CA + GlobalSign 中间证书的 TLS 上下文（进程级缓存一次）。"""
    ctx = ssl.create_default_context()
    ctx.load_verify_locations(cadata=MAIMAIDX_INTERMEDIATE_PEM)
    return ctx
