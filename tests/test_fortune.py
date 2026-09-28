"""awmc.fortune 今日mai测试：同日确定性输出（文本 + 推荐曲卡）与 qqhash 稳定性。"""

import pytest
from nonebug import App


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


def _b64(data: bytes) -> bytes:
    import base64

    return base64.b64encode(data)


@pytest.mark.asyncio
async def test_fortune(app: App, songs):
    """今日mai：同日确定性输出（文本 + 推荐曲卡）。"""
    import random as _random_mod

    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    from nonebot_plugin_awmc_helper.plugins import fortune
    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.render import song as song_render
    from nonebot_plugin_awmc_helper.plugins.fortune import FORTUNE, qqhash

    seed = int("12345678")
    fh = qqhash(seed)
    daily = _random_mod.Random(fh)
    all_songs = await song_service.get_all()
    song = daily.choice(all_songs)
    ds = "/".join(f"{d.level_value:.1f}" for d in song.get_difficulties())

    rp = fh % 100
    h = fh
    lines = [f" 今日人品值：{rp}"]
    for i in range(11):
        wm = h & 3
        h >>= 2
        if wm == 3:
            lines.append(f"宜 {FORTUNE[i]}")
        elif wm == 0:
            lines.append(f"忌 {FORTUNE[i]}")
    lines.append("打机时不要大力拍打或滑动哦")
    lines.append(f"今日推荐歌曲：ID.{song.id} - {song.title}（定数 {ds}）")
    text = "\n".join(lines)

    import nonebot as _nb

    event = fake_group_message_event_v11(message="今日mai")
    expected = Message(
        [
            MessageSegment.at(12345678),
            MessageSegment.text(text),
            MessageSegment.image(
                f"base64://{_b64(song_render.song_card_bytes(song)).decode()}"
            ),
        ]
    )
    async with app.test_matcher(fortune.today_fortune) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=_nb.get_adapter(OnebotV11Adapter))
        ctx.receive_event(bot, event)
        ctx.should_call_api(
            "get_group_info",
            {"group_id": 87654321},
            result={
                "group_id": 87654321,
                "group_name": "g",
                "member_count": 1,
                "max_member_count": 10,
            },
        )
        ctx.should_call_api(
            "get_group_member_info",
            {"group_id": 87654321, "user_id": 12345678, "no_cache": True},
            result={"user_id": 12345678, "role": "member", "card": "", "nickname": "t"},
        )
        ctx.should_call_send(event, expected, result=None, bot=bot)
        ctx.should_finished()
        _ = _nb


@pytest.mark.asyncio
async def test_fortune_hash_stable():
    """同日同 QQ 哈希稳定（确定性）。"""
    from nonebot_plugin_awmc_helper.plugins.fortune import qqhash

    assert qqhash(123456) == qqhash(123456)
    assert qqhash(1) != qqhash(2)
