"""日服 NET 数据源测试：B50 组装（JP 视图映射）+ 绑定/切换/查询命令流。

绑定日服仅限私聊（SEGA 密码不进聊天记录），命令流用例均走私聊事件；
组装层不触发抓取（fetch_records 由命令流测试 monkeypatch），JP 视图
注入用 make_song/make_diff 构造 + monkeypatch song_service 内部视图。
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
        # 旧版本 DX 曲（v=20500 → b35）：コネクト MASTER 真实定数 12.8
        make_song(
            21,
            "コネクト",
            diffs=[
                make_diff(
                    type=SongType.DX,
                    level_index=LevelIndex.MASTER,
                    level="12",
                    level_value=12.8,
                    version=20500,
                )
            ],
        ),
        # MAGiCAL 曲（v=27000 → b15）：物語はここから MASTER 真实定数 13.5
        make_song(
            2020,
            "物語はここから",
            version=27000,
            diffs=[
                make_diff(
                    type=SongType.DX,
                    level_index=LevelIndex.MASTER,
                    level="13",
                    level_value=13.5,
                    version=27000,
                )
            ],
        ),
        # SD/DX 折叠曲（真实老曲补 DX）：Believe the Rainbow——
        # SD master 13.4（FiNALE 19999 → b35）、DX master 13.0（PRiSM PLUS → b15）
        make_song(
            835,
            "Believe the Rainbow",
            diffs=[
                make_diff(
                    type=SongType.STANDARD,
                    level_index=LevelIndex.MASTER,
                    level="13",
                    level_value=13.4,
                    version=19999,
                ),
                make_diff(
                    type=SongType.DX,
                    level_index=LevelIndex.MASTER,
                    level="13",
                    level_value=13.0,
                    version=25500,
                ),
            ],
        ),
    ]

    async def fake_jp_all():
        return songs

    async def fake_jp_map():
        return {s.id: s for s in songs}

    monkeypatch.setattr(song_service, "jp_all", fake_jp_all)
    monkeypatch.setattr(song_service, "_jp_songs_map", fake_jp_map)
    monkeypatch.setattr(songdb, "CURRENT_FINGERPRINT", "net-test-fp")
    return songs


@pytest.fixture
def net_service(monkeypatch):
    from nonebot_plugin_awmc_helper.config import plugin_config
    from nonebot_plugin_awmc_helper.core.net_score import net_score_service

    net_score_service._window_cache.clear()
    net_score_service._fail_until.clear()
    net_score_service._title_index = (None, {})
    monkeypatch.setattr(plugin_config, "awmc_net_cooldown_minutes", 15)
    return net_score_service


def _records():
    from nonebot_plugin_awmc_helper.core.ext.net import NetRecord

    return [
        # コネクト 100.5% → ra = int(22.4 * 12.8 * 1.005)（b35）
        NetRecord(
            title="コネクト",
            type="dx",
            difficulty="master",
            achievement=100.5,
            dx_score=1500,
            dx_score_total=2000,
            fc="ap",
            fs=None,
        ),
        # 物語はここから 100.0% → ra = int(21.6 * 13.5)（b15）
        NetRecord(
            title="物語はここから",
            type="dx",
            difficulty="master",
            achievement=100.0,
            dx_score=1800,
            dx_score_total=2000,
            fc=None,
            fs="fsd",
        ),
        # Believe the Rainbow DX master 13.0 + SD master 13.4（老曲补 DX，真实双版本）
        NetRecord(
            title="Believe the Rainbow",
            type="dx",
            difficulty="master",
            achievement=100.5,
            dx_score=1950,
            dx_score_total=2000,
            fc=None,
            fs=None,
        ),
        NetRecord(
            title="Believe the Rainbow",
            type="standard",
            difficulty="master",
            achievement=98.0,
            dx_score=800,
            dx_score_total=1000,
            fc=None,
            fs=None,
        ),
        # 未匹配（数据滞后）→ 跳过并 warning（构造条目）
        NetRecord(
            title="（构造）不在库曲",
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

    old = next(s for s in all_scores if s.title == "コネクト")
    assert old.id == 21 + 10000  # DX 谱 id = 根 id + 10000
    assert old.dx_rating == int(
        22.4 * 12.8 * 1.005
    )  # ra = int(c * ds * min(100.5, a)/100)
    assert old.rate.name == "SSSP"
    assert old.fc.name == "AP"
    assert old.version == 20500

    new = next(s for s in all_scores if s.title == "物語はここから")
    assert new.dx_rating == int(21.6 * 13.5)  # 100% → c=21.6, a=100
    assert new.fs.name == "FSD"

    # b35/b15 用日服现行版本（current_version_jp = MAGiCAL 27000）划分：
    # 仅物語はここから（27000）入 b15；835 新旧两体与コネクト全在 b35
    assert {s.title for s in bests.scores_b15} == {"物語はここから"}
    assert {s.title for s in bests.scores_b35} == {"コネクト", "Believe the Rainbow"}
    # SD 与 DX 是两条成绩（同曲折叠视图，谱面类型区分）
    assert {s.type for s in bests.scores_b35} == {SongType.STANDARD, SongType.DX}
    # rating = b35 + b15 的 ra 合计（835 SD 98% → c=20.3 × 13.4 × 0.98）
    assert bests.rating == bests.rating_b35 + bests.rating_b15
    assert bests.rating_b35 == int(22.4 * 12.8 * 1.005) + int(20.3 * 13.4 * 0.98) + int(
        22.4 * 13.0 * 1.005
    )
    assert bests.rating_b15 == int(21.6 * 13.5)


@pytest.mark.asyncio
async def test_build_b50_sorted_and_capped(net_service, jp_view):
    """b35/b15 按 (ra, dx_score, achievements) 降序，容量 35/15 封顶。"""
    from nonebot_plugin_awmc_helper.core.ext.net import NetRecord

    records = [
        NetRecord(
            title="コネクト",
            type="dx",
            difficulty="master",
            achievement=100.5,
            dx_score=100 + i,
            dx_score_total=2000,
        )
        for i in range(40)
    ]
    bests = await net_service.build_b50(records)
    # 全部映射到同一谱面（标题相同）→ b35 35 条（v=20500），b15 空
    assert len(bests.scores_b35) == 35
    assert bests.scores_b15 == []
    ratings = [s.dx_rating for s in bests.scores_b35]
    assert ratings == sorted(ratings, reverse=True)

    ratings = [s.dx_rating for s in bests.scores_b35]
    assert ratings == sorted(ratings, reverse=True)


@pytest.mark.asyncio
async def test_title_index_with_none_fingerprint(net_service, jp_view, monkeypatch):
    """指纹为 None（重启后未重建）时索引不得误命中空缓存——服务器实测回归：

    CURRENT_FINGERPRINT 仅在重建管线赋值，重启后为 None；命中判定若不检查
    索引非空，初始空缓存（None == None）直接返回 → 全部记录未匹配。
    """
    from nonebot_plugin_awmc_helper.core import songdb

    monkeypatch.setattr(songdb, "CURRENT_FINGERPRINT", None)
    net_service._title_index = (None, {})  # 模拟重启后的初始态
    index = await net_service._title_index_map()
    assert index  # 空索引未误命中：已按 JP 视图构建
    chart = net_service._resolve_chart(index, _records()[0])
    assert chart is not None  # 「コネクト」可匹配


@pytest.mark.asyncio
async def test_window_cache_and_backoff(net_service, jp_view, monkeypatch):
    """窗口缓存：首查抓取、窗口内复用（0 请求）；抓取失败进入短退避。"""
    import time as _time

    from nonebot_plugin_awmc_helper.core.binding import binding_service
    from nonebot_plugin_awmc_helper.core.ext.net import NetError
    from nonebot_plugin_awmc_helper.core.net_score import NetScoreError

    binding = await binding_service.ensure("OneBot V11", "12345678")
    await binding_service.bind_net(binding, sega_id="sid", password="pw")

    calls = []

    async def fake_fetch(b):
        calls.append(1)
        return _records(), None

    monkeypatch.setattr(net_service, "fetch_records", fake_fetch)

    assert net_service.needs_fetch(binding)  # 无缓存 → 需要抓取
    scores, from_cache = await net_service.get_scores(binding)
    assert not from_cache
    assert len(scores) == 4
    assert not net_service.needs_fetch(binding)  # 窗口内不再抓取
    again, from_cache = await net_service.get_scores(binding)
    assert from_cache
    assert again is scores
    assert len(calls) == 1

    # 过期 → 重新抓取
    fetched_at, cached, _ = net_service._window_cache[
        (binding.platform, binding.user_id)
    ]
    net_service._window_cache[(binding.platform, binding.user_id)] = (
        fetched_at - 16 * 60,
        cached,
        None,
    )
    assert net_service.needs_fetch(binding)
    _, from_cache = await net_service.get_scores(binding)
    assert not from_cache
    assert len(calls) == 2

    # 抓取失败（清缓存后触发）→ 透传异常 + 短退避：退避内不再发起真实抓取
    net_service._window_cache.clear()

    async def fail_fetch(b):
        calls.append(1)
        raise NetError("invalid_credentials")

    monkeypatch.setattr(net_service, "fetch_records", fail_fetch)
    with pytest.raises(NetError):
        await net_service.get_scores(binding)
    assert len(calls) == 3
    with pytest.raises(NetScoreError):
        await net_service.get_scores(binding)
    assert len(calls) == 3  # 退避窗口内未再抓取
    # 退避过期后恢复
    net_service._fail_until[(binding.platform, binding.user_id)] = _time.monotonic() - 1
    with pytest.raises(NetError):
        await net_service.get_scores(binding)
    assert len(calls) == 4

    # 冷却窗口置 0：每次都视为需要抓取（禁用缓存）
    from nonebot_plugin_awmc_helper.config import plugin_config

    monkeypatch.setattr(plugin_config, "awmc_net_cooldown_minutes", 0)
    assert net_service.needs_fetch(binding)


@pytest.mark.asyncio
async def test_player_identity_cached_with_window(net_service, jp_view, monkeypatch):
    """首页身份随窗口缓存：抓取时写入、窗口内 player_of 复用、过期后随成绩重抓。"""
    from nonebot_plugin_awmc_helper.core.binding import binding_service
    from nonebot_plugin_awmc_helper.core.ext.net import NetPlayer

    binding = await binding_service.ensure("OneBot V11", "12345678")
    await binding_service.bind_net(binding, sega_id="sid", password="pw")

    player = NetPlayer(name="プレイヤー", rating=10516)

    async def fake_fetch(b):
        return _records(), player

    monkeypatch.setattr(net_service, "fetch_records", fake_fetch)
    await net_service.get_scores(binding)
    assert net_service.player_of(binding) is player

    # 人为过期 → 重抓 → 身份随之更新
    fetched_at, cached, _ = net_service._window_cache[
        (binding.platform, binding.user_id)
    ]
    net_service._window_cache[(binding.platform, binding.user_id)] = (
        fetched_at - 16 * 60,
        cached,
        None,
    )
    player2 = NetPlayer(name="改名後", rating=11000)

    async def fake_fetch2(b):
        return _records(), player2

    monkeypatch.setattr(net_service, "fetch_records", fake_fetch2)
    await net_service.get_scores(binding)
    assert net_service.player_of(binding) is player2


@pytest.mark.asyncio
async def test_nameplate_url_persisted_and_backfilled(
    net_service, jp_view, db, monkeypatch
):
    """装备名牌 kv_cache 持久化：抓到即存，后续弹回时回填上次结果。"""
    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.core.binding import binding_service
    from nonebot_plugin_awmc_helper.core.ext.net import NetPlayer

    binding = await binding_service.ensure("OneBot V11", "12345678")
    await binding_service.bind_net(binding, sega_id="sid", password="pw")
    key = "net_nameplate:OneBot V11:12345678"

    player = NetPlayer(name="p", rating=1, nameplate_url="https://x/a.png")

    async def fake_fetch(b):
        return _records(), player

    monkeypatch.setattr(net_service, "fetch_records", fake_fetch)
    await net_service.get_scores(binding)
    assert await store.kv_get(key) == "https://x/a.png"

    # 下一次抓取弹回（url 为 None）→ 从 kv 回填，卡面不抖回缺省
    player2 = NetPlayer(name="p", rating=1, nameplate_url=None)

    async def fake_fetch2(b):
        return _records(), player2

    monkeypatch.setattr(net_service, "fetch_records", fake_fetch2)
    net_service._window_cache.clear()
    await net_service.get_scores(binding)
    assert net_service.player_of(binding).nameplate_url == "https://x/a.png"
    # 未更新时不重复写库
    row = await store.kv_get(key)
    assert row == "https://x/a.png"


@pytest.mark.asyncio
async def test_nameplate_default_clears_cached_custom(
    net_service, jp_view, db, monkeypatch
):
    """确认装备「デフォルト」框 → 清兜底缓存，旧自定义名牌不复活。

    收藏品页抓得到时默认框是**确认态**（区别于抓取失败的未知态）：反向写
    空串而非回填；之后某次弹回（未知态）读到空串也不回填，卡面稳定落缺省牌。
    """
    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.core.binding import binding_service
    from nonebot_plugin_awmc_helper.core.ext.net import NetPlayer

    binding = await binding_service.ensure("OneBot V11", "12345678")
    await binding_service.bind_net(binding, sega_id="sid", password="pw")
    key = "net_nameplate:OneBot V11:12345678"

    player = NetPlayer(name="p", rating=1, nameplate_url="https://x/a.png")

    async def fake_fetch(b):
        return _records(), player

    monkeypatch.setattr(net_service, "fetch_records", fake_fetch)
    await net_service.get_scores(binding)
    assert await store.kv_get(key) == "https://x/a.png"

    # 换回「デフォルト」框：确认态 → 反向写空串，而非回填旧自定义名牌
    default_player = NetPlayer(name="p", rating=1, nameplate_is_default=True)

    async def fake_fetch_default(b):
        return _records(), default_player

    monkeypatch.setattr(net_service, "fetch_records", fake_fetch_default)
    net_service._window_cache.clear()
    await net_service.get_scores(binding)
    assert await store.kv_get(key) == ""
    assert net_service.player_of(binding).nameplate_url is None
    assert net_service.player_of(binding).nameplate_is_default is True

    # 之后某次收藏品区弹回（未知态）：空串不回填，nameplate_url 保持 None
    async def fake_fetch_unknown(b):
        return _records(), NetPlayer(name="p", rating=1)

    monkeypatch.setattr(net_service, "fetch_records", fake_fetch_unknown)
    net_service._window_cache.clear()
    await net_service.get_scores(binding)
    assert net_service.player_of(binding).nameplate_url is None


# ---------------------------------------------------------------------------
# 命令流：绑定日服（仅私聊）/ 数据源 2 / b50 / minfo（nonebug + respx）
# ---------------------------------------------------------------------------


def _mock_net_login_success(m):
    m.get(f"{MOBILE}/").respond(text=LOGIN_PAGE)
    m.post(f"{MOBILE}/submit/").respond(302, headers={"location": f"{MOBILE}/home"})
    m.get(f"{MOBILE}/aimeList/").respond(text="ok")
    m.get(f"{MOBILE}/aimeList/submit/", params={"idx": "0"}).respond(text="ok")
    m.get(f"{MOBILE}/home/").respond(text="ok")


def nonebot_get_adapter():
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    return nonebot.get_adapter(OnebotV11Adapter)


async def _assert_reply(
    app: App,
    matcher,
    text: str,
    reply: str,
    *,
    user_id=12345678,
    private: bool = True,
):
    """断言单条文本回复；私聊（默认）：无 at、发送层去前导空格。"""
    from fake import (
        fake_group_message_event_v11,
        fake_private_message_event_v11,
    )
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment

    event = (
        fake_private_message_event_v11(message=text, user_id=user_id)
        if private
        else fake_group_message_event_v11(message=text, user_id=user_id)
    )
    async with app.test_matcher(matcher) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot_get_adapter())
        ctx.receive_event(bot, event)
        if not private:
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
            expected = Message(
                [MessageSegment.at(user_id), MessageSegment.text(f" {reply}")]
            )
        else:
            expected = Message([MessageSegment.text(reply)])
        ctx.should_call_send(event, expected, result=None, bot=bot)
        ctx.should_finished()


@pytest.mark.asyncio
async def test_net_bind_group_rejected(app: App, db):
    """群内绑定日服 → 直接拒绝（密码不进聊天记录），不触发网络。"""
    from nonebot_plugin_awmc_helper.plugins import bind

    await _assert_reply(
        app,
        bind.net_bind,
        "绑定日服 sega_user password123",
        "绑定日服需要提交 SEGA 账号密码，请私聊机器人操作",
        private=False,
    )


@pytest.mark.asyncio
async def test_net_bind_command(app: App, db, net_service):
    """私聊绑定日服：respx 登录成功 → 凭据落库 + service=net。"""
    from nonebot_plugin_awmc_helper.plugins import bind
    from nonebot_plugin_awmc_helper.core.binding import SERVICE_NET, binding_service

    with respx.mock(assert_all_called=False) as m:
        _mock_net_login_success(m)
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
        "官方 maimai NET 抓取成绩）。密码级别敏感，不要使用与其他服务"
        "相同的密码。"
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
    assert fresh is not None
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
    """b50 全链路（私聊）：抓取提示 → NET 组装 → B50 图渲染 → 窗口缓存生效。"""
    import base64

    from fake import fake_private_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment

    from nonebot_plugin_awmc_helper.plugins import score_query
    from nonebot_plugin_awmc_helper.core.binding import binding_service
    from nonebot_plugin_awmc_helper.core.render.best50 import best50_bytes

    binding = await binding_service.ensure("OneBot V11", "12345678")
    await binding_service.bind_net(binding, sega_id="sid", password="pw")

    from nonebot_plugin_awmc_helper.core.ext.net import NetPlayer

    # 首页身份（icon_url 置 None：头像下载走 ensure_icon 网络路径，此测不触发）
    net_player = NetPlayer(
        name="Ｔｅｆｇ",  # 全角字符（真实玩家名形态）
        rating=10516,
        icon_url=None,
        trophy_name="アウラ、フルコンしろ。",
        trophy_color="Normal",
    )

    async def fake_fetch(b):
        return _records(), net_player

    monkeypatch.setattr(net_service, "fetch_records", fake_fetch)

    bests = await net_service.build_b50(_records())
    expected_png = await best50_bytes(
        net_player.name,  # 卡片显示名 = NET 首页玩家名（缺失才回退 SEGA ID）
        bests.rating,
        bests.rating_b35,
        bests.rating_b15,
        bests.scores_b35,
        bests.scores_b15,
        player=None,
        qqid=12345678,
        service="net",
        theme="prism_plus",
        trophy_name=net_player.trophy_name,
        trophy_color=net_player.trophy_color,
    )
    event = fake_private_message_event_v11(message="b50", user_id=12345678)
    async with app.test_matcher(score_query.b50) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot_get_adapter())
        ctx.receive_event(bot, event)
        # 首查先发抓取提示，再发 B50 图（私聊无 at、去前导空格）
        ctx.should_call_send(
            event,
            Message([MessageSegment.text("正在登录日服 NET 抓取成绩，请稍候…")]),
            result=None,
            bot=bot,
        )
        ctx.should_call_send(
            event,
            Message(
                [
                    MessageSegment.image(
                        f"base64://{base64.b64encode(expected_png).decode()}"
                    )
                ]
            ),
            result=None,
            bot=bot,
        )
        ctx.should_finished()
    # 窗口缓存生效：再次查询不触发抓取
    scores, from_cache = await net_service.get_scores(binding)
    assert from_cache
    assert len(scores) == 4


@requires_assets
@pytest.mark.asyncio
async def test_minfo_net_command(app: App, db, net_service, jp_view, monkeypatch):
    """minfo 全链路（私聊）：JP 视图曲解析 + 窗口缓存成绩 → 日服谱面卡。"""
    import base64

    from fake import fake_private_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment

    from nonebot_plugin_awmc_helper.plugins import score_query
    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.render import jp_cover
    from nonebot_plugin_awmc_helper.core.binding import binding_service
    from nonebot_plugin_awmc_helper.core.render.info import song_play_data

    binding = await binding_service.ensure("OneBot V11", "12345678")
    await binding_service.bind_net(binding, sega_id="sid", password="pw")

    async def fake_fetch(b):
        return _records(), None

    monkeypatch.setattr(net_service, "fetch_records", fake_fetch)

    async def fake_ensure(song_id, cache_dir=None):
        return True  # 测试不拉日服封面

    monkeypatch.setattr(jp_cover, "ensure", fake_ensure)

    # JP 别名/标题解析路径会 ensure_loaded（真实环境触发曲库预热）：
    # 测试注入静态视图后 no-op，避免联网
    async def fake_noop():
        return None

    monkeypatch.setattr(song_service, "ensure_loaded", fake_noop)

    scores, from_cache = await net_service.get_scores(binding)  # 预填窗口缓存
    assert not from_cache
    _scores2, from_cache2 = await net_service.get_scores(binding)
    assert from_cache2  # minfo 查询时命中缓存（不发抓取提示、0 请求）
    song = next(s for s in jp_view if s.title == "コネクト")
    expected_png = song_play_data(
        song,
        [s for s in scores if s.id % 10000 == song.id],
        service="net",
        theme="prism_plus",
        prefer_type=None,
    )
    event = fake_private_message_event_v11(message="minfo コネクト", user_id=12345678)
    async with app.test_matcher(score_query.minfo) as ctx:
        bot = ctx.create_bot(base=Bot, adapter=nonebot_get_adapter())
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event,
            Message(
                [
                    MessageSegment.image(
                        f"base64://{base64.b64encode(expected_png).decode()}"
                    )
                ]
            ),
            result=None,
            bot=bot,
        )
        ctx.should_finished()
    assert any(t.name == "SSSP" for t in [s.rate for s in scores])  # 组装口径未回归


@requires_assets
@pytest.mark.asyncio
async def test_b50_at_net_target(app: App, db, net_service, jp_view, monkeypatch):
    """b50 @NET 目标（群聊代查）：走目标 NET 凭据抓取，窗口缓存按目标键控，
    与发送者/其他人的缓存互不干扰。"""
    import base64

    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment

    from nonebot_plugin_awmc_helper.plugins import score_query
    from nonebot_plugin_awmc_helper.core.binding import binding_service
    from nonebot_plugin_awmc_helper.core.render.best50 import best50_bytes

    binding = await binding_service.ensure("OneBot V11", "99999999")
    await binding_service.bind_net(binding, sega_id="sid", password="pw")

    async def fake_fetch(b):
        assert (b.platform, b.user_id) == ("OneBot V11", "99999999")
        return _records(), None

    monkeypatch.setattr(net_service, "fetch_records", fake_fetch)

    bests = await net_service.build_b50(_records())
    expected_png = await best50_bytes(
        "sid",
        bests.rating,
        bests.rating_b35,
        bests.rating_b15,
        bests.scores_b35,
        bests.scores_b15,
        player=None,
        qqid=99999999,
        service="net",
        theme="prism_plus",
    )
    event = fake_group_message_event_v11(
        message=Message([MessageSegment.text("b50 "), MessageSegment.at(99999999)]),
        user_id=12345678,
    )
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
        # 代查触发目标凭据的真实抓取：先提示，再出图（at 发送者）
        ctx.should_call_send(
            event,
            Message(
                [
                    MessageSegment.at(12345678),
                    MessageSegment.text(" 正在登录日服 NET 抓取成绩，请稍候…"),
                ]
            ),
            result=None,
            bot=bot,
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
    # 窗口缓存按目标键控
    _, from_cache = await net_service.get_scores(binding)
    assert from_cache
