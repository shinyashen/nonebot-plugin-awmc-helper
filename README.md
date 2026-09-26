<div align="center">
    <a href="https://v2.nonebot.dev/store">
    <img src="https://raw.githubusercontent.com/fllesser/nonebot-plugin-template/refs/heads/resource/.docs/NoneBotPlugin.svg" width="310" alt="logo"></a>

## ✨ nonebot-plugin-awmc-helper ✨
[![LICENSE](https://img.shields.io/github/license/shinyashen/nonebot-plugin-awmc-helper.svg)](./LICENSE)
[![pypi](https://img.shields.io/pypi/v/nonebot-plugin-awmc-helper.svg)](https://pypi.python.org/pypi/nonebot-plugin-awmc-helper)
[![python](https://img.shields.io/badge/python-3.10|3.11|3.12|3.13-blue.svg)](https://www.python.org)
[![uv](https://img.shields.io/badge/package%20manager-uv-black?style=flat-square&logo=uv)](https://github.com/astral-sh/uv)
<br/>
[![ruff](https://img.shields.io/badge/code%20style-ruff-black?style=flat-square&logo=ruff)](https://github.com/astral-sh/ruff)
[![pre-commit](https://results.pre-commit.ci/badge/github/shinyashen/nonebot-plugin-awmc-helper/master.svg)](https://results.pre-commit.ci/latest/github/shinyashen/nonebot-plugin-awmc-helper/master)

</div>

## 📖 介绍

NoneBot2 的「舞萌DX」(maimaiDX) 街机音游辅助插件：查歌、别名、查分（B50/AP50）、
表格与牌子进度、分数线/推分、猜歌、机厅排卡，一应俱全。

- **功能基准**：[Yuri-YuzuChaN/maimaiDX](https://github.com/Yuri-YuzuChaN/maimaiDX)
  （HoshinoBot 版），包括 NoneBot 版没有的「机厅排卡」模块；
- **数据层**：[TrueRou/maimai.py](https://github.com/TrueRou/maimai.py)（PyPI `maimai-py`）
  统一曲目/成绩模型与多查分器对接；水鱼 / 落雪 / 柚子三源；
- **架构**：主插件提供核心服务（core），每个功能是独立子插件（plugins/），
  按 NoneBot 官方《嵌套插件》机制加载，可通过 `awmc_disabled_plugins` 独立停用。

> 素材（曲绘/UI 图片）版权归 SEGA / 华立所有，本仓库不分发素材包，仅供个人学习使用。

### 功能一览

| 子插件 | 指令示例 | 说明 |
|---|---|---|
| bind | `绑定水鱼` / `绑定落雪` / `数据源` / `主题` | 查分器绑定与设置 |
| music_query | `查歌` / `定数查歌` / `bpm查歌` / `曲师查歌` / `谱师查歌` / `<别名>是什么歌` / `id <数字>` | 曲库查询 |
| alias | `<名称>有什么别名` / `添加别名` / `同意别名` / `当前投票` / `开启别名推送` | 别名查询、申请与推送 |
| score_query | `b50` / `ap50` / `minfo <曲>` / `ginfo <难度><曲>` | 查分 |
| score_tools | `分数线 紫799 100` / `我要上20分` / `查看排名` | 分数线/推分/水鱼 RA 排名 |
| tables | `<等级>定数表` / `13fc完成表` / `真将完成表` / `13fc进度` / `13+分数列表` / `牌子条件` | 完成度表格 |
| random_song | `来个紫13+` / `随个dx14` / `mai什么` | 随机谱面 |
| fortune | `今日mai` | 今日运势 |
| guess | `猜歌` / `猜曲绘` | 群内猜歌游戏 |
| arcade | `开启/关闭排卡` / `添加机厅` / `订阅机厅` / `XX店+2人` / `机厅几人` | 机厅排卡（默认关，群管开通） |

## 💿 安装

<details open>
<summary>使用 nb-cli 安装</summary>
在 nonebot2 项目的根目录下打开命令行, 输入以下指令即可安装

    nb plugin install nonebot-plugin-awmc-helper --upgrade
使用 **pypi** 源安装

    nb plugin install nonebot-plugin-awmc-helper --upgrade -i "https://pypi.org/simple"
使用**清华源**安装

    nb plugin install nonebot-plugin-awmc-helper --upgrade -i "https://pypi.tuna.tsinghua.edu.cn/simple"

</details>

<details>
<summary>使用包管理器安装</summary>
在 nonebot2 项目的插件目录下, 打开命令行, 根据你使用的包管理器, 输入相应的安装命令

<details open>
<summary>uv</summary>

    uv add nonebot-plugin-awmc-helper
安装仓库 master 分支

    uv add git+https://github.com/shinyashen/nonebot-plugin-awmc-helper@master
</details>

<details>
<summary>pdm</summary>

    pdm add nonebot-plugin-awmc-helper
安装仓库 master 分支

    pdm add git+https://github.com/shinyashen/nonebot-plugin-awmc-helper@master
</details>
<details>
<summary>poetry</summary>

    poetry add nonebot-plugin-awmc-helper
安装仓库 master 分支

    poetry add git+https://github.com/shinyashen/nonebot-plugin-awmc-helper@master
</details>

打开 nonebot2 项目根目录下的 `pyproject.toml` 文件, 在 `[tool.nonebot]` 部分追加写入

    plugins = ["nonebot_plugin_awmc_helper"]

</details>

### 下载素材包

与原版 maimaiDX 相同的官方素材（曲绘 / UI / 字体），下载后解压到任意目录：

- 全量包 `Resource CN1.55.7z`：
  [Cloudreve](https://cloud.yuzuchan.moe/f/34s7/Resource%20CN1.55.7z) ｜
  [OneDrive](https://yuzuai-my.sharepoint.com/:u:/g/personal/yuzu_yuzuchan_moe/IQBGKHie6MAaTZy3rME7Q-ruAVKgXDCKROqz5e25KtMeeVY?e=53eC6a)
- 增量包 `Resource CN1.56 UPDATE.7z`（解压覆盖到同一目录）：
  [Cloudreve](https://cloud.yuzuchan.moe/f/Jvhl/Resource%20CN1.56%20UPDATE.7z) ｜
  [OneDrive](https://yuzuai-my.sharepoint.com/:u:/g/personal/yuzu_yuzuchan_moe/IQDS_RzM66klSqvHtUhfFPTfAfpJcbGlIbL-7Q6eSPxM4CA?e=xRPo7b)

下载地址来自上游 maimaiDX README，可能随版本轮换，以
[其 README](https://github.com/Yuri-YuzuChaN/maimaiDX) 最新为准。

## ⚙️ 配置

在 nonebot2 项目的 `.env` 文件中添加下表配置：

| 配置项 | 必填 | 默认值 | 说明 |
| :--- | :---: | :---: | :--- |
| `awmc_static_path` | ✔ | 无 | 素材包 static 目录绝对路径 |
| `awmc_disabled_plugins` | 否 | `[]` | 停用的子插件目录名列表，如 `["arcade","guess"]` |
| `awmc_default_provider` | 否 | `divingfish` | 默认查分器 `divingfish` / `lxns` |
| `awmc_divingfish_developer_token` | 否 | 无 | 水鱼开发者 token |
| `awmc_lxns_developer_token` | 否 | 无 | 落雪开发者 token |
| `awmc_lxns_client_id` / `awmc_lxns_client_secret` / `awmc_lxns_redirect_uri` | 否 | 无 | 落雪 OAuth 应用（`绑定落雪` 必需） |
| `awmc_yuzu_proxy` | 否 | `false` | 柚子 API 走 `.cn` 中转域 |
| `awmc_alias_push` | 否 | `true` | 别名推送默认态（群可指令覆盖） |
| `awmc_assets_online` | 否 | `true` | icon/plate 素材在线获取 |
| `awmc_save_in_memory` | 否 | `true` | 素材常驻内存 |
| `awmc_cache_ttl_hours` | 否 | `24` | 曲库缓存 TTL（小时） |
| `awmc_guess_enabled` | 否 | `true` | 猜歌默认态（群可指令覆盖） |
| `awmc_guess_interval` | 否 | `8` | 猜歌提示间隔（秒） |
| `awmc_guess_duration` | 否 | `30` | 猜歌揭晓时长（秒） |
| `awmc_arcade_max_delta` | 否 | `30` | 机厅人数单次变更上限 |
| `awmc_arcade_enabled` | 否 | `false` | 排卡默认态（群可 `开启/关闭排卡` 覆盖；默认关规避宽正则误触） |
| `awmc_startup_tasks` | 否 | `true` | 启动时执行后台任务（曲库预热、别名 SSE）；测试/CI 置 `false` |

## 🎉 使用

### 指令表

完整指令表见 [docs/commands.md](docs/commands.md)。

### 🎨 效果图

待补充。

## 数据说明

- 运行时数据（绑定、群开关、本地别名、机厅、数据快照）统一存放在
  localstore 数据目录下的 `awmc.db`（SQLite）；
- 用户的查分器凭据（水鱼 Import-Token、落雪个人 token）同样只落盘于该数据库，
  请妥善保管服务器与数据目录的访问权限；
- 曲库数据每次刷新成功后快照入库，断网时自动降级为上次快照。

## 扩展本插件

想基于本插件写自己的功能？两种方式：

- **第三方独立扩展**（推荐）：写一个独立 NoneBot 插件，`require()` 本插件后
  使用其 core 公开接口。把它放进 bot 工作目录的 `awmc_plugins/` 即自动加载
  （零配置），详见 [docs/subplugin-dev-guide.md](docs/subplugin-dev-guide.md)
  的「第三方独立扩展插件」与官方示例模板
  [awmc_plugins/nonebot_plugin_awmc_example](awmc_plugins/nonebot_plugin_awmc_example/)；
- **仓内子插件**：向本仓库贡献，在 `plugins/` 下新增子插件，指南见
  [docs/subplugin-dev-guide.md](docs/subplugin-dev-guide.md)。

## 开发

```bash
uv sync
uv run poe test   # pytest + nonebug
uv run ruff check .
```

欢迎通过 Issue / PR 反馈问题；提交信息遵循 Conventional Commits
（英文头 + 中文描述），详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 鸣谢

- [Yuri-YuzuChaN/maimaiDX](https://github.com/Yuri-YuzuChaN/maimaiDX) —— 功能基准与素材包
- [TrueRou/maimai.py](https://github.com/TrueRou/maimai.py)（[许可](https://github.com/TrueRou/maimai.py/blob/main/LICENSE)）—— 数据层
- [NoneBot2](https://nonebot.dev) 与社区插件生态

## License

[MIT](LICENSE)
