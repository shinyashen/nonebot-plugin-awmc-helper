"""M6：ext/wahlap 与 arcade 排卡子插件测试。"""

import respx
import pytest
from nonebug import App


@pytest.fixture
async def db(tmp_path):
    from nonebot_plugin_awmc_helper.core import store

    store.set_db_file(tmp_path / "awmc.db")
    await store.init_db()
    yield
    store.set_db_file(None)


@pytest.mark.asyncio
async def test_wahlap_fetch():
    from nonebot_plugin_awmc_helper.core.ext import wahlap as wahlap_ext

    payload = [
        {
            "id": 345,
            "arcadeName": "华立乐园",
            "address": "某某路 1 号",
            "province": "广东",
            "mall": "某某广场",
            "machineCount": 8,
        }
    ]
    with respx.mock(assert_all_called=False) as m:
        m.get("https://wc.wahlap.net/maidx/rest/location").respond(json=payload)
        data = await wahlap_ext.fetch_locations()
    assert len(data) == 1
    assert data[0].name == "华立乐园"
    assert data[0].machine_count == 8


@pytest.mark.asyncio
async def test_sync_and_reset(db):
    from nonebot_plugin_awmc_helper.core import store

    # 预置自定义机厅并给官方机厅设置人数
    await store.save_arcade(
        store.Arcade(id=20000, name="自定义厅", machines=2, person=5, is_custom=True)
    )
    with respx.mock(assert_all_called=False) as m:
        m.get("https://wc.wahlap.net/maidx/rest/location").respond(
            json=[
                {
                    "id": 345,
                    "arcadeName": "华立乐园",
                    "address": "某某路 1 号",
                    "province": "广东",
                    "mall": "某某广场",
                    "machineCount": 8,
                }
            ]
        )
        # 给官方机厅预置人数（先手工入库再同步，验证同步保留本地人数字段后由清零归零）
        await store.save_arcade(store.Arcade(id=345, name="旧名", machines=1, person=3))
        from nonebot_plugin_awmc_helper.plugins.arcade import sync_and_reset

        count = await sync_and_reset()
    assert count == 1
    arcade = await store.get_arcade(345)
    assert arcade is not None
    assert arcade.name == "华立乐园"
    assert arcade.machines == 8
    assert arcade.person == 0  # 同步后清零
    custom = await store.get_arcade(20000)
    assert custom is not None
    assert custom.is_custom
    assert custom.person == 0  # 自定义保留、人数清零


@pytest.fixture
async def arcade_seed(db):
    from nonebot_plugin_awmc_helper.core import store

    await store.save_arcade(
        store.Arcade(
            id=10000, name="游戏厅", address="某路 2 号", machines=4, is_custom=True
        )
    )
    await store.add_arcade_alias(10000, "Game")
    return


async def _run(
    app: App,
    matcher,
    text: str,
    reply: str,
    *,
    user_id=12345678,
    role="member",
    with_session=True,
    session_fetches: int = 1,
):
    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter
    from nonebot.adapters.onebot.v11.event import Sender

    event = fake_group_message_event_v11(
        message=text, user_id=user_id, sender=Sender(card="", nickname="t", role=role)
    )
    async with app.test_matcher(matcher) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter))
        ctx.receive_event(bot, event)
        for _ in range(session_fetches if with_session else 0):
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
                result={"user_id": user_id, "role": role, "card": "", "nickname": "t"},
            )
        ctx.should_call_send(
            event,
            Message([MessageSegment.at(user_id), MessageSegment.text(f" {reply}")]),
            result=None,
            bot=bot,
        )
        ctx.should_finished()


@pytest.mark.asyncio
async def test_search_arcade(app: App, arcade_seed):
    from nonebot_plugin_awmc_helper.plugins import arcade

    await _run(
        app,
        arcade.arcade_search,
        "查找机厅 游戏",
        "为您找到以下机厅：\n==========\n店名：游戏厅\n"
        "    - 地址：某路 2 号\n    - ID：10000\n    - 机台：4\n    - 排卡：0 人",
        with_session=False,
    )


@pytest.mark.asyncio
async def test_add_person_flow(app: App, arcade_seed):
    """订阅 → 加人 → 减人 → 超限拒绝。"""
    from nonebot_plugin_awmc_helper.plugins import arcade

    await _run(
        app,
        arcade.arcade_sub,
        "订阅机厅 游戏厅",
        "已订阅「游戏厅」",
        role="admin",
        session_fetches=2,
    )
    await _run(
        app,
        arcade.arcade_add_person,
        "游戏厅+2人",
        "「游戏厅」当前排卡 2 人",
        role="admin",
        session_fetches=2,
    )
    await _run(
        app,
        arcade.arcade_add_person,
        "Game-1人",
        "「游戏厅」当前排卡 1 人",
        role="admin",
        session_fetches=2,
    )
    # 退订后再操作 → 拒绝
    await _run(
        app,
        arcade.arcade_sub,
        "取消订阅机厅 游戏厅",
        "已取消订阅「游戏厅」",
        role="admin",
        session_fetches=2,
    )
    await _run(
        app,
        arcade.arcade_add_person,
        "游戏厅+2人",
        "该群未订阅机厅，无法更改机厅人数",
        role="admin",
        session_fetches=2,
    )


@pytest.mark.asyncio
async def test_person_query(app: App, arcade_seed):
    import nonebot

    # on_regex 版本：通过正则分组取店名
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    from nonebot_plugin_awmc_helper.plugins import arcade

    event = fake_group_message_event_v11(message="游戏厅有几人")
    async with app.test_matcher(arcade.arcade_person_num_2) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter))
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            Message(
                [
                    MessageSegment.at(12345678),
                    MessageSegment.text(" 「游戏厅」排卡 0 人"),
                ]
            ),
            result=None,
            bot=bot,
        )
        ctx.should_finished()


@pytest.mark.asyncio
async def test_jtj_unsubscribed(app: App, db):
    from nonebot_plugin_awmc_helper.plugins import arcade

    await _run(app, arcade.arcade_person_num, "机厅几人", "该群未订阅任何机厅")
