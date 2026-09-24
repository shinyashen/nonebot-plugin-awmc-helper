"""第三方子插件目录（awmc_plugins/）的发现与加载。

目录约定：bot 工作目录（CWD）下的 ``awmc_plugins/``，存在才扫描——这是
第三方插件「git clone 即安装」的固定落点，不设配置项。一级子目录支持
两种布局：

- 平铺：目录即插件包（直接含 ``__init__.py``），目录名须为合法标识符；
- src 布局：``<目录>/src/<包>/__init__.py``（官方插件模板结构，clone 整仓即可）。

安全边界：目录内代码会被本进程执行，等价于部署者的信任声明；单个插件
加载失败只记日志（load_plugin 内部隔离异常），不影响主插件与其余插件。
"""

import sys
from pathlib import Path

from nonebot import logger, load_plugin


def discover_extra_plugins(base: Path) -> list[tuple[str, str, Path]]:
    """扫描 base 下的候选插件，返回 (一级目录名, 模块名, sys.path 条目) 列表。

    ``_`` 开头的目录视为停用（官方 load_plugins 约定）；同名模块只保留
    目录名字典序第一个，其余忽略。
    """
    if not base.is_dir():
        return []
    candidates: list[tuple[str, str, Path]] = []
    for d in sorted(
        p for p in base.iterdir() if p.is_dir() and not p.name.startswith("_")
    ):
        if (d / "__init__.py").is_file():
            if d.name.isidentifier():
                candidates.append((d.name, d.name, base))
            else:
                logger.warning(
                    f"awmc_plugins/{d.name}：平铺布局要求目录名为合法 Python"
                    " 标识符，已跳过（src 布局不受此限）"
                )
            continue
        src = d / "src"
        if src.is_dir():
            for pkg in sorted(src.iterdir()):
                if (
                    pkg.is_dir()
                    and (pkg / "__init__.py").is_file()
                    and pkg.name.isidentifier()
                ):
                    candidates.append((d.name, pkg.name, src))
    seen: set[str] = set()
    unique: list[tuple[str, str, Path]] = []
    for top, module_name, entry in candidates:
        if module_name not in seen:
            seen.add(module_name)
            unique.append((top, module_name, entry))
    return unique


def load_extra_plugins(base: Path, disabled: set[str]) -> list[str]:
    """发现并加载 base 下的第三方子插件，返回成功加载的模块名。

    一级目录名或模块名命中 disabled（awmc_disabled_plugins）即停用，
    与内置子插件按目录名停用的语义对齐；sys.path 用 append 追加，
    避免遮蔽 site-packages 已安装的同名包。
    """
    loaded: list[str] = []
    for top, module_name, path_entry in discover_extra_plugins(base):
        if top in disabled or module_name in disabled:
            logger.info(f"awmc_plugins/{top} 已按 awmc_disabled_plugins 停用")
            continue
        if str(path_entry) not in sys.path:
            sys.path.append(str(path_entry))
        if load_plugin(module_name) is not None:
            logger.info(f"awmc_plugins/{top} 已作为第三方子插件加载（{module_name}）")
            loaded.append(module_name)
        else:
            logger.warning(f"awmc_plugins/{top}（{module_name}）加载失败，已跳过")
    return loaded
