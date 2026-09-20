# 贡献指南

感谢参与贡献！请先阅读 [README](README.md) 与 [架构说明](docs/architecture.md)。

## 开发环境

```bash
git clone https://github.com/shinyashen/nonebot-plugin-awmc-helper
cd nonebot-plugin-awmc-helper
uv sync                # 安装依赖（推荐 uv ≥ 0.12）
uv run poe test        # pytest + nonebug，必须全绿
uv run ruff check .    # lint
uv run ruff format .   # 格式化
```

## 分支与提交

- 从 `master` 拉出功能分支开发，PR 回 `master`；
- 提交信息遵循 **Conventional Commits**：标准英文头 + 中文描述：

  ```
  feat: 新增xx功能
  fix(core): 修复曲库缓存刷新
  docs: 补充排卡指令文档
  ```

- 保持提交原子化：一个功能点/修复一个提交。

## 代码规范

- Python ≥ 3.10，pydantic v2，全异步（httpx / aiosqlite，不用 aiohttp）；
- 注释与文档字符串使用中文；只写代码本身说不清的约束（坑、上游怪癖）；
- 架构硬规则见 [子插件开发指南](docs/subplugin-dev-guide.md)，
  其中最重要的是：**maimai_py 只允许在 `core/` 内 import**、子插件之间不互相 import。

## 测试要求

新功能必须附带测试：

- matcher 用 nonebug（`tests/fake.py` 假事件）；
- HTTP 交互用 respx 拦截，测试不得联网；
- 绘图函数做冒烟测试（非空 PNG）。

## 提交 PR

1. 确保测试全绿、ruff 通过；
2. 描述清楚改动动机与影响面；
3. 涉及指令变化的，同步更新 `docs/commands.md` 与 README。

## License

提交即表示同意以 [MIT](LICENSE) 协议开源你的贡献。
