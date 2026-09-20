# 子插件开发指南

本插件采用 NoneBot 官方《嵌套插件》结构：`plugins/` 下的每个子目录是一个
独立子插件，可被 `awmc_disabled_plugins` 配置独立停用。

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
- `uv run poe test` 全绿 + `uv run ruff check .` 通过。
