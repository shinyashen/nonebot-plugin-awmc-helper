"""awmc.score_query 查分子插件测试。"""

import pytest
from mocks import requires_assets
from nonebug import App

BASE_DF = "https://www.diving-fish.com/api/maimaidxprober"


@pytest.fixture
async def db(tmp_path):
    from nonebot_plugin_awmc_helper.core import store

    store.set_db_file(tmp_path / "awmc.db")
    await store.init_db()
    yield
    store.set_db_file(None)


@pytest.fixture
async def songs(db):
    from mocks import seed_service

    from nonebot_plugin_awmc_helper.core.songs import song_service

    await seed_service(song_service)
    yield
    song_service._ready.clear()


async def _assert_reply(
    app: App, matcher, text: str, reply: str, *, user_id=12345678, with_session=True
):
    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    event = fake_group_message_event_v11(message=text, user_id=user_id)
    async with app.test_matcher(matcher) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter))
        ctx.receive_event(bot, event)
        if with_session:
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
                {"group_id": 87654321, "user_id": user_id, "no_cache": True},
                result={
                    "user_id": user_id,
                    "role": "member",
                    "card": "",
                    "nickname": "t",
                },
            )
        ctx.should_call_send(
            event,
            Message([MessageSegment.at(user_id), MessageSegment.text(reply)]),
            result=None,
            bot=bot,
        )
        ctx.should_finished()


@pytest.mark.asyncio
async def test_b50_unbound_hint(app: App, db, songs):
    """无凭据（非 qq 平台默认绑定）时提示先绑定。"""
    from nonebot_plugin_awmc_helper.plugins import score_query

    await _assert_reply(
        app,
        score_query.b50,
        "b50",
        "尚未绑定查分器，请先使用「绑定水鱼」或「绑定落雪」进行绑定",
    )


@requires_assets
@pytest.mark.asyncio
async def test_b50_username_lookup(app: App, db, songs):
    """b50 <水鱼用户名> 公开代查：respx mock 水鱼接口，期望图与真实渲染同源。"""
    import base64

    import respx
    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    from nonebot_plugin_awmc_helper.plugins import score_query
    from nonebot_plugin_awmc_helper.core.score import score_service
    from nonebot_plugin_awmc_helper.core.render.best50 import best50_bytes

    payload = {
        "username": "someone",
        "rating": 350,
        "nickname": "someone",
        "plate": "彩将",
        "additional_rating": 1,
        "charts": {
            "sd": [],
            "dx": [
                {
                    "song_id": 10231,  # 231 的 DX 谱
                    "level": "13",
                    "level_index": 3,
                    "achievements": 100.5,
                    "fc": "ap",
                    "fs": "fsd",
                    "dxScore": 2000,
                    "rate": "sssp",
                    "ra": 350,
                }
            ],
        },
    }
    with respx.mock(assert_all_called=False) as m:
        m.post(f"{BASE_DF}/query/player").respond(json=payload)
        # 与 handler 相同的调用路径 → 相同数据 → 相同渲染
        player, bests = await score_service.get_b50_by_username("someone")
        expected_png = best50_bytes(
            player.name,
            bests.rating,
            bests.rating_b35,
            bests.rating_b15,
            bests.scores_b35,
            bests.scores_b15,
        )

        event = fake_group_message_event_v11(message="b50 someone")
        expected = Message(
            [
                MessageSegment.at(12345678),
                MessageSegment.image(
                    f"base64://{base64.b64encode(expected_png).decode()}"
                ),
            ]
        )
        async with app.test_matcher(score_query.b50) as ctx:
            bot = ctx.create_bot(
                base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter)
            )
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
                result={
                    "user_id": 12345678,
                    "role": "member",
                    "card": "",
                    "nickname": "t",
                },
            )
            ctx.should_call_send(event, expected, result=None, bot=bot)
            ctx.should_finished()
