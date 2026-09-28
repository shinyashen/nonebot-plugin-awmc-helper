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
    # 水鱼开发者 token（2026-10-01 起水鱼 developer 端点全部 410，
    # 此配置不再有实际作用，仅保留读兼容）
    awmc_divingfish_developer_token: str | None = None
    # 水鱼 OAuth 应用（developer-token 日落迁移，见
    # local/reference/divingfish-oauth-sunset.md；机密客户端 + 设备码绑定，
    # 未配置时水鱼仅公开查询/Import-Token 路径可用）
    awmc_divingfish_oauth_client_id: str | None = None
    awmc_divingfish_oauth_client_secret: str | None = None
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
    # 排卡功能部署级默认态（群可用 开启/关闭排卡 覆盖；默认关对齐原版按群显式开通，
    # 规避宽正则（XX店+2人/XX店有几人）在未开通群的日常聊天误触）
    awmc_arcade_enabled: bool = False
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
    # gamerch wiki 运行时补充（fill 语义，仅补规范表空字段）：重建/重载后对缺口
    # 谱面抓取 gamerch 页面回填，稳态零抓取
    awmc_gamerch_fill: bool = True
    # gamerch 页面磁盘缓存 TTL（小时）
    awmc_gamerch_max_age: int = 24
    # MuNET current_jp 版本批次补充（当批曲目谱面/当前定数/别名/发布日期，
    # fill 语义 + 创建缺失曲；每日管线内 title-diff 候选，默认关需显式开启）
    awmc_munet_batch: bool = False
    # MuNET 别名全量走查刷新间隔（天，0=禁用默认关；走查有断点续走与单次
    # 60 分钟时间预算，实测约 30 分钟单晚走完）
    awmc_munet_alias_days: int = 0
    # 日服 NET 数据源 per-user 查询冷却（分钟，0=禁用；dxrating 同款 15 分钟）
    awmc_net_cooldown_minutes: int = 15
    # 落雪令牌每日保活（refresh_token 30 天不刷新即失效；每日 4:30 对全部
    # 持有 refresh_token 的绑定续期一次，防止闲置绑定走进续期死局）
    awmc_lxns_keepalive: bool = True


plugin_config: Config = get_plugin_config(Config)
global_config = get_driver().config

# 全局名称
NICKNAME: str = next(iter(global_config.nickname), "")
