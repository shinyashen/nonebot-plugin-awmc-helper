# Changelog

本项目的所有重要变更都记录在本文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Added

- 水鱼 OAuth 设备码绑定指令「绑定水鱼」（handoff=code 确认码回填，对齐 Hoshino 上游）：
  水鱼 2026-09-28 起写路径强制 OAuth（Import-Token 带真实数据写入返回 500），绑定后
  查询与导分统一走 OAuth 凭据；绑定表新增 `divingfish_oauth`/`divingfish_sub`。
- 水鱼 subject 不再依赖默认查分器偏好派生（导分插件对 service=net/lxns 的用户同样
  装配水鱼 OAuth 目标）。
- 落雪令牌每日保活任务（`AWMC_LXNS_KEEPALIVE`，默认开；每日 4:30）：refresh_token 30 天
  不刷新即失效（落雪 OAuth 文档口径），闲置超期的绑定会走进「续期死局」——任务对全部
  持有 refresh_token 的绑定逐个续期一次（复用按需续期的并发安全链路），存在需重新绑定
  的用户时向 SUPERUSER 私聊汇总。
- 查分服务 `notify_slow` 慢查询提示：落雪令牌续期后进入 10s 退避档时向用户发一句
  「比预期时间要长」的非技术提示（b50/ap50/minfo/牌子/推分/我的排名已接线）。

### Changed

- B50 卡（日服 NET）：装备的是游戏初始「デフォルト」姓名框/头像时不再贴官方素色
  默认图——姓名框落水鱼缺省牌（UI_Plate_550101，官方默认框放大到 800 宽太糊），
  头像直接用 QQ 头像（无可回退缺省 509506）；自定义装备不受影响。收藏品页确认
  装备默认框时会清掉名牌兜底缓存（`net_nameplate:*`），旧自定义名牌不复活。

### Fixed

- 落雪令牌续期后重试撞「新令牌生效延迟」（实测通常 ≤10s、偶发数分钟）导致查询/导分
  首次必失败且文案误导：续期核心三态化（refreshed/dead/skip），续期成功后 0/5/10s
  阶梯重试，全败给「落雪查分器暂时无法访问，请一分钟后再试」；dead（refresh_token
  已过期，30 天不用即失效）改报「落雪授权已过期，请重新『绑定落雪』」而非
  「没有找到这个玩家」；OAuth 错误体（`error`/`error_description`，无 message 字段）
  正确解析。
- 导分上传已删除曲目（如过期限时宴谱）被落雪整批拒绝（HTTP 400 song not found，
  maimai_py #60 的本地库守卫对「规范表保留的已删曲」是盲区）：落雪目标按其当前曲库
  列表预过滤，剔除部分计入跳过数照常上传其余目标；此前该场景以「成绩导入 token 无效」
  误导且水鱼/落雪部分上传。
- 导分落雪 401 续期重试：5s/10s 阶梯退避 + 非技术兜底文案（同查询路径）。
- 导分水鱼上传迁移 OAuth subject 统一凭据（Import-Token 写入已被水鱼拒绝）；
  未授权用户得到「发送『绑定水鱼』完成授权」的准确引导文案。
- 「水鱼授权码」命令撞名（既是「绑定水鱼token」的别名又是确认码回填命令）：
  同优先级 matcher 并发双响应——确认码被同时当 Import-Token 落库并完成 OAuth
  绑定，用户收到两条矛盾回复且凭据被污染。现在「水鱼授权码」唯一归属确认码
  回填，Import-Token 入口仅「绑定水鱼token」；补命令名/别名两两不相交的结构
  回归测试。
- 排卡人数「+1」并发丢更新：读快照→内存算→整行回写的写入路径改为单语句相对
  增量（SQLite RETURNING），两人同时 +1 净加 2；回复人数取数据库实况，机台数
  修改同样不再整行回写（顺带修掉覆盖并发排卡人数的隐患）。
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
