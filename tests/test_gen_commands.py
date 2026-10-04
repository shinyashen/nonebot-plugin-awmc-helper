"""docs/commands.md 防漂移测试（M10 后置项：注册表生成器）。

docs/commands.md 由帮助注册表经 core/help_md.registry_markdown 生成
（uv run poe gen-commands），本测试把生成结果与库内文档逐字节锁死：
改了声明块忘重新生成 → 这里红；手工改了文档 → 这里也红。

导入一律收在函数内：模块顶层 import 会触发插件加载链，而 nonebot 要到
conftest 的 session fixture 才 init（与仓内其他测试口径一致）。
"""

from pathlib import Path

# 全部内置子插件：文档必须逐个覆盖（防加载失败静默缩水）。清单动态取自
# plugins/ 目录（真源是主插件 __init__ 的自动发现，不手抄）
_PLUGIN_DIR = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "nonebot_plugin_awmc_helper"
    / "plugins"
)
SUBPLUGINS = tuple(
    sorted(
        d.name
        for d in _PLUGIN_DIR.iterdir()
        if d.is_dir() and (d / "__init__.py").exists()
    )
)

_DOC = Path(__file__).resolve().parent.parent / "docs" / "commands.md"


def _registry_markdown() -> str:
    from nonebot_plugin_awmc_helper.core.help import help_registry
    from nonebot_plugin_awmc_helper.core.help_md import registry_markdown

    return registry_markdown(help_registry)


def test_commands_md_matches_registry():
    """文档与注册表渲染逐字节一致；不一致先跑 uv run poe gen-commands。"""
    actual = _DOC.read_text(encoding="utf-8")
    expected = _registry_markdown()
    assert actual == expected, (
        "docs/commands.md 与帮助注册表不一致："
        "运行 uv run poe gen-commands 重新生成（勿手工改文档）"
    )


def test_commands_md_covers_all_subplugins():
    """13 个内置子插件的声明块全部进了文档（加载失败即在此显式失败）。"""
    from nonebot_plugin_awmc_helper.core.help import help_registry

    plugins = {b.plugin for b in help_registry.blocks}
    missing = [n for n in SUBPLUGINS if f"awmc.{n}" not in plugins]
    assert not missing, f"子插件帮助块缺失（未加载或未声明）：{missing}"
    doc = _DOC.read_text(encoding="utf-8")
    for name in SUBPLUGINS:
        assert f"（{name}）" in doc, f"docs/commands.md 缺少子插件 {name} 的分组"


def test_registry_markdown_is_stable():
    """渲染为纯函数：同一注册表两次输出一致（生成可重入、无隐藏状态）。"""
    assert _registry_markdown() == _registry_markdown()
