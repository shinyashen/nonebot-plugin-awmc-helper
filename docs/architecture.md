# 架构说明

## 总体结构

```
nonebot-plugin-awmc-helper（主插件，官方《嵌套插件》结构）
├── core/                        核心层：唯一允许 import maimai_py 的地方
│   ├── client.py                唯一的异步 MaimaiClient 单例 + provider 装配 + 缓存刷新
│   ├── songs.py                 曲库服务：预热/每日刷新/查询代理/别名索引/快照降级
│   ├── store.py                 统一 SQLite（SQLModel + aiosqlite，awmc.db）
│   ├── binding.py               用户绑定 + PlayerIdentifier 装配 + 绑定回填会话
│   ├── score.py                 成绩查询薄封装 + MaimaiPyError → 用户文案映射
│   ├── calc.py                  分数线/推分推荐/DX 星（RA 复用 maimai-py ScoreCoefficient）
│   ├── utils.py                 分页 + handler 异常兜底装饰器
│   ├── ext/                     maimai-py 未覆盖的外部 API 直连层
│   │   ├── yuzu.py              柚子别名申请/投票 + SSE 常驻推送
│   │   ├── divingfish.py        水鱼 RA 排行
│   │   ├── lxns.py              落雪 OAuth 授权码换 Token
│   │   └── wahlap.py            华立机厅 location
│   └── render/                  PIL 绘图：字体/素材缓存/卡片/B50/表格/饼图/FFT 裁剪
└── plugins/                     子插件层（每个目录独立插件，可独立停用）
    ├── base / bind / music_query / alias / score_query
    ├── score_tools / tables / random_song / fortune / guess / arcade
```

## 关键设计

### 子插件加载

按 NoneBot 官方《嵌套插件》结构，`plugins/` 下每个子目录是独立插件。
主插件逐个调用官方 `load_plugin("nonebot_plugin_awmc_helper.plugins.<name>")`
加载（与文档"停用扩展"同款 API）。`awmc_disabled_plugins` 配置可按目录名停用。

### 数据层

- `MaimaiClient` 全进程只有一个实例（`core/client.py` 导入期构造）；
  曲库缓存按 provider 组合哈希命中，刷新 = 删除哈希键后同组 provider 重拉；
- 曲库每日 4 点全量刷新；刷新成功后快照例行写入 `kv_cache` 表，
  启动拉取失败自动降级为快照（仅查询可用）；
- 别名索引由曲库服务自维护（柚子别名 + 本地 DB 别名热合并），
  同一别名可对应多曲。

### 统一存储

全部运行时数据集中在 localstore 数据目录的 `awmc.db`：

| 表 | 用途 |
|---|---|
| `user_binding` | 用户绑定（查分器凭据/主题） |
| `group_switch` | 群级开关显式覆盖（部署默认走 .env） |
| `local_alias` | 本地别名（唯一约束，热更新） |
| `arcade` 族 | 机厅/别称/订阅/人数流水 |
| `kv_cache` | 曲库快照（断网降级） |

不入库：素材文件（static/）、.env 配置、纯内存态（绑定回填会话、猜歌对局）。

### 开关两层设计

部署级默认值走 `.env`（pydantic 字段默认，如 `awmc_guess_enabled`）；
群级运行时开关只在 `group_switch` 表存显式覆盖，未覆盖取默认值。

### ext 直连层

maimai-py 未覆盖的外部接口全部收敛在 `core/ext`：
统一共享 httpx 客户端、`ExtError`/`ExtNetworkError` 异常语义、独立 respx 测试。
SSE 推送为常驻协程：断线指数退避 3s→60s、Last-Event-ID 断点续传。

### 会话型交互

绑定授权码回填、猜歌答题均不使用 `got` 独占会话，
而是「内存 TTL 会话表 + priority=0 的 on_message Rule」前置拦截，
群内其他指令不受影响。

### 权限

- SUPERUSER：NoneBot 内置；
- 群管/群主：uninfo 的 `ADMIN()` Permission（多适配器通用）。

## 测试

- nonebug + OneBot v11 假事件覆盖全部 matcher；
- maimai-py / ext 的 HTTP 交互用 respx 拦截（不联网）；
- 绘图函数做冒烟测试（输出非空 PNG）；
- 曲库通过向 MaimaiClient 缓存注入样例数据绕过网络（`tests/mocks.py`）。

## 渲染坐标规范（以 Hoshino 为准）

`core/render/` 各绘图模块的**坐标、素材引用与图层顺序，一律以 Hoshino 版
maimaiDX（`local/repos/maimaiDX/core/image/`）为唯一权威**；NB 版
（`local/repos/nonebot-plugin-maimaidx`）仅作结构参考。改动任何渲染布局前，
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
