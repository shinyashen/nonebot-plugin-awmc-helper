# 技术债与已知问题清单

来源：2026-09-24 第一次全量代码审查（core / render / plugins 三区 + maimai_py 对照）；
2026-09-25 第二次全量代码审查（增量并入）。

**已处理**（第一次审查同日四批，原条目已删除）：

- 第一批：4 个 P0 缺陷、死代码清理、maimai_py 能力对齐（经作者 fork 交付，该分支已合入上游 v1.5.3）；
- 第二批：P1 功能缺陷 10 项、P2 性能、健壮性/一致性 10 项、架构违规
  （core/types 门面、core/plates 下沉、私有入口转正、常量去重）；
- 第三批：可合并/抽象的主体与架构遗留——装饰底图/B50 行卡/布局常量/配色/
  署名串/FC-FS 映射收敛，CN/JP 查询 scope 参数化，ext 公共 fetch_json，
  plugins 会话与权限助手下沉、数字 id 解析单源、巨型 handler 拆分；
- 第四批：`_merge_extra_docs` 与 search_alias_song 拆分、前缀知识单源化
  （CHART_TYPE_BY_PREFIX，含命中词大小写归一）、渲染蓝色系归一
  （tools.TEXT_BLUE/TITLE_BLUE）。

**已处理**（第二次审查 2026-09-25，三批原子提交，原条目已删除）：

- 缺陷批：arcade 排卡别称匹配多别称折叠（补回归测试）、tables 进度总览
  `get_cleared` 双调、music_query `query_chart` 收编 `_resolve_raw_id` 单源
  （别名入口 id 分支顺带接受空格）、日服 fallback 结果去重回查；
- 收敛批：FC/FS 素材名映射收敛 constants（plate_table_draw 内联字典、
  rating_table 透传包装）、署名行走 `credit_text`（nb_chart 两处）、评级阈值
  改由系数表推导（`RANK_ACHIEVEMENT_THRESHOLDS = ACHIEVEMENT_LIST[-6:]`）、
  评级键辅助去死参数；
- 死代码批：`upsert_arcade`/`alias_ids_of`/`genre_zh`/`_alias_matches`/
  nb_chart TYPE_CHECKING 空桩/包根无引用再导出/`VERSION_IMAGE` 死兜底；
- 重构批（render，全部经全量测试，ReM 收敛另做改造前后双页渲染 MD5
  逐字节比对）：东亚字宽表单源（删 nb_chart 整套拷贝）+ Hoshino 双参数截断
  收敛 `truncate_hoshino`（nb_chart/best50/score/info/song 五处）、舞/霸 ReM
  槽位代表谱面规则收敛 `table_layout.slot_rep`（模板与叠章同源）+ 分页/组序
  键 `level_page_key`、在线下载公共化 `download.download_to_file` +
  `DownloadGate`（best50 收藏品与 jp_cover 曲绘共用）、曲绘候选链 static 环
  复用 `assets.cover_candidates`、SD/DX 徽章 `assets.type_badge`（五处）、
  `CIRCLE_PINK`/`DIFF_DISPLAY_NAMES`/`LEVEL_INDEX_EN` 单源、默认主题走
  `DEFAULT_THEME`、函数内 PIL/tools 导入全部上移。

**已处理**（第二次审查第三/四批，2026-09-25 同日，原条目已删除）：

- P0 速赢批：`songdb._genre_of` 删平行映射表改用 maimai_py `name_to_genre`；
  info DX 星改取库算 `ScoreExtend.dx_star`（删 `calc.dx_star_ratio`）；store
  会话入口统一 `session()`（删 `_open_session` 双名）；`Scope` 收敛 songdb
  单源；gamerch 复用 `songdb.norm_title`；`binding.service_display()` 单源
  （tables×3 + score_tools）；猜歌 `_hint_loop/_pic_loop` 合并 `_game_loop`；
  `prefer_type_from_raw_id` 改 `SongType._from_id` 包装；`load()` 去重复
  `get_all`（4→1）；机厅几人 N+1 改批量、别名推送标题回取并发化；
  `reset_all_persons` 改单条 UPDATE；
- P1 渲染批：行卡难度底图 `_score_row_bg` lru_cache；`text_to_image` 整行
  快路径 + 增量测宽消 O(n²)（advance 口径，折行点最多差 1px）；三色渐变
  1×N 线按高度缓存；info/nb_chart 全部素材走 assets 缓存与回退链——新增
  `Assets.canvas()`（画布返回**副本**，防类级缓存被就地绘制污染，pic 结果
  仅作只读源）；
- P2/P3 结构批：maimaidx.jp 中间证书与 `maimaidx_ssl_context()`（lru_cache）
  挪 `core/http.py`（渲染层不再持有会过期的运维数据）；CN/JP 双视图宴谱
  `diff_id → 宿主 id` 索引（随视图缓存重建，`by_utage_id` 免线性扫描）；
  `core.songs.chart_entries()` 单源派生 `available_ids` 与查歌条目展开
  （`display_song_id`/`_display_card_id` 确认为曲级/卡级特例单源，见 §二）；
  快照/注入改走 `ListSongProvider`（内容哈希）正规 `client.songs` 通道——
  删 `_seed_versions` 与快照路径全部 `client._cache` 直写（AGENTS 规则 10
  冲突消除；注入路径保留 stale 单曲键清理，待上游 `_configure` 清命名空间
  行为落地后移除）。

当前剩余待办如下；修复后请删除对应条目。行号会漂移，定位以符号名为准。

## 一、上游 maimai.py（方针不变，无库侧待办）

**无待办**（2026-09-24 与维护者确认，撤销审查报告中的全部库侧提请）：

- 私有 API（`RateType._from_achievement`、`name_to_genre`、`plate_aliases`、
  `plate_to_version` 等）**直接使用即可**——库维护者即本插件作者，下划线名在两侧
  同步演进，无转正/导出的必要；
- `divingfish_to_version` **不应**补 DX 时代 PLUS 各版：该表键域镜像国服（水鱼）
  `from` 字段的实际取值，而国服 DX 时代 PLUS 不作独立版本（`plate_to_version`
  CN 表熊/华同码即同一口径）。maimaiinfo all_data 命名域里的 PLUS 名由本地
  `SOURCE_NAME_TO_VERSION` 承接，各自归位。

第二次审查补充定案（2026-09-25，与维护者确认）：

- **快照/测试注入的 provider 化是项目内改造**：`ISongProvider`/`IAliasProvider`
  接口已有（AwmcSongProvider 即其子类），无需上游新增「歌曲侧 LocalProvider」——
  项目侧写 `SnapshotSongProvider`（kv 快照反序列化 + 内容哈希）配快照别名
  provider，`_load_snapshot`/`inject` 即可走正规 `client.songs(provider=...)`，
  `_seed_versions` 与 `client._cache` 直写（含删键，AGENTS 规则 10 冲突）随之消除；
- 唯一值得与上游讨论（**不阻塞**项目侧改造）：`MaimaiSongs._configure` 在
  provider 哈希变化时清理 songs/tracks 命名空间残留键——可恢复 provider 化后
  丢失的「换库后 by_id 不命中已删曲」保证（现由 inject 手工删键提供），且该
  改进对所有 provider 切换场景成立；
- **不上游化（重申维持本地）**：宴谱 id 编解码（`songdb.utage_ids/utage_diff_id`）、
  `Song`↔dict 序列化（`songs.song_to_dict/song_from_dict`）、calc 算法族
  （`score_line`/`rise_recommend`/`min_ds_of_ra`）；
- 私有 API 直接用方针下的两个收编（见 §3.2）：DX 星直接取库算
  `ScoreExtend.dx_star`（删 `calc.dx_star_ratio`）；`songdb._genre_of` 删
  `_GENRE_ALIASES` 改用 `name_to_genre`（逐对重复已核实）。

## 二、架构边界（已确认为合理现状，仅备忘）

- tables 插件对 `core.render.table_template` 的公开调用（SUPERUSER 更新指令 +
  `draw_*_with_fallback` 兜底入口）属合理的指令→core 边界，无需再分层；
- guess 极端并发观感：开局串行锁落地后「开始」消息必先于对局可答；曲绘提示与
  首条提示的交织理论上仍可能出现（量级极小，观察即可）；
- `core/plates.py::major_type_of_plate/plate_version_range` 对
  `MaimaiPlates._configure/_major_type` 语义的本地复刻属**有意保留**：库侧逻辑
  绑死在实例方法（需 client + 全量成绩），无纯函数可调；若未来库内出现牌子
  纯函数再收编；
- `render/stats.py` 曲线缺失的 `raise ValueError` 为**防御性断言**（ginfo
  handler 已前置检查 `diff.curve`），静默降级无意义，维持现状；
- `Assets.canvas(name, theme)` 为「会就地绘制的画布」专用入口（返回缓存
  副本）；`pic()` 结果只能作只读源（alpha_composite 源 / resize / crop），
  直接当画布会污染类级缓存——两次渲染间装饰叠加是典型症状；
- `constants.display_song_id`（曲级展示 id）与 `nb_chart._display_card_id`
  （卡片代表 id，跟随主类型谱面组）是 `chart_entries` 之外的**特例单源**，
  语义不同勿强行合并；
- 渲染层其余 `"prism_plus"` 字面量为**固定素材命名空间/字典键/用户文案**语义，
  非「默认主题」值，勿改 `DEFAULT_THEME`（改了会在 DEFAULT_THEME 变更时误切
  固定版式素材）：`score.py::DrawScore.__init__`（行卡画布固定版式）、
  `nb_chart.py` 宴会卡 logo、`song.py` chara_left 立绘、`plate_table_draw.py`
  评级章、`best50.py::FOOTER_COLORS` 字典键、`bind.py` 主题用法文案。

## 二A、渲染全量对照 Hoshino（✅ 2026-09-24 完成）

规范见 architecture.md「渲染坐标规范」。进度总览（plate_progress）、完成表
（plate_table_draw + _plate_grid）与其余全部渲染器已逐个对照 Hoshino
`core/image/` 同名实现并修正，每项含临时合成数据渲染 + 人工看图验证：

- ✅ `rating_table.py`（定数表叠章）← Hoshino `rating_table.py`：lv15 大格
  分支盖章（评级章原尺寸 (x+55,y+115)、FC/AP 计划 PlayBonus 大章 200×200、
  不画完成底）与全曲徽章评级阈值（ACHIEVEMENT_LIST[-6:] 六档，曾误用
  range(6) 致恒判 SSSp）；
- ✅ `score.py`（推分/进度/分数列表）← Hoshino `score.py` + `base.py`：未游玩
  小卡图层序（曲绘下、难度框上）、推分 ds 字号 18、标题截断 >26→25、未游玩
  推荐行不画旧评级章；
- ✅ `best50.py` ← Hoshino `best50.py`：头部名片/行卡坐标全对齐，无需修；
- ✅ `nb_chart.py` / `info.py` ← Hoshino `chart.py` / `info.py`：宴谱卡描边改
  回紫 (210,57,174,255)、截断规则（>L 才截、截后 ≤L-1）全线上对齐、info
  署名行改 Hoshino 形式（Data from 嵌入、20pt）；
- ✅ `song.py`（搜歌列表）← Hoshino `song.py`：整体重写为 PRiSM 曲卡网格版式；
- ✅ 舞/霸双页端到端复核（合成舞代曲库 + 双页底图 + 叠章）：组内排序键改
  Hoshino get_ds_sort_key（ReM 曲按 ReM 定数，模板/叠章两侧同步）、统计区
  wu 专用布局（292/204/条宽 176 + plate_progress_wu，曾致 ReM 列出画面）、
  头部计数整牌口径（分页前累计）。2026-09-25 该规则的模板/叠章两侧已收敛
  `table_layout.slot_rep`（改造前后双页渲染 MD5 逐字节一致）。

对照中确认的**有意偏差**（代码注释已记，此处汇总）：

- `best50.py`：DX 星直接取 maimai_py `ScoreExtend.dx_star`（库算），与
  Hoshino 现算 `dx_score()` 仅在恰好 85/90/93/95/97% 边界时差一星，库口径
  更贴近官方 ≥ 语义；
- `song.py::draw_song_card`（猜歌/运势线索卡）Hoshino 无对应实现，维持自有
  版式；搜歌列表每页条数维持本项目 25/页（Hoshino PAGE_SIZE=14 属其翻页
  UX），网格几何随条数自适应；
- `nb_chart.py`：版本 logo 走 `fit_version_logo` 等比适配（Hoshino 直接
  resize 有拉伸）；日服视图不渲染「新曲だよ!」标（国服口径）；Sync 计划
  （rating_table/plate 叠章）为我方扩展，lv15 无 Sync 大章素材沿用小章；
- 素材包差异：`progress_big.png` 43 高等裁剪 clamp 均按素材实际值。
  `mai/plate_version/` 26 组牌头即真实牌单：各版本 将/极/神/舞舞 四牌齐全，
  例外为 舞（四牌，特牌）、霸（仅 者，全游戏唯一「者」尾牌）、**真（无将，
  仅 极/神/舞舞；原版 Hoshino 亦硬编码拒绝）**、**初（整代无牌，国服牌单
  自真起）**。`core.plates` 的 `_PLATE_KINDS_ROSTER` 即此表，`plate_kinds`
  （真实牌单，`is_valid_plate` 用它拒答不存在的牌名并提示该代真实牌种）与
  `plate_kinds_of`（预渲染枚举，只出「将」；真无将故跳过）均由其派生。
  另：真牌谱面范围含初代（`_SD_FIRST_PLATES`）——初代曲国服无自己的牌，
  库与原版都把 初+真 并作真牌范围，否则完成表与牌子进度会差一整代曲目。

## 三、待办（暂缓项；修复后删除条目）

- 分页大小各自硬编码（alias 25 / score_tools 50 / tables 80）。
- best50 的 RA_THRESHOLD / RA_STAR_*（DXRating 展示口径）宜集中到 constants。
- `core/songs.py` `inject` 的 stale 单曲键清理：上游 `MaimaiSongs._configure`
  在 provider 哈希变化时清理 songs/tracks 命名空间后即可删除（§一待议项）。

## 四、建议批次（第二次审查）

- ~~批次 A（缺陷 + 死代码）~~ ✅ 2026-09-25 完成；
- ~~批次 B（收敛收尾，低风险）~~ ✅ 2026-09-25 完成；
- ~~批次 C（渲染重构）~~ ✅ 2026-09-25 完成（ReM 收敛经渲染 MD5 等价验证）；
- ~~批次 D（架构）~~ ✅ 2026-09-25 完成（provider 化、chart_entries、store
  整理、PEM 迁移、宴谱索引、性能与口径统一全部落地；仅剩 §三暂缓项与
  上游待议的 stale 键清理移除条件）。

审查来源的剩余跟进面已收敛为：两条暂缓项 + 一条上游待议项。
