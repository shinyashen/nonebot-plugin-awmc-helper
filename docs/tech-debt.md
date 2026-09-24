# 技术债与已知问题清单

来源：2026-09-24 全量代码审查（core / render / plugins 三区 + maimai_py 对照）。

**已处理**（同日三批，原条目已删除）：

- 第一批：4 个 P0 缺陷、死代码清理、maimai_py fork 能力对齐；
- 第二批：P1 功能缺陷 10 项、P2 性能、健壮性/一致性 10 项、架构违规
  （core/types 门面、core/plates 下沉、私有入口转正、常量去重）；
- 第三批：可合并/抽象的主体与架构遗留——装饰底图/B50 行卡/布局常量/配色/
  署名串/FC-FS 映射收敛，CN/JP 查询 scope 参数化，ext 公共 fetch_json，
  plugins 会话与权限助手下沉、数字 id 解析单源、巨型 handler 拆分。

当前剩余待办如下；修复后请删除对应条目。行号会漂移，定位以符号名为准。

## 一、上游 maimai.py

**无待办**（2026-09-24 与维护者确认，撤销此前审查报告中的全部库侧提请）：

- 私有 API（`RateType._from_achievement`、`name_to_genre`、`plate_aliases`、
  `plate_to_version` 等）**直接使用即可**——库维护者即本插件作者，下划线名在两侧
  同步演进，无转正/导出的必要；
- `divingfish_to_version` **不应**补 DX 时代 PLUS 各版：该表键域镜像国服（水鱼）
  `from` 字段的实际取值，而国服 DX 时代 PLUS 不作独立版本（`plate_to_version`
  CN 表熊/华同码即同一口径）。maimaiinfo all_data 命名域里的 PLUS 名由本地
  `SOURCE_NAME_TO_VERSION` 承接，各自归位；

## 二、可合并 / 抽象（剩余小项）

- **`_merge_extra_docs`（core/songdb.py，约 177 行）拆分**：锚定版本计算、
  逐曲校验、notes/level 三分支解析可提为独立函数（其余回填/留档样板已收敛）。
- **music_query.search_alias_song 进一步拆分**：数字 id 解析已收敛
  `_resolve_raw_id`，条目展开与宴谱分支仍内联在 handler 中，可按「解析 →
  分派渲染」拆为两个子函数。

## 三、架构遗留

- **tables 插件对 `core.render.table_template` 的公开调用仅剩 SUPERUSER 更新
  指令与查询兜底入口**（`draw_*_with_fallback` 已是公开 API，属合理边界）；
  后续若继续分层，可把预渲染调度并入 core/songs 的 `prerender_templates` 管线。
- **guess 极端并发观感**：开局串行锁落地后，「开始」消息必先于对局可答；曲绘
  提示与首条提示的交织在理论上仍可能出现（量级极小，观察即可）。

## 四、硬编码收敛（暂缓，2026-09-24 与维护者确认）

- `music_query._PREFIX_TO_TYPE` 与 `constants.strip_chart_prefix` 的前缀知识
  双份维护。
- 分页大小各自硬编码（alias 25 / score_tools 50 / tables 80）。
- best50 的 RA_THRESHOLD / RA_STAR_*（DXRating 展示口径）宜集中到 constants。
- 渲染蓝色系三组并存：`(124,129,255)`（6 文件内联）、`FONT_BLUE`、`_TEXT_BLUE`
  → 集中到 render 公共层。
