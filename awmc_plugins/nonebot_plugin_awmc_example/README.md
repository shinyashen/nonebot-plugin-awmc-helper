# nonebot-plugin-awmc-example

[nonebot-plugin-awmc-helper](https://github.com/shinyashen/nonebot-plugin-awmc-helper)
的**官方第三方扩展示例**：演示一个独立发布的 NoneBot 插件如何复用其 core 公开接口
（曲库查询、文字转图、统一异常兜底、多平台会话），可作为你自己插件的起点模板。

指令：`ext点歌 <关键词|编号>`——按编号/别名/标题搜索曲目，结果渲染成图回复。

源码即文档：与「仓内嵌套子插件」的差异点（require 顺序、绝对导入、命名、
适配器继承）都在 `src/nonebot_plugin_awmc_example/__init__.py` 里以【差异】
注释逐条标出。core 接口清单见主插件 `docs/api.md`。

## 与仓内嵌套子插件的差异

| | 仓内嵌套子插件 | 第三方独立扩展（本示例） |
|---|---|---|
| 位置 | 主插件包内 `plugins/` 目录 | 任意独立仓库，clone 进 `awmc_plugins/` |
| 导入 core | 相对导入 `from ...core import ...` | 先 `require()` 再绝对导入 |
| 命名 | `awmc.<目录名>`（主插件保留前缀） | 第三方自有命名 |
| 加载 | 主插件自动 | 固定目录 / plugin_dirs / pip |
| 停用 | `awmc_disabled_plugins` | 同配置按目录名，或目录改名加 `_` 前缀 |

## 使用

### 方式一：放进 `awmc_plugins/`（主推，零配置）

主插件启动时固定扫描 **bot 工作目录下的 `awmc_plugins/`**，把每个子目录作为
子插件自动加载。把本目录整个放进 `awmc_plugins/` 即可（本示例是 src 布局，
加载器直接支持「clone 整仓」；也支持目录即包的平铺布局）。

```text
你的 bot 目录/
├── bot.py
└── awmc_plugins/
    └── nonebot_plugin_awmc_example/   # clone 或拷贝进来即生效，重启后加载
```

停用：目录改名加 `_` 前缀，或在 `.env` 的 `awmc_disabled_plugins` 里加目录名。

### 方式二：bot 顶层 plugin_dirs

把 `src/nonebot_plugin_awmc_example/` 拷入你的插件目录，然后在 bot 的
`pyproject.toml` 里：

```toml
[tool.nonebot]
plugin_dirs = ["你的插件目录"]
```

### 方式三：pip / PyPI

发布到 PyPI 后可 `nb plugin install nonebot-plugin-awmc-example`，并把它加进
加载列表。注意主插件 `nonebot-plugin-awmc-helper` 0.1.0 尚未上架 PyPI：
clone 部署与本地开发无需 pip 安装（运行环境里已有主插件），走 PyPI 路线需等
主插件发版，或本地改用 path 依赖。

## 改造成你自己的插件

1. 全局改名：`nonebot_plugin_awmc_example` → `nonebot_plugin_你的名字`
   （`pyproject.toml` 的 name、`src/` 下包目录名两处）；
2. 改 `PluginMetadata` 的 name/description/usage/homepage；
3. 改指令名与 handler 逻辑——core 能力清单见主插件 `docs/api.md`；
4. 遵守主插件 `docs/subplugin-dev-guide.md` 的硬性规则：禁止 `import maimai_py`、
   数据访问只走 core 公开接口、中文注释等。

版本与来源以普通文件承载（`__version__`、pyproject、本 README），不依赖 git
嵌套；请保留 README 顶部对源仓库的链接。
