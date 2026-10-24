"""awmc.chart_preview 谱面预览子插件测试。

- matcher 流程：patch preview 层的 build_preview/cached_path（不真渲染），
  断言 定位/多命中列表/渲染提示与视频发送 链路；曲库 = mocks.sample_songs
  （199 チルノ，SD/DX/宴多条目）。
- 编排（build_preview）：respx mock assets2（谱面 = 真实快照 199.txt，
  音频 = ffmpeg 生成静音 mp3）+ 真渲染 2.5s 限长（无 ffmpeg 跳过）。
"""

import shutil
from pathlib import Path

import respx
import pytest
import nonebot
from mocks import sample_songs
from nonebug import App
from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

SNAPSHOTS = Path(__file__).parent / "data" / "snapshots" / "simai"
BASE = "https://assets2.lxns.net"


@pytest.fixture
async def db(tmp_path):
    from nonebot_plugin_awmc_helper.core import store

    store.set_db_file(tmp_path / "awmc.db")
    await store.init_db()
    yield
    store.set_db_file(None)


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    from nonebot_plugin_awmc_helper.plugins.chart_preview import preview

    target = tmp_path / "preview_cache"
    monkeypatch.setattr(preview, "cache_dir", lambda: target)
    return target


@pytest.fixture
def songs():
    return sample_songs()


@pytest.fixture
def limit_short(monkeypatch):
    """渲染限长 2.5s（编排用真渲染提速）。"""
    from nonebot_plugin_awmc_helper.config import plugin_config

    monkeypatch.setattr(plugin_config, "awmc_chart_preview_limit_seconds", 2.5)


@pytest.mark.asyncio
async def test_command_not_found(app: App, db, cache_dir, songs):
    """未命中 → 未找到文案。"""
    from fake import fake_group_message_event_v11

    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.plugins.chart_preview import chart_preview

    await song_service.inject(songs)
    async with app.test_matcher(chart_preview) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter))
        event = fake_group_message_event_v11(message="谱面预览 不存在的曲名xyz")
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            Message(
                [
                    MessageSegment.at(event.user_id),
                    MessageSegment.text(
                        " 未找到该歌曲，请确认曲名/别名，或先用「查歌」确认 id 后用"
                        "「谱面预览 id xxxxx」指定"
                    ),
                ]
            ),
            result=None,
            bot=bot,
        )
        ctx.should_finished()


@pytest.mark.asyncio
async def test_command_multi_hits_lists_ids(app: App, db, cache_dir, songs):
    """别名命中多条目（199 宿主：SD/DX/宴）→ 列表并提示用 id 指定。"""
    from fake import fake_group_message_event_v11

    from nonebot_plugin_awmc_helper.constants import display_song_id
    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.plugins.chart_preview import chart_preview

    await song_service.inject(songs)
    async with app.test_matcher(chart_preview) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter))
        event = fake_group_message_event_v11(message="谱面预览 琪露诺")
        ctx.receive_event(bot, event)
        # 期望列表由同一数据源动态构造（199 宿主的多条目展开）
        entries = await song_service.entries_for_name("琪露诺")
        lines = "找到多首匹配曲目，请用 id 指定：\n" + "\n".join(
            f"「{display_song_id(s)}」{s.title}" for _i, s, _p in entries[:10]
        )
        ctx.should_call_send(
            event,
            Message(
                [MessageSegment.at(event.user_id), MessageSegment.text(f" {lines}")]
            ),
            result=None,
            bot=bot,
        )
        ctx.should_finished()


@pytest.mark.asyncio
async def test_command_single_hit_sends_video(
    app: App, db, cache_dir, songs, monkeypatch, tmp_path
):
    """单命中：渲染提示 + 视频发送（build_preview 打桩返回临时文件）。"""

    from fake import fake_group_message_event_v11

    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.plugins.chart_preview import matchers
    from nonebot_plugin_awmc_helper.plugins.chart_preview.__init__ import chart_preview

    fake_out = tmp_path / "fake_preview.mp4"
    fake_out.write_bytes(b"\x00" * 128)

    async def fake_build(entry, index):
        assert index == 3  # 默认 Master
        return fake_out, "13+"

    monkeypatch.setattr(matchers, "build_preview", fake_build)
    await song_service.inject(songs)
    async with app.test_matcher(chart_preview) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter))
        event = fake_group_message_event_v11(message="谱面预览 8")
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            Message(
                [
                    MessageSegment.at(event.user_id),
                    MessageSegment.text(" 谱面预览渲染中，整曲约需 1 分钟，请稍等…"),
                ]
            ),
            result=None,
            bot=bot,
        )
        ctx.should_call_send(
            event,
            Message(
                [
                    MessageSegment.at(event.user_id),
                    MessageSegment.video(f"file://{fake_out}"),
                ]
            ),
            result=None,
            bot=bot,
        )
        ctx.should_finished()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="需要 ffmpeg")
@pytest.mark.asyncio
async def test_build_preview_orchestration(
    app: App, db, cache_dir, songs, limit_short, tmp_path
):
    """编排链路：respx 命中真实 199 快照谱面 + 静音音频 → 产出 mp4 落缓存。"""
    import subprocess

    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.types import SongType
    from nonebot_plugin_awmc_helper.plugins.chart_preview.preview import build_preview

    await song_service.inject(songs)
    # 199 的 SD 条目（display_id=199, prefer=STANDARD）
    song_8 = next(s for s in songs if s.id == 8)
    entry = (8, song_8, SongType.STANDARD)

    silence = tmp_path / "silence.mp3"
    subprocess.run(  # noqa: ASYNC221 — 测试夹具一次性调用
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=stereo",
            "-t",
            "3",
            "-b:a",
            "32k",
            str(silence),
        ],
        check=True,
    )
    with respx.mock(assert_all_called=False) as m:
        m.get(f"{BASE}/maimai/chart/8.txt").respond(
            text=(SNAPSHOTS / "199.txt").read_text()
        )
        m.get(f"{BASE}/maimai/music/8.mp3").respond(content=silence.read_bytes())
        out, label = await build_preview(entry, 2)  # EXPERT

    assert out.exists()
    assert out.stat().st_size > 10_000
    assert out.parent == cache_dir
    assert label == "10"  # TH SD EXPERT（样例库真实值）
    # 二次调用命中缓存（respx 未再放行即触网必失败）
    out2, _ = await build_preview(entry, 2)
    assert out2 == out
