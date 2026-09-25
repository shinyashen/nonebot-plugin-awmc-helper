# 技术债与已知问题清单

来源：2026-09-24 第一次全量代码审查（core / render / plugins 三区 + maimai_py 对照）；
2026-09-25 第二次全量代码审查（增量并入）。

**已处理**（第一次审查同日四批，原条目已删除）：

- 第一批：4 个 P0 缺陷、死代码清理、maimai_py fork 能力对齐；
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
  牌头文件恰为真实牌表：舞将/舞極/舞神/舞舞舞/霸者（不存在 舞者——舞代
  牌仅 将/极/神/舞舞，霸前缀仅 霸者，是唯一「者」尾牌）。`core.plates`
  据此收紧：`plate_kinds_of`（预渲染枚举）与 `is_valid_plate`（查询校验，
  舞者/霸将/霸极/霸神/霸舞舞 直接提示不存在）。

## 三、待办（第二次审查遗留；修复后删除条目）

### 3.1 性能与一致性

- [ ] `render/info.py` `song_play_data`：全程裸 `Image.open` 绕过 `assets`
  内存缓存（5 次难度循环内反复开 fcfs/ra_dx/评级图），且 `play_info.png/
  logo.png` 无存在性检查（别处均走 pic/pic_optional 回退链）——统一走
  assets + 静默降级。
- [ ] `render/best50.py` `draw_score_row`：每行重开难度底图（B50 50 行、
  score.whiledraw 复用时最坏 80 行；DX/SD 徽章已随 type_badge 走缓存），
  照同文件 `_utage_score_bg` 的 lru_cache 范式进程级缓存。
- [ ] `render/tools.py` `text_to_image`：逐字符全串 text_size 测宽 O(n²)，
  长帮助文本明显劣化，按已有字宽表累增。
- [ ] `render/tools.py` `tricolor_gradient_prism_plus` 逐像素 putpixel：
  1×N 渐变常驻缓存后 resize。
- [ ] `render/jp_cover.py` `_INTERMEDIATE_PEM`（2029-06 到期的 GlobalSign
  中间证书）挪出渲染层独立模块（可配置覆盖 + 到期前提醒）；`_ssl_context()`
  模块级缓存一次。
- [ ] `core/songs.py` `load()`：一次加载对 `songs.get_all()` 调 4 次（判空/
  计数/_apply/快照各一次全量 multi_get），取一次传引用。
- [ ] `core/songs.py` `by_utage_id`：每次全库线性扫描找 diff_id，
  `_apply_to_cache` 时建 `diff_id → (song_id, level_id)` 索引。
- [ ] `plugins/arcade/__init__.py` 机厅几人：逐条 `get_arcade` N+1，改
  `get_arcade_by_ids`；`plugins/alias/__init__.py` `push_apply` 循环内逐曲
  串行 `by_id` 可并发。
- [ ] `core/store.py` `reset_all_persons`：逐行 Python 循环换单条 UPDATE。
- [ ] 渲染错误口径统一：`stats.py` 曲线缺失直接 raise vs 其余模块静默降级；
  `info/nb_chart` 直开主题底图无检查 vs `table_template` 底图缺失 fallback
  重建——统一为「回退链 + warning」。

### 3.2 架构与下沉（中等工程量）

- [ ] **快照/注入 provider 化**（§一定案）：`SnapshotSongProvider`（kv 快照
  反序列化 + 内容哈希）+ 快照别名 provider，`_load_snapshot`/`inject` 走
  `client.songs(provider=...)`；删 `_seed_versions`、删 `client._cache` 直写
  （消除 AGENTS 规则 10 字面冲突与 tracks 命名空间 stale 残留）；stale 键
  保证视上游讨论结果处理；测试注入路径同步迁移。
- [ ] **per-type 条目/id 知识单源**：`music_query._type_entries`、
  `song_service.available_ids`、`constants.display_song_id`、
  `nb_chart._display_card_id` 四处同规则（SD=根 id / DX=+10000 / 宴=diff_id），
  core 下沉 `chart_entries(song)` 派生其余。
- [ ] `core/songdb.py` `_genre_of/_GENRE_ALIASES`：改用 maimai_py
  `name_to_genre`（§一定案）。
- [ ] `render/info.py` DX 星改取 `score.dx_star`（ScoreExtend 已带库算值，
  None 即不画星，与现 `dx_star_ratio` 行为一致），删 `calc.dx_star_ratio`。
- [ ] store 层整理：`session()`/`_open_session()` 双名统一；`Scope =
  Literal["cn","jp"]` 在 songdb/provider 双定义收敛；`core/ext/gamerch.py`
  `_norm_title` 复用 `songdb.norm_title`（songdb 对 ext 均为函数内延迟导入，
  无环）。
- [ ] 小合并：`SERVICE_DISPLAY.get(binding.service, binding.service)` 五处 →
  `core/binding.service_display()`；猜歌 `_hint_loop/_pic_loop` 超时揭晓路径
  合并；`songs.prefer_type_from_raw_id` 以 `SongType._from_id` 一行表达
  （宴 → None）。

### 3.3 暂缓（第一次审查遗留，维持）

- 分页大小各自硬编码（alias 25 / score_tools 50 / tables 80）。
- best50 的 RA_THRESHOLD / RA_STAR_*（DXRating 展示口径）宜集中到 constants。

## 四、建议批次（第二次审查）

- ~~批次 A（缺陷 + 死代码）~~ ✅ 2026-09-25 完成；
- ~~批次 B（收敛收尾，低风险）~~ ✅ 2026-09-25 完成；
- ~~批次 C（渲染重构）~~ ✅ 2026-09-25 完成（ReM 收敛经渲染 MD5 等价验证）；
- **批次 D（架构，单独排期）**：§3.2 全部——provider 化（根治 `_cache` 直写
  与 tracks 残留）+ chart_entries 单源 + store 整理；provider 化落地前可与
  上游讨论 `_configure` 清残留键一事（不阻塞）。§3.1 性能项可穿插在功能
   开发的空档逐条清。
