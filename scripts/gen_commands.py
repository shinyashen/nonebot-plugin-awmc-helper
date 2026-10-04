"""docs/commands.md 生成脚本：帮助注册表 → markdown（M10 后置项）。

注册表是唯一事实源：本脚本加载主插件及其全部子插件（与运行时同口径——
完整模块名逐个 ``load_plugin``），把 ``core/help_md.registry_markdown`` 的
输出写入 docs/commands.md；tests/test_gen_commands.py 与文档逐字节锁死，
声明块改动后跑 ``uv run poe gen-commands`` 重新生成即可。

用法：仓库根目录 ``uv run poe gen-commands``（或 ``uv run python
scripts/gen_commands.py``）。只需 nonebot.init 装配 import 环境，不启动 bot、
不连适配器；``_env_file=None`` 跳过本地 .env，保证生成不受部署配置
（如 awmc_disabled_plugins）影响。
"""

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# 直接 python 运行（未 pip 安装）时补 src 路径；uv run 下 venv 已含本包
sys.path.insert(0, str(REPO_ROOT / "src"))

import nonebot

# awmc_static_path 为必填配置（素材目录，缺省启动即报错）；生成文档只需
# import 装配，指向仓库内 static/（素材包就位后的常规 checkout 均存在）
nonebot.init(_env_file=None, awmc_static_path=str(REPO_ROOT / "static"))

# 子插件清单动态取自目录（真源是主插件 __init__ 的自动发现；手抄清单会在
# 新增子插件时静默失真）
PLUGINS = sorted(
    d.name
    for d in (REPO_ROOT / "src" / "nonebot_plugin_awmc_helper" / "plugins").iterdir()
    if d.is_dir() and (d / "__init__.py").exists()
)


def main() -> None:
    from nonebot_plugin_awmc_helper.core.help import help_registry
    from nonebot_plugin_awmc_helper.core.help_md import registry_markdown

    # 与运行时同口径：完整模块名逐个加载（首个子插件 import 触发主插件
    # __init__，其自身也会装配全部子插件；此处显式逐个调用保持幂等口径）
    for name in PLUGINS:
        nonebot.load_plugin(f"nonebot_plugin_awmc_helper.plugins.{name}")

    missing = [
        name
        for name in PLUGINS
        if not any(b.plugin == f"awmc.{name}" for b in help_registry.blocks)
    ]
    if missing:
        raise SystemExit(f"子插件声明缺失（加载失败或未声明帮助块）：{missing}")

    content = registry_markdown(help_registry)
    target = REPO_ROOT / "docs" / "commands.md"
    # 原子写（临时文件 + replace）：半途失败不留截断文档
    tmp = target.with_suffix(".md.tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, target)
    total = sum(
        len(b.commands) for b in help_registry.blocks if b.plugin.startswith("awmc.")
    )
    # 生成结果提示走 stdout（脚本输出，无事件上下文可用 logger）
    sys.stdout.write(
        f"已生成 {target}（{len(PLUGINS)} 个子插件 / {total} 条指令声明）\n"
    )


if __name__ == "__main__":
    main()
