# 子插件开发指南

本插件采用 NoneBot 官方《嵌套插件》结构：`plugins/` 下的每个子目录是一个
独立子插件，可被 `awmc_disabled_plugins` 配置独立停用。第三方开发者不走
嵌套结构，请直接看下方[第三方独立扩展插件](#第三方独立扩展插件)一节。

## 新建一个子插件

1. 在 `src/nonebot_plugin_awmc_helper/plugins/` 下新建目录（目录名即停用配置名）：

   ```
   plugins/
   └── my_feature/
       └── __init__.py
   ```

2. `__init__.py` 骨架：

   ```python
   """awmc.my_feature：一句话说明。"""

   from nonebot import on_command
   from nonebot.plugin import PluginMetadata
   from nonebot_plugin_alconna.uniseg import UniMessage

   from ...core.songs import song_service
   from ...core.utils import handle_errors

   __plugin_meta__ = PluginMetadata(
       name="awmc.my_feature",           # 必须以 awmc. 前缀命名
       description="...",
       usage="...",
       type="application",
       homepage="https://github.com/shinyashen/nonebot-plugin-awmc-helper",
   )

   my_cmd = on_command("我的指令", block=True)

   @my_cmd.handle()
   @handle_errors()          # 统一异常兜底（透传 MatcherException）
   async def _():
       data = await song_service.by_title("xxx")
       ...
       await UniMessage.text("done").finish()
   ```

3. 在 `docs/commands.md` 补充指令表，并补 nonebug 测试。

> 上方单文件骨架即下方[单文件豁免](#单文件豁免)形态；功能变大后按
> [目录结构约定](#目录结构约定)拆分。

## 目录结构约定

子插件内部按「装配 / 指令入口 / 域模块」三层组织（2026-09-25 定案，与
nonebot-plugin-maimaidx 等主流插件的 commands/ + core/ 组织方式一致）：

```text
plugins/
└── my_feature/
    ├── __init__.py     # 装配：PluginMetadata + 生命周期钩子注册 + matcher 导出，零业务
    ├── matchers.py     # 指令入口：matcher 定义与 handler 同文件，matcher 私有小辅助可放此
    └── resolve.py      # 域模块：跨指令的业务/编排/渲染逻辑，按域命名
                        # （resolve / render / game / sheet / sync / push …均为先例）
```

- **`__init__.py` 只做三件事**：定义 `PluginMetadata`、注册生命周期钩子
  （`on_startup`、apscheduler 定时任务等——生命周期注册一律收在装配层）、
  从 `matchers.py` 导出全部 matcher（nonebug 测试按包属性取用 matcher，
  依赖这一导出）；
- **域模块无注册副作用**：不定义 matcher、不挂驱动钩子；matcher 在 import
  时注册，装配层 import 即完成注册；域模块不得反向 import `matchers`
  （禁止循环依赖）；
- matcher 之间共享的编排/解析/渲染逻辑下沉域模块；多个子插件都要用的能力
  下沉 core（硬性规则 2）；
- 独立扩展插件包（`awmc_plugins/`，见文末）内部同样适用此分层，import
  路径按[第三方差异](#与仓内嵌套子插件的差异)换成绝对导入。

### 单文件豁免

功能面小的子插件允许保持单文件 `__init__.py`（即「新建一个子插件」的骨架
形态），判断口径为**同时满足**：

1. 无独立域逻辑——没有状态机、定时任务、推送管线、多级兜底编排等需要独立
   模块承载的内容；
2. 全文件约 ≤250 行。

越过口径（引入独立域，或体量自然增长）时，在**下次触碰该插件时**按本约定
拆分即可，不要求专门开重构轮次。

> 现状（2026-09-25）：12 个内置子插件中 music_query / guess / tables /
> arcade / alias / score_query 已按三层拆分；base / songdb / fortune /
> random_song / score_tools / bind 按豁免保持单文件。

## 硬性规则

1. **禁止 `import maimai_py`**——数据访问一律走 `..core` 公开接口；
2. **子插件之间不互相 import**——共享能力下沉 core；
3. **禁止直接读写数据库 / 全局配置**——统一经 `core.store` / `core.binding`；
4. 主插件 `__init__.py` 不注册面向用户的 matcher；
5. `PluginMetadata.name` 必须 `awmc.` 前缀；
6. 注释与文档字符串用中文；提交信息 Conventional Commits（英文头 + 中文描述）。

## core 接口约定

- core 服务对外全部是**异步可等待**接口，曲库未就绪时内部 `await ensure_loaded()`；
- maimai-py 异常只在 `core/score.py` 捕获并转 `UserScoreError`；
  handler 用 `@handle_errors(except_with_message=(UserScoreError,))` 透传文案；
- ext 直连层异常为 `ExtError`/`ExtNetworkError`，message 面向用户；
- 会话型交互用「TTL 会话表 + priority=0 on_message Rule」，不用 `got`；
- 群级开关用 `store.get_switch(group_id, feature, default)`：
  默认值来自 `.env` 配置，群级覆盖存 `group_switch` 表。

## 常用依赖注入

```python
from nonebot_plugin_uninfo import Session, UniSession

@my_cmd.handle()
async def _(
    session: Session = UniSession(),     # 多平台会话（platform/user/scene/member）
    message: Message = CommandArg(),
):
    group_id = session.scene.id if session.scene.type == SceneType.GROUP else None
```

## 测试要求

- matcher 用 nonebug（`tests/fake.py` 构造 OneBot v11 事件）；
- HTTP 交互用 respx 拦截（水鱼/落雪/柚子/华立均不联网）；
- 曲库数据用 `tests/mocks.py::seed_service` 注入；
- 绘图函数做冒烟测试（非空 PNG）；
- monkeypatch 插件内部符号时，patch 目标必须是**定义该名字的模块**——
  patch 包命名空间（`__init__.py`）不影响模块内的全局名字查找；
- `uv run poe test` 全绿 + `uv run ruff check .` 通过。

## 第三方独立扩展插件

不进主插件仓库、想发布自己的插件？写一个**独立 NoneBot 插件**，把
`nonebot-plugin-awmc-helper` 当依赖库用。官方示例模板在本仓库
`awmc_plugins/nonebot_plugin_awmc_example/`（src 布局，可直接当起点复制）。

### 与仓内嵌套子插件的差异

1. **先 `require()` 再 import**：`require("nonebot_plugin_awmc_helper")` 必须先于
   一切对主插件的 import（官方《跨插件访问》规范）；
2. **绝对路径导入**：`from nonebot_plugin_awmc_helper.core.songs import song_service`
   （嵌套子插件用相对导入 `from ...core import ...`）；
3. **命名**：用你自己的插件名（`nonebot-plugin-xxx` / `nonebot_plugin_xxx`），
   `awmc.` 前缀是主插件仓内子插件保留的；
4. **不声明 `supported_adapters`**：经 `awmc_plugins/` 加载时主插件元数据尚未
   就绪，`inherit_supported_adapters` 会 ValueError；确需声明就显式写
   `supported_adapters={"~onebot.v11"}`；
5. 硬性规则不变：禁止 `import maimai_py`、数据访问只走 core 公开接口
   （接口清单见 `docs/api.md`）。

### 安装（固定目录，零配置）

主插件启动时固定扫描 **bot 工作目录（CWD）下的 `awmc_plugins/`**，把其中每个
子目录作为子插件自动加载。支持两种布局：

```text
你的 bot 目录/
└── awmc_plugins/
    ├── nonebot_plugin_yours/            # 平铺：目录即插件包（目录名须合法标识符）
    │   └── __init__.py
    └── your-plugin-repo/                # src 布局：clone 整仓即可（目录名任意）
        └── src/
            └── nonebot_plugin_yours/
                └── __init__.py
```

- 目录**不存在 = 零影响**；`_` 开头的目录视为停用（官方 `load_plugins` 约定）；
- `awmc_disabled_plugins` 同样生效（命中一级目录名或模块名）；
- **信任边界**：目录内的代码会被 bot 进程执行，只放你自己审查过的插件；
- 同名模块冲突时只加载字典序第一个；单个插件加载失败只记日志，不影响其余插件。

### 其余加载方式

- bot 顶层 `[tool.nonebot]` `plugin_dirs`（NoneBot 标准目录加载，要求目录在
  CWD 之下）；
- pip 安装 + 加载列表（发布 PyPI 后；主插件 0.1.0 上架前无法走此路线）。

一份代码三种方式通吃——加载路径不影响插件内部写法。
