"""唯一的异步 ``MaimaiClient`` 进程单例与 provider 装配。

- MaimaiClient 本身是全异步客户端（所有方法 ``await``），且 ``__new__`` 层面即进程单例，
  二次实例化只会告警并返回同一实例——本模块在导入期构造一次，此后任何代码不得再写
  ``MaimaiClient()``；NoneBot 单事件循环下无需 ``MaimaiClientMultithreading``。
- provider 实例不是单例，但同样按配置构造一次并复用（曲库缓存按 provider 组合哈希命中，
  复用同一实例才能保证「传相同 provider 不重拉」的语义成立）。
- 曲库刷新（2026-09-22 起）：运行时 CN 视图由 ``AwmcSongProvider``（规范表构造，
  ``_hash`` = 规范表指纹）提供——规范表重建后指纹变化，``client.songs()`` 自行重建
  缓存，**无需任何手动失效**；早期的「删 provider 哈希键」hack 已随落雪直连曲库源
  一并退役。
"""

from maimai_py import LXNSProvider, MaimaiClient, YuzuProvider, DivingFishProvider

from .http import build_smart_transport
from ..config import plugin_config
from .ext.yuzu import YUZU_DOMAIN_CN


class ProxyYuzuProvider(YuzuProvider):
    """走 .cn 中转域的柚子别名源（YuzuProvider 的 base_url 是类属性，子类化改写）。"""

    base_url = f"{YUZU_DOMAIN_CN}/api/"


def _build_yuzu() -> YuzuProvider:
    return ProxyYuzuProvider() if plugin_config.awmc_yuzu_proxy else YuzuProvider()


client = MaimaiClient(
    # 默认 20s：传分全量/大增量 POST update_records 实测会 ReadTimeout（2026-09-26）
    timeout=120.0,
    cache_ttl=plugin_config.awmc_cache_ttl_hours * 3600,
    transport=build_smart_transport(),
)
"""唯一的 MaimaiClient 实例（进程单例），全插件共享；transport 挂智能代理层。"""

divingfish_provider = DivingFishProvider(
    client_id=plugin_config.awmc_divingfish_oauth_client_id,
    client_secret=plugin_config.awmc_divingfish_oauth_client_secret,
)
"""水鱼 provider：OAuth 凭据启用 Bearer 路径（全量/单曲/写）；未配置时水鱼仅
公开查询/Import-Token 路径可用（developer token 已随 2026-10-01 端点日落整体移除，
1.6.0 构造函数不再接受该参数）。"""

divingfish_public_provider = DivingFishProvider()
"""水鱼**无凭据** provider：专供公开端点（b50 代查、公开键回退）。

1.6.0 起配了 client 凭据的 provider 会把「裸 username」拼成 ``username:`` subject
换票走 Bearer（未授权陌生人必败）；公开查询（/query/player 系）不收 subject，
必须用本实例保住匿名语义。不要给它配置任何凭据。"""

lxns_provider = LXNSProvider(developer_token=plugin_config.awmc_lxns_developer_token)
"""落雪 provider：开发者 token 用于 friend_code/QQ 查询。"""

yuzu_provider = _build_yuzu()
"""柚子别名 provider（默认别名源）。"""
