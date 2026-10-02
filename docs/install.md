# 安装部署

## 1. 环境要求

- Python ≥ 3.10（推荐 3.12）
- NoneBot2 ≥ 2.4.3，OneBot v11 适配器（推荐，其他适配器经 alconna/uninfo 尽量兼容）
- SQLite（Python 自带，无需安装数据库）

## 2. 安装插件

```bash
# nb-cli
nb plugin install nonebot-plugin-awmc-helper

# 或 uv
uv add nonebot-plugin-awmc-helper
```

在 `pyproject.toml` 的 `[tool.nonebot]` 中确认：

```toml
[tool.nonebot]
plugins = ["nonebot_plugin_awmc_helper"]
```

## 3. 下载素材包

绘图功能依赖与原版 maimaiDX 相同的官方素材（曲绘 / UI / 字体），
下载后解压到任意目录：

- 全量包 `Resource CN1.55.7z`：
  [Cloudreve](https://cloud.yuzuchan.moe/f/34s7/Resource%20CN1.55.7z)
- 增量包 `Resource CN1.56 UPDATE.7z`（解压覆盖到同一目录）：
  [Cloudreve](https://cloud.yuzuchan.moe/f/Jvhl/Resource%20CN1.56%20UPDATE.7z)

> 下载地址来自上游 maimaiDX README，可能随版本轮换，以其 README 最新为准。
> 无素材包时插件可加载、纯文本指令可用，绘图指令会提示素材缺失。

## 4. 配置

最低配置（`.env.*`）：

```dotenv
AWMC_STATIC_PATH=/abs/path/to/static   # 素材包目录（必填）
```

完整配置项见 [README](../README.md#️-配置)。

按需启用的能力：

| 能力 | 需要的配置 |
|---|---|
| 曲库加载（多源规范表管线） | 无（公开数据源；可选 `AWMC_GITHUB_TOKEN` 提高 otoge-db PR 预读限额） |
| 落雪好友码查询 | `AWMC_LXNS_DEVELOPER_TOKEN` |
| 绑定落雪（OAuth） | `AWMC_LXNS_CLIENT_ID` / `AWMC_LXNS_CLIENT_SECRET` / `AWMC_LXNS_REDIRECT_URI` |
| 别名推送 | `AWMC_ALIAS_PUSH=true`（默认） |

## 5. 启动验证

```bash
python bot.py
```

启动日志依次出现：

- `Succeeded to load plugin "nonebot_plugin_awmc_helper:…"`（各子插件）
- `曲库加载完成：国服 N 首，日服 M 首`（联网构建曲库；失败时降级为上次快照并告警）

## 6. 数据与维护

- 全部运行时数据在 localstore 数据目录的 `awmc.db`（SQLite），
  备份该文件即备份全部状态（含用户 Token，注意权限）；
- 曲库每日 4:05 自动全量重建（国服更新按 `AWMC_CN_POLL_MINUTES` 轮询增量触发），
  排卡人数每日 4 点清零；
- 升级插件后建议 `uv lock --upgrade-package maimai-py` 跟进数据层新版本。

## 7. 常见问题

- **启动报 `awmc_static_path` 校验失败**：未配置素材目录；
- **查分提示「开发者令牌无效」**：检查水鱼/落雪开发者 Token 配置；
- **别名推送收不到**：确认 `AWMC_ALIAS_PUSH=true` 且群内已 `开启别名推送`；
- **绘图乱码**：素材包字体缺失（`static/font` 下 4 个字体文件）。
