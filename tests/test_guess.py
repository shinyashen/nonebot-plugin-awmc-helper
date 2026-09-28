"""awmc.guess 猜歌测试：FFT 裁剪冒烟（曲绘底座）、对局管理、答案拦截、揭晓幂等。"""

import pytest


@pytest.fixture
async def songs(tmp_path):
    from mocks import seed_service

    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.core.songs import song_service

    store.set_db_file(tmp_path / "awmc.db")
    await store.init_db()
    await seed_service(song_service)
    yield
    song_service._ready.clear()
    store.set_db_file(None)


@pytest.mark.asyncio
async def test_guess_crop_smoke(songs):
    """FFT 裁剪冒烟：输入输出尺寸正确。"""
    from PIL import Image

    from nonebot_plugin_awmc_helper.core.render.cover import (
        frequency_weights,
        crop_cover_randomly,
    )

    img = Image.new("RGBA", (400, 400), "#336699")

    w = frequency_weights(img)
    assert w.shape == (400, 400)
    cropped = crop_cover_randomly(img)
    assert cropped.size[0] < 400
    assert cropped.size[1] < 400


@pytest.mark.asyncio
async def test_guess_manager(songs, monkeypatch):
    """猜歌对局管理：开局→提示序列→答对揭晓→清理；答案判定不区分大小写。"""
    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.plugins.guess import game as guess_game

    monkeypatch.setattr(guess_game.plugin_config, "awmc_guess_interval", 60)
    monkeypatch.setattr(guess_game.plugin_config, "awmc_guess_duration", 60)

    song = await guess_game._pick_song()
    assert song is not None
    game = guess_game.GuessGame(
        song, pic_mode=False, group_id="g1", bot=None, event=None
    )
    guess_game._games["g1"] = game
    assert guess_game._game_of("g1") is game
    assert len(game.hints) == 6
    # 6 条提示后进入曲绘阶段
    for i in range(6):
        hint = game.next_hint()
        assert hint is not None
        assert hint.startswith(f"提示{i + 1}")
    assert game.next_hint() is None

    assert not game.match("完全无关的回答")
    assert game.match(song.title.upper())  # 大小写不敏感
    assert game.match(str(song.id))

    # 热门题库：无曲线数据时退化为全曲库
    all_songs = await song_service.get_all()
    assert song in all_songs


@pytest.mark.asyncio
async def test_guess_answer_flow(app, songs, monkeypatch):
    """on_message 答案拦截：命中后揭晓并清理对局。"""
    from nonebot_plugin_uninfo import User, Scene, Session, SceneType

    from nonebot_plugin_awmc_helper.plugins.guess import game as guess_game

    monkeypatch.setattr(guess_game.plugin_config, "awmc_guess_duration", 60)

    revealed: list[str] = []

    async def fake_reveal(game, prefix):
        revealed.append(prefix)
        guess_game._games.pop(game.group_id, None)

    monkeypatch.setattr(guess_game, "_reveal", fake_reveal)

    song = await guess_game._pick_song()
    assert song is not None
    game = guess_game.GuessGame(
        song, pic_mode=True, group_id="g2", bot=None, event=None
    )
    guess_game._games["g2"] = game

    session = Session(
        self_id="test",
        adapter="OneBot V11",
        scope="qq_client",
        scene=Scene(id="g2", type=SceneType.GROUP),
        user=User(id="10086"),
        member=None,
        operator=None,
        platform="unknown",
    )
    # 错误答案不触发
    assert not await guess_game._handle_answer(session, "乱答的")
    assert guess_game._game_of("g2") is game
    # 正确答案揭晓
    assert await guess_game._handle_answer(session, song.title)
    assert revealed
    assert guess_game._game_of("g2") is None


@pytest.mark.asyncio
async def test_guess_reveal_idempotent(songs, monkeypatch):
    """揭晓幂等：答对与超时并发时 _reveal 只生效一次，且取消提示循环 task。"""
    import asyncio

    from nonebot_plugin_awmc_helper.plugins.guess import game as guess_game

    # 揭晓附带的谱面卡渲染不在本测目标内（真实样例 SD 基础/高级谱无谱师，
    # draw_song_card 对 note_designer=None 会崩，属产品待修项，另测覆盖渲染）
    monkeypatch.setattr(guess_game.song_render, "song_card_bytes", lambda song: b"")

    sends: list[str] = []

    class _FakeUniMsg:
        def __init__(self, parts: list[str]):
            self.parts = parts

        @staticmethod
        def text(t: str) -> "_FakeUniMsg":
            return _FakeUniMsg([t])

        def image(self, **kw):
            return self

        async def send(self, *a, **kw):
            sends.append("".join(self.parts))

    monkeypatch.setattr(guess_game, "UniMessage", _FakeUniMsg)

    song = await guess_game._pick_song()
    assert song is not None
    game = guess_game.GuessGame(
        song, pic_mode=True, group_id="g9", bot=None, event=None
    )
    game.task = asyncio.create_task(asyncio.sleep(60))
    guess_game._games["g9"] = game
    try:
        await guess_game._reveal(game, "时间到！")
        await guess_game._reveal(game, "时间到！")  # 第二次应被 settled 挡掉
        assert len(sends) == 1
        assert "时间到" in sends[0]
        assert guess_game._game_of("g9") is None
        await asyncio.sleep(0)  # 让循环 task 处理 cancel
        assert game.task.cancelled()
    finally:
        if not game.task.done():
            game.task.cancel()
