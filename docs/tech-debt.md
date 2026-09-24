# 技术债与已知问题清单

来源：2026-09-24 全量代码审查（core / render / plugins 三区 + maimai_py 对照）。
当日已处理：4 个 P0 缺陷（猜歌吞指令与竞态、快照缺 versions 键、牌子完成表牌头简繁）、
core/render/plugins 死代码清理、maimai_py fork 能力对齐（MAGiCAL、回牌、name_to_genre、
系数表、get_divingfish_id 等）。

本文记录**剩余**待办，按优先级分组；修复后请删除对应条目。行号会漂移，定位以符号名为准。

## 一、上游 maimai.py

**无待办**（2026-09-24 与维护者确认，撤销此前审查报告中的全部库侧提请）：

- 私有 API（`RateType._from_achievement`、`name_to_genre`、`plate_aliases`、
  `plate_to_version` 等）**直接使用即可**——库维护者即本插件作者，下划线名在两侧
  同步演进，无转正/导出的必要；
- `divingfish_to_version` **不应**补 DX 时代 PLUS 各版：该表键域镜像国服（水鱼）
  `from` 字段的实际取值，而国服 DX 时代 PLUS 不作独立版本（`plate_to_version`
  CN 表熊/华同码即同一口径）。maimaiinfo all_data 命名域里的 PLUS 名由本地
  `SOURCE_NAME_TO_VERSION` 承接，各自归位；

## 二、P1 功能缺陷

- **best50 `_inflight` 永不清理**（render/best50.py `fetch_item_image`）：在线牌子/头像
  一次下载失败后，失败 task 永久滞留 dict，后续永远 await 同一失败结果——该素材进程内
  永不重试且 dict 无界增长。对照 jp_cover.py `_download` 的 finally-pop 正确写法。
- **ap50 的 b35/b15 拆分失衡**（plugins/score_query ap50 handler）：先全局排序截 50 再按
  版本切，可能 35 侧空、15 侧 50 条，与 B50「旧 35 + 新 15」语义和模板布局不符；应各侧取
  top35/top15。
- **ginfo 颜色前缀误剥**（plugins/score_query `_at_target`/ginfo 入口 regex）：曲名以
  颜色字开头（如「白い…」）时首字被当难度色，查询必然失败；需空格强制或失败回退重试。
- **fortune 非数字 user_id 哈希不稳定**（plugins/fortune handler）：`abs(hash(str))` 受
  PYTHONHASHSEED 盐化，多平台场景每次重启运势变化，与「同人同日稳定」矛盾；改稳定哈希
  （如 zlib.crc32）。
- **guess_switch 无权限校验**（plugins/guess）：任意成员可 `开启|关闭mai猜歌`，与
  alias/arcade 同类开关的管理员校验不一致。
- **arcade「+2卡」单位被吞**（plugins/arcade）：regex 捕获单位后忽略，卡数按人数写入
  count log；操作符 regex 与 SET_OPS/DECREASE_OPS 双份维护，漏改即「减」落成「加」；
  「取消」靠明文包含判断，参数含「取消」二字会误触。
- **score_tools 纯数字水鱼用户名被当页码**（plugins/score_tools rating_ranking）。
- **cover.py 全黑曲绘无防护**：`magnitude.max()==0` 时归一化得 NaN，`np.random.choice`
  抛错；裁剪区域无法注入种子（rng 参数只控 scale）。
- **minfo 同一请求两次远端查分**（plugins/score_query minfo → `_b50_rise_tips` 内再
  `get_b50`）：同源同凭据可合并/复用。
- **bind 无参场景白建绑定行**（plugins/bind df_bind 等）：先 `ensure`（无则建行）才发现
  无参只回用法提示；未绑定用户查一次用法落一行库。

## 三、P2 性能

- **`build_song` 循环内全组扫描**（core/songdb.py）：scope="cn" 时每张谱面调一次
  `state.cn_current_version()`（内部遍历全部 groups）；物化 ~1900 曲 × ~6 谱为上万次，
  应提到循环外。最热路径，建议最先做。
- **`standard_json`/`song_standard_json` O(曲²)**：每曲全量遍历 charts+groups；全库导出
  约 1900 × 14000 次键比较，每次 rebuild 触发。
- **`apply_missing` 逐曲 `groups_of_song`**（O(曲×组)）；`tables` 插件三处
  `get_all → 双层 get_difficulties → 过滤` 同构循环可抽公共并预建索引。
- **`jp_by_alias_detail` 每次查询重建全库别名索引**（core/songs.py）。
- **`aliases_of` N+1 全表 IO**：每曲一次 `get_local_aliases()` 全表读；alias 插件循环
  调用成 N 次全表。
- **串行 await 可并发**：`refresh_all` 六源串行拉取；`get_b50_by_username` 两查询；
  provider 柚子/落雪别名两源；`_hourly_cn_poll` 两请求；music_query 三处逐条 `by_id`
  （含同根曲重复查，应 set 去重 + gather）；arcade 三处逐 id `get_arcade`（store 应加
  `get_arcades(ids)` 批量接口）；`sync_and_reset` 每机厅独立 session 串行。
- **tables `LEVEL_LIST.index` 循环内 O(n)**：预建 dict。

## 四、健壮性 / 一致性

- **控制流依赖 `finish()` 异常**（plugins/music_query search 的 5 个分支）：正确性完全
  依赖 `_render_jp_result` 内部必抛 FinishedException，加 return 路径即双发；改返回值驱动。
- **异常静默无日志**：chart_card B50 拉取失败 `except: pass`；music_query `_vote_hint`；
  alias 推送循环与全局开关广播（失败不计数不告警）。至少 `logger.debug`。
- **`_notify_superusers` docstring 与实现不符**（core/songs.py）：自称跨适配器，实现
  硬编码 onebot11。
- **`store.upsert_arcade` 不保留 `updated_at`**：同步后行时间戳被重置。
- **`get_arcades_by_name` 全表载入内存过滤**：机厅量级小尚可，宜 SQL LIKE。
- **score_query `_at_target` 仅适配 OB11 at 段形状**：非 OB11 静默退化为查自己，宜注释。
- **score_tools `rise_score` 手写异常转文案**：与同文件 `except_with_message` 风格不一致。
- **alias_status 分页变量冗余**（`real_page` 恒等于 page）。
- **arcade_add 自定义 id 段未隔离**（≥10000 起但与官方段同空间，`max+1` 全表扫描取 id）。
- **guess 双揭晓之外的残余竞态**：开局「开始」发送 await 期间被抢先揭晓时，提示循环有
  归属守卫不再进入，但「猜歌开始」消息可能在揭晓之后才发出（窗口极小，观感问题）。

## 五、可合并 / 可抽象（重构收益排序）

### render 层
1. **装饰底图生成 3 份逐字相同**（score.DrawScore.__init__ / table_template._generate_bg /
   plate_progress）→ 抽 `generate_prism_bg(height)`。
2. **B50 行卡 2 份**：best50._draw_row 与 score.DrawScore.whiledraw 坐标/字号/配色一致
   → 抽公共行卡函数。
3. **模板/叠章双层布局常量双写**（最大结构性风险）：定数表（85px/14列/450）、lv15
   （425×450）、牌子表（96px/12列/490）在 table_template 与 rating_table/plate_table_draw
   各一份；`_group_by_ds`、`level_of`/`_by_level` 亦成对 → 收进单一布局模块。
4. 东亚字宽表+截断 2 份（best50 / nb_chart）→ 移 tools；难度配色 3 份；FC/FS→素材名
   映射 5 份；进度条头部 2 份（plate_progress / plate_table_draw）；credit 署名串 7+ 处
   → `render_credit()`；best50 四个徽章函数绕过 `Assets.pic` 统一入口。

### core 层
1. `apply_jp`/`apply_cn` 三段回填重复（行字段 / notes 五元组 / 宴 kanji）→ 抽公共。
2. JP 视图物化三条等价路径（provider scope="jp" / songs.jp_songs / _jp_songs_map）→ 收敛。
3. CN/JP 查询 6 对同构方法 → `scope` 参数化。
4. 版本名→码两处（`_source_version` / `_crosscheck_df`）；`_archive_raw` 六段行构造表驱动；
   `_merge_extra_docs` 177 行巨型函数拆分。
5. ext 四份「get→包装→状态码检查」→ 抽公共 `_fetch_json_checked`；maimaiinfo/otoge_db
   `_fetch_json` 逐字同；lxns fetch/refresh_token 近重复合并；lxns `fetch_song_list`
   是唯一不包装网络异常的。

### plugins 层
1. 群/用户 id 提取三胞胎（alias/arcade/guess）与管理员校验三连 → 下沉 plugins/base。
2. music_query「国服 miss→日服 fallback」模板 7 处 → 单函数；数字 id→宴谱判定→回查
   流水线 2 份近同构（search_alias_song / query_chart，score_query._resolve_song 为第三份
   简化版）→ 合并 `_render_by_raw_id`。
3. RA 反推定数公式与 `22.4`/`100.5` 常数 2 处（score_tools/random_song）→ 下沉 core/calc。
4. alias 两个申请 handler 前置校验重复；分页样板 2 处（alias/score_tools）→ core/utils；
   OB11 try-import 2 处（alias / core/forward）→ 判定能力暴露自 forward。
5. 巨型 handler 拆分：music_query.search_alias_song（~166 行）、tables.plate_cmd（~128 行，
   完成表+进度两条流水线）。

## 六、架构违规（AGENTS.md 硬规则，随重构清理）

- **6 个子插件直接 import maimai_py**（规则禁止）：music_query、guess、tables、
  random_song、score_query、score_tools——用途均为类型标注/isinstance/plate_to_version/
  current_version/FCType，应由 core 再导出或提供等价入口。
- **子插件摸 core 私有**：plugins/songdb import `_prerender_templates`；plugins/tables
  拼装 table_template 三个下划线函数（应转正为 core 公开「按牌名取谱面范围」接口，
  顺带把牌子范围逻辑从 render 层挪回 core）。
- **songdb 7 处 `store._open_session()`** 绕过 store 公共接口。
- **render 内部**：nb_chart 三私有函数（`_major_diffs`/`_version_image`/`_fit_version_logo`）
  被 info/table_template import → 转正移 tools。
- **双份定义**：THEMES（core/binding 与 render/assets）；柚子域名（core/client 与
  ext/yuzu）；字体名裸字符串 11 处绕过 fonts 常量。

## 七、硬编码收敛（非库职责，留本地但应集中）

- `music_query._PREFIX_TO_TYPE` 与 `constants.strip_chart_prefix` 的前缀知识双份维护。
- `constants.SERVICE_DISPLAY` 与 best50.SERVICE_NAMES 语义重复但值不同（署名口径分裂，
  先统一再合并）。
- 分页大小各自硬编码（alias 25 / score_tools 50 / tables 80）。
- best50 的 RA_THRESHOLD / RA_STAR_*（DXRating 展示口径）宜集中到 constants。
- 渲染蓝色系三组并存：`(124,129,255)`（6 文件内联）、`FONT_BLUE`、`_TEXT_BLUE` → 集中
  到 render 公共层。
