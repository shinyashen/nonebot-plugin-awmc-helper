# 技术债与已知问题清单

来源：2026-09-24 全量代码审查（core / render / plugins 三区 + maimai_py 对照）。

**已处理**（同日两批，修复后原条目已从本清单删除）：

- 第一批：4 个 P0 缺陷（猜歌吞指令与竞态、快照缺 versions 键、牌子完成表牌头简繁）、
  core/render/plugins 死代码清理、maimai_py fork 能力对齐（MAGiCAL、回牌、
  name_to_genre、系数表、get_divingfish_id 等）；
- 第二批：原清单第二节（P1 功能缺陷 10 项）、第三节（P2 性能：O(n²) 索引化、
  串行 await 并发化、N+1 收敛）、第四节（健壮性/一致性 10 项）、第六节（架构违规：
  core/types 门面、core/plates 下沉、私有入口转正、常量去重）。

当前剩余待办如下（第五、七节）；修复后请删除对应条目。行号会漂移，定位以符号名为准。

## 一、上游 maimai.py

**无待办**（2026-09-24 与维护者确认，撤销此前审查报告中的全部库侧提请）：

- 私有 API（`RateType._from_achievement`、`name_to_genre`、`plate_aliases`、
  `plate_to_version` 等）**直接使用即可**——库维护者即本插件作者，下划线名在两侧
  同步演进，无转正/导出的必要；
- `divingfish_to_version` **不应**补 DX 时代 PLUS 各版：该表键域镜像国服（水鱼）
  `from` 字段的实际取值，而国服 DX 时代 PLUS 不作独立版本（`plate_to_version`
  CN 表熊/华同码即同一口径）。maimaiinfo all_data 命名域里的 PLUS 名由本地
  `SOURCE_NAME_TO_VERSION` 承接，各自归位；

## 二、可合并 / 可抽象（重构收益排序）

### render 层
1. **装饰底图生成 3 份逐字相同**（score.DrawScore.__init__ / table_template._generate_bg /
   plate_progress）→ 抽 `generate_prism_bg(height)`。
2. **B50 行卡 2 份**：best50._draw_row 与 score.DrawScore.whiledraw 坐标/字号/配色一致
   → 抽公共行卡函数。
3. **模板/叠章双层布局常量双写**（最大结构性风险）：定数表（85px/14列/450）、lv15
   （425×450）、牌子表（96px/12列/490）在 table_template 与 rating_table/plate_table_draw
   各一份；`_group_by_ds`、`level_of`/`_by_level` 亦成对 → 收进单一布局模块。
4. 难度配色 3 份（best50/score/plate_table_draw）；FC/FS→素材名映射 5 份；进度条头部
   2 份（plate_progress / plate_table_draw）；credit 署名串 7+ 处 → `render_credit()`；
   best50 四个徽章函数绕过 `Assets.pic` 统一入口。

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
2. music_query 数字 id→宴谱判定→回查流水线 2 份近同构（search_alias_song /
   query_chart，score_query._resolve_song 为第三份简化版）→ 合并 `_render_by_raw_id`。
3. RA 反推定数公式与 `22.4`/`100.5` 常数 2 处（score_tools/random_song）→ 下沉 core/calc。
4. alias 两个申请 handler 前置校验重复；分页样板 2 处（alias/score_tools）→ core/utils；
   OB11 try-import 2 处（alias / core/forward）→ 判定能力暴露自 forward。
5. 巨型 handler 拆分：music_query.search_alias_song（~166 行）、tables.plate_cmd（~128 行，
   完成表+进度两条流水线）。

## 三、架构遗留（随重构清理）

- **tables 插件 `from ...core.render import table_template` 仅剩完成表底图缺失兜底
  （generate_plate_template）一个用途**，渲染调度宜一并下沉 core；
- **best50.SERVICE_NAMES 与 constants.SERVICE_DISPLAY 值不同**（署名口径分裂，先统一
  再合并）；
- **guess 双揭晓之外的残余观感**：极端并发下「开始」消息先发出后才可能有答案，但
  曲绘提示的发送仍可能与首条提示交织（量级极小，观察即可）。

## 四、硬编码收敛（非库职责，留本地但应集中）

- `music_query._PREFIX_TO_TYPE` 与 `constants.strip_chart_prefix` 的前缀知识双份维护。
- 分页大小各自硬编码（alias 25 / score_tools 50 / tables 80）。
- best50 的 RA_THRESHOLD / RA_STAR_*（DXRating 展示口径）宜集中到 constants。
- 渲染蓝色系三组并存：`(124,129,255)`（6 文件内联）、`FONT_BLUE`、`_TEXT_BLUE` → 集中
  到 render 公共层。
