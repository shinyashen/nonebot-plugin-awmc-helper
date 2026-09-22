"""awmc.bind 绑定子插件测试。"""

import pytest
from nonebug import App


@pytest.fixture
async def db(tmp_path):
    from nonebot_plugin_awmc_helper.core import store

    store.set_db_file(tmp_path / "awmc.db")
    await store.init_db()
    yield
    store.set_db_file(None)


async def _assert_reply(app: App, matcher, text: str, reply: str, *, user_id=12345678):
    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    event = fake_group_message_event_v11(message=text, user_id=user_id)
    async with app.test_matcher(matcher) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter))
        ctx.receive_event(bot, event)
        ctx.should_call_api(
            "get_group_info",
            {"group_id": 87654321},
            result={
                "group_id": 87654321,
                "group_name": "测试群",
                "member_count": 10,
                "max_member_count": 100,
            },
        )
        ctx.should_call_api(
            "get_group_member_info",
            {"group_id": 87654321, "user_id": user_id, "no_cache": True},
            result={
                "user_id": user_id,
                "role": "member",
                "card": "",
                "nickname": "test",
            },
        )
        ctx.should_call_send(
            event,
            Message([MessageSegment.at(user_id), MessageSegment.text(f" {reply}")]),
            result=None,
            bot=bot,
        )
        ctx.should_finished()


@pytest.mark.asyncio
async def test_bind_divingfish_username(app: App, db):
    from nonebot_plugin_awmc_helper.plugins import bind
    from nonebot_plugin_awmc_helper.core.binding import binding_service

    await _assert_reply(
        app,
        bind.df_bind,
        "绑定水鱼 测试者",
        "已绑定水鱼账号「测试者」（公开查询）。\n"
        "如需查询全量成绩（牌子/表格），请使用「绑定水鱼token <Import-Token>」",
    )
    binding = await binding_service.get("unknown", "12345678")
    assert binding is not None
    assert binding.divingfish_username == "测试者"


@pytest.mark.asyncio
async def test_bind_divingfish_token(app: App, db):
    from nonebot_plugin_awmc_helper.plugins import bind
    from nonebot_plugin_awmc_helper.core.binding import binding_service

    await _assert_reply(
        app,
        bind.df_token,
        "绑定水鱼token abc-def-1234567890",
        "已保存水鱼 Import-Token（仅存于本机数据库，用于查询全量成绩）",
    )
    binding = await binding_service.get("unknown", "12345678")
    assert binding is not None
    assert binding.divingfish_import_token == "abc-def-1234567890"


@pytest.mark.asyncio
async def test_bind_lxns_direct_and_switch(app: App, db):
    from nonebot_plugin_awmc_helper.plugins import bind
    from nonebot_plugin_awmc_helper.core.binding import binding_service

    await _assert_reply(
        app,
        bind.lx_bind,
        "绑定落雪 123456789",
        "已绑定落雪好友码 123456789（需要部署配置开发者 Token 才能查询）",
    )
    binding = await binding_service.get("unknown", "12345678")
    assert binding is not None
    assert binding.lxns_friend_code == 123456789

    # 数据源切换到落雪（已有好友码，允许）
    await _assert_reply(app, bind.set_provider, "数据源 1", "数据源已切换为落雪")
    # 未配置水鱼凭据 → 切回水鱼仍可（公开查询）
    await _assert_reply(app, bind.set_provider, "数据源 0", "数据源已切换为水鱼")
    # 无落雪凭据的用户切换 → 拒绝
    await _assert_reply(
        app,
        bind.set_provider,
        "数据源 1",
        "尚未绑定落雪查分器，无法切换数据源",
        user_id=999,
    )


@pytest.mark.asyncio
async def test_theme_and_mybind_and_unbind(app: App, db):
    from nonebot_plugin_awmc_helper.plugins import bind
    from nonebot_plugin_awmc_helper.core.binding import binding_service

    await _assert_reply(app, bind.set_theme, "主题 1", "主题已切换")
    binding = await binding_service.get("unknown", "12345678")
    assert binding is not None
    assert binding.theme == "circle"

    await _assert_reply(app, bind.my_bind, "我的绑定", "数据源：水鱼\n主题：circle")
    await _assert_reply(app, bind.unbind, "解绑", "已解除绑定")
    await _assert_reply(app, bind.unbind, "解绑", "尚未绑定")


@pytest.mark.asyncio
async def test_default_service_public_query(app: App, db):
    """未绑定时 my_bind 显示尚未绑定；ensure 自动创建默认源（qq 公开查询凭据）。"""
    from nonebot_plugin_awmc_helper.plugins import bind
    from nonebot_plugin_awmc_helper.core.binding import binding_service

    await _assert_reply(app, bind.my_bind, "我的绑定", "尚未绑定")
    binding = await binding_service.ensure("unknown", "12345678")
    assert binding.service == "divingfish"  # 部署默认
