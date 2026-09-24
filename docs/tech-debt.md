# 技术债与已知问题清单

来源：2026-09-24 全量代码审查（core / render / plugins 三区 + maimai_py 对照）。

**已处理**（同日四批，原条目已删除）：

- 第一批：4 个 P0 缺陷、死代码清理、maimai_py fork 能力对齐；
- 第二批：P1 功能缺陷 10 项、P2 性能、健壮性/一致性 10 项、架构违规
  （core/types 门面、core/plates 下沉、私有入口转正、常量去重）；
- 第三批：可合并/抽象的主体与架构遗留——装饰底图/B50 行卡/布局常量/配色/
  署名串/FC-FS 映射收敛，CN/JP 查询 scope 参数化，ext 公共 fetch_json，
  plugins 会话与权限助手下沉、数字 id 解析单源、巨型 handler 拆分；
- 第四批：`_merge_extra_docs` 与 search_alias_song 拆分、前缀知识单源化
  （CHART_TYPE_BY_PREFIX，含命中词大小写归一）、渲染蓝色系归一
  （tools.TEXT_BLUE/TITLE_BLUE）。

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

## 二、架构边界（已确认为合理现状，仅备忘）

- tables 插件对 `core.render.table_template` 的公开调用（SUPERUSER 更新指令 +
  `draw_*_with_fallback` 兜底入口）属合理的指令→core 边界，无需再分层；
- guess 极端并发观感：开局串行锁落地后「开始」消息必先于对局可答；曲绘提示与
  首条提示的交织理论上仍可能出现（量级极小，观察即可）。

## 三、硬编码收敛（暂缓，其余两条 2026-09-24 已完成）

- 分页大小各自硬编码（alias 25 / score_tools 50 / tables 80）。
- best50 的 RA_THRESHOLD / RA_STAR_*（DXRating 展示口径）宜集中到 constants。

已完成的两条：前缀知识单源（`constants.CHART_TYPE_BY_PREFIX`，music_query 的
`_PREFIX_TO_TYPE` 删除并顺带修复命中词大小写未归一的隐藏问题）；渲染蓝色系
归一（`render/tools.TEXT_BLUE/TITLE_BLUE`，11 处内联蓝与 FONT_BLUE/_TEXT_BLUE/
_DEFAULT_TEXT_COLOR/_TEXT_COLOR 四个散落别名全部收敛）。
