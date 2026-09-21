"""自定义 maimai_py provider：规范表 → 库对象（song-db-design §5.6）。

参考 maimai.py 自带的 ``LocalProvider``（``_hash`` 由数据内容计算）与
``docs/concepts/caches.md``「覆写 provider 即替换缓存源」：

- ``_hash()`` 返回规范表内容指纹（``songdb.CURRENT_FINGERPRINT``，rebuild 末尾刷新），
  数据变更后哈希自动变化，``client.songs()`` 自行重建缓存，**无需手动删键**——
  这正是 ``core/client.py:refresh_songs_cache`` 缓存键 hack 要退役的原因；
- ``get_songs()`` 按 scope（cn/jp）从规范表物化 ``Song`` 列表（§5.5 转化层）；
- CN 运行时视图暂仍由落雪构造（§5.1「行为与现状零漂移」，切换时机待作者定，
  见 QUESTIONS Q23），本 provider 现阶段服务 JP 视图与数据入口统一（§5.4）。
"""

from typing import Literal

from maimai_py.models import Song
from maimai_py.providers.base import ISongProvider

from . import songdb

Scope = Literal["cn", "jp"]


class AwmcSongProvider(ISongProvider):
    """以规范表为数据源的曲库 provider（scope=cn/jp 双视图共用一个实现）。"""

    def __init__(self, scope: Scope = "cn") -> None:
        self.scope: Scope = scope

    async def get_songs(self, client) -> list[Song]:  # noqa: ANN001（与库接口签名一致）
        state = await songdb.State.load()
        return songdb.all_songs(state, self.scope)

    def _hash(self) -> str:
        return songdb.CURRENT_FINGERPRINT or "empty"
