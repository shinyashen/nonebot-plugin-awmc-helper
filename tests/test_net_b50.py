"""日服 NET 数据源测试：B50 组装（JP 视图映射）+ 绑定/切换/查询命令流。

组装层不触发抓取（fetch_records 由命令流测试 monkeypatch），JP 视图
注入用 make_song/make_diff 构造 + monkeypatch song_service.jp_all。
"""

import respx
import pytest
import nonebot
from mocks import make_diff, make_song, requires_assets
from nonebug import App

BASE = "https://maimaidx.jp"
MOBILE = f"{BASE}/maimai-mobile"

LOGIN_PAGE = (
    "<html><body><form>"
    '<input type="hidden" name="token" value="tok123">'
    "</form></body></html>"
)


@pytest.fixture
async def db(tmp_path):
    from nonebot_plugin_awmc_helper.core import store

    store.set_db_file(tmp_path / "awmc.db")
    await store.init_db()
    yield store
    store.set_db_file(None)


@pytest.fixture
def jp_view(monkeypatch):
    """注入最小 JP 视图（绕过规范表）：新旧版本两曲 + 同名 SD/DX 折叠曲。"""
    from maimai_py import SongType, LevelIndex

    from nonebot_plugin_awmc_helper.core import songdb
    from nonebot_plugin_awmc_helper.core.songs import song_service

    songs = [
        # 旧版本 DX 曲（v=24000 → b35）：定数 13.0
        make_song(
            1,
            "旧曲テスト",
            diffs=[
                make_diff(
                    type=SongType.DX,
                    level_index=LevelIndex.MASTER,
                    level="13",
                    level_value=13.0,
                    version=24000,
                )
            ],
        ),
        # MAGiCAL 曲（v=27000 → b15）：定数 14.0
        make_song(
            2,
            "新曲テスト",
            version=27000,
            diffs=[
                make_diff(
                    type=SongType.DX,
                    level_index=LevelIndex.ReMASTER,
                    level="14",
                    level_value=14.0,
                    version=27000,
                )
            ],
        ),
        # SD/DX 折叠曲：SD master 12.0（旧）、DX master 13.7（新）
        make_song(
            3,
            "折曲",
            diffs=[
                make_diff(
                    type=SongType.STANDARD,
                    level_index=LevelIndex.MASTER,
                    level="12",
                    level_value=12.0,
                    version=24000,
                ),
                make_diff(
                    type=SongType.DX,
                    level_index=LevelIndex.MASTER,
                    level="13+",
                    level_value=13.7,
                    version=27000,
                ),
            ],
        ),
    ]

    async def fake_jp_all():
        return songs

    monkeypatch.setattr(song_service, "jp_all", fake_jp_all)
    monkeypatch.setattr(songdb, "CURRENT_FINGERPRINT", "net-test-fp")
    return songs


@pytest.fixture
def net_service():
    from nonebot_plugin_awmc_helper.core.net_score import net_score_service

    net_score_service.cooldown = type(net_score_service.cooldown)()  # 全新冷却表
    net_score_service._title_index = (None, {})
    return net_score_service


def _records():
    from nonebot_plugin_awmc_helper.core.ext.net import NetRecord

    return [
        # 旧曲 100.5% → ra = int(22.4 * 13.0 * 100.5/100) = 292（b35）
        NetRecord(
            title="旧曲テスト",
            type="dx",
            difficulty="master",
            achievement=100.5,
            dx_score=1500,
            dx_score_total=2000,
            fc="ap",
            fs=None,
        ),
        # 新曲 100.0% → ra = int(21.6 * 14.0) = 302（b15）
        NetRecord(
            title="新曲テスト",
            type="dx",
            difficulty="remaster",
            achievement=100.0,
            dx_score=1800,
            dx_score_total=2000,
            fc=None,
            fs="fsd",
        ),
        # 折曲 DX master 13.7（v=27000 → b15）100.5% → int(22.4 * 13.7 * 1.005)
        NetRecord(
            title="折曲",
            type="dx",
            difficulty="master",
            achievement=100.5,
            dx_score=1950,
            dx_score_total=2000,
            fc=None,
            fs=None,
        ),
        # 折曲 SD master 12.0（v=24000 → b35）98.0% → int(20.3 * 12.0)
        NetRecord(
            title="折曲",
            type="standard",
            difficulty="master",
            achievement=98.0,
            dx_score=800,
            dx_score_total=1000,
            fc=None,
            fs=None,
        ),
        # 未匹配（数据滞后）→ 跳过并 warning
        NetRecord(
            title="不存在曲",
            type="dx",
            difficulty="master",
            achievement=95.0,
        ),
    ]


@pytest.mark.asyncio
async def test_build_b50_mapping_and_split(net_service, jp_view):
    """NET 记录 → ScoreExtend：ra/DX id 偏移/FC-FS/b35-b15 版本划分/rating 合计。"""
    from maimai_py import SongType

    bests = await net_service.build_b50(_records())
    all_scores = bests.scores
    assert len(all_scores) == 4  # 未匹配曲被跳过

    old = next(s for s in all_scores if s.title == "旧曲テスト")
    assert old.id == 1 + 10000  # DX 谱 id = 根 id + 10000
    assert old.dx_rating == int(
        22.4 * 13.0 * 1.005
    )  # ra = int(c * ds * min(100.5, a)/100)
    assert old.rate.name == "SSSP"
    assert old.fc.name == "AP"
    assert old.version == 24000

    new = next(s for s in all_scores if s.title == "新曲テスト")
    assert new.dx_rating == int(21.6 * 14.0)  # 100% → c=21.6, a=100
    assert new.fs.name == "FSD"

    assert {s.title for s in bests.scores_b15} == {"新曲テスト", "折曲"}
    assert {s.title for s in bests.scores_b35} == {"旧曲テスト", "折曲"}
    # SD 与 DX 是两条成绩（同曲折叠视图，谱面类型区分）
    assert {s.type for s in bests.scores_b15} == {SongType.DX}
    # rating = b35 + b15 的 ra 合计（折曲 SD 98% → c=20.3 × 12.0 × 0.98）
    assert bests.rating == bests.rating_b35 + bests.rating_b15
    assert bests.rating_b35 == int(22.4 * 13.0 * 1.005) + int(20.3 * 12.0 * 0.98)
    assert bests.rating_b15 == int(21.6 * 14.0) + int(22.4 * 13.7 * 1.005)


@pytest.mark.asyncio
async def test_build_b50_sorted_and_capped(net_service, jp_view):
    """b35/b15 按 (ra, dx_score, achievements) 降序，容量 35/15 封顶。"""
    from nonebot_plugin_awmc_helper.core.ext.net import NetRecord

    records = [
        NetRecord(
            title="旧曲テスト",
            type="dx",
            difficulty="master",
            achievement=100.5,
            dx_score=100 + i,
            dx_score_total=2000,
        )
        for i in range(40)  # 同一谱面同记录重复：去重键在 dxrating 侧，这里 40 条同名
    ]
    bests = await net_service.build_b50(records)
    # 全部映射到同一谱面（标题相同）→ b35 35 条（v=24000），b15 空
    assert len(bests.scores_b35) == 35
    assert bests.scores_b15 == []
    ratings = [s.dx_rating for s in bests.scores_b35]
    assert ratings == sorted(ratings, reverse=True)


@pytest.mark.asyncio
async def test_cooldown_book(net_service, monkeypatch):
    """冷却书：首次 0、窗口内返剩余秒、release 清零、配置 0 关闭。"""
    import pytest_asyncio  # noqa: F401

    from nonebot_plugin_awmc_helper.config import plugin_config
    from nonebot_plugin_awmc_helper.core.net_score import NetCooldownBook

    book = NetCooldownBook()
    monkeypatch.setattr(plugin_config, "awmc_net_cooldown_minutes", 15)
    assert book.try_acquire("p", "u") == 0
    assert 0 < book.try_acquire("p", "u") <= 900
    assert book.try_acquire("p", "other") == 0  # 用户间互不影响
    book.release("p", "u")
    assert book.try_acquire("p", "u") == 0

    monkeypatch.setattr(plugin_config, "awmc_net_cooldown_minutes", 0)
    book2 = NetCooldownBook()
    assert book2.try_acquire("p", "u") == 0
    assert book2.try_acquire("p", "u") == 0


# ---------------------------------------------------------------------------
# 命令流：绑定日服 / 数据源 2 / b50（nonebug + respx）
# ---------------------------------------------------------------------------


async def _mock_net_login_success(m):
    m.get(f"{MOBILE}/").respond(text=LOGIN_PAGE)
    m.post(f"{MOBILE}/submit/").respond(302, headers={"location": f"{MOBILE}/home"})
    m.get(f"{MOBILE}/aimeList/").respond(text="ok")
    m.get(f"{MOBILE}/aimeList/submit/", params={"idx": "0"}).respond(text="ok")
    m.get(f"{MOBILE}/home/").respond(text="ok")


async def _assert_reply(app, matcher, text, reply, *, user_id=12345678):
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment

    event = fake_group_message_event_v11(message=text, user_id=user_id)
    async with app.test_matcher(matcher) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot_get_adapter())
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


def nonebot_get_adapter():
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    return nonebot.get_adapter(OnebotV11Adapter)


@pytest.mark.asyncio
async def test_net_bind_command(app: App, db, net_service):
    """绑定日服：respx 登录成功 → 凭据落库 + service=net。"""
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter  # noqa: F401

    from nonebot_plugin_awmc_helper.plugins import bind
    from nonebot_plugin_awmc_helper.core.binding import SERVICE_NET, binding_service

    with respx.mock(assert_all_called=False) as m:
        await _mock_net_login_success(m)
        await _assert_reply(
            app,
            bind.net_bind,
            "绑定日服 sega_user password123",
            "已绑定日服 NET（SEGA ID：sega_user），"
            "当前数据源已切换为日服。支持指令：b50",
        )
    binding = await binding_service.get("OneBot V11", "12345678")
    assert binding is not None
    assert binding.net_sega_id == "sega_user"
    assert binding.net_password == "password123"
    assert binding.service == SERVICE_NET


@pytest.mark.asyncio
async def test_net_bind_wrong_password(app: App, db, net_service):
    """凭据验证失败（invalid_credentials）→ 不保存绑定。"""
    from nonebot_plugin_awmc_helper.plugins import bind
    from nonebot_plugin_awmc_helper.core.binding import binding_service

    with respx.mock(assert_all_called=False) as m:
        m.get(f"{MOBILE}/").respond(text=LOGIN_PAGE)
        m.post(f"{MOBILE}/submit/").respond(
            302, headers={"location": f"{MOBILE}/error/?code=701"}
        )
        await _assert_reply(
            app,
            bind.net_bind,
            "绑定日服 sega_user badpw",
            "SEGA ID 或密码错误，绑定未保存",
        )
    binding = await binding_service.get("OneBot V11", "12345678")
    # ensure 会建默认行，但凭据与 service 均未变更
    assert binding is not None
    assert binding.net_sega_id is None
    assert binding.net_password is None


@pytest.mark.asyncio
async def test_net_bind_usage_hint(app: App, db):
    """无参数 → 用法与风险提示，不触发网络。"""
    from nonebot_plugin_awmc_helper.plugins import bind

    usage = (
        "用法：绑定日服 <SEGA ID> <密码>\n\n"
        "⚠️ 该数据源需提供 SEGA 账号密码（仅存于本机数据库，用于登录"
        "官方 maimai NET 抓取成绩，仅支持 b50）。密码级别敏感，"
        "建议私聊机器人操作，且不要使用与其他服务相同的密码。"
    )
    await _assert_reply(app, bind.net_bind, "绑定日服", usage)


@pytest.mark.asyncio
async def test_set_provider_net(app: App, db, net_service):
    """数据源 2：未绑定 NET 时拒绝；绑定后切换成功。"""
    from nonebot_plugin_awmc_helper.plugins import bind
    from nonebot_plugin_awmc_helper.core.binding import SERVICE_NET, binding_service

    await _assert_reply(
        app, bind.set_provider, "数据源 2", "尚未绑定日服 NET，无法切换数据源"
    )
    binding = await binding_service.ensure("OneBot V11", "12345678")
    await binding_service.bind_net(binding, sega_id="sid", password="pw")
    await _assert_reply(app, bind.set_provider, "数据源 2", "数据源已切换为日服 NET")
    fresh = await binding_service.get("OneBot V11", "12345678")
    assert fresh.service == SERVICE_NET


@pytest.mark.asyncio
async def test_b50_net_unsupported_commands(app: App, db, net_service, jp_view):
    """net 数据源下 ap50 等指令给出能力边界提示（score 层统一拦截）。"""
    from nonebot_plugin_awmc_helper.plugins import score_query
    from nonebot_plugin_awmc_helper.core.binding import binding_service

    binding = await binding_service.ensure("OneBot V11", "12345678")
    await binding_service.bind_net(binding, sega_id="sid", password="pw")
    await _assert_reply(
        app,
        score_query.ap50,
        "ap50",
        "日服数据源（NET）暂不支持该指令，敬请期待后续版本",
    )


@requires_assets
@pytest.mark.asyncio
async def test_b50_net_command(app: App, db, net_service, jp_view, monkeypatch):
    """b50 全链路：NET 数据源 → 抓取（mock）→ JP 组装 → B50 图渲染。"""
    import base64

    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment

    from nonebot_plugin_awmc_helper.plugins import score_query
    from nonebot_plugin_awmc_helper.core.binding import binding_service
    from nonebot_plugin_awmc_helper.core.render.best50 import best50_bytes

    binding = await binding_service.ensure("OneBot V11", "12345678")
    await binding_service.bind_net(binding, sega_id="sid", password="pw")

    async def fake_fetch(b):
        return _records()

    monkeypatch.setattr(net_service, "fetch_records", fake_fetch)

    bests = await net_service.build_b50(_records())
    expected_png = await best50_bytes(
        "sid",  # 卡片显示名 = SEGA ID（NET 首页玩家名结构未考证）
        bests.rating,
        bests.rating_b35,
        bests.rating_b15,
        bests.scores_b35,
        bests.scores_b15,
        player=None,
        qqid=12345678,
        service="net",
        theme="prism_plus",
    )
    event = fake_group_message_event_v11(message="b50", user_id=12345678)
    async with app.test_matcher(score_query.b50) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot_get_adapter())
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
                        f"base64://{base64.b64encode(expected_png).decode()}"
                    ),
                ]
            ),
            result=None,
            bot=bot,
        )
        ctx.should_finished()
    # 成功查询后冷却生效：再次查询提示冷却
    remain = net_service.cooldown.try_acquire("OneBot V11", "12345678")
    assert remain > 0
