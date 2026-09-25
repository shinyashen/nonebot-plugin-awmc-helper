"""awmc.music_query 查歌子插件测试。"""

import base64

import pytest
from mocks import requires_assets
from nonebug import App


@pytest.fixture
async def db(tmp_path):
    from nonebot_plugin_awmc_helper.core import store

    store.set_db_file(tmp_path / "awmc.db")
    await store.init_db()
    yield
    store.set_db_file(None)


@pytest.fixture
async def songs(tmp_path):
    """注入样例曲库 + 独立临时数据库。"""
    from mocks import make_song, sample_songs, seed_service

    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.core.songs import song_service

    store.set_db_file(tmp_path / "awmc.db")
    await store.init_db()
    extra = [
        make_song(700, "PENGUIN RUSH"),
        make_song(701, "PENGUIN LAND"),
        make_song(702, "PENGUIN PARTY"),
        make_song(703, "PENGUIN STAR"),
    ]
    await seed_service(song_service, sample_songs() + extra)
    yield
    song_service._ready.clear()
    store.set_db_file(None)


async def _assert_reply(
    app: App, matcher_name: str, text: str, reply: str, *, user_id=12345678
):
    """构造群消息并断言文本回复（handler 注入 uninfo Session，需 mock 其信息拉取）。"""
    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    from nonebot_plugin_awmc_helper.plugins import music_query

    matcher = getattr(music_query, matcher_name)
    event = fake_group_message_event_v11(message=text)
    async with app.test_matcher(matcher) as ctx:
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
            {"group_id": 87654321, "user_id": user_id, "no_cache": True},
            result={"user_id": user_id, "role": "member", "card": "", "nickname": "t"},
        )
        expected = Message(
            [MessageSegment.at(user_id), MessageSegment.text(f" {reply}")]
        )
        ctx.should_call_send(event, expected, result=None, bot=bot)
        ctx.should_finished()


async def _assert_image_reply(
    app: App,
    matcher_name: str,
    text: str,
    expect_png,
    suffix: str = "",
    prefix: str = "",
    *,
    user_id=12345678,
):
    """断言回复为渲染图（bytes 与同一渲染函数一致）+ 可选文本后缀。

    `expect_png` 是无参函数，返回渲染字节（保证期望值与实际发送同源）。
    """
    import inspect

    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    from nonebot_plugin_awmc_helper.plugins import music_query

    matcher = getattr(music_query, matcher_name)
    result = expect_png()
    if inspect.isawaitable(result):
        result = await result
    png = result
    segments = [MessageSegment.at(user_id)]
    if prefix:
        segments.append(MessageSegment.text(f" {prefix}"))
    segments.append(MessageSegment.image(f"base64://{base64.b64encode(png).decode()}"))
    expected = (
        Message(segments)
        if not suffix
        else Message([*segments, MessageSegment.text(suffix)])
    )

    event = fake_group_message_event_v11(message=text)
    async with app.test_matcher(matcher) as ctx:
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
            {"group_id": 87654321, "user_id": user_id, "no_cache": True},
            result={"user_id": user_id, "role": "member", "card": "", "nickname": "t"},
        )
        ctx.should_call_send(event, expected, result=None, bot=bot)
        ctx.should_finished()


@requires_assets
@pytest.mark.asyncio
async def test_search_single_draws_card(app: App, songs):
    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.chart_card import chart_card_bytes

    song = await song_service.by_id(500)
    assert song is not None
    await _assert_image_reply(
        app, "search", "查歌 Preferences", lambda: chart_card_bytes(song, None)
    )


@pytest.mark.asyncio
async def test_search_multi_text(app: App, songs):
    await _assert_reply(
        app,
        "search",
        "查歌 PENGUIN",
        "「231」 PENGUIN\n「700」 PENGUIN RUSH\n「701」 PENGUIN LAND"
        "\n「702」 PENGUIN PARTY\n「703」 PENGUIN STAR",
    )


@pytest.mark.asyncio
async def test_search_not_found(app: App, songs):
    await _assert_reply(
        app,
        "search",
        "查歌 不存在的曲子",
        "没有找到这样的乐曲。\n※ 如果是别名请使用「XXX是什么歌」指令进行查询哦。",
    )


@requires_assets
@pytest.mark.asyncio
async def test_search_list_image(app: App, songs):
    """5 条以上走列表图：PENGUIN 族 3 首 + Preferences + 宴会曲相关……用「n」命中。"""
    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.render import song as song_render

    matched = await song_service.by_title_fuzzy("p")
    assert len(matched) >= 5
    page = 1
    suffix = f"第 {page} 页，共 {len(matched)} 首，可用「查歌 <标题> {page + 1}」翻页"
    await _assert_image_reply(
        app,
        "search",
        f"查歌 p {page}",
        lambda: song_render.song_list_bytes(matched, page),
        suffix=suffix,
    )


@pytest.mark.asyncio
async def test_alias_search_multi(app: App, songs):
    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.core.songs import song_service

    await store.add_local_alias(231, "共同别名", "u1")
    await store.add_local_alias(500, "共同别名", "u1")
    await song_service.reload_alias_index()
    await _assert_reply(
        app,
        "search_alias_song",
        "共同别名是什么歌",
        "找到4个谱面："
        "\n231：PENGUIN"
        "\n10231：PENGUIN"
        "\n500：Preferences"
        "\n10500：Preferences"
        "\n※ 请使用「id xxxxx」查询指定谱面",
    )


@requires_assets
@pytest.mark.asyncio
async def test_alias_search_single(app: App, songs):
    from maimai_py import SongType

    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.chart_card import chart_card_bytes

    song = await song_service.by_id(500)
    assert song is not None
    # 双谱歌曲无前缀搜索 → 列出谱面类型条目供选择（不设偏好）
    await _assert_reply(
        app,
        "search_alias_song",
        "普瑞是什么歌",
        "找到2个谱面："
        "\n500：Preferences"
        "\n10500：Preferences"
        "\n※ 请使用「id xxxxx」查询指定谱面",
    )
    # 带 dx 前缀 → 直接定位 DX 条目出卡
    await _assert_image_reply(
        app,
        "search_alias_song",
        "dx普瑞是什么歌",
        lambda: chart_card_bytes(song, None, SongType.DX),
        suffix="您要找的是不是这首？",
    )
    # 带「标」前缀 → SD 条目出卡
    await _assert_image_reply(
        app,
        "search_alias_song",
        "标普瑞是什么歌",
        lambda: chart_card_bytes(song, None, SongType.STANDARD),
        suffix="您要找的是不是这首？",
    )


@pytest.mark.asyncio
async def test_query_chart_not_found(app: App, songs):
    await _assert_reply(app, "query_chart", "id 99999", "未找到ID为「99999」的乐曲")


@requires_assets
@pytest.mark.asyncio
async def test_query_chart_card(app: App, songs):
    from maimai_py import SongType

    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.chart_card import chart_card_bytes

    song = await song_service.by_id(231)
    assert song is not None
    # SD 形状 id → 卡片显示标准谱（NB 双条目语义：id 即条目类型）
    await _assert_image_reply(
        app,
        "query_chart",
        "id 231",
        lambda: chart_card_bytes(song, None, SongType.STANDARD),
    )


@requires_assets
@pytest.mark.asyncio
async def test_render_smoke(songs):
    """绘图冒烟：卡片与列表图渲染出非空 PNG（新列表版式依赖素材包）。"""
    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.render.song import (
        song_card_bytes,
        song_list_bytes,
    )

    song231 = await song_service.by_id(231)
    assert song231 is not None
    card = song_card_bytes(song231)
    assert card.startswith(b"\x89PNG")
    assert len(card) > 1000
    listing = song_list_bytes(await song_service.get_all(), 1)
    assert listing.startswith(b"\x89PNG")
    assert len(listing) > 1000


def _best_entry(song_id: int, type_, level_index, ra: float):
    """b50 成绩桩：侧别选择路径只消费 dx_rating（渲染语义另有单测锚定）。"""
    from types import SimpleNamespace

    return SimpleNamespace(
        id=song_id, type=type_, level_index=level_index, dx_rating=ra
    )


async def _capture_chart_card(monkeypatch, song, binding, prefer_type, bests):
    """打桩查分与渲染，捕获 chart_card_bytes 组装给渲染层的参数。"""
    from nonebot_plugin_awmc_helper.core import score as score_mod
    from nonebot_plugin_awmc_helper.core.render import nb_chart
    from nonebot_plugin_awmc_helper.core.chart_card import chart_card_bytes

    captured = {}

    async def fake_b50(_binding):
        return bests

    def fake_render(
        song, calc, is_full, best_list, theme, prefer_type=None, jp=False, **kw
    ):
        captured.update(
            calc=calc,
            is_full=is_full,
            best_list=list(best_list),
            theme=theme,
            prefer_type=prefer_type,
            jp=jp,
        )
        return b"png"

    monkeypatch.setattr(score_mod.score_service, "get_b50", fake_b50)
    monkeypatch.setattr(nb_chart, "song_chart_info", fake_render)
    assert await chart_card_bytes(song, binding, prefer_type) == b"png"
    return captured


@pytest.mark.asyncio
async def test_chart_card_b50_side_by_version(monkeypatch):
    """查歌卡 b50 侧别按谱面组登场版本分段，不按 SD/DX 类型过滤。

    b35 侧混含老曲 DX 谱（b50 版本口径）：修复前按类型过滤拍平列表会把
    DX 条目剔出 b35 侧，is_full 误判 False，加分预测退化为「未满线全量
    虚高」（用户实测 b35 满线仍全显 ↑原值）；老曲 DX 卡同样与 SD 谱
    同池竞争 b35，而非 b15。
    """
    from types import SimpleNamespace

    from mocks import make_diff, make_song
    from maimai_py import SongType, LevelIndex

    from nonebot_plugin_awmc_helper.core.store import UserBinding

    song = make_song(231, "PENGUIN")  # 谱面组版本 25000 < 当前版本 → 旧曲
    binding = UserBinding(platform="OneBot V11", user_id="12345678")
    b35 = [
        _best_entry(100 + i, SongType.STANDARD, LevelIndex.MASTER, 400 - i)
        for i in range(30)
    ] + [
        _best_entry(200 + i, SongType.DX, LevelIndex.MASTER, 340 - i) for i in range(5)
    ]
    b15 = [
        _best_entry(300 + i, SongType.DX, LevelIndex.MASTER, 500 - i) for i in range(15)
    ]
    # scores = 拍平视图（maimai_py b50_only 语义）：旧实现按类型过滤它，
    # 保留本字段使修复前代码可运行并在 is_full 断言上失败
    bests = SimpleNamespace(scores=b35 + b15, scores_b35=b35, scores_b15=b15)
    expected_b35 = sorted(b35, key=lambda s: s.dx_rating or 0, reverse=True)

    # 旧曲 SD 卡 → b35 侧满线（35 条，含 DX 条目；b15 不掺入）
    captured = await _capture_chart_card(
        monkeypatch, song, binding, SongType.STANDARD, bests
    )
    assert captured["calc"] is True
    assert captured["is_full"] is True
    assert captured["best_list"] == expected_b35

    # 旧曲 DX 卡 → 同取 b35 侧（老曲 DX 谱进 b35 竞争）
    captured = await _capture_chart_card(monkeypatch, song, binding, SongType.DX, bests)
    assert captured["is_full"] is True
    assert captured["best_list"] == expected_b35

    # 当前版本新曲卡（谱面组 version ≥ 当前版本）→ b15 侧
    new_song = make_song(
        705,
        "NEW SONG",
        version=99999,
        diffs=[
            make_diff(
                type=SongType.STANDARD,
                level_index=LevelIndex.EXPERT,
                version=25500,
            ),
            make_diff(
                type=SongType.DX,
                level_index=LevelIndex.MASTER,
                version=25500,
            ),
        ],
    )
    captured = await _capture_chart_card(
        monkeypatch, new_song, binding, SongType.DX, bests
    )
    assert captured["is_full"] is True
    assert captured["best_list"] == sorted(
        b15, key=lambda s: s.dx_rating or 0, reverse=True
    )


def test_new_best_score_baseline_from_version_side():
    """加分基线 = 版本分段列表的入线线（最低 RA，不分谱面类型）。

    锚定用户案例（b35 最低 315）：未入线 313 无提升（负值由渲染层隐藏）、
    336 → +21；已在列表按自身既有 RA 差值；b35 里的 DX 条目既参与入线线、
    也不影响 SD 谱按 id+类型+难度精确匹配。
    """
    from typing import cast

    from maimai_py import SongType, LevelIndex, ScoreExtend

    from nonebot_plugin_awmc_helper.core.render.nb_chart import new_best_score

    # _best_entry 为 new_best_score 所需字段的极简假对象（id/type/level_index/dx_rating）
    b35 = cast(
        "list[ScoreExtend]",
        [
            _best_entry(834, SongType.STANDARD, LevelIndex.MASTER, 340),
            _best_entry(999, SongType.STANDARD, LevelIndex.MASTER, 320),
            _best_entry(888, SongType.DX, LevelIndex.MASTER, 315),
        ],
    )
    # 未入线：相对入线线 315（DX 条目也是 b35 一员，若按 SD 过滤会误取 320）
    assert (
        new_best_score(900, LevelIndex.MASTER.value, 313, b35, SongType.STANDARD) == -2
    )
    assert (
        new_best_score(900, LevelIndex.MASTER.value, 336, b35, SongType.STANDARD) == 21
    )
    # 已在列表：按自身既有 RA 差值（低于旧 RA → 0）
    assert (
        new_best_score(834, LevelIndex.MASTER.value, 350, b35, SongType.STANDARD) == 10
    )
    assert (
        new_best_score(834, LevelIndex.MASTER.value, 330, b35, SongType.STANDARD) == 0
    )
    # b35 里的 DX 谱按类型精确匹配（同曲双谱互不串）
    assert new_best_score(888, LevelIndex.MASTER.value, 330, b35, SongType.DX) == 15


def _utage_host_song():
    """纯宴曲宿主：title/别名含「牛奶」，宴谱 diff_id=100363。"""
    from mocks import make_song, make_utage
    from maimai_py import Genre

    return make_song(
        363,
        "Milky Beat",
        genre=Genre.宴会場,
        aliases=["牛奶"],
        diffs=[],
        utage=[make_utage(diff_id=100363, kanji="牛", description="牛奶宴会")],
    )


@pytest.mark.asyncio
async def test_by_utage_id_and_keyword(db):
    """宴谱 6 位 id 与「宴XX」关键词定位宿主曲（diff_id 不被根 id 取模吞掉）。"""
    from mocks import seed_service

    from nonebot_plugin_awmc_helper.core.songs import song_service

    host = _utage_host_song()
    await seed_service(song_service, [host])

    hit = await song_service.by_utage_id(100363)
    assert hit is not None
    host_got, utage_diff = hit
    # 一个 diff_id 对应一张宴谱：返回宿主曲与命中的那张谱
    assert host_got.id == 363
    assert utage_diff.diff_id == 100363
    assert await song_service.by_utage_id(999999) is None

    ut_songs = await song_service.utage_by_keyword("牛奶")
    assert [s.id for s in ut_songs] == [363]
    # 宿主曲无 DX 谱面（纯宴曲）
    assert not host.difficulties.dx
    assert host.difficulties.utage


@requires_assets
@pytest.mark.asyncio
async def test_utage_id_command_draws_banquet_card(app: App, db):
    """id 100363（宴谱机台 id）→ 宴会场卡而非同号普通曲 DX 卡。"""
    from mocks import seed_service

    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.render import nb_chart

    host = _utage_host_song()
    await seed_service(song_service, [host])
    await _assert_image_reply(
        app,
        "query_chart",
        "id100363",
        lambda: nb_chart.song_chart_banquet_info(host),
    )


@requires_assets
@pytest.mark.asyncio
async def test_utage_alias_keyword_draws_banquet_card(app: App, db):
    """「宴牛奶是什么歌」：别名命中普通曲无宴谱时，回退按关键词搜宴曲。"""
    from mocks import make_song, seed_service

    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.render import nb_chart

    host = _utage_host_song()
    normal = make_song(364, "Milky Normal", aliases=["牛奶"])
    await seed_service(song_service, [host, normal])
    # 「宴牛奶」精确未命中 → 剥「宴」命中普通曲 364（无宴谱）→ 回退宴曲搜索
    await _assert_image_reply(
        app,
        "search_alias_song",
        "宴牛奶是什么歌",
        lambda: nb_chart.song_chart_banquet_info(host),
        suffix="您要找的是不是这首？",
    )


@pytest.mark.asyncio
async def test_by_utage_id_jp_view_fallback(db, monkeypatch):
    """日服宴曲不在 CN 视图：by_utage_id / utage_by_keyword 走 JP 视图兜底。"""
    from mocks import make_song, make_utage, seed_service

    from nonebot_plugin_awmc_helper.core.songs import song_service

    host = make_song(
        363,
        "Milky Beat JP",
        aliases=["牛奶"],
        diffs=[],
        utage=[make_utage(diff_id=100363)],
    )
    await seed_service(song_service, [])  # CN 运行时视图为空

    async def fake_jp_map():
        return {363: host}

    monkeypatch.setattr(song_service, "_jp_songs_map", fake_jp_map)

    hit = await song_service.by_utage_id(100363)
    assert hit is not None
    host_got, utage_diff = hit
    assert host_got.id == 363
    assert utage_diff.diff_id == 100363
    ut_songs = await song_service.utage_by_keyword("牛奶")
    assert [s.id for s in ut_songs] == [363]


@requires_assets
@pytest.mark.asyncio
async def test_utage_id_jp_only_host_card(app: App, db, monkeypatch):
    """日服限定宴曲（JP 视图兜底）：宴会卡不挂国服新曲标，回复补日服限定标注。"""
    from mocks import make_song, make_utage, seed_service

    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.render import nb_chart

    host = make_song(
        363,
        "Milky Beat JP",
        version=99999,  # 恒判「当前版本」：修复前宴会卡会错误挂新曲标
        utage=[make_utage(diff_id=100363)],
    )
    await seed_service(song_service, [])  # CN 运行时视图为空

    async def fake_jp_map():
        return {363: host}

    monkeypatch.setattr(song_service, "_jp_songs_map", fake_jp_map)
    await _assert_image_reply(
        app,
        "query_chart",
        "id100363",
        lambda: nb_chart.song_chart_banquet_info(host, jp=True),
        suffix="\n此歌曲为日服限定",
    )


@requires_assets
@pytest.mark.asyncio
async def test_utage_id_jp_chart_on_cn_host(app: App, db, monkeypatch):
    """日服宴谱挂在国服宿主曲（悪戯センセーション形态）：仍按日服卡渲染。

    宿主曲国服有普通谱、宴谱仅日服——修复前按宿主曲级判定 jp，会出
    国服卡（国服版本标志/挂新曲标/无日服标注）。
    """
    from mocks import make_song, make_utage, seed_service

    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.render import nb_chart

    cn_host = make_song(363, "Milky Beat CN", version=21004)  # 国服：仅普通谱
    jp_host = make_song(
        363,
        "Milky Beat JP",
        version=21000,
        utage=[make_utage(diff_id=100363, version=26509)],
    )
    await seed_service(song_service, [cn_host])

    async def fake_jp_map():
        return {363: jp_host}

    monkeypatch.setattr(song_service, "_jp_songs_map", fake_jp_map)
    await _assert_image_reply(
        app,
        "query_chart",
        "id100363",
        lambda: nb_chart.song_chart_banquet_info(jp_host, jp=True),
        suffix="\n此歌曲为日服限定",
    )


@requires_assets
@pytest.mark.asyncio
async def test_utage_id_on_mixed_host_draws_banquet_card(app: App, db):
    """宴谱挂在普通曲上（宿主有 DX 谱）：id100363 也必须出宴会卡而非 DX 卡。"""
    from mocks import make_diff, make_song, make_utage, seed_service
    from maimai_py import SongType, LevelIndex

    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.render import nb_chart

    host = make_song(
        363,
        "Milky Beat Mixed",
        aliases=["牛奶"],
        utage=[make_utage(diff_id=100363)],
        diffs=[
            make_diff(
                type=SongType.DX,
                level_index=LevelIndex.MASTER,
                level="13",
                level_value=13.5,
            )
        ],
    )
    await seed_service(song_service, [host])
    # 回归锚点：宿主有 DX 谱，is_banquet 为 False（修复前误出 DX 卡）
    await _assert_image_reply(
        app,
        "query_chart",
        "id100363",
        lambda: nb_chart.song_chart_banquet_info(host),
    )
