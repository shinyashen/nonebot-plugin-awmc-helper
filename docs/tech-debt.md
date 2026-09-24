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
  头部计数整牌口径（分页前累计）。

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
- 素材包差异：缺 `舞者.png` 牌头（舞将/舞極/舞神/霸者 齐全），舞+者 完成表
  牌头留白跳过不报错；`progress_big.png` 43 高等裁剪 clamp 均按素材实际值。

## 三、硬编码收敛（暂缓，其余两条 2026-09-24 已完成）

- 分页大小各自硬编码（alias 25 / score_tools 50 / tables 80）。
- best50 的 RA_THRESHOLD / RA_STAR_*（DXRating 展示口径）宜集中到 constants。

已完成的两条：前缀知识单源（`constants.CHART_TYPE_BY_PREFIX`，music_query 的
`_PREFIX_TO_TYPE` 删除并顺带修复命中词大小写未归一的隐藏问题）；渲染蓝色系
归一（`render/tools.TEXT_BLUE/TITLE_BLUE`，11 处内联蓝与 FONT_BLUE/_TEXT_BLUE/
_DEFAULT_TEXT_COLOR/_TEXT_COLOR 四个散落别名全部收敛）。
