"""awmc.dan 段位子插件 handler 测试（nonebug + onebot v11 假事件）。

覆盖边界：只覆盖不依赖真实曲库重设施的分支——裸「段位」列表回复、未识别
段位名报错、段位名出卡（版本前缀解析 + 渲染层 mock，成绩链以未绑定降级）、
随机档位总览（合并转发成功与协议端失败降级两分支）。段位表数据沿用
test_core_dan 的内联 fixture 口径（真实快照同构构造）；课题曲 join 走
song_id=None 降级（离线口径见 test_core_dan.test_card_data_degrades_without_binding），
出卡/渲染细节由 test_core_dan 与 test_render_dan.py 覆盖。
"""

import base64

import pytest
from nonebug import App

# 内联 fixture：MAGiCAL 段位表（27000，三段）+ BUDDiES 段位表（24000，供
# 「舞萌dx2024十段」版本前缀跨版本路径）+ 随机段位一档。谱面行取真实快照曲。
FIXTURE = """
- title: MAGiCAL 段位認定
  id: magical-dan
  sections:
    - title: 【初段】
      description: ❤ 350｜-0/-2/-5｜+20
      sheets:
        - 魂のルフラン|dx|basic
        - WARNING×WARNING×WARNING|dx|basic
        - オーバーライド|dx|basic
        - その群青が愛しかったようだった|std|basic
    - title: 【十段】
      description: ❤ 900｜-2/-2/-5｜+30
      sheets:
        - Nyan Cat EX|std|master
        - LiftOff|dx|master
        - Destiny Runner|dx|master
        - るろうらんる|dx|master
    - title: 【裏皆伝】
      description: ❤ 10｜-1/-3/-10｜+0
      sheets:
        - PANDORA PARADOXXX|std|remaster
        - 存在しない曲名|dx|master
- title: BUDDiES 段位認定
  id: bud-dan
  sections:
    - title: 【十段】
      description: ❤ 900｜-2/-2/-5｜+30
      sheets:
        - Nyan Cat EX|std|master
        - LiftOff|dx|master
        - Destiny Runner|dx|master
        - るろうらんる|dx|master
- title: ランダム段位認定
  id: random-dan
  sections:
    - title: 【MASTER 超上級】
      description: ❤ 100｜-2/-3/-5｜+10
      sheets:
        - ランダムで選曲されます|rnd|master|14~14+
        - ランダムで選曲されます|rnd|master|14~14+
        - ランダムで選曲されます|rnd|master|14~14+
        - ランダムで選曲されます|rnd|master|14~14+
"""

_SELF_ID = "1234567890"  # 合并转发节点 uin 取 bot self_id，须为数字串
_FAKE_PNG = b"\x89PNG-fake-render"


@pytest.fixture
async def dan_ready(tmp_path):
    """独立临时库 + 段位表入库（refresh 后 KV 时间戳新鲜，ensure_loaded 不触网）。"""
    from nonebot_plugin_awmc_helper.core import dan, store

    store.set_db_file(tmp_path / "awmc.db")
    await store.init_db()
    await dan.refresh(text=FIXTURE)
    yield
    store.set_db_file(None)


async def _run(
    app: App,
    matcher,
    text: str,
    reply: str,
    *,
    user_id=12345678,
    session_fetches: int = 1,
):
    """群聊事件跑 handler：uninfo 会话（n 组群/成员信息）+ at 前缀文本回复。"""
    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    event = fake_group_message_event_v11(message=text, user_id=user_id)
    async with app.test_matcher(matcher) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter))
        ctx.receive_event(bot, event)
        for _ in range(session_fetches):
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
            Message([MessageSegment.at(user_id), MessageSegment.text(f" {reply}")]),
            result=None,
            bot=bot,
        )
        ctx.should_finished()


@pytest.mark.asyncio
async def test_dan_list_reply(app: App, dan_ready):
    """裸「段位」：回复当前版本可用段位与随机/跨版本用法（列表分支）。"""
    from nonebot_plugin_awmc_helper.plugins.dan import matchers

    await _run(
        app,
        matchers.dan_cmd,
        "段位",
        "可用段位（当前版本）：初段 / 十段 / 裏皆伝\n"
        "随机段位：EXPERT/MASTER × 初級~超上級（如「段位 紫超上」）；\n"
        "跨版本：段位名前加版本前缀（如「段位 bud+裏皆传」「段位 dx初段」"
        "「段位 舞萌2022十段」），不带前缀为当前版本；"
        "「段位 随机」查看随机段位规则总览",
    )


@pytest.mark.asyncio
async def test_dan_unknown_name(app: App, dan_ready):
    """未识别段位名：报错并引导回列表（不再尝试出卡）。"""
    from nonebot_plugin_awmc_helper.plugins.dan import matchers

    await _run(
        app,
        matchers.dan_cmd,
        "段位 不存在的段位",
        "未识别的段位名；发送「段位」查看可用段位",
    )


@pytest.mark.asyncio
async def test_dan_card_by_version_prefix(app: App, dan_ready, monkeypatch):
    """「舞萌dx2024十段」：年份前缀查表 24000 → 出卡（渲染层 mock，未绑定降级）。"""
    from nonebot_plugin_awmc_helper.plugins.dan import matchers

    async def _no_binding(session, event):
        return None

    monkeypatch.setattr(matchers, "_usable_binding", _no_binding)
    monkeypatch.setattr(matchers, "render_dan_card", lambda data: _FAKE_PNG)

    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    event = fake_group_message_event_v11(
        message="段位 舞萌dx2024十段", user_id=12345678
    )
    async with app.test_matcher(matchers.dan_cmd) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot.get_adapter(OnebotV11Adapter))
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
        ctx.should_call_send(
            event,
            Message(
                [
                    MessageSegment.at(12345678),
                    MessageSegment.image(
                        f"base64://{base64.b64encode(_FAKE_PNG).decode()}"
                    ),
                ]
            ),
            result=None,
            bot=bot,
        )
        ctx.should_finished()


def _forward_nodes(texts: list[str]) -> list[dict]:
    """try_send_forward 的节点构造期望（name/uin 键裸 dict，见 core/forward.py）。"""
    from nonebot_plugin_awmc_helper.core.forward import NODE_NICKNAME

    return [
        {
            "type": "node",
            "data": {
                "name": NODE_NICKNAME,
                "uin": _SELF_ID,
                "content": [{"type": "text", "data": {"text": text}}],
            },
        }
        for text in texts
    ]


_RANDOM_OVERVIEW = [
    "【随机段位认定】（四曲独立随机抽取，重复不回避）",
    "MASTER 超上級：Lv 14~14+（定数 14.5~14.9，实测）\n❤100 -2/-3/-5 每曲 +10",
    "通关奖励：1.5 倍奖励票；「段位 <档名>」查看单档段位卡",
]


@pytest.mark.asyncio
async def test_dan_random_overview_forward(app: App, dan_ready):
    """「段位 随机」：OB11 走合并转发（头部/每档/尾注各一节点），不再发普通消息。"""
    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    from nonebot_plugin_awmc_helper.plugins.dan import matchers

    forward = _forward_nodes(_RANDOM_OVERVIEW)
    event = fake_group_message_event_v11(message="段位 随机", user_id=12345678)
    async with app.test_matcher(matchers.dan_cmd) as ctx:
        bot = ctx.create_bot(
            base=Bot,
            adapter=nonebot.get_adapter(OnebotV11Adapter),
            self_id=_SELF_ID,
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
        # message/messages 双参数（LLOneBot 读 message、NapCat 读 messages）
        ctx.should_call_api(
            "send_group_forward_msg",
            {"group_id": 87654321, "message": forward, "messages": forward},
            result=None,
        )
        ctx.should_finished()


@pytest.mark.asyncio
async def test_dan_random_overview_fallback(app: App, dan_ready):
    """合并转发失败（协议端报错）→ 降级为单条文本总览。"""
    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    from nonebot_plugin_awmc_helper.plugins.dan import matchers

    forward = _forward_nodes(_RANDOM_OVERVIEW)
    event = fake_group_message_event_v11(message="段位 随机", user_id=12345678)
    async with app.test_matcher(matchers.dan_cmd) as ctx:
        bot = ctx.create_bot(
            base=Bot,
            adapter=nonebot.get_adapter(OnebotV11Adapter),
            self_id=_SELF_ID,
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
        ctx.should_call_api(
            "send_group_forward_msg",
            {"group_id": 87654321, "message": forward, "messages": forward},
            result=None,
            exception=RuntimeError("protocol error"),
        )
        ctx.should_call_send(
            event,
            Message(
                [
                    MessageSegment.at(12345678),
                    MessageSegment.text(" " + "\n".join(_RANDOM_OVERVIEW)),
                ]
            ),
            result=None,
            bot=bot,
        )
        ctx.should_finished()
