# 核心层 API 参考（core）

子插件只允许通过 `nonebot_plugin_awmc_helper.core` 公开接口访问数据；
maimai_py 仅可在 core 内 import。本文是**精选清单**（第三方扩展最常用的面），
以源码为准；曲库规范表构建（`core/songdb.py`）与每日/轮询管线属内部实现，
不承诺对外稳定。

## client

```python
client: MaimaiClient                      # 唯一进程单例（不得再实例化）
divingfish_provider: DivingFishProvider   # 水鱼 provider（OAuth 凭据启用 Bearer 路径）
divingfish_public_provider: DivingFishProvider  # 水鱼无凭据 provider（公开查询专用）
lxns_provider: LXNSProvider               # 落雪 provider（含开发者 Token）
yuzu_provider: YuzuProvider               # 柚子别名 provider
```

曲库刷新不走删缓存键：日常重建经 `song_service.refresh()`，全量管线经
`core.songdb.full_refresh()`（规范表重建后 provider 指纹变化，缓存自动失效）。

## songs

```python
song_service: SongService   # CN 视图为默认查询面；jp_* 方法走日服视图

await song_service.ensure_loaded() -> MaimaiSongs   # 等待就绪（幂等）
await song_service.load() -> bool                   # 曲库就绪（冷启动重建/快照降级）
await song_service.refresh() -> bool                # 重建规范表并刷新运行时
await song_service.get_all(include_disabled=False) -> list[Song]
await song_service.by_id(id) -> Song | None
await song_service.by_title_fuzzy(title) -> list[Song]       # 子串、忽略大小写
await song_service.by_alias(alias) -> list[Song]             # 柚子+落雪+本地，可多条
await song_service.by_alias_detail(alias)
#   -> (list[Song], 前后缀剥离信息 | None)，带谱面类型前缀剥离重查
await song_service.entries_for_name(name) -> list[...]       # 别名→ID→标题 统一入口
await song_service.by_artist(artist) -> list[Song]
await song_service.by_bpm(min, max) -> list[Song]
await song_service.by_level_value(min_ds, max_ds) -> list[Song]
await song_service.by_note_designer(name) -> list[Song]      # 谱师等价类包含式
await song_service.random(*, song_type, genre, level, level_index, exclude_utage)
await song_service.by_utage_id(diff_id) -> Song | None       # 宴谱 6 位 id
await song_service.aliases_of(song_id) -> list[str] | None   # 反向查别名
await song_service.reload_alias_index() -> None              # 本地别名变更后调用
await song_service.inject(songs) -> None                     # 测试注入（不联网）

# 模块级函数
chart_entries(song) -> list[...]   # 曲谱条目展开（查歌/列表单源）
song_to_dict(song) / song_from_dict(dict)   # Song ↔ 标准字典（快照/注入）
```

## store

```python
await init_db() -> None
# 绑定
await get_binding(platform, user_id) -> UserBinding | None
await save_binding(binding) -> None
await delete_binding(platform, user_id) -> bool
await get_lxns_refreshable_bindings() -> list[UserBinding]   # 落雪保活用
# 群开关（覆盖 / 生效值）
await get_group_switch(group_id, feature) -> bool | None
await get_switch(group_id, feature, default) -> bool
await set_group_switch(group_id, feature, enabled) -> None
# 本地别名
await add_local_alias(song_id, alias, created_by) -> bool
await get_local_aliases() -> list[LocalAlias]
# 曲库规范表（song/song_sheet_group/song_chart/song_chart_level/song_alias/
# song_source_raw/song_pending 表族的读写，经 core.songdb 使用）
await save_song_aliases(...) / load_song_aliases() / upsert_song_aliases(...)
await song_image_url(song_id) -> str | None
# KV 快照
await kv_get(key) -> Any | None
await kv_set(key, payload) -> None
# 机厅
await get_arcade / get_arcade_by_ids / get_all_arcades / get_arcades_by_name
await save_arcade / update_arcade_count / upsert_arcades / delete_arcade
await add_arcade_alias / remove_arcade_alias_by_name
await get_arcade_aliases / get_arcade_aliases_by_ids
await subscribe / unsubscribe / get_subscriptions
await add_count_log / reset_all_persons
```

## binding

```python
binding_service: BindingService
pending_bindings: PendingBindingStore      # 授权码回填会话（内存 TTL）

SERVICE_DIVINGFISH / SERVICE_LXNS          # 服务标识常量（"divingfish"/"lxns"）

await binding_service.ensure(platform, user_id) -> UserBinding   # 自动创建默认源
await binding_service.get(platform, user_id) -> UserBinding | None
await binding_service.unbind(platform, user_id) -> bool
await binding_service.set_service(binding, service) -> None      # 校验落雪凭据
await binding_service.set_theme(binding, theme) -> None
await binding_service.resolve_query(platform, sender_id, at_target)
#   代查目标绑定解析：无 at 自动建行；有 at 只读，QQ 平台未绑定回退水鱼公开查询
await binding_service.refresh_lxns(binding) -> str   # "refreshed"/"dead"/"skip"
await binding_service.bind_divingfish_username / bind_divingfish_token
await binding_service.bind_divingfish_oauth(...)     # OAuth 凭据落库
await binding_service.bind_lxns(...)
await binding_service.bind_net(platform, user_id, sega_id, password)  # 日服 NET

pending_bindings.start(platform, user_id, kind, ttl=1200)
pending_bindings.is_active(platform, user_id, kind=None) -> bool
pending_bindings.consume(platform, user_id) -> str | None
pending_bindings.discard(platform, user_id)
```

## score / sources

```python
score_service: ScoreService

await score_service.get_player(binding) -> Player
await score_service.get_b50(binding) -> MaimaiScores          # b35/b15 + rating
await score_service.get_scores_all(binding) -> MaimaiScores   # 全量成绩
await score_service.get_b50_by_username(username) -> tuple[DivingFishPlayer, MaimaiScores]
await score_service.get_minfo(song, binding|None) -> PlayerSong | None
await score_service.get_plates(binding, plate) -> MaimaiPlates
await score_service.get_my_ranking(binding) -> tuple[RankUser, int] | None
await score_service.notify_fetch_if_needed(binding)           # NET 慢查询前置提示

# 数据源注册表（能力域由各 Source 声明，适用性标注自动派生）
class Capability(str, Enum)   # B50/MINFO/SCORES_ALL/PLATES/PLAYER/MY_RANKING
SOURCES: dict[str, SourceBase]
source_of(service) -> SourceBase
command_hints(service) -> str     # 指令支持清单（帮助「数据源」详情用）
support_note(cap) -> str | None   # 能力不支持时的用户提示（无则 None）
UserScoreError                    # message 为用户可读文案（sources 定义）
```

## calc

```python
compute_rating(ds, achievement) -> int          # RA（maimai-py ScoreCoefficient）
min_ds_of_ra(ra) -> float                       # 由 RA 反推定数下界
rate_of(achievement) -> str                     # SSS+ 等评级名
score_line(diff, line) -> dict | None           # 分数线容错
rise_recommend(scores, songs, *, level, target, ...) -> list[dict]  # 推分推荐
build_bests(scores) / build_flat_bests(scores)  # B50/B35/B15 组装
```

## chart_card

```python
resolve_card_view(song, binding=None)           # 出卡路由（CN/日服视图、NET 嵌 B50）
await chart_card_bytes(song, ...) -> bytes      # 查歌富卡（多子插件共用单源）
```

## ext（maimai-py 未覆盖的外部 API 直连层）

```python
# yuzu（柚子别名；插件侧惯用 `from ...core.ext import yuzu as yuzu_ext`）
yuzu_client.get_status() -> list[AliasVote]
yuzu_client.get_alias(song_id) -> ServerAlias | None
yuzu_client.get_apply_songs(name) -> ApplySongs | None
yuzu_client.apply_alias(song_id, alias, user_id, group_id) -> str
yuzu_client.agree_alias(tag, user_id) -> str
yuzu_ext.start_alias_push(on_apply) / await yuzu_ext.stop_alias_push()
yuzu_ext.iter_sse(lines) -> AsyncIterator[SSEMessage]   # 断线退避 + Last-Event-ID 续传

# divingfish（水鱼；惯用别名 df_ext）
await df_ext.rating_ranking() -> list[RankUser]     # RA 降序全量
await df_ext.device_authorize(...) / await df_ext.redeem(...)  # OAuth 设备码授权
df_ext.oauth_ready() -> bool
await df_ext.fetch_music_data(...)                  # 曲库数据源（规范表管线用）

# lxns（落雪；惯用别名 lxns_ext）
lxns_ext.oauth_configured() -> bool
lxns_ext.build_authorize_url() -> str
await lxns_ext.fetch_token(code) -> LxnsToken
await lxns_ext.refresh_token(refresh_token) -> LxnsToken  # 每日保活/按需续期共用
lxns_ext.extract_authorization_code(text) -> str | None

# wahlap（华立；惯用别名 wahlap_ext）
await wahlap_ext.fetch_locations() -> list[WahlapArcade]

# net（日服 maimai NET；登录抓取 + 记录页解析）
MaimaiNetClient   # 登录流/玩家页/收藏品装备解析/记录页抓取，错误分类 NetError
```

## utils / forward / http

```python
paginate(data, page, per_page) -> tuple[list, int]
parse_page(args, default=1) -> int                # 指令参数 → 页码（非数字回默认）
group_id_of(session) / is_private_session(session) / user_id_of(session)
handle_errors(fallback, except_with_message=())   # handler 异常兜底装饰器
                                                  # （透传 MatcherException）
slow_notice(text=...)                             # 慢查询中途一次性提示
group_admin(...)                                  # 群管权限（uninfo，多适配器通用）
await notify_superusers(bot, text)                # SUPERUSER 私聊通知
await ensure_group_admin(session)                 # 群管校验（不满足抛权限异常）

await try_send_forward(bot, entries, *, group_id=None, user_id=None) -> bool
#   OB11 合并转发（节点可为文本或 UniMessage；失败/超时返回 False 由调用方降级）

build_smart_transport() -> httpx transport | None # HTTP 智能代理层（未配代理返回 None）
create_smart_client(**kwargs) -> httpx.AsyncClient
maimaidx_ssl_context() -> ssl.SSLContext          # maimaidx.jp 中间证书
```

## render

消费方按子模块导入（`from ...core.render.tools import ...`）；素材访问统一走
`assets` 单例。公开入口精选：

```python
assets.cover(song_id) / assets.pic(name, theme) / assets.canvas(name, theme)
#   pic/canvas 结果只读，canvas 返回副本供就地绘制；素材缺失走 0.png 兜底
font(size, name)                                  # 素材包字体
text_to_image(text, ...) / text_image_bytes(...)  # 长文本转图（发送出口）
image_to_bytes(img) -> bytes
crop_cover_randomly(cover) -> Image               # FFT 频域权重裁剪（猜歌/运势）
await jp_cover.asset_bytes(...)                   # 日服官方素材在线获取

song.song_card_bytes / song_list_bytes            # 查歌谱面卡 / 搜歌列表图
chart_card.chart_card_bytes                       # 查歌富卡（嵌 B50 成绩）
best50.best50_bytes / best50_flat_bytes           # B50 / 条件50 平铺卡
nb_chart.song_chart_info / song_chart_banquet_info  # 谱面详情卡 / 宴谱卡
info.song_play_data / stats.song_global_data      # ginfo 数据组装（曲线/评级分布）
score.DrawScore                                   # 行卡体系（推分/进度/分数列表）
rating_table.draw_rating_table / draw_rating_table_cond     # 定数表叠章
table_template.rating_table_text_bytes / rating_table_cond_text_bytes
plate_table_draw.draw_plate_table                 # 完成表叠章
plate_progress.plate_progress_bytes               # 牌子进度总览
```

渲染坐标与素材引用以 Hoshino 版 maimaiDX 为唯一权威（详见 architecture.md
「渲染坐标规范」）。

## help

```python
help_registry: HelpRegistry              # 帮助注册表进程单例（M10 帮助系统）

help_registry.declare(*, plugin, title, category, description="", commands)
#   子插件帮助块；同 (plugin, category) 整块替换，未知类别自动创建
help_registry.declare_guide(guide: Guide)  # 流程指南（intro/prerequisites 必填）
help_registry.resolve(query, *, include_hidden=False) -> Page
#   消歧顺序：指令 > 类别 > 指南；hidden 指令对普通用户按未命中处理
page_entries(help_registry, page) -> list[str | UniMessage]   # 页面 → 转发节点序列
page_text(help_registry, page) -> str                         # 页面 → 纯文本（降级素材）

@dataclass CommandSpec:   # matcher / name / aliases / brief / detail / example
                          # / scope / capability（core.sources.Capability 值，
                          # 适用性标注由数据源注册表派生）/ hidden
@dataclass Guide:         # key / title / aliases / intro / prerequisites / steps
@dataclass GuideStep:     # text / commands（按名引用）/ image（独立纯图节点）
```

内置类别表（`BUILTIN_CATEGORIES`）：基础 / 绑定 / 查歌 / 别名 / 查分 / 工具 /
表格 / 娱乐 / 排卡 / 管理；内建指南「查分上手」由 `core/help.py` 注册。
声明方式与守卫测试见 [子插件开发指南](subplugin-dev-guide.md)「帮助注册」。
