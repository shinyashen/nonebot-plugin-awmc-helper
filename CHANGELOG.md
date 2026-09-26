# Changelog

本项目的所有重要变更都记录在本文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Added

- 初始开发版本：主插件 + 子插件架构（官方嵌套插件机制），数据层基于 maimai-py。
- 子插件：bind（绑定与设置）、music_query（查歌）、alias（别名）、score_query（查分）、
  score_tools（分数线/推分/排名）、tables（定数表/完成表/牌子/进度/分数列表）、
  random_song（随机谱面）、fortune（今日运势）、guess（猜歌）、arcade（机厅排卡）。
- 统一 SQLite 存储（localstore 数据目录 `awmc.db`），曲库每日 4 点自动刷新并快照入库，
  断网自动降级上次快照。
- 核心层：唯一 MaimaiClient 单例、绑定（水鱼用户名/Import-Token、落雪 OAuth/好友码）、
  成绩服务（异常统一映射）、分数线/推分计算（RA 复用 maimai-py ScoreCoefficient）。
- ext 直连层：柚子别名投票 + SSE 推送、水鱼 RA 排行、落雪 OAuth、华立机厅数据。
- 每日 4 点：曲库刷新、华立机厅同步与排卡人数清零。
- 完整 nonebug + respx 测试（80 例）与入库文档（docs/ 7 篇 + CONTRIBUTING）。
### Fixed

- b50/minfo：@ 代查他人成绩不可用（服务器实测）——`CommandArg()` 未过滤 at 段，
  `b50 @某人` 的 at 变成 CQ 码字符串被当水鱼用户名查询；参数改取
  `extract_plain_text`，代查目标经 `binding_service.resolve_query` 解析
  （目标绑定行只读 → QQ 平台水鱼按 at QQ 公开查询（对齐 Hoshino，不落库）→
  非 QQ 平台降级提示），NET 数据源同样支持 @ 代查（走目标凭据，窗口缓存按
  目标键控）。
- 水鱼凭据装配拆两档：公开键（b50/players/minfo）绑定用户名优先且不带 QQ
  （maimai_py 中 QQ 优先于用户名，聊天 QQ ≠ 水鱼账号 QQ 时会查错账号）；
  全量键（scores/plates）Import-Token 优先且不带用户名（maimai_py 把
  `username+credentials` 组合视为「用户名+密码」登录水鱼，「用户名+token
  双绑定」的全量查询此前必失败）。
- 机厅排卡：多别称机厅仅最后一条别称能命中（别称集合按机厅折叠所致），补回归测试。
- 查歌：`id` 指令解析收编 `_resolve_raw_id` 单源（6 位宴 id / DX 展示 id 回查与
  别名入口同口径），「是什么歌」入口的 `id 数字` 分支接受空格。
- 完成表：牌子进度总览删除重复的 `get_cleared()` 全量判定调用。

### Changed

- 排卡功能改为群级开关门禁（对齐 Hoshino 原版 `maimaiDX排卡` Service
  `enable_on_default=False`）：新增群管指令 `开启排卡` / `关闭排卡` 与部署配置
  `AWMC_ARCADE_ENABLED`（默认 **false**）。机厅排卡移植时丢失了原版的 Service
  按群开通语义，宽正则（`<店名>+2人`、`<店名>有多少人` 等）在所有群默认生效，
  日常聊天带前后缀即误触；现未开通群在 rule 层静默拦截（不消费事件，不影响
  其他插件），帮助指令不设门禁以便发现开启入口。同时恢复原版无店名静默语义
  （`+1`/`=5` 不再回复格式提示），排卡人数操作恢复人人可用（原版无权限限制）。
- 大规模收敛与性能清理（第二次全量代码审查，见 docs/tech-debt.md）：字宽表/
  截断规则/FC-FS 映射/署名行/评级阈值/难度配色等十余处单源化；舞/霸 ReM 槽位
  代表谱面规则收敛 `table_layout.slot_rep`（改造前后渲染逐字节一致）；在线素材
  下载公共化；B50 行卡底图与渐变底缓存；`text_to_image` 消除 O(n²) 测宽；
  info/nb_chart 素材统一走 assets 缓存与回退链（新增 `Assets.canvas` 画布副本
  语义）；机厅/别名推送批量与并发化；快照/测试注入改走 provider 内容哈希通道；
  maimai-py 私有 API 对齐（DX 星/分类归一取库值）。
- basedpyright 全量归零（22 处类型缺陷修复）。

### Added

- GitHub Actions CI 完善与部署脚本（local/，不入库）。
