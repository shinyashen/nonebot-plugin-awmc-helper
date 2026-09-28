"""core/ext/otoge_pr：自动化 PR 分支预读（分层修复 + main 外新条目提取）。"""

import json

import httpx
import respx
import pytest


@pytest.fixture
def otoge_pr():
    from nonebot_plugin_awmc_helper.core.ext import otoge_pr as mod

    return mod


MAIN = [
    {"title": "Old Song", "image_url": "old.png", "version": "25000"},
    {"title": "Tell Your World", "image_url": "tyw.png", "version": "11003"},
]

NEW_ENTRY = {
    "title": "The Happycore Idol",
    "image_url": "922bd88061ebb54f.png",
    "version": "27001",
    "date_added": "20260925",
}


def _branch_text_with_field_conflict() -> str:
    """模拟 #1206 实测损坏：文件尾部对象内部字段级冲突（ours/stash 两值）。"""
    head = json.dumps([*MAIN, NEW_ENTRY], ensure_ascii=False, indent=2)
    broken = head.replace(
        json.dumps(NEW_ENTRY["version"]),
        '<<<<<<< Updated upstream\n    "27001",\n=======\n'
        '    "27001-bot",\n>>>>>>> Stashed changes',
    )
    assert "<<<<<<<" in broken
    return broken


def test_parse_strict_layer(otoge_pr):
    entries, layer = otoge_pr.parse_music_ex(json.dumps(MAIN))
    assert layer == "strict"
    assert len(entries) == 2


def test_parse_markers_layer_keeps_branch_side(otoge_pr):
    entries, layer = otoge_pr.parse_music_ex(_branch_text_with_field_conflict())
    assert layer == "markers"
    target = next(e for e in entries if e["title"] == "The Happycore Idol")
    assert target["version"] == "27001-bot"  # 分支侧（theirs）
    assert target["date_added"] == "20260925"


def test_parse_salvage_layer_recovers_intact_objects(otoge_pr):
    broken = (
        "服务器错误 HTML <html>_prefix_garbage{\n"
        + json.dumps(MAIN[0], ensure_ascii=False)
        + "\n<<<<<<< garbage\n"
        + json.dumps(MAIN[1], ensure_ascii=False)
        + "\n]\n"
    )
    parsed = otoge_pr.parse_music_ex(broken)
    assert parsed is not None
    entries, layer = parsed
    assert layer == "salvage"
    # 失败跨度前移重扫：垃圾 `{` 吞掉的首对象也能被内层救回
    assert {e["title"] for e in entries} == {"Old Song", "Tell Your World"}


def test_parse_unrecoverable_returns_none(otoge_pr):
    assert otoge_pr.parse_music_ex("not json at all {{{") is None


def test_extract_new_entries_dedup(otoge_pr):
    entries = [*MAIN, NEW_ENTRY, dict(NEW_ENTRY)]
    new = otoge_pr.extract_new_entries(entries, {e["title"] for e in MAIN})
    assert [e["title"] for e in new] == [NEW_ENTRY["title"]]


@respx.mock
async def test_load_open_pr_new_entries(otoge_pr):
    respx.get("https://api.github.com/repos/zvuc/otoge-db/pulls").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "number": 1206,
                    "title": "[Automation] maimai: Add new songs (20260925)",
                    "head": {"ref": "maimai/update-20260925"},
                },
                {  # 非 maimai/update-* 分支应被过滤
                    "number": 1208,
                    "title": "CHUNITHM constants",
                    "head": {"ref": "chunithm/constants"},
                },
            ],
        )
    )
    respx.get(
        "https://raw.githubusercontent.com/zvuc/otoge-db/main/maimai/data/music-ex.json"
    ).mock(return_value=httpx.Response(200, text=json.dumps(MAIN, ensure_ascii=False)))
    respx.get(
        "https://raw.githubusercontent.com/zvuc/otoge-db/"
        "maimai/update-20260925/maimai/data/music-ex.json"
    ).mock(return_value=httpx.Response(200, text=_branch_text_with_field_conflict()))
    new = await otoge_pr.load_open_pr_new_entries()
    assert [e["title"] for e in new] == [NEW_ENTRY["title"]]
    assert new[0]["image_url"] == NEW_ENTRY["image_url"]


@respx.mock
async def test_load_open_pr_guards_entry_count(otoge_pr):
    """分支条数少于 main（损坏）→ 合理性门拦截，不产出。"""
    respx.get("https://api.github.com/repos/zvuc/otoge-db/pulls").mock(
        return_value=httpx.Response(
            200,
            json=[{"number": 1, "title": "x", "head": {"ref": "maimai/update-1"}}],
        )
    )
    respx.get(
        "https://raw.githubusercontent.com/zvuc/otoge-db/main/maimai/data/music-ex.json"
    ).mock(return_value=httpx.Response(200, text=json.dumps(MAIN, ensure_ascii=False)))
    respx.get(
        url__startswith="https://raw.githubusercontent.com/zvuc/otoge-db/maimai/update-1/"
    ).mock(
        return_value=httpx.Response(
            200, text=json.dumps([NEW_ENTRY], ensure_ascii=False)
        )
    )
    assert await otoge_pr.load_open_pr_new_entries() == []
