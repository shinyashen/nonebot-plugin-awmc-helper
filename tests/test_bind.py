"""awmc.bind 绑定子插件测试。"""

import base64

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
    binding = await binding_service.get("OneBot V11", "12345678")
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
    binding = await binding_service.get("OneBot V11", "12345678")
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
    binding = await binding_service.get("OneBot V11", "12345678")
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
    binding = await binding_service.get("OneBot V11", "12345678")
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
    binding = await binding_service.ensure("OneBot V11", "12345678")
    assert binding.service == "divingfish"  # 部署默认


@pytest.mark.asyncio
async def test_lxns_oauth_flow(app: App, db, monkeypatch):
    """落雪 OAuth 授权流：发「绑定落雪」返回新文案（90 秒/直接回复）并开启回填会话。"""
    from nonebot_plugin_awmc_helper.config import plugin_config
    from nonebot_plugin_awmc_helper.plugins import bind
    from nonebot_plugin_awmc_helper.core.ext import lxns as lxns_ext
    from nonebot_plugin_awmc_helper.core.binding import pending_bindings

    monkeypatch.setattr(plugin_config, "awmc_lxns_client_id", "cid")
    monkeypatch.setattr(plugin_config, "awmc_lxns_client_secret", "secret")
    monkeypatch.setattr(plugin_config, "awmc_lxns_redirect_uri", "oob")

    link = lxns_ext.build_authorize_url()
    await _assert_reply(
        app,
        bind.lx_bind,
        "绑定落雪",
        f"请点击以下链接完成落雪授权（授权码 90 秒内有效）：\n{link}\n\n"
        "完成后请直接把授权码回复给我（无需任何前缀）",
    )
    assert pending_bindings.is_active("OneBot V11", "12345678", "lxns")


@pytest.mark.asyncio
async def test_lxns_pending_expiry_hint(app: App, db, monkeypatch):
    """回填会话超时后仍发码 → 超时指引而非静默。"""
    import time as _time
    from types import SimpleNamespace

    import nonebot
    import nonebot_plugin_uninfo
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    from nonebot_plugin_awmc_helper.plugins import bind
    from nonebot_plugin_awmc_helper.core.binding import pending_bindings

    fake_sess = SimpleNamespace(
        platform=None,
        adapter=SimpleNamespace(value="OneBot V11"),
        user=SimpleNamespace(id="12345678"),
    )

    async def fake_get_session(bot, event):
        return fake_sess

    monkeypatch.setattr(nonebot_plugin_uninfo, "get_session", fake_get_session)

    pending_bindings.start("OneBot V11", "12345678", "lxns")
    sess = pending_bindings._sessions[("OneBot V11", "12345678")]
    sess.expires = _time.monotonic() - 1  # 置为已过期

    event = fake_group_message_event_v11(message="X7TF-J3TU-AXSH")
    async with app.test_matcher(bind.bind_code_expired) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter))
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            Message(
                [
                    MessageSegment.at("12345678"),
                    MessageSegment.text(
                        " 落雪授权已超时，请重新发送「绑定落雪」获取新的授权链接"
                    ),
                ]
            ),
            result=None,
            bot=bot,
        )


@pytest.mark.asyncio
async def test_df_oauth_device_flow(app: App, db, monkeypatch):
    """水鱼 OAuth 设备码绑定（handoff=code 确认码回填，对齐 Hoshino 上游）：

    「绑定水鱼」无参 → 发起设备码授权并回授权链接（绑定身份遮罩 + 有效期），
    开启 20 分钟回填会话；回填确认码 → confirmation-code 兑换 → 落
    divingfish_oauth 标志与水鱼用户 ID。"""
    import hashlib
    import re
    import urllib.parse

    import respx

    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.config import plugin_config
    from nonebot_plugin_awmc_helper.plugins import bind
    from nonebot_plugin_awmc_helper.core.binding import (
        binding_service,
        pending_bindings,
    )

    monkeypatch.setattr(plugin_config, "awmc_divingfish_oauth_client_id", "cid")
    monkeypatch.setattr(plugin_config, "awmc_divingfish_oauth_client_secret", "sec")

    def fake_jwt(sub: str) -> str:
        payload = (
            base64.urlsafe_b64encode(b'{"sub":"' + sub.encode() + b'"}')
            .decode()
            .rstrip("=")
        )
        return f"eyJhbGciOiJQUzI1NiJ9.{payload}.sig"

    with respx.mock(assert_all_called=False) as m:
        m.post(url__regex=r".*/oauth/device_authorization").respond(
            200,
            json={
                "device_code": "dev",
                "user_code": "BCDF-GHJK-LMNP",
                "verification_uri": "https://auth.diving-fish.com/device",
                "verification_uri_complete": "https://auth.diving-fish.com/device?user_code=BCDF-GHJK-LMNP",
                "expires_in": 1200,
                "interval": 5,
            },
        )
        await _assert_reply(
            app,
            bind.df_bind,
            "绑定水鱼",
            (
                "水鱼已要求所有成绩写入走 OAuth 授权，请完成一次绑定：\n\n"
                "1. 打开以下链接并登录水鱼账号，授权本 BOT 访问您的水鱼查分器数据\n"
                "=======================\n"
                "https://auth.diving-fish.com/device?user_code=BCDF-GHJK-LMNP\n"
                "=======================\n"
                "2. 确认页面显示的绑定身份为「QQ 12****78」后点击「同意授权」\n"
                "3. 复制页面给出的确认码，直接发送给我（无需任何前缀）\n\n"
                "本次绑定 20 分钟内有效，确认码只能使用一次；"
                "超时或失效后请重新发送「绑定水鱼」。\n"
                "=======================\n"
                "请注意！！链接与确认码都仅供您本人使用，请勿转发他人。\n"
                "确认码建议在与 BOT 的私聊中发送，避免被他人看到。\n"
                "如需取消授权，请前往 https://auth.diving-fish.com/apps"
            ),
        )
        assert pending_bindings.is_active("OneBot V11", "12345678", "divingfish")
        # 发起请求确实带 handoff=code（device_code 换不到令牌）与 subject_ref 摘要
        device_calls = [c for c in m.routes[-1].calls if c.request.content]
        assert device_calls, "设备码发起请求未发出"
        form = urllib.parse.parse_qs(device_calls[0].request.content.decode())
        assert form["handoff"] == ["code"]
        assert form["scope"] == ["prober.records.read prober.records.write"]
        expect_ref = hashlib.sha256(b"cid:12345678").hexdigest()
        assert form["subject_ref"] == [expect_ref]
        assert form["binding_label"] == ["QQ 12****78"]

        # 回填确认码 → 兑换 → 落标志
        m.post(url__regex=r".*/oauth/token").respond(
            200,
            json={
                "access_token": fake_jwt("987654321"),
                "token_type": "Bearer",
                "expires_in": 900,
                "scope": "prober.records.read prober.records.write",
                "sub": "987654321",
            },
        )
        await _assert_reply(
            app,
            bind.df_code,
            "水鱼授权码 BCDF-GHJK-LMNP",
            "水鱼查分器授权完成，现在可以直接使用查询指令了。",
        )
        row = await store.get_binding("OneBot V11", "12345678")
        assert row is not None
        assert row.divingfish_oauth is True
        assert row.divingfish_sub == "987654321"
        assert not pending_bindings.is_active("OneBot V11", "12345678", "divingfish")
        # subject 与水鱼侧公式一致
        from nonebot_plugin_awmc_helper.config import plugin_config as cfg

        expect = (
            "ref:"
            + hashlib.sha256(
                f"{cfg.awmc_divingfish_oauth_client_id}:12345678".encode()
            ).hexdigest()
        )
        binding = await binding_service.get("OneBot V11", "12345678")
        assert binding is not None
        assert binding_service.divingfish_subject(binding) == expect
        assert re.fullmatch(r"[0-9a-f]{64}", expect[4:])


@pytest.mark.asyncio
async def test_df_subject_derivable_regardless_of_service(db, monkeypatch):
    """subject 派生不依赖 service（导分插件对 service=net/lxns 用户同样要
    装配水鱼 OAuth 目标；2026-09-28 写路径强制 OAuth）。"""
    from nonebot_plugin_awmc_helper.config import plugin_config
    from nonebot_plugin_awmc_helper.core.store import UserBinding
    from nonebot_plugin_awmc_helper.core.binding import binding_service

    monkeypatch.setattr(plugin_config, "awmc_divingfish_oauth_client_id", "cid")
    monkeypatch.setattr(plugin_config, "awmc_divingfish_oauth_client_secret", "sec")

    b = UserBinding(platform="OneBot V11", user_id="935302685", service="net")
    assert binding_service.divingfish_subject(b) is not None
    assert binding_service.divingfish_subject(b) == binding_service.divingfish_subject(
        UserBinding(platform="OneBot V11", user_id="935302685", service="divingfish")
    )


def test_bind_command_names_disjoint():
    """on_command 的命令名/别名两两不相交（撞名回归测试）。

    「水鱼授权码」曾同时是 df_token 的别名与 df_code 的命令名：同优先级
    matcher 在 NoneBot 中并发运行（block 只截断向更低优先级的传播），
    两条会同时响应——确认码被当 Import-Token 落库污染凭据。matcher 级
    nonebug 测试绕过真实 dispatch，测不出这类撞名，只能结构化断言。
    """
    from nonebot.rule import CommandRule
    from nonebot_plugin_awmc_helper.plugins import bind

    owners: dict[str, str] = {}
    matchers = [
        bind.df_bind,
        bind.df_token,
        bind.lx_bind,
        bind.lx_code,
        bind.df_code,
        bind.net_bind,
        bind.unbind,
        bind.set_provider,
        bind.set_theme,
        bind.my_bind,
    ]
    for matcher in matchers:
        for checker in matcher.rule.checkers:
            if not isinstance(checker, CommandRule):
                continue
            for path in checker.commands:
                name = "".join(path)
                assert name not in owners, (
                    f"命令撞名：「{name}」同时属于 {owners[name]} 与 {matcher}"
                )
                owners[name] = str(matcher)
