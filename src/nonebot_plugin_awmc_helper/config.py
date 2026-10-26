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
    # 水鱼 OAuth 应用（developer-token 已随 2026-10-01 端点日落整体移除，见
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
    # GitHub API 访问令牌（可选）：otoge-db 自动化 PR 预读等 api.github.com 调用；
    # 配置后限额 5000 次/小时（匿名仅 60 次/小时，共享出口 IP 易触顶 403）
    awmc_github_token: str | None = None
    # MuNET current_jp 版本批次补充（当批曲目谱面/当前定数/别名/发布日期，
    # fill 语义 + 创建缺失曲；每日管线内 title-diff 候选，默认关需显式开启）
    awmc_munet_batch: bool = False
    # 曲库屏蔽名单（根 id 或显示 id 皆可，读入归一根 id）：人工确认的删除曲
    # 从曲库与全部重建/合并路径剔除。自动信号对其无效的场合使用——上线前
    # 删除曲（id12055 实测）maimaiinfo 机台全集持续收录、MuNET 未清理条目
    # （与现役条目完全同构）、otoge 从未记录，三方数据都无法区分「删除」与
    # 「存在」，只有人工知识可判（2026-10-05 定案）。默认值烧入已人工确认的
    # 12055（ループザルーム，MAGiCAL 上线前删除、maimaiinfo 638126f 起收录）
    awmc_song_denylist: list[int] = [12055]
    # MuNET 别名全量走查刷新间隔（天，0=禁用默认关；走查有断点续走与单次
    # 60 分钟时间预算，实测约 30 分钟单晚走完）
    awmc_munet_alias_days: int = 0
    # 日服 NET 数据源 per-user 查询冷却（分钟，0=禁用；dxrating 同款 15 分钟）
    awmc_net_cooldown_minutes: int = 15
    # 落雪令牌每日保活（refresh_token 30 天不刷新即失效；每日 4:30 对全部
    # 持有 refresh_token 的绑定续期一次，防止闲置绑定走进续期死局）
    awmc_lxns_keepalive: bool = True
    # 谱面预览（渲染 mp4，CPU 密集 + 热链落雪素材站）部署级开关
    awmc_chart_preview_enabled: bool = True
    # 谱面预览单视频渲染时长上限（秒，0=整曲；QQ 对长视频有压缩/限制风险，
    # 可设 60 只渲染前 60 秒）
    awmc_chart_preview_limit_seconds: int = 0
    # 谱面预览渲染缓存总量上限（MB，超限按最旧清理）
    awmc_chart_preview_cache_mb: int = 800
    # 谱面预览 ffmpeg 可执行文件（命令名或绝对路径；空=从 PATH 自动查找）
    awmc_chart_preview_ffmpeg: str | None = None
    # 谱面预览分帧渲染线程数（0=自动：上限 4；1=串行）
    awmc_chart_preview_workers: int = 0
    # 谱面预览超采样倍率（画质档）：2.0=最清、1.5=肉眼几乎无感但整曲快约
    # 1.7 倍（默认）、1.0=最快（边缘锯齿可见）。有效范围 1.0-2.0
    awmc_chart_preview_supersample: float = 1.5
    # 谱面预览输出帧率（30=站内一致；24/20 更省渲染时间，静态画质不变）
    awmc_chart_preview_fps: int = 30
    # 谱面预览打击音效（站内 AnswerSound 采样混音，接近实机打击感）
    awmc_chart_preview_hit_sounds: bool = True
    # 谱面预览打击音效偏移（ms）：负值提前触发，站内默认 -50（音效偏移滑块 0）
    awmc_chart_preview_sound_offset_ms: float = -50.0
    # 谱面预览片段的前置空小节数（音乐先放、画面不渲染音效，0=关闭）
    awmc_chart_preview_preroll_measures: int = 2
    # 谱面预览音频响度目标（LUFS，两遍 loudnorm 线性增益，TP ≤ -1.5 dBTP）
    awmc_chart_preview_loudness_lufs: float = -16.0


plugin_config: Config = get_plugin_config(Config)
global_config = get_driver().config

# 全局名称
NICKNAME: str = next(iter(global_config.nickname), "")
