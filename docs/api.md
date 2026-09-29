# 核心层 API 参考（core）

子插件只允许通过 `nonebot_plugin_awmc_helper.core` 公开接口访问数据；
maimai_py 仅可在 core 内 import。

## client

```python
client: MaimaiClient                      # 唯一进程单例（不得再实例化）
divingfish_provider: DivingFishProvider   # 水鱼 provider（含开发者 Token）
lxns_provider: LXNSProvider               # 落雪 provider（含开发者 Token）
yuzu_provider: YuzuProvider               # 柚子别名 provider

async def refresh_songs_cache() -> None
# 删除曲库缓存的 provider 哈希键，下一次 songs() 强制重建
```

## songs

```python
song_service: SongService

await song_service.ensure_loaded() -> MaimaiSongs   # 等待就绪（幂等）
await song_service.load() -> bool                   # 拉取曲库并写快照
await song_service.refresh() -> bool                # 删哈希键后重拉
await song_service.get_all(include_disabled=False) -> list[Song]
await song_service.by_id(id) -> Song | None
await song_service.by_title(title) -> Song | None
await song_service.by_title_fuzzy(title) -> list[Song]       # 子串、忽略大小写
await song_service.by_alias(alias) -> list[Song]             # 柚子 + 本地，可多条
await song_service.by_keywords(kw) -> list[Song]
await song_service.by_artist(artist) -> list[Song]
await song_service.by_bpm(min, max) -> list[Song]
await song_service.by_genre(genre) -> list[Song]
await song_service.by_level_value(min_ds, max_ds) -> list[Song]
await song_service.by_note_designer(name) -> list[Song]
await song_service.random(*, song_type, genre, level, level_index, exclude_utage) -> tuple[Song, SongDifficulty] | None
await song_service.aliases_of(song_id) -> list[str] | None   # 反向查别名
await song_service.reload_alias_index() -> None              # 本地别名变更后调用
await song_service.inject(songs) -> None                     # 测试注入（不联网）
```

## store

```python
await init_db() -> None
# 绑定
await get_binding(platform, user_id) -> UserBinding | None
await save_binding(binding) -> None
await delete_binding(platform, user_id) -> bool
# 群开关（覆盖 / 生效值）
await get_group_switch(group_id, feature) -> bool | None
await get_switch(group_id, feature, default) -> bool
await set_group_switch(group_id, feature, enabled) -> None
# 本地别名
await add_local_alias(song_id, alias, created_by) -> bool
await remove_local_alias(song_id, alias) -> bool
await get_local_aliases() -> list[LocalAlias]
# KV 快照
await kv_get(key) -> Any | None
await kv_set(key, payload) -> None
await kv_updated_at(key) -> datetime | None
# 机厅
await get_arcade / get_all_arcades / get_arcades_by_name / save_arcade / upsert_arcade / delete_arcade
await add_arcade_alias / remove_arcade_alias / remove_arcade_alias_by_name / get_arcade_aliases
await subscribe / unsubscribe / get_subscriptions
await add_count_log / get_count_logs / reset_all_persons
```

## binding

```python
binding_service: BindingService
pending_bindings: PendingBindingStore

SERVICE_DIVINGFISH / SERVICE_LXNS / THEMES

await binding_service.ensure(platform, user_id) -> UserBinding   # 自动创建默认源
await binding_service.unbind(platform, user_id) -> bool
await binding_service.set_service(binding, service) -> None      # 校验落雪凭据
await binding_service.set_theme(binding, theme) -> None
binding_service.identifier(binding) -> PlayerIdentifier          # 不可查时抛 BindingError
binding_service.identifier_or_none(binding) -> PlayerIdentifier | None
binding_service.provider(binding)                                # 按绑定取 provider
await binding_service.bind_divingfish_username / bind_divingfish_token / bind_lxns

pending_bindings.start(platform, user_id, kind, ttl=1200)
pending_bindings.is_active(platform, user_id, kind=None) -> bool
pending_bindings.consume(platform, user_id) -> str | None
pending_bindings.discard(platform, user_id)
```

## score

```python
score_service: ScoreService
UserScoreError  # message 为用户可读文案

await score_service.get_player(binding) -> Player
await score_service.get_b50(binding) -> MaimaiScores          # b35/b15 + rating
await score_service.get_scores_all(binding) -> MaimaiScores   # 全量成绩
await score_service.get_b50_by_username(username) -> tuple[DivingFishPlayer, MaimaiScores]
await score_service.get_minfo(song, binding|None) -> PlayerSong | None
await score_service.get_plates(binding, plate) -> MaimaiPlates
```

## calc

```python
compute_rating(ds, achievement) -> int          # RA（maimai-py ScoreCoefficient）
rate_of(achievement) -> str                     # SSS+ 等评级名
score_line(diff, line) -> dict | None           # 分数线容错
dx_star_ratio(dx_score, level_dx_score) -> int  # DX 星 0-5
rise_recommend(scores, songs, *, level, target, ...) -> list[dict]  # 推分推荐
```

## ext

```python
# yuzu
yuzu_client.get_status() -> list[AliasVote]
yuzu_client.get_alias(song_id) -> ServerAlias | None
yuzu_client.get_apply_songs(name) -> ApplySongs | None
yuzu_client.apply_alias(song_id, alias, user_id, group_id) -> str
yuzu_client.agree_alias(tag, user_id) -> str
yuzu_ext.start_alias_push(on_apply) / stop_alias_push()
yuzu_ext.iter_sse(lines) -> AsyncIterator[SSEMessage]

# divingfish
df_ext.rating_ranking() -> list[RankUser]        # RA 降序全量

# lxns
lxns_ext.oauth_configured() -> bool
lxns_ext.build_authorize_url() -> str
lxns_ext.fetch_token(code) -> LxnsToken
lxns_ext.extract_authorization_code(text) -> str | None

# wahlap
wahlap_ext.fetch_locations() -> list[WahlapArcade]
```

## utils / render

```python
paginate(data, page, per_page) -> tuple[list, int]
handle_errors(fallback, except_with_message=())   # handler 异常兜底装饰器
build_smart_transport() -> httpx transport | None # HTTP 智能代理层（未配代理返回 None）
try_send_forward(bot, entries, *, group_id=None, user_id=None) -> bool
                                                  # OB11 合并转发（LLBot/NapCat 兼容，
                                                  # 失败/超时返回 False 由调用方降级）

font(size, name)                                  # 素材包字体
assets.cover(song_id) / assets.pic(name, theme)   # 素材访问（0.png 兜底）
image_to_bytes(img) / text_to_image(text, ...)    # 发送出口 / 长文本转图
crop_cover_randomly(cover) -> Image               # FFT 频域权重裁剪
song_render.song_card_bytes / song_list_bytes / random_song_bytes
b50_render.best50_bytes / score_list_bytes
pie_render.pie_bytes(title, data)
table_render.completion_grid_bytes(title, items)
```

## help

```python
help_registry: HelpRegistry              # 帮助注册表进程单例（M10 帮助系统）

help_registry.declare(*, plugin, title, category, description, commands)
#   子插件帮助块；同 (plugin, category) 整块替换，未知类别自动创建
help_registry.declare_guide(guide: Guide)  # 流程指南（intro/prerequisites/steps 必填）
help_registry.resolve(query, *, include_hidden=False) -> Page
#   指令 > 类别 > 指南 消歧；hidden 指令对普通用户按未命中处理
page_entries(help_registry, page) -> list[str | UniMessage]  # 页面 → 转发节点序列
page_text(help_registry, page) -> str                        # 页面 → 纯文本（降级素材）

@dataclass CommandSpec:  # matcher / name / aliases / brief / detail / example / scope / hidden
@dataclass Guide:        # key / title / aliases / intro / prerequisites / steps / source
@dataclass GuideStep:    # text / commands（按名引用）/ image（独立纯图节点）
```
