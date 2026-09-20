"""唯一的异步 ``MaimaiClient`` 进程单例与 provider 装配。

- MaimaiClient 本身是全异步客户端（所有方法 ``await``），且 ``__new__`` 层面即进程单例，
  二次实例化只会告警并返回同一实例——本模块在导入期构造一次，此后任何代码不得再写
  ``MaimaiClient()``；NoneBot 单事件循环下无需 ``MaimaiClientMultithreading``。
- provider 实例不是单例，但同样按配置构造一次并复用（曲库缓存按 provider 组合哈希命中，
  复用同一实例才能保证「传相同 provider 不重拉」的语义成立）。
- 刷新策略（maimai-py 1.5.x 源码核实）：曲库缓存以 provider 组合哈希为键、TTL =
  ``cache_ttl``，删除该哈希键后以**同一组 provider** 调 ``songs()`` 即触发重建。
  此实现依赖库内部细节，收敛在本模块一处；上游提供公开缓存失效 API 后立即替换。
"""

from maimai_py import LXNSProvider, MaimaiClient, YuzuProvider, DivingFishProvider

from ..config import plugin_config


class ProxyYuzuProvider(YuzuProvider):
    """走 .cn 中转域的柚子别名源（YuzuProvider 的 base_url 是类属性，子类化改写）。"""

    base_url = "https://www.yuzuchan.cn/api/"


def _build_yuzu() -> YuzuProvider:
    return ProxyYuzuProvider() if plugin_config.awmc_yuzu_proxy else YuzuProvider()


client = MaimaiClient(cache_ttl=plugin_config.awmc_cache_ttl_hours * 3600)
"""唯一的 MaimaiClient 实例（进程单例），全插件共享。"""

divingfish_provider = DivingFishProvider(
    developer_token=plugin_config.awmc_divingfish_developer_token
)
"""水鱼 provider：开发者 token 用于查公开数据与拟合曲线。"""

lxns_provider = LXNSProvider(developer_token=plugin_config.awmc_lxns_developer_token)
"""落雪 provider：开发者 token 用于 friend_code/QQ 查询。"""

yuzu_provider = _build_yuzu()
"""柚子别名 provider（默认别名源）。"""


async def refresh_songs_cache() -> None:
    """删除曲库缓存的 provider 哈希键，使下一次 ``songs()`` 强制重建。

    只删哈希键、不清整个缓存（``_cache.clear()`` 会连歌曲数据一起清掉）。
    """
    await client._cache.delete("provider", namespace="songs")
