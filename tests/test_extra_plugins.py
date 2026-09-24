"""固定目录 awmc_plugins/ 第三方子插件加载器测试。

用 tmp 目录构造 require 形态的假第三方插件（与示例插件同款写法），
覆盖发现/加载/停用/隔离各分支；加载走 nonebot.load_plugin 真实链路。
"""

import sys
import importlib
import importlib.util
from pathlib import Path

import pytest
from nonebug import App

_FLAT_SRC = '''\
"""测试用平铺第三方子插件。"""
from nonebot import require

require("nonebot_plugin_awmc_helper")

from nonebot import on_command
from nonebot.plugin import PluginMetadata
from nonebot_plugin_alconna.uniseg import UniMessage
from nonebot_plugin_awmc_helper.core.utils import handle_errors

__plugin_meta__ = PluginMetadata(
    name="{meta_name}",
    description="",
    usage="",
    type="application",
    homepage="https://example.com",
)

cmd = on_command("{command}", block=True)


@cmd.handle()
@handle_errors()
async def _():
    await UniMessage.text(" {reply}").finish()
'''


def _flat_src(meta_name: str, command: str, reply: str) -> str:
    return _FLAT_SRC.format(meta_name=meta_name, command=command, reply=reply)


def _make_flat(root: Path, dirname: str, code: str) -> Path:
    d = root / dirname
    d.mkdir(parents=True)
    (d / "__init__.py").write_text(code, encoding="utf-8")
    return d


def _make_src(root: Path, dirname: str, pkgname: str, code: str) -> Path:
    pkg = root / dirname / "src" / pkgname
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(code, encoding="utf-8")


async def _assert_reply(app: App, matcher, text: str, reply: str):
    """驱动假插件的 matcher，断言纯文本回复（handler 无 UniSession，无需 mock API）。"""
    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    event = fake_group_message_event_v11(message=text)
    async with app.test_matcher(matcher) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter))
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event, Message([MessageSegment.text(f" {reply}")]), result=None, bot=bot
        )
        ctx.should_finished()


def test_discover_missing_dir(tmp_path):
    from nonebot_plugin_awmc_helper.plugin_loader import discover_extra_plugins

    assert discover_extra_plugins(tmp_path / "awmc_plugins") == []


def test_discover_skips(tmp_path):
    """``_`` 前缀目录与非法标识符的平铺目录都不作为候选。"""
    from nonebot_plugin_awmc_helper.plugin_loader import discover_extra_plugins

    _make_flat(tmp_path, "_hidden", _flat_src("awmcx-hidden", "x", "x"))
    _make_flat(tmp_path, "not-valid-name", _flat_src("awmcx-invalid", "x", "x"))
    assert discover_extra_plugins(tmp_path) == []


@pytest.mark.asyncio
async def test_load_flat_and_reply(app: App, tmp_path):
    """平铺布局：发现 → 加载为独立插件 → 指令可响应。"""
    from nonebot_plugin_awmc_helper.plugin_loader import (
        load_extra_plugins,
        discover_extra_plugins,
    )

    _make_flat(
        tmp_path, "awmcx_f1", _flat_src("awmcx-flat-f1", "awmcx_f1_cmd", "flat-ok")
    )
    assert discover_extra_plugins(tmp_path) == [("awmcx_f1", "awmcx_f1", tmp_path)]
    assert load_extra_plugins(tmp_path, set()) == ["awmcx_f1"]

    mod = importlib.import_module("awmcx_f1")
    await _assert_reply(app, mod.cmd, "awmcx_f1_cmd", "flat-ok")


@pytest.mark.asyncio
async def test_load_src_layout_and_reply(app: App, tmp_path):
    """src 布局：clone 整仓（外层目录名任意）→ 按包名加载。"""
    from nonebot_plugin_awmc_helper.plugin_loader import load_extra_plugins

    _make_src(
        tmp_path,
        "some-repo",
        "awmcx_s1",
        _flat_src("awmcx-flat-s1", "awmcx_s1_cmd", "src-ok"),
    )
    assert load_extra_plugins(tmp_path, set()) == ["awmcx_s1"]

    mod = importlib.import_module("awmcx_s1")
    await _assert_reply(app, mod.cmd, "awmcx_s1_cmd", "src-ok")


def test_disabled_by_dir_and_module_name(tmp_path):
    """awmc_disabled_plugins 命中一级目录名或模块名均停用。

    平铺布局下目录名=模块名（按目录名命中）；src 布局目录名≠模块名，
    用它验证按模块名命中。
    """
    from nonebot_plugin_awmc_helper.plugin_loader import load_extra_plugins

    _make_flat(tmp_path, "awmcx_d1", _flat_src("awmcx-d1", "x", "x"))
    _make_src(tmp_path, "awmcx_d2repo", "awmcx_d2m", _flat_src("awmcx-d2m", "x", "x"))
    loaded = load_extra_plugins(tmp_path, {"awmcx_d1", "awmcx_d2m"})
    assert loaded == []
    assert "awmcx_d1" not in sys.modules
    assert "awmcx_d2m" not in sys.modules


def test_bad_plugin_isolated(tmp_path):
    """导入即炸的插件只记日志跳过，不抛出、不影响返回值结构。"""
    from nonebot_plugin_awmc_helper.plugin_loader import load_extra_plugins

    _make_flat(tmp_path, "awmcx_bad", "raise RuntimeError('boom')\n")
    assert load_extra_plugins(tmp_path, set()) == []
    assert "awmcx_bad" not in sys.modules


def test_duplicate_module_name_keeps_first(tmp_path):
    """两个目录声明同名模块时只保留字典序第一个，避免重复加载。"""
    from nonebot_plugin_awmc_helper.plugin_loader import discover_extra_plugins

    _make_src(tmp_path, "repo_b", "awmcx_dup", "")
    _make_src(tmp_path, "repo_a", "awmcx_dup", "")
    result = discover_extra_plugins(tmp_path)
    assert len(result) == 1
    top, module_name, _entry = result[0]
    assert (top, module_name) == ("repo_a", "awmcx_dup")


@pytest.mark.asyncio
async def test_unused_import_paths_not_polluted(tmp_path):
    """discover 不做任何加载（纯函数），不往 sys.path 塞条目。"""
    from nonebot_plugin_awmc_helper.plugin_loader import discover_extra_plugins

    before = set(sys.path)
    _make_flat(tmp_path, "awmcx_never", _flat_src("awmcx-never", "x", "x"))
    assert discover_extra_plugins(tmp_path) != []
    assert set(sys.path) == before
    assert importlib.util.find_spec("awmcx_never") is None
