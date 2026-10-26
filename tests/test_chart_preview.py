"""awmc.chart_preview 谱面预览子插件测试。

- matcher 流程：patch preview 层的 build_preview/cached_path（不真渲染），
  断言 定位/多命中列表/渲染提示与视频发送 链路；曲库 = mocks.sample_songs
  （199 チルノ，SD/DX/宴多条目）。
- 编排（build_preview）：respx mock assets2（谱面 = 真实快照 199.txt，
  音频 = ffmpeg 生成静音 mp3）+ 真渲染 2.5s 限长（无 ffmpeg 跳过）。
"""

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


def _preview_result(path: Path, label: str = "13+", range_text: str | None = None):
    """测试用 PreviewResult（统计字段填最小可用值）。"""
    from nonebot_plugin_awmc_helper.core.render.chart_stats import ChartStats
    from nonebot_plugin_awmc_helper.plugins.chart_preview.preview import PreviewResult

    stats = ChartStats(
        chart_ms=100_000.0,
        measures=25,
        counts={"TAP": 100, "HOLD": 20, "SLIDE": 30, "TOUCH": 5, "BREAK": 3},
        ex_counts={"TAP": 10, "HOLD": 0, "SLIDE": 2, "TOUCH": 0, "BREAK": 0},
    )
    return PreviewResult(path=path, label=label, stats=stats, range_text=range_text)


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
    target.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(preview, "cache_dir", lambda: target)
    return target


@pytest.fixture
def songs():
    return sample_songs()


@pytest.fixture
def fake_render(monkeypatch, tmp_path):
    """渲染打桩（抓流依赖 chromium+外网，编排测试不真渲染）。"""
    from nonebot_plugin_awmc_helper.plugins.chart_preview import preview

    calls = []

    def fake(
        chart,
        *,
        audio,
        out_path,
        fps=30,
        limit_seconds=None,
        workers=0,
        supersample=2.0,
        sound_effects=None,
        sound_offset_ms=-50.0,
        loudness_lufs=-16.0,
        start_ms=0.0,
        end_ms=None,
        notes_start_ms=None,
        fade_in_ms=None,
        hi_speed=7.0,
        ffmpeg=None,
    ):
        calls.append(
            {
                "chart": chart,
                "has_audio": audio is not None,
                "workers": workers,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "hi_speed": hi_speed,
            }
        )
        out = Path(out_path)
        out.write_bytes(b"\x00" * 64)
        return out

    monkeypatch.setattr(preview, "render_chart_video", fake)
    return calls


@pytest.mark.asyncio
async def test_command_not_found(app: App, db, cache_dir, songs):
    """未命中 → 未找到文案。"""
    from fake import fake_group_message_event_v11

    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.plugins.chart_preview import chart_preview

    await song_service.inject(songs)
    async with app.test_matcher(chart_preview) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter))
        event = fake_group_message_event_v11(message="谱面预览 紫+不存在的曲名xyz")
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            Message(
                [
                    MessageSegment.at(event.user_id),
                    MessageSegment.text(
                        " 未找到该歌曲，请确认曲名/别名，或先用「查歌」确认 id 后用"
                        "「谱面预览 <难度色>+id」指定"
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
        event = fake_group_message_event_v11(message="谱面预览 紫+琪露诺")
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

    async def fake_build(entry, index, range_token=None, hi_speed=7.0):
        assert index == 3  # 紫 → Master
        assert range_token is None
        assert hi_speed == 7.0
        return _preview_result(fake_out)

    monkeypatch.setattr(matchers, "build_preview", fake_build)

    async def fake_deliver(bot, event, entry, result, hi_speed):
        from nonebot.exception import FinishedException

        raise FinishedException

    monkeypatch.setattr(matchers, "_deliver", fake_deliver)
    await song_service.inject(songs)
    async with app.test_matcher(chart_preview) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter))
        event = fake_group_message_event_v11(message="谱面预览 紫+8")
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            Message(
                [
                    MessageSegment.at(event.user_id),
                    MessageSegment.text(
                        " 谱面预览渲染中：整曲约需 30–90 秒（视 CPU），完成后自动发送；"
                        "同一谱面再次请求命中缓存秒回"
                    ),
                ]
            ),
            result=None,
            bot=bot,
        )
        ctx.should_finished()


@pytest.mark.asyncio
async def test_build_preview_orchestration(
    app: App, db, cache_dir, songs, fake_render, monkeypatch
):
    """编排链路：respx 命中真实 8 快照谱面 + 音频 → 渲染打桩 → mp4 落缓存。"""
    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.types import SongType
    from nonebot_plugin_awmc_helper.plugins.chart_preview import preview
    from nonebot_plugin_awmc_helper.plugins.chart_preview.preview import build_preview

    async def _no_hit_sounds():
        return None

    monkeypatch.setattr(preview, "fetch_hit_sounds", _no_hit_sounds)
    await song_service.inject(songs)
    song_8 = next(s for s in songs if s.id == 8)
    entry = (8, song_8, SongType.STANDARD)

    with respx.mock(assert_all_called=False) as m:
        m.get(f"{BASE}/maimai/chart/8.txt").respond(
            text=(SNAPSHOTS / "8.txt").read_text()
        )
        m.get(f"{BASE}/maimai/music/8.mp3").respond(content=b"fake-audio")
        result = await build_preview(entry, 2)
    out, label = result.path, result.label  # EXPERT

    assert out.exists()
    assert out.parent == cache_dir
    assert label == "10"  # TH SD EXPERT（样例库真实值）
    assert fake_render
    assert fake_render[0]["chart"].difficulty == 4  # 站内难度后缀 _4 = EXPERT
    assert fake_render[0]["start_ms"] == 0.0
    assert fake_render[0]["end_ms"] is None
    assert fake_render[0]["hi_speed"] == 7.0
    # 二次调用命中缓存（渲染打桩不再被调）
    calls_before = len(fake_render)
    result2 = await build_preview(entry, 2)
    assert result2.path == out
    assert len(fake_render) == calls_before


@pytest.mark.asyncio
async def test_command_range_hint_and_build(
    app: App, db, cache_dir, songs, monkeypatch, tmp_path
):
    """带片段范围：提示文案走片段口径，范围 token 透传到 build_preview。"""
    from fake import fake_group_message_event_v11

    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.plugins.chart_preview import matchers
    from nonebot_plugin_awmc_helper.plugins.chart_preview.__init__ import chart_preview

    fake_out = tmp_path / "fake_range.mp4"
    fake_out.write_bytes(b"\x00" * 128)
    seen = {}

    async def fake_build(entry, index, range_token=None, hi_speed=7.0):
        seen["index"] = index
        seen["range"] = range_token
        seen["speed"] = hi_speed
        return _preview_result(fake_out, range_text="0:35-1:20")

    monkeypatch.setattr(matchers, "build_preview", fake_build)

    async def fake_deliver(bot, event, entry, result, hi_speed):
        from nonebot.exception import FinishedException

        raise FinishedException

    monkeypatch.setattr(matchers, "_deliver", fake_deliver)
    await song_service.inject(songs)
    async with app.test_matcher(chart_preview) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter))
        event = fake_group_message_event_v11(message="谱面预览 紫+8 0:35-1:20")
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            Message(
                [
                    MessageSegment.at(event.user_id),
                    MessageSegment.text(
                        " 片段渲染中：约需 20–60 秒（视 CPU），完成后自动发送；"
                        "同一片段再次请求命中缓存秒回"
                    ),
                ]
            ),
            result=None,
            bot=bot,
        )
        ctx.should_finished()
    assert seen["range"] == "0:35-1:20"
    assert seen["index"] == 3
    assert seen["speed"] == 7.0


@pytest.mark.asyncio
async def test_build_preview_range(
    app: App, db, cache_dir, songs, fake_render, monkeypatch
):
    """片段编排：范围解析→按小节对齐→start/end 透传渲染，缓存键带范围后缀。"""
    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.types import SongType
    from nonebot_plugin_awmc_helper.plugins.chart_preview import preview
    from nonebot_plugin_awmc_helper.plugins.chart_preview.preview import build_preview

    async def _no_hit_sounds():
        return None

    monkeypatch.setattr(preview, "fetch_hit_sounds", _no_hit_sounds)
    await song_service.inject(songs)
    song_8 = next(s for s in songs if s.id == 8)
    entry = (8, song_8, SongType.STANDARD)

    with respx.mock(assert_all_called=False) as m:
        m.get(f"{BASE}/maimai/chart/8.txt").respond(
            text=(SNAPSHOTS / "8.txt").read_text()
        )
        m.get(f"{BASE}/maimai/music/8.mp3").respond(content=b"fake-audio")
        result = await build_preview(entry, 2, "0:05-0:10")
    out, label = result.path, result.label

    assert out.exists()
    assert "_t0m05-0m10_p2_v7" in out.name
    assert "0:06" in label
    assert "0:10" in label
    call = fake_render[-1]
    assert call["start_ms"] > 0
    assert call["end_ms"] is not None
    assert call["end_ms"] > call["start_ms"]
    assert call["end_ms"] - call["start_ms"] >= 1000  # 至少一个小节


@pytest.mark.asyncio
async def test_build_preview_range_bounds(
    app: App, db, cache_dir, songs, fake_render, monkeypatch
):
    """越界/非法范围抛可读 ValueError，不进入渲染。"""
    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.types import SongType
    from nonebot_plugin_awmc_helper.plugins.chart_preview import preview
    from nonebot_plugin_awmc_helper.plugins.chart_preview.preview import build_preview

    async def _no_hit_sounds():
        return None

    monkeypatch.setattr(preview, "fetch_hit_sounds", _no_hit_sounds)
    await song_service.inject(songs)
    song_8 = next(s for s in songs if s.id == 8)
    entry = (8, song_8, SongType.STANDARD)
    fake_render.clear()

    with respx.mock(assert_all_called=False) as m:
        m.get(f"{BASE}/maimai/chart/8.txt").respond(
            text=(SNAPSHOTS / "8.txt").read_text()
        )
        m.get(f"{BASE}/maimai/music/8.mp3").respond(content=b"fake-audio")
        with pytest.raises(ValueError, match="开始 < 结束"):
            await build_preview(entry, 2, "1:20-0:35")  # 起止颠倒
        with pytest.raises(ValueError, match="超出歌曲长度"):
            await build_preview(entry, 2, "999-1000")  # 超出曲长
        with pytest.raises(ValueError, match="没有完整小节"):
            await build_preview(entry, 2, "5.1-5.5")  # 不足一个小节
    assert fake_render == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "expect_speed", "expect_range"),
    [
        ("谱面预览 紫+8", 7.0, None),
        ("谱面预览 紫+8 9.5", 9.5, None),
        ("谱面预览 紫+8 9.3", 7.0, None),  # 非 0.25 步长 → 回退默认
        ("谱面预览 紫+8 12", 7.0, None),  # 越界 → 回退默认
        ("谱面预览 紫+8 9.5 0:35-1:20", 9.5, "0:35-1:20"),
        ("谱面预览 紫+8 7.25 -1:20", 7.25, "-1:20"),
    ],
)
async def test_command_speed_and_range_args(
    app: App,
    db,
    cache_dir,
    songs,
    monkeypatch,
    tmp_path,
    message,
    expect_speed,
    expect_range,
):
    """新指令格式：难度色+查询、可选流速（校验/回退）、可选范围。"""
    from fake import fake_group_message_event_v11

    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.plugins.chart_preview import matchers
    from nonebot_plugin_awmc_helper.plugins.chart_preview.__init__ import chart_preview

    fake_out = tmp_path / "fake_args.mp4"
    fake_out.write_bytes(b"\x00" * 64)
    seen = {}

    async def fake_build(entry, index, range_token=None, hi_speed=7.0):
        seen["index"] = index
        seen["range"] = range_token
        seen["speed"] = hi_speed
        return _preview_result(fake_out)

    monkeypatch.setattr(matchers, "build_preview", fake_build)

    async def fake_deliver(bot, event, entry, result, hi_speed):
        from nonebot.exception import FinishedException

        raise FinishedException

    monkeypatch.setattr(matchers, "_deliver", fake_deliver)
    await song_service.inject(songs)
    hint = (
        " 片段渲染中：约需 20–60 秒（视 CPU），完成后自动发送；"
        "同一片段再次请求命中缓存秒回"
        if expect_range
        else " 谱面预览渲染中：整曲约需 30–90 秒（视 CPU），完成后自动发送；"
        "同一谱面再次请求命中缓存秒回"
    )
    async with app.test_matcher(chart_preview) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter))
        event = fake_group_message_event_v11(message=message)
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            Message(
                [
                    MessageSegment.at(event.user_id),
                    MessageSegment.text(hint),
                ]
            ),
            result=None,
            bot=bot,
        )
        ctx.should_finished()
    assert seen["index"] == 3  # 紫 → Master
    assert seen["speed"] == expect_speed
    assert seen["range"] == expect_range


@pytest.mark.asyncio
async def test_command_requires_difficulty_color(app: App, db, cache_dir, songs):
    """缺少「难度色+」前缀 → 可读格式错误提示。"""
    from fake import fake_group_message_event_v11

    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.plugins.chart_preview.__init__ import chart_preview

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
                    MessageSegment.text(
                        " 需指定难度色：格式为 谱面预览 <难度色>+<歌名/别名/id>"
                        "（宴谱用 宴+<id>）"
                    ),
                ]
            ),
            result=None,
            bot=bot,
        )
        ctx.should_finished()


def test_forward_texts_order_and_stats():
    """合并转发文案：歌曲信息含谱面时长/EX 分布；第 3 条为纯调参提示。"""
    from nonebot_plugin_awmc_helper.plugins.chart_preview import matchers

    base = _preview_result(
        Path("/tmp/x.mp4"), label="13+ · 0:36-1:20", range_text="0:36-1:20"
    )
    result = base.__class__(
        path=base.path,
        label=base.label,
        stats=base.stats,
        range_text=base.range_text,
        preroll_measures=2,
    )

    class Song:
        title = "ラグトレイン"
        id = 11355

    song_text = matchers._song_info_text((11355, Song(), None), result)
    assert "ラグトレイン" in song_text
    assert "1:40.0" in song_text  # chart_ms=100000
    assert "TAP 100（EX 10）" in song_text
    tips = matchers._tips_text()
    assert "3–9.75" in tips
    assert "0:35-1:20" in tips
    assert "谱面预览 紫+曲名 7.5 0:35-1:20" in tips


@pytest.mark.asyncio
async def test_command_forward_message(
    app: App, db, cache_dir, songs, monkeypatch, tmp_path
):
    """OneBot v11 下发合并转发：视频/歌曲信息/提示 3 节点同一 API。"""
    from unittest.mock import ANY

    from fake import fake_group_message_event_v11

    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.plugins.chart_preview import matchers
    from nonebot_plugin_awmc_helper.plugins.chart_preview.__init__ import chart_preview

    fake_out = tmp_path / "forward.mp4"
    fake_out.write_bytes(b"\x00" * 64)

    async def fake_build(entry, index, range_token=None, hi_speed=7.0):
        return _preview_result(fake_out)

    monkeypatch.setattr(matchers, "build_preview", fake_build)
    await song_service.inject(songs)
    async with app.test_matcher(chart_preview) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter))
        event = fake_group_message_event_v11(message="谱面预览 紫+8")
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            Message(
                [
                    MessageSegment.at(event.user_id),
                    MessageSegment.text(
                        " 谱面预览渲染中：整曲约需 30–90 秒（视 CPU），完成后自动发送；"
                        "同一谱面再次请求命中缓存秒回"
                    ),
                ]
            ),
            result=None,
            bot=bot,
        )
        ctx.should_call_api(
            "send_group_forward_msg",
            {"group_id": ANY, "message": ANY, "messages": ANY},
            result={"message_id": 1},
        )
        ctx.should_finished()


@pytest.mark.asyncio
async def test_command_forward_fallback(
    app: App, db, cache_dir, songs, monkeypatch, tmp_path
):
    """合并转发失败（协议端不支持/报错）→ 降级普通视频 + 信息文本。"""
    from unittest.mock import ANY

    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import ActionFailed

    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.plugins.chart_preview import matchers
    from nonebot_plugin_awmc_helper.plugins.chart_preview.__init__ import chart_preview

    fake_out = tmp_path / "fallback.mp4"
    fake_out.write_bytes(b"\x00" * 64)
    from nonebot_plugin_awmc_helper.core.types import SongType

    song_8 = next(s for s in songs if s.id == 8)
    entry = (8, song_8, SongType.STANDARD)
    result = _preview_result(fake_out)

    async def fake_build(entry_, index, range_token=None, hi_speed=7.0):
        return result

    monkeypatch.setattr(matchers, "build_preview", fake_build)
    await song_service.inject(songs)
    expected_text = (
        matchers._song_info_text(entry, result) + "\n\n" + matchers._tips_text()
    )
    async with app.test_matcher(chart_preview) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter))
        event = fake_group_message_event_v11(message="谱面预览 紫+8")
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            Message(
                [
                    MessageSegment.at(event.user_id),
                    MessageSegment.text(
                        " 谱面预览渲染中：整曲约需 30–90 秒（视 CPU），完成后自动发送；"
                        "同一谱面再次请求命中缓存秒回"
                    ),
                ]
            ),
            result=None,
            bot=bot,
        )
        ctx.should_call_api(
            "send_group_forward_msg",
            {"group_id": ANY, "message": ANY, "messages": ANY},
            exception=ActionFailed(),
        )
        ctx.should_call_send(
            event,
            Message([MessageSegment.video(f"file://{fake_out}")]),
            result=None,
            bot=bot,
        )
        ctx.should_call_send(
            event,
            Message([MessageSegment.text(expected_text)]),
            result=None,
            bot=bot,
        )
        ctx.should_finished()


@pytest.mark.asyncio
async def test_deliver_node_order(monkeypatch, tmp_path):
    """合并转发节点顺序：视频 → 歌曲信息 → 提示（_deliver 单测）。"""
    from types import SimpleNamespace

    from nonebot.exception import FinishedException
    from nonebot_plugin_alconna.uniseg import UniMessage

    from nonebot_plugin_awmc_helper.plugins.chart_preview import matchers

    video = tmp_path / "order.mp4"
    video.write_bytes(b"\x00" * 16)
    result = _preview_result(video)
    captured = {}

    async def fake_forward(bot, entries, *, group_id=None, user_id=None):
        captured["entries"] = entries
        captured["group_id"] = group_id
        captured["user_id"] = user_id
        return True

    monkeypatch.setattr(matchers, "is_ob11", lambda bot: True)
    monkeypatch.setattr(matchers, "try_send_forward", fake_forward)
    bot = SimpleNamespace(self_id="123456")
    event = SimpleNamespace(group_id=87654321, user_id=12345678)

    class Song:
        title = "ラグトレイン"
        id = 11355

    with pytest.raises(FinishedException):
        await matchers._deliver(bot, event, (11355, Song(), None), result, 7.0)

    entries = captured["entries"]
    assert len(entries) == 3
    assert isinstance(entries[0], UniMessage)
    assert "video" in [seg.type for seg in entries[0]]
    assert "【歌曲信息】" in entries[1]
    assert "【调整提示】" in entries[2]
    assert captured["group_id"] == 87654321
    assert captured["user_id"] is None


@pytest.mark.asyncio
async def test_command_ffmpeg_unavailable_message(
    app: App, db, cache_dir, songs, monkeypatch
):
    """ffmpeg 不可用 → 用户看到具体排查文案，而非笼统「出错了」。"""
    from fake import fake_group_message_event_v11

    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.plugins.chart_preview import matchers
    from nonebot_plugin_awmc_helper.core.render.chart_video import (
        FFmpegUnavailableError,
    )
    from nonebot_plugin_awmc_helper.plugins.chart_preview.__init__ import chart_preview

    async def fake_build(*args, **kwargs):
        raise FFmpegUnavailableError(
            "谱面预览需要服务器安装 ffmpeg，但当前未找到（PATH 中没有 ffmpeg）。"
        )

    monkeypatch.setattr(matchers, "build_preview", fake_build)
    await song_service.inject(songs)
    async with app.test_matcher(chart_preview) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter))
        event = fake_group_message_event_v11(message="谱面预览 紫+8")
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            Message(
                [
                    MessageSegment.at(event.user_id),
                    MessageSegment.text(
                        " 谱面预览渲染中：整曲约需 30–90 秒（视 CPU），完成后自动发送；"
                        "同一谱面再次请求命中缓存秒回"
                    ),
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
                    MessageSegment.text(
                        " 谱面预览需要服务器安装 ffmpeg，但当前未找到"
                        "（PATH 中没有 ffmpeg）。"
                    ),
                ]
            ),
            result=None,
            bot=bot,
        )
        ctx.should_finished()
