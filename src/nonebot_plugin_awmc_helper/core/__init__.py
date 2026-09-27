"""核心层：唯一允许 import maimai_py 的地方。

导入本包即构造唯一的异步 ``MaimaiClient`` 进程单例（见 ``client.py``）。
子插件一律通过 ``..core`` 公开接口访问数据，禁止自行实例化客户端。
"""

from . import lxns_keepalive  # noqa: F401  # 导入即注册每日落雪令牌保活任务
