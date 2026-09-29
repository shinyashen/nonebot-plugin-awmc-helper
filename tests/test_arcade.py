"""awmc.arcade 排卡子插件测试：机厅同步与重置、指令流（查找/订阅/加减人/查询）、
群级开关与部署默认。"""

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


@pytest.fixture
async def arcade_enabled(arcade_seed):
    """排卡部署默认关：群内流程测试先显式开通本群（等价 开启排卡）。"""
    from nonebot_plugin_awmc_helper.core import store

    await store.set_group_switch("87654321", "arcade", True)


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


async def _run_silent(app: App, matcher, text: str, *, with_session=True):
    """开关拦截路径：rule 建会话判开关后静默返回——无回复、无 finished。"""
    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter
    from nonebot.adapters.onebot.v11.event import Sender

    event = fake_group_message_event_v11(
        message=text,
        user_id=12345678,
        sender=Sender(card="", nickname="t", role="member"),
    )
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
                {"group_id": 87654321, "user_id": 12345678, "no_cache": True},
                result={
                    "user_id": 12345678,
                    "role": "member",
                    "card": "",
                    "nickname": "t",
                },
            )


@pytest.mark.asyncio
async def test_search_arcade(app: App, arcade_enabled):
    from nonebot_plugin_awmc_helper.plugins import arcade

    await _run(
        app,
        arcade.arcade_search,
        "查找机厅 游戏",
        "为您找到以下机厅：\n==========\n店名：游戏厅\n"
        "    - 地址：某路 2 号\n    - ID：10000\n    - 机台：4\n    - 排卡：0 人",
    )


@pytest.mark.asyncio
async def test_add_person_flow(app: App, arcade_enabled):
    """订阅 → 加人 → 减人 → 超限拒绝；排卡操作人人可用（对齐原版，无权限限制）。"""
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
        session_fetches=1,
    )
    await _run(
        app,
        arcade.arcade_add_person,
        "Game-1人",
        "「游戏厅」当前排卡 1 人",
        session_fetches=1,
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
        session_fetches=1,
    )


@pytest.mark.asyncio
async def test_add_person_multi_alias(app: App, arcade_enabled):
    """多别称机厅：任一别称都须命中（回归：曾按机厅折叠字典只剩最后一条别称）。"""
    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.plugins import arcade

    await store.add_arcade_alias(10000, "Hall")
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
        "Game+2人",
        "「游戏厅」当前排卡 2 人",
        session_fetches=1,
    )
    await _run(
        app,
        arcade.arcade_add_person,
        "Hall-1人",
        "「游戏厅」当前排卡 1 人",
        session_fetches=1,
    )


@pytest.mark.asyncio
async def test_person_query(app: App, arcade_enabled):
    from nonebot_plugin_awmc_helper.plugins import arcade

    await _run(app, arcade.arcade_person_num_2, "游戏厅有几人", "「游戏厅」排卡 0 人")


@pytest.mark.asyncio
async def test_jtj_unsubscribed(app: App, arcade_enabled):
    from nonebot_plugin_awmc_helper.plugins import arcade

    await _run(app, arcade.arcade_person_num, "机厅几人", "该群未订阅任何机厅")


@pytest.mark.asyncio
async def test_switch_default_off(app: App, arcade_seed):
    """部署默认关：未覆盖群一切排卡指令在 rule 层静默拦截（含宽正则），帮助除外。"""
    from nonebot_plugin_awmc_helper.plugins import arcade

    await _run_silent(app, arcade.arcade_search, "查找机厅 游戏")
    await _run_silent(app, arcade.arcade_person_num_2, "游戏厅有几人")
    await _run_silent(app, arcade.arcade_add_person, "游戏厅+2人")
    await _run_silent(app, arcade.arcade_person_num, "机厅几人")


@pytest.mark.asyncio
async def test_switch_toggle(app: App, arcade_seed):
    """开启/关闭排卡：成员拒绝；管理员开启后群级覆盖部署默认；关闭回归静默。"""
    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.plugins import arcade

    await _run(
        app,
        arcade.arcade_switch,
        "开启排卡",
        "权限不足：仅群管理员可用",
        role="member",
        session_fetches=2,
    )
    await _run(
        app,
        arcade.arcade_switch,
        "开启排卡",
        "已开启本群排卡",
        role="admin",
        session_fetches=2,
    )
    assert await store.get_switch("87654321", "arcade", False) is True
    await _run(
        app,
        arcade.arcade_search,
        "查找机厅 游戏",
        "为您找到以下机厅：\n==========\n店名：游戏厅\n"
        "    - 地址：某路 2 号\n    - ID：10000\n    - 机台：4\n    - 排卡：0 人",
    )
    await _run(
        app,
        arcade.arcade_switch,
        "关闭排卡",
        "已关闭本群排卡",
        role="admin",
        session_fetches=2,
    )
    assert await store.get_switch("87654321", "arcade", False) is False
    await _run_silent(app, arcade.arcade_search, "查找机厅 游戏")


@pytest.mark.asyncio
async def test_switch_private_follows_default(app: App, arcade_seed, monkeypatch):
    """私聊取部署默认：默认关静默；AWMC_ARCADE_ENABLED=true 后可用。"""
    import nonebot
    from fake import fake_private_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    from nonebot_plugin_awmc_helper.config import plugin_config
    from nonebot_plugin_awmc_helper.plugins import arcade

    def _private_event():
        return fake_private_message_event_v11(message="查找机厅 游戏", user_id=12345678)

    # 部署默认关：rule 判私聊取默认 False → 静默（私聊会话构建零 API）
    event = _private_event()
    async with app.test_matcher(arcade.arcade_search) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter))
        ctx.receive_event(bot, event)

    monkeypatch.setattr(plugin_config, "awmc_arcade_enabled", True)
    event = _private_event()
    async with app.test_matcher(arcade.arcade_search) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter))
        ctx.receive_event(bot, event)
        # 私聊：无 at，发送层去前导空格
        ctx.should_call_send(
            event,
            Message(
                [
                    MessageSegment.text(
                        "为您找到以下机厅：\n==========\n店名：游戏厅\n"
                        "    - 地址：某路 2 号\n    - ID：10000\n"
                        "    - 机台：4\n    - 排卡：0 人"
                    )
                ]
            ),
            result=None,
            bot=bot,
        )
        ctx.should_finished()


@pytest.mark.asyncio
async def test_add_person_silent_no_name(app: App, arcade_enabled):
    """无店名消息（如「+2」「=5」）静默忽略，对齐 Hoshino 原版
    （if match.group(1) 无 else）。「1+1」的店名为「1」，属有店名路径：
    未订阅时与原版一样拒绝（不静默）。
    """
    from nonebot_plugin_awmc_helper.plugins import arcade

    await _run_silent(app, arcade.arcade_add_person, "+2")
    await _run_silent(app, arcade.arcade_add_person, "=5")
    await _run(
        app,
        arcade.arcade_add_person,
        "1+1",
        "该群未订阅机厅，无法更改机厅人数",
        session_fetches=1,
    )


@pytest.mark.asyncio
async def test_find_arcade_ambiguous_lists_candidates(app: App, arcade_enabled):
    """模糊名多命中列候选终止，不静默取第一个（L-30，对齐 Hoshino「请使用店铺ID」）。"""
    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.plugins.arcade import matchers as am

    await store.save_arcade(
        store.Arcade(
            id=20001, name="游戏天堂", address="某路 3 号", machines=2, is_custom=True
        )
    )
    await _run(
        app,
        am.arcade_alias_set,
        "添加机厅别名 游戏 别名x",
        "找到 2 个与「游戏」相关的机厅，请使用 ID 指定：\n"
        "ID 10000：游戏厅\nID 20001：游戏天堂",
    )
    # 终止而非误操作：新别名未写入任何一店
    assert await store.get_arcade(10000) is not None
    assert await store.get_arcade(20001) is not None
    assert "别名x" not in [a.alias for a in await store.get_arcade_aliases(10000)]
    assert await store.get_arcade_aliases(20001) == []
