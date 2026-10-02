# 架构说明

## 总体结构

```
nonebot-plugin-awmc-helper（主插件，官方《嵌套插件》结构）
├── constants.py                 包级常量（评级中文/展示 id/JP 版本 logo 名等共享表）
├── core/                        核心层：唯一允许 import maimai_py 的地方
│   ├── client.py                唯一的异步 MaimaiClient 单例 + provider 装配（智能代理层）
│   ├── songs.py                 曲库服务：CN/JP 双视图查询代理/别名索引/注入/启动与轮询任务
│   ├── songdb.py                规范表构建管线：多源解析→并集合并→CN 定数推导→双视图物化
│   ├── provider.py              AwmcSongProvider（规范表指纹 _hash）+ 快照/注入 provider
│   ├── store.py                 统一 SQLite（SQLModel + aiosqlite，awmc.db）
│   ├── sources.py               查分数据源注册表：水鱼/落雪/日服 NET 按 Capability 域路由
│   ├── score.py                 查分门面（薄封装，按绑定路由到 sources）
│   ├── binding.py               用户绑定 + PlayerIdentifier 装配 + 授权码回填 + 代查解析
│   ├── net_score.py             日服 NET 成绩组装（b35/b15 按 current_version_jp）
│   ├── combo.py                 条件组合查询（条件串解析与过滤，KarenBot 语义复刻）
│   ├── designer.py              谱师等价类（马甲/合作名义归一）
│   ├── calc.py                  分数线/推分推荐/RA 计算（maimai-py ScoreCoefficient）
│   ├── plates.py                牌子表纯函数（牌单/牌种校验）
│   ├── chart_card.py            查歌富卡组装（嵌 B50 成绩，多子插件共用）
│   ├── help.py                  帮助注册表（类别/指令/指南三级树，M10）
│   ├── http.py                  智能代理 transport + 共享客户端 + maimaidx.jp 证书
│   ├── forward.py               OB11 合并转发发送出口（失败降级）
│   ├── session_store.py         内存 TTL 会话表（授权码回填/猜歌答题单源）
│   ├── lxns_keepalive.py        落雪令牌每日保活任务（导入即注册）
│   ├── utils.py                 分页/会话取值/异常兜底/权限助手/SUPERUSER 通知
│   ├── ext/                     maimai-py 未覆盖的外部 API 直连层
│   │   ├── yuzu.py              柚子别名申请/投票 + SSE 常驻推送
│   │   ├── divingfish.py        水鱼 RA 排行 + OAuth 设备码授权
│   │   ├── lxns.py              落雪 OAuth 授权码换 Token + 令牌续期
│   │   ├── net.py               日服 maimai NET 登录抓取与记录页解析
│   │   ├── wahlap.py            华立机厅 location
│   │   ├── maimaiinfo.py / otoge_db.py / otoge_pr.py / gamerch.py / munet.py
│   │   │                        规范表管线的日侧源与补充源（曲库构建专用）
│   └── render/                  PIL 绘图：字体/素材缓存/卡片/B50/表格/饼图/FFT 裁剪
└── plugins/                     子插件层（每个目录独立插件，可独立停用）
    ├── base / bind / music_query / alias / score_query
    ├── score_tools / tables / random_song / fortune / guess / arcade / songdb
```

## 关键设计

### 子插件加载

按 NoneBot 官方《嵌套插件》结构，`plugins/` 下每个子目录是独立插件。
主插件逐个调用官方 `load_plugin("nonebot_plugin_awmc_helper.plugins.<name>")`
加载（与文档"停用扩展"同款 API）。`awmc_disabled_plugins` 配置可按目录名停用。

### 数据层

- `MaimaiClient` 全进程只有一个实例（`core/client.py` 导入期构造，transport 挂
  智能代理层）；曲库运行时 CN 视图由 `AwmcSongProvider` 提供（provider 指纹 =
  规范表内容哈希，重建后 maimai_py 缓存自动失效）；
- 曲库是**多源规范表管线**（`core/songdb.py`）：国服骨架（水鱼/落雪双源，每小时
  轮询交集判定国服更新）∪ 日服全集（maimaiinfo + otoge-db，MAGiCAL 批次经
  MuNET 补充）∪ 补充源（gamerch wiki 字段回填、`awmc_extra_song_sources` 外部
  JSON），落库到 SQLite 的 song 表族并物化 CN/JP 双视图；每日 4:05 全量重建，
  国服更新自动重建 + 触发底图预渲染 + SUPERUSER 通知（均可配置）；
- 冷启动空表先全量重建；联网失败降级为上次快照（仅查询可用）；
- 别名索引由曲库服务自维护（柚子 + 落雪 + 本地 DB 别名热合并），
  同一别名可对应多曲；谱师查询走等价类（马甲/合作名义归一，`core/designer.py`）。

### 查分数据源

`core/sources.py` 数据源注册表：水鱼 / 落雪 / 日服 NET 三个 `SourceBase` 子类
按 `Capability` 能力域（b50/单曲/全量成绩/牌子/玩家资料/RA 排名）声明支持面，
`ScoreService` 按用户绑定的数据源路由；指令侧的「当前数据源暂不支持」提示由
注册表派生（`support_note`/`command_hints`），不在各插件手写副本。日服 NET
查询走窗口缓存（默认 15 分钟冷却，防官方风控）。

### 条件组合查询

`<条件串>50/40`、条件定数表/完成表/进度/分数列表共用 `core/combo.py` 的条件串
解析（同类「或」、跨类「且」，版本/世代/分类/谱面/成绩/修改六类条件词）；
`b40` 等旧系数口径按 FiNALE 系数重算。

### 统一存储

全部运行时数据集中在 localstore 数据目录的 `awmc.db`：

| 表 | 用途 |
|---|---|
| `user_binding` | 用户绑定（查分器凭据/主题/日服 NET 账号） |
| `group_switch` | 群级开关显式覆盖（部署默认走 .env） |
| `local_alias` | 本地别名（唯一约束，热更新） |
| `arcade` 族 | 机厅/别称/订阅/人数流水 |
| `song` 族 | 曲库规范表（song/sheet_group/chart/chart_level/alias/source_raw/pending） |
| `kv_cache` | 曲库快照、管线幂等标记、预渲染缓存键 |

不入库：素材文件（static/）、.env 配置、纯内存态（授权码回填会话、猜歌对局）。

### 开关两层设计

部署级默认值走 `.env`（pydantic 字段默认，如 `awmc_guess_enabled`）；
群级运行时开关只在 `group_switch` 表存显式覆盖，未覆盖取默认值。

### ext 直连层

maimai-py 未覆盖的外部接口全部收敛在 `core/ext`（含日服 NET 登录抓取 `net.py`
与规范表管线的日侧/补充源模块）：
统一共享 httpx 客户端、`ExtError`/`ExtNetworkError` 异常语义、独立 respx 测试。
SSE 推送为常驻协程：断线指数退避 3s→60s、Last-Event-ID 断点续传。

### 会话型交互

绑定授权码回填、猜歌答题均不使用 `got` 独占会话，
而是「内存 TTL 会话表（`core/session_store.py` 单源）+ priority=0 的
on_message Rule」前置拦截，群内其他指令不受影响。

### 权限

- SUPERUSER：NoneBot 内置；
- 群管/群主：uninfo 的 `ADMIN()` Permission（多适配器通用）。

## 测试

- nonebug + OneBot v11 假事件覆盖全部 matcher；
- maimai-py / ext 的 HTTP 交互用 respx 拦截（不联网）；
- 绘图函数做冒烟测试（输出非空 PNG）；
- 曲库通过 `song_service.inject`（maimai_py provider 正规通道）注入样例数据
  绕过网络（`tests/mocks.py`）。

## 渲染坐标规范（以 Hoshino 为准）

`core/render/` 各绘图模块的**坐标、素材引用与图层顺序，一律以 Hoshino 版
maimaiDX（Yuri-YuzuChaN/maimaiDX，开发机本地克隆于 `local/repos/maimaiDX/`）为
唯一权威**；NB 版（nonebot-plugin-maimaidx）仅作结构参考。改动任何渲染布局前，
先对照 Hoshino 同名实现（`plate_table.py` / `rating_table.py` / `score.py` /
`best50.py` / `chart.py` / `info.py` 等）确认坐标与素材名。

注意事项（实测踩坑）：

- **素材引用以 Hoshino `assets.py` 的映射为准**，不要按文件名望文生义：
  完成表头部大面板是 `plate_progress.png`（`_plate_progress_bg`），
  `progress_bg.png` 是进度总览每槽的底部小条（`_plate_progress_bottom_bg`）；
  进度总览头部面板是 `plate_progress_2.png`；
- **素材包的简繁命名与 maimai_py `plate_aliases` 不同**：牌头仅 晓/樱/堇/辉/华 +
  「極」用繁体（`render/assets.py` 的 `_S2T_VERSION`/`_S2T_KIND`），牌种
  將/鏡 等在素材包中是简体，不能从 `plate_aliases` 反推；
- **素材尺寸与 Hoshino 原版可能不同**（如 `progress_big.png` 为 43 高，
  Hoshino 按 92 裁）：裁剪一律 clamp 到素材实际尺寸；
- 牌子完成表/进度总览的网格为「**一格一曲**」语义（四槽完成小标画在同一格），
  模板（`table_template._plate_grid`）与叠章（`plate_table_draw.draw_plate_table`）
  的分组、排序、分页必须逐条一致，改动任一侧先核对另一侧。
