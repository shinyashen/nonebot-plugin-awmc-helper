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
    # 智能代理地址（如 http://127.0.0.1:7796）：国外站代理优先、国内站直连优先，
    # 连接失败自动互为回退；空 = 不启用代理层
    awmc_proxy: str | None = None
    # 追加的「国外站」host 后缀（内置 GitHub 系；命中后缀的站走代理优先）
    awmc_foreign_hosts: list[str] = []
    # 别名推送默认态（兼 SSE 常驻连接的启动开关）
    awmc_alias_push: bool = True
    # 合并转发开关（多曲别名等场景）：协议端转发实现损坏时（如 LLOneBot 8.1.x
    # 群聊 MultiMsg 上传不可读）置 false，一律降级为普通消息
    awmc_forward: bool = True
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
    # 国服曲库轮询间隔（分钟，0=禁用）；检测到国服更新时自动重建规范表并刷新运行时
    awmc_cn_poll_minutes: int = 60
    # 检测到国服更新后是否自动重建定数表/完成表底图
    awmc_auto_templates: bool = True
    # 检测到国服更新后是否向 SUPERUSER 私聊推送通知
    awmc_update_notify: bool = True
    # 外部补充源列表（标准 JSON 文件路径或 http(s) URL，仅允许补充日服侧数据，匿名读取；
    # 可加 ::fill / ::override 后缀指定该源合并模式，默认 override）
    awmc_extra_song_sources: list[str] = []


plugin_config: Config = get_plugin_config(Config)
global_config = get_driver().config

# 全局名称
NICKNAME: str = next(iter(global_config.nickname), "")
