"""core.help 帮助注册表与页面装配单元测试（M10）。

导入一律收在函数内：模块顶层 import 会触发插件加载链，而 nonebot 要到
conftest 的 session fixture 才 init（与仓内其他测试口径一致）。
"""

import pytest
from nonebug import App


class _FakeMatcher:
    """matcher 替身：注册表只以 id() 为键，无需真实 Matcher。"""


def _registry():
    from nonebot_plugin_awmc_helper.core.help import HelpRegistry

    return HelpRegistry()


def _spec(name: str, **kw):
    from nonebot_plugin_awmc_helper.core.help import CommandSpec

    return CommandSpec(matcher=_FakeMatcher(), name=name, **kw)


def _guide(key: str, steps=None, **kw):
    from nonebot_plugin_awmc_helper.core.help import Guide, GuideStep

    return Guide(
        key=key,
        title=key,
        intro=kw.pop("intro", "i"),
        prerequisites=kw.pop("prerequisites", "p"),
        steps=steps if steps is not None else [GuideStep(text="t")],
        source="x",
        **kw,
    )


def test_declare_and_resolve_order():
    """消歧顺序：指令 > 类别 > 指南；空查询=总览；未命中=NotFound。"""
    from nonebot_plugin_awmc_helper.core.help import (
        GuidePage,
        CommandPage,
        CategoryPage,
        NotFoundPage,
        OverviewPage,
    )

    reg = _registry()
    reg.declare(
        plugin="x",
        title="X",
        category="query",
        commands=[_spec("cmd1", aliases=("c1",), brief="b")],
    )
    reg.declare_guide(_guide("g1"))
    assert isinstance(reg.resolve("cmd1"), CommandPage)
    assert isinstance(reg.resolve("c1"), CommandPage)
    assert isinstance(reg.resolve("查歌"), CategoryPage)  # 内置类别按标题可查
    assert isinstance(reg.resolve("g1"), GuidePage)
    assert isinstance(reg.resolve(""), OverviewPage)
    assert isinstance(reg.resolve("不存在"), NotFoundPage)


def test_command_beats_category_and_guide():
    """同名冲突时指令优先（拍板：消歧顺序 指令 > 类别 > 指南）。"""
    from nonebot_plugin_awmc_helper.core.help import CommandPage

    reg = _registry()
    reg.declare(
        plugin="x",
        title="X",
        category="query",
        commands=[_spec("导分", brief="同名指令")],
    )
    reg.declare_guide(_guide("导分"))
    assert isinstance(reg.resolve("导分"), CommandPage)


def test_hidden_visibility():
    """hidden 指令：普通用户不可见不可查；超管（include_hidden）可见可查。"""
    from nonebot_plugin_awmc_helper.core.help import (
        CommandPage,
        NotFoundPage,
        OverviewPage,
        page_entries,
    )

    reg = _registry()
    reg.declare(
        plugin="x",
        title="X",
        category="tools",
        commands=[_spec("密令", hidden=True, brief="管理用"), _spec("明令")],
    )
    assert isinstance(reg.resolve("密令"), NotFoundPage)
    assert isinstance(reg.resolve("密令", include_hidden=True), CommandPage)

    overview = page_entries(reg, OverviewPage(include_hidden=False))
    assert all("密令" not in b for b in overview)
    overview_su = page_entries(reg, OverviewPage(include_hidden=True))
    assert any("密令" in b for b in overview_su)  # 超管总览追加「管理」节点


def test_name_conflict_keeps_first():
    """跨插件指令名冲突：保留先注册者并告警（不抛异常）。"""
    reg = _registry()
    first = _spec("同名", brief="first")
    reg.declare(plugin="a", title="A", category="tools", commands=[first])
    reg.declare(plugin="b", title="B", category="query", commands=[_spec("同名")])
    assert reg.lookup_command("同名") is first


def test_guide_validation_rejects_shell():
    """空壳流程拒收：无步骤或 intro/prerequisites 双空直接抛错。"""
    reg = _registry()
    with pytest.raises(ValueError, match="空壳流程"):
        reg.declare_guide(_guide("空", intro="", prerequisites="", steps=[]))
    with pytest.raises(ValueError, match="空壳流程"):
        reg.declare_guide(_guide("无步骤", intro="有简介", steps=[]))


def test_unknown_category_autocreate():
    """第三方未知类别键自动创建（builtin=False），总览可见。"""
    from nonebot_plugin_awmc_helper.core.help import OverviewPage, page_entries

    reg = _registry()
    reg.declare(plugin="t", title="T", category="自定义类", commands=[_spec("t1")])
    assert reg.categories["自定义类"].builtin is False
    overview = "\n".join(page_entries(reg, OverviewPage(include_hidden=False)))
    assert "自定义类" in overview


def test_guide_missing_command_marked():
    """指南引用未注册指令：渲染不崩，标注当前不可用。"""
    from nonebot_plugin_awmc_helper.core.help import GuidePage, GuideStep, page_entries

    reg = _registry()
    reg.declare_guide(_guide("g", steps=[GuideStep(text="s", commands=("幽灵指令",))]))
    entries = page_entries(reg, GuidePage(guide=reg.guides["g"]))
    assert any("当前不可用" in e for e in entries)


def test_guide_backlink_on_command_page():
    """反向索引：指令详情页自动回链所属指南与步骤号。"""
    from nonebot_plugin_awmc_helper.core.help import (
        GuideStep,
        CommandPage,
        page_entries,
    )

    reg = _registry()
    reg.declare(plugin="x", title="X", category="tools", commands=[_spec("步骤指令")])
    reg.declare_guide(_guide("g", steps=[GuideStep(text="s", commands=("步骤指令",))]))
    page = page_entries(reg, CommandPage(spec=reg.lookup_command("步骤指令")))
    assert any("属于指南" in b and "第 1 步" in b for b in page)


def test_image_step_yields_unimessage_node():
    """附图步骤：独立纯图节点（UniMessage），其余为文本节点。"""
    from nonebot_plugin_alconna.uniseg import UniMessage

    from nonebot_plugin_awmc_helper.core.help import GuidePage, GuideStep, page_entries

    reg = _registry()
    reg.declare_guide(
        _guide("g", steps=[GuideStep(text="看图"), GuideStep(image="x.jpg")])
    )
    entries = page_entries(reg, GuidePage(guide=reg.guides["g"]))
    assert isinstance(entries[0], str)
    assert isinstance(entries[2], UniMessage)


def test_page_text_flattens():
    """降级素材：page_text 把节点序列拍平为纯文本。"""
    from nonebot_plugin_awmc_helper.core.help import CategoryPage, page_text

    reg = _registry()
    reg.declare(
        plugin="x", title="X", category="tools", commands=[_spec("t1", brief="b")]
    )
    text = page_text(reg, CategoryPage(reg.categories["tools"]))
    assert "t1" in text
    assert "b" in text


def test_singleton_builtin_guide_and_live_registry():
    """进程单例：内建指南已装载；全插件加载后真实指令在索引中。"""
    from nonebot_plugin_awmc_helper.core.help import help_registry

    assert "查分上手" in help_registry.guides
    assert help_registry.lookup_command("b50") is not None
    assert help_registry.lookup_command("绑定水鱼token") is not None


def test_builtin_categories_cover_blocks():
    """内置类别表完整：所有内置子插件声明的类别都在表内（无意外自动建类）。"""
    from nonebot_plugin_awmc_helper.core.help import help_registry

    live = {b.category for b in help_registry.blocks if b.plugin.startswith("awmc.")}
    for key in live:
        cat = help_registry.categories.get(key)
        assert cat is not None, f"内置子插件使用了未知类别：{key}"
        assert cat.builtin, f"内置子插件类别应属内置表：{key}"


@pytest.mark.asyncio
async def test_no_orphan_matchers(app: App):
    """守卫：内置子插件的全部 matcher 必有帮助声明或列入内部白名单。"""
    import nonebot

    from nonebot_plugin_awmc_helper.core.help import help_registry
    from nonebot_plugin_awmc_helper.plugins.bind.matchers import bind_code_fill
    from nonebot_plugin_awmc_helper.plugins.guess.matchers import guess_answer

    allowed = {id(bind_code_fill), id(guess_answer)}
    missing = []
    for p in nonebot.get_loaded_plugins():
        if not (p.metadata and p.metadata.name.startswith("awmc.")):
            continue
        for m in p.matcher:
            if help_registry.spec_of(m) is None and id(m) not in allowed:
                missing.append(f"{p.metadata.name}#<matcher {id(m):#x}>")
    assert not missing, f"未声明帮助的 matcher：{missing}"
