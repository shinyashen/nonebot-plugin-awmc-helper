"""插件配置项（.env 按 pydantic 字段名大写书写）。

开关分两层：本模块的字段默认值即「部署级默认」；
群级运行时开关存 DB（core/store.py 的 group_switch 表，只记显式覆盖）。
"""

from typing import Literal
from pathlib import Path

from nonebot import get_driver, get_plugin_config
from pydantic import BaseModel


class Config(BaseModel):
    # 素材包 static 目录绝对路径（必填，缺配置启动即报错；素材不入库，用户自备）
    awmc_static_path: Path
    # 停用的子插件目录名列表（官方嵌套插件加载的停用扩展）
    awmc_disabled_plugins: set[str] = set()
    # 默认查分器
    awmc_default_provider: Literal["divingfish", "lxns"] = "divingfish"
    # 水鱼开发者 token（查公开数据/拟合曲线）
    awmc_divingfish_developer_token: str | None = None
    # 落雪开发者 token
    awmc_lxns_developer_token: str | None = None
    # 落雪 OAuth 应用（绑定落雪 lxbind 必需）
    awmc_lxns_client_id: str | None = None
    awmc_lxns_client_secret: str | None = None
    awmc_lxns_redirect_uri: str | None = None
    # 柚子 API 走 .cn 中转域
    awmc_yuzu_proxy: bool = False
    # 别名推送默认态（兼 SSE 常驻连接的启动开关）
    awmc_alias_push: bool = True
    # icon/plate 素材在线获取
    awmc_assets_online: bool = True
    # 素材常驻内存（小内存部署可关）
    awmc_save_in_memory: bool = True
    # 曲库缓存 TTL（小时，透传 MaimaiClient）
    awmc_cache_ttl_hours: int = 24
    # 猜歌功能默认态（群可用指令覆盖）
    awmc_guess_enabled: bool = True
    # 猜歌提示间隔（秒）
    awmc_guess_interval: int = 8
    # 猜歌揭晓时长（秒）
    awmc_guess_duration: int = 30
    # 机厅人数单次变更上限
    awmc_arcade_max_delta: int = 30
    # 启动时是否执行后台任务（曲库预热、别名 SSE）；测试/CI 置 false
    awmc_startup_tasks: bool = True


plugin_config: Config = get_plugin_config(Config)
global_config = get_driver().config

# 全局名称
NICKNAME: str = next(iter(global_config.nickname), "")
