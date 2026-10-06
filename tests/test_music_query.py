"""awmc.music_query 查歌子插件测试。

锚全部为真实曲目（2026-09-29 取材）：样例库见 mocks.sample_songs
（199 チルノ SD+DX+蛸宴 / 8 True Love Song / 624 KISS CANDY FLAVOR），
标题家族与宴宿主等扩展曲同样取真实曲（値为各自实测值）；「快照无原型」的
路径以（构造）曲补位并注明。
"""

import base64

import pytest
from mocks import requires_assets
from nonebug import App
from songdb_fixtures import make_pending_revealed


@pytest.fixture
async def db(tmp_path):
    from nonebot_plugin_awmc_helper.core import store

    store.set_db_file(tmp_path / "awmc.db")
    await store.init_db()
    yield
    store.set_db_file(None)


def _extra_songs():
    """标题家族扩展曲（真实曲与实测值；列表/模糊查询用）。"""
    from mocks import make_diff, make_song
    from maimai_py import Genre, SongType, LevelIndex

    def sd_mas(song_id, title, level, value, version, notes, designer=None):
        return make_song(
            song_id,
            title,
            version=version,
            diffs=[
                make_diff(
                    type=SongType.STANDARD,
                    level_index=LevelIndex.MASTER,
                    level=level,
                    level_value=value,
                    note_designer=designer,
                    version=version,
                    tap_num=notes[0],
                    hold_num=notes[1],
                    slide_num=notes[2],
                    touch_num=0,
                    break_num=notes[3],
                )
            ],
        )

    return [
        # チルノ ⑨周年（SD 13/13.4 + DX 12/12.8，真实 MURASAKi PLUS/DX 值）
        make_song(
            665,
            "チルノのパーフェクトさんすう教室　⑨周年バージョン",
            version=16500,
            diffs=[
                make_diff(
                    type=SongType.STANDARD,
                    level_index=LevelIndex.MASTER,
                    level="13",
                    level_value=13.4,
                    version=16500,
                    tap_num=519,
                    hold_num=63,
                    slide_num=90,
                    touch_num=0,
                    break_num=99,
                ),
                make_diff(
                    type=SongType.DX,
                    level_index=LevelIndex.MASTER,
                    level="12",
                    level_value=12.8,
                    version=20000,
                    tap_num=483,
                    hold_num=53,
                    slide_num=60,
                    touch_num=102,
                    break_num=42,
                ),
            ],
        ),
        # ラグトレイン（DX 13/13.2，UNiVERSE）
        make_song(
            1355,
            "ラグトレイン",
            genre=Genre.niconicoボーカロイド,
            version=22000,
            diffs=[
                make_diff(
                    type=SongType.DX,
                    level_index=LevelIndex.MASTER,
                    level="13",
                    level_value=13.2,
                    note_designer="はっぴー",
                    version=22000,
                    tap_num=551,
                    hold_num=84,
                    slide_num=96,
                    touch_num=30,
                    break_num=11,
                )
            ],
        ),
        # マトリョシカ（SD MASTER 11+/11.7）
        sd_mas(71, "マトリョシカ", "11+", 11.7, 10000, (0, 0, 0, 0)),
        # ナイト・オブ・ナイツ（SD MASTER 13/13.3，GreeN）
        sd_mas(204, "ナイト・オブ・ナイツ", "13", 13.3, 12000, (0, 0, 0, 0)),
        # セツナトリップ（SD MASTER 13/13.3，GreeN）——凑列表图阈值（>5 首）
        sd_mas(193, "セツナトリップ", "13", 13.3, 12000, (0, 0, 0, 0)),
    ]


@pytest.fixture
async def songs(tmp_path):
    """注入样例曲库 + 独立临时数据库。"""
    from mocks import sample_songs, seed_service

    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.core.songs import song_service

    store.set_db_file(tmp_path / "awmc.db")
    await store.init_db()
    await seed_service(song_service, sample_songs() + _extra_songs())
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
    from nonebot_plugin_awmc_helper.core.store import UserBinding
    from nonebot_plugin_awmc_helper.core.chart_card import chart_card_bytes

    song = await song_service.by_id(8)
    assert song is not None
    # handler 以 SessionBinding 出卡（与 None 的渲染差异在 b50 嵌入口径）
    binding = UserBinding(platform="OneBot V11", user_id="12345678")
    await _assert_image_reply(
        app,
        "search",
        "查歌 True Love Song",
        lambda: chart_card_bytes(song, binding),
    )


@pytest.mark.asyncio
async def test_search_multi_text(app: App, songs):
    await _assert_reply(
        app,
        "search",
        "查歌 チルノ",
        "「199」 チルノのパーフェクトさんすう教室"
        "\n「665」 チルノのパーフェクトさんすう教室　⑨周年バージョン",
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
    """6 条以上走列表图：真实「ト」标题家族 6 首（チルノ×2/ラグトレイン/
    マトリョシカ/ナイト・オブ・ナイツ/セツナトリップ）。"""
    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.render import song as song_render

    matched = await song_service.by_title_fuzzy("ト")
    assert len(matched) >= 5
    page = 1
    suffix = f"第 {page} 页，共 {len(matched)} 首，可用「查歌 <标题> {page + 1}」翻页"
    await _assert_image_reply(
        app,
        "search",
        f"查歌 ト {page}",
        lambda: song_render.song_list_bytes(matched, page),
        suffix=suffix,
    )


@pytest.mark.asyncio
async def test_alias_search_multi(app: App, songs):
    """「糖糖」为 8/624 在柚子别名库共持的真实别名 → 双曲条目列表。"""
    await _assert_reply(
        app,
        "search_alias_song",
        "糖糖是什么歌",
        "找到2个谱面："
        "\n8：True Love Song"
        "\n624：KISS CANDY FLAVOR"
        "\n※ 请使用「id xxxxx」查询指定谱面",
    )


@requires_assets
@pytest.mark.asyncio
async def test_alias_search_single(app: App, songs):
    from maimai_py import SongType

    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.store import UserBinding
    from nonebot_plugin_awmc_helper.core.chart_card import chart_card_bytes

    song = await song_service.by_id(199)
    assert song is not None
    binding = UserBinding(platform="OneBot V11", user_id="12345678")
    # 双谱歌曲无前缀搜索 → 列出谱面类型条目供选择（不设偏好；含蛸宴条目）
    await _assert_reply(
        app,
        "search_alias_song",
        "琪露诺是什么歌",
        "找到3个谱面："
        "\n199：チルノのパーフェクトさんすう教室"
        "\n10199：チルノのパーフェクトさんすう教室"
        "\n100199：チルノのパーフェクトさんすう教室"
        "\n※ 请使用「id xxxxx」查询指定谱面",
    )
    # 带 dx 前缀 → 直接定位 DX 条目出卡
    await _assert_image_reply(
        app,
        "search_alias_song",
        "dx琪露诺是什么歌",
        lambda: chart_card_bytes(song, binding, SongType.DX),
        suffix="您要找的是不是这首？",
    )
    # 带「标」前缀 → SD 条目出卡
    await _assert_image_reply(
        app,
        "search_alias_song",
        "标琪露诺是什么歌",
        lambda: chart_card_bytes(song, binding, SongType.STANDARD),
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
    from nonebot_plugin_awmc_helper.core.store import UserBinding
    from nonebot_plugin_awmc_helper.core.chart_card import chart_card_bytes

    song = await song_service.by_id(199)
    assert song is not None
    # SD 形状 id → 卡片显示标准谱（NB 双条目语义：id 即条目类型）
    await _assert_image_reply(
        app,
        "query_chart",
        "id 199",
        lambda: chart_card_bytes(
            song,
            UserBinding(platform="OneBot V11", user_id="12345678"),
            SongType.STANDARD,
        ),
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

    song199 = await song_service.by_id(199)
    assert song199 is not None
    card = song_card_bytes(song199)
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

    # 谱面组版本（10000/22000）< maimai_py 当前版本（25500）→ 旧曲
    song = make_song(
        8,
        "True Love Song",
        version=10000,
        diffs=[
            make_diff(
                type=SongType.STANDARD,
                level_index=LevelIndex.EXPERT,
                level="10",
                level_value=10.2,
                version=10000,
            ),
            make_diff(
                type=SongType.DX,
                level_index=LevelIndex.MASTER,
                level="13",
                level_value=13.2,
                version=22000,
            ),
        ],
    )
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
    # （真实 MAGiCAL 新曲 物語はここから，addVersion 27 → 27000）
    new_song = make_song(
        2020,
        "物語はここから",
        version=27000,
        diffs=[
            make_diff(
                type=SongType.STANDARD,
                level_index=LevelIndex.EXPERT,
                version=27000,
            ),
            make_diff(
                type=SongType.DX,
                level_index=LevelIndex.MASTER,
                version=27000,
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
    也不影响 SD 谱按 id+类型+难度精确匹配。id 用真实曲（834 PANDORA
    PARADOXXX 为真实 b35 锚；其余为该纯函数的机械样例）。
    """
    from typing import cast

    from maimai_py import SongType, LevelIndex, ScoreExtend

    from nonebot_plugin_awmc_helper.core.render.nb_chart import new_best_score

    # _best_entry 为 new_best_score 所需字段的极简假对象
    # （id/type/level_index/dx_rating）
    b35 = cast(
        "list[ScoreExtend]",
        [
            _best_entry(834, SongType.STANDARD, LevelIndex.MASTER, 340),
            _best_entry(8, SongType.STANDARD, LevelIndex.MASTER, 320),
            _best_entry(1355, SongType.DX, LevelIndex.MASTER, 315),
        ],
    )
    # 未入线：相对入线线 315（DX 条目也是 b35 一员，若按 SD 过滤会误取 320）
    assert (
        new_best_score(199, LevelIndex.MASTER.value, 313, b35, SongType.STANDARD) == -2
    )
    assert (
        new_best_score(199, LevelIndex.MASTER.value, 336, b35, SongType.STANDARD) == 21
    )
    # 已在列表：按自身既有 RA 差值（低于旧 RA → 0）
    assert (
        new_best_score(834, LevelIndex.MASTER.value, 350, b35, SongType.STANDARD) == 10
    )
    assert (
        new_best_score(834, LevelIndex.MASTER.value, 330, b35, SongType.STANDARD) == 0
    )
    # b35 里的 DX 谱按类型精确匹配（同曲双谱互不串）
    assert new_best_score(1355, LevelIndex.MASTER.value, 330, b35, SongType.DX) == 15


def _utage_host_song():
    """宴谱宿主：真实蛸チルノ（199，SD+DX+宴 三组并存），宴 diff_id=100199。"""
    from mocks import make_song, make_utage

    return make_song(
        199,
        "チルノのパーフェクトさんすう教室",
        aliases=["琪露诺"],
        utage=[make_utage()],
    )


def _utage_only_helper(**kw):
    """纯宴曲宿主（快照无原型，构造补位）：宴 diff_id=100901。"""
    from mocks import make_song, make_utage
    from maimai_py import Genre

    return make_song(
        901,
        "（构造）纯宴样例",
        genre=Genre.宴会場,
        aliases=["宴曲"],
        diffs=[],
        utage=[make_utage(diff_id=100901, kanji="宴", **kw)],
    )


@pytest.mark.asyncio
async def test_by_utage_id_and_keyword(db):
    """宴谱 6 位 id 与「宴XX」关键词定位宿主曲（diff_id 不被根 id 取模吞掉）。"""
    from mocks import seed_service

    from nonebot_plugin_awmc_helper.core.songs import song_service

    host = _utage_host_song()
    await seed_service(song_service, [host])

    hit = await song_service.by_utage_id(100199)
    assert hit is not None
    host_got, utage_diff = hit
    # 一个 diff_id 对应一张宴谱：返回宿主曲与命中的那张谱
    assert host_got.id == 199
    assert utage_diff.diff_id == 100199
    assert await song_service.by_utage_id(999999) is None

    ut_songs = await song_service.utage_by_keyword("琪露诺")
    assert [s.id for s in ut_songs] == [199]
    # 宿主曲为混合形态（SD+DX+宴并存，真实蛸チルノ）
    assert host_got.difficulties.utage
    assert host_got.difficulties.dx


@requires_assets
@pytest.mark.asyncio
async def test_utage_id_command_draws_banquet_card(app: App, db):
    """id 100199（宴谱机台 id）→ 宴会场卡而非同号普通曲 DX 卡。"""
    from mocks import seed_service

    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.render import nb_chart

    host = _utage_host_song()
    await seed_service(song_service, [host])
    await _assert_image_reply(
        app,
        "query_chart",
        "id100199",
        lambda: nb_chart.song_chart_banquet_info(host),
    )


@requires_assets
@pytest.mark.asyncio
async def test_utage_alias_keyword_draws_banquet_card(app: App, db):
    """「宴琪露诺是什么歌」：剥「宴」命中无宴谱的曲（⑨周年）→ 回退宴曲搜索。"""
    from mocks import make_diff, make_song, seed_service
    from maimai_py import SongType, LevelIndex

    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.render import nb_chart

    host = _utage_host_song()
    normal = make_song(
        665,
        "チルノのパーフェクトさんすう教室　⑨周年バージョン",
        aliases=["チルノ"],
        diffs=[
            make_diff(
                type=SongType.STANDARD,
                level_index=LevelIndex.MASTER,
                level="13",
                level_value=13.4,
                version=16500,
            )
        ],
    )
    await seed_service(song_service, [host, normal])
    # 「宴チルノ」精确未命中 → 剥「宴」命中 ⑨周年（无宴谱）→ 回退宴曲搜索
    await _assert_image_reply(
        app,
        "search_alias_song",
        "宴チルノ是什么歌",
        lambda: nb_chart.song_chart_banquet_info(host),
        suffix="您要找的是不是这首？",
    )


@pytest.mark.asyncio
async def test_by_utage_id_jp_view_fallback(db, monkeypatch):
    """日服宴曲不在 CN 视图：by_utage_id / utage_by_keyword 走 JP 视图兜底。

    真实锚：[協]青春コンプレックス（CN 已于 2026-08-07 下架，宴体 121634 仅日服）。
    """
    from mocks import make_song, make_utage, seed_service

    from nonebot_plugin_awmc_helper.core.songs import song_service

    host = make_song(
        1634,
        "[協]青春コンプレックス",
        diffs=[],
        utage=[make_utage(diff_id=121634, kanji="協", level="14+", level_value=14.7)],
    )
    await seed_service(song_service, [])  # CN 运行时视图为空

    async def fake_jp_map():
        return {1634: host}

    monkeypatch.setattr(song_service, "_jp_songs_map", fake_jp_map)

    hit = await song_service.by_utage_id(121634)
    assert hit is not None
    host_got, utage_diff = hit
    assert host_got.id == 1634
    assert utage_diff.diff_id == 121634
    ut_songs = await song_service.utage_by_keyword("青春")
    assert [s.id for s in ut_songs] == [1634]


@requires_assets
@pytest.mark.asyncio
async def test_utage_id_jp_only_host_card(app: App, db, monkeypatch):
    """日服限定宴曲（JP 视图兜底）：宴会卡不挂国服新曲标，回复补日服限定标注。"""
    from mocks import make_song, make_utage, seed_service

    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.render import nb_chart

    host = make_song(
        1634,
        "[協]青春コンプレックス",
        version=24000,
        diffs=[],
        utage=[make_utage(diff_id=121634, kanji="協", level="14+", level_value=14.7)],
    )
    await seed_service(song_service, [])  # CN 运行时视图为空

    async def fake_jp_map():
        return {1634: host}

    monkeypatch.setattr(song_service, "_jp_songs_map", fake_jp_map)
    await _assert_image_reply(
        app,
        "query_chart",
        "id121634",
        lambda: nb_chart.song_chart_banquet_info(host, jp=True),
        suffix="\n此歌曲为日服限定",
    )


@requires_assets
@pytest.mark.asyncio
async def test_utage_id_jp_chart_on_cn_host(app: App, db, monkeypatch):
    """日服宴谱挂在国服宿主曲（[協]ラグトレイン 形态）：仍按日服卡渲染。

    宿主曲国服有 DX 普通谱（1355）、宴谱（111355）仅日服——修复前按宿主曲级
    判定 jp，会出国服卡（国服版本标志/挂新曲标/无日服标注）。
    """
    from mocks import make_song, make_utage, seed_service

    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.render import nb_chart

    cn_host = make_song(1355, "ラグトレイン", version=22000)  # 国服：DX 普通谱
    jp_host = make_song(
        1355,
        "[協]ラグトレイン",
        version=24000,
        utage=[make_utage(diff_id=111355, kanji="協", version=24000)],
    )
    await seed_service(song_service, [cn_host])

    async def fake_jp_map():
        return {1355: jp_host}

    monkeypatch.setattr(song_service, "_jp_songs_map", fake_jp_map)
    await _assert_image_reply(
        app,
        "query_chart",
        "id111355",
        lambda: nb_chart.song_chart_banquet_info(jp_host, jp=True),
        suffix="\n此歌曲为日服限定",
    )


@requires_assets
@pytest.mark.asyncio
async def test_utage_id_on_mixed_host_draws_banquet_card(app: App, db):
    """宴谱挂在普通曲上（宿主有 DX 谱）：id111355 也必须出宴会卡而非 DX 卡。"""
    from mocks import make_song, make_utage, seed_service

    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.render import nb_chart

    host = make_song(
        1355,
        "[協]ラグトレイン",
        aliases=["ラグトレイン"],
        utage=[make_utage(diff_id=111355, kanji="協")],
    )
    await seed_service(song_service, [host])
    # 回归锚点：宿主有 DX 谱，is_banquet 为 False（修复前误出 DX 卡）
    await _assert_image_reply(
        app,
        "query_chart",
        "id111355",
        lambda: nb_chart.song_chart_banquet_info(host),
    )


# ---------------------------------------------------------------------------
# 指令链路（nonebug）
# ---------------------------------------------------------------------------


async def _assert_pending_card(app: App, matcher_name: str, text: str):
    """断言回复 = at + pending 提示文本 + pending 临时卡（与实现同源渲染）。"""
    import base64

    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    from nonebot_plugin_awmc_helper.plugins import music_query
    from nonebot_plugin_awmc_helper.core.songdb import pending_search
    from nonebot_plugin_awmc_helper.plugins.music_query.render import (
        PENDING_NOTE,
        _pending_card,
    )

    matcher = getattr(music_query, matcher_name)
    pending = (await pending_search(title="物語はここから"))[0]
    png = await _pending_card(pending)  # 与 handler 同源
    note = PENDING_NOTE

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
            {"group_id": 87654321, "user_id": 12345678, "no_cache": True},
            result={
                "user_id": 12345678,
                "role": "member",
                "card": "",
                "nickname": "t",
            },
        )
        expected = Message(
            [
                MessageSegment.at(12345678),
                MessageSegment.text(f" {note}"),
                MessageSegment.image(f"base64://{base64.b64encode(png).decode()}"),
            ]
        )
        ctx.should_call_send(event, expected, result=None, bot=bot)
        ctx.should_finished()


async def _seed_pending():
    """真实 MAGiCAL 新曲 pending（定数揭晓形态 = MuNET 实测值，gate 可查）。"""
    from nonebot_plugin_awmc_helper.core.songdb import upsert_pending

    await upsert_pending(
        "otoge-db", "title:物語はここから", "missing_id", make_pending_revealed()
    )


@requires_assets
@pytest.mark.asyncio
async def test_search_pending_fallback_card(app: App, songs):
    """查歌：CN/JP 视图均 miss → pending 兜底出临时卡。"""
    await _seed_pending()
    await _assert_pending_card(app, "search", "查歌 物語")


@requires_assets
@pytest.mark.asyncio
async def test_ds_search_pending_fallback_card(app: App, songs):
    """定数查歌：样例库无 7.5 定数 → pending 兜底出临时卡（定数揭晓即可查）。"""
    await _seed_pending()
    await _assert_pending_card(app, "search", "定数查歌 7.4 7.6")


@requires_assets
@pytest.mark.asyncio
async def test_alias_song_pending_fallback_card(app: App, songs, monkeypatch):
    """是什么歌：输入为新曲歌名（无别名）→ 投票提示落空后 pending 兜底。"""
    from nonebot_plugin_awmc_helper.core.ext.yuzu import yuzu_client

    async def _boom(*a, **k):
        raise RuntimeError("测试跳过网络")

    monkeypatch.setattr(yuzu_client, "get_apply_songs", _boom)
    await _seed_pending()
    await _assert_pending_card(app, "search_alias_song", "物語はここから是什么歌")


@pytest.mark.asyncio
async def test_search_pending_empty_falls_through(app: App, songs):
    """pending 无命中时回落既有「未找到」路径（不吞掉回复，回归 bool 返回修复）。"""
    import nonebot
    from fake import fake_group_message_event_v11
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    from nonebot_plugin_awmc_helper.plugins import music_query
    from nonebot_plugin_awmc_helper.plugins.music_query.render import NOT_FOUND

    event = fake_group_message_event_v11(message="查歌 查無此曲XYZ")
    async with app.test_matcher(music_query.search) as ctx:
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
        note = NOT_FOUND
        expected = Message(
            [MessageSegment.at(12345678), MessageSegment.text(f" {note}")]
        )
        ctx.should_call_send(event, expected, result=None, bot=bot)
        ctx.should_finished()


# ---------------------------------------------------------------------------
# NET 绑定用户的查歌路由（L-5 拍板）：一律走日服视图出卡
# ---------------------------------------------------------------------------


def _jp_view_song():
    """与 CN 同根的日服视图对象（version 可辨：BUDDIES 24000）。"""
    from mocks import make_diff, make_song
    from maimai_py import SongType, LevelIndex

    return make_song(
        199,
        "チルノのパーフェクトさんすう教室",
        version=24000,
        diffs=[
            make_diff(
                type=SongType.STANDARD,
                level_index=LevelIndex.MASTER,
                level="13",
                level_value=13.4,
                version=24000,
            )
        ],
    )


async def _seed_net_binding():
    """预置 NET 绑定（SessionBinding ensure 命中既有行）。"""
    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.core.store import UserBinding
    from nonebot_plugin_awmc_helper.core.binding import SERVICE_NET

    await store.save_binding(
        UserBinding(
            platform="OneBot V11",
            user_id="12345678",
            service=SERVICE_NET,
            net_sega_id="123456789001",
        )
    )


@requires_assets
@pytest.mark.asyncio
async def test_query_chart_net_routes_jp_view(app: App, songs, monkeypatch):
    """NET 绑定 id 199（国服在架）：出日服视图卡，不弹「日服限定」提示。

    标注与路由解耦：jp 视图路由本身不触发提示，提示只看国服缺席判定。
    """
    from maimai_py import SongType

    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.chart_card import chart_card_bytes

    jp_song = _jp_view_song()

    async def fake_jp_map():
        return {199: jp_song}

    monkeypatch.setattr(song_service, "_jp_songs_map", fake_jp_map)
    await _seed_net_binding()
    from nonebot_plugin_awmc_helper.core.store import UserBinding

    net_binding = UserBinding(
        platform="OneBot V11",
        user_id="12345678",
        service="net",
        net_sega_id="123456789001",
    )
    await _assert_image_reply(
        app,
        "query_chart",
        "id 199",
        lambda: chart_card_bytes(jp_song, net_binding, SongType.STANDARD, True),
    )


@requires_assets
@pytest.mark.asyncio
async def test_query_chart_net_jp_only_keeps_note(app: App, songs, monkeypatch):
    """NET 绑定查国服缺席曲：仍按日服限定补标注（cn 缺席判定，非路由触发）。"""
    from mocks import make_diff, make_song, seed_service
    from maimai_py import SongType, LevelIndex

    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.chart_card import chart_card_bytes

    jp_song = _jp_view_song()
    await seed_service(
        song_service,
        [
            make_song(
                8,
                "True Love Song",
                diffs=[
                    make_diff(
                        type=SongType.STANDARD,
                        level_index=LevelIndex.MASTER,
                        level="11",
                        level_value=11.5,
                        version=10000,
                    )
                ],
            )
        ],
    )  # CN 视图无 199

    async def fake_jp_map():
        return {199: jp_song}

    monkeypatch.setattr(song_service, "_jp_songs_map", fake_jp_map)
    await _seed_net_binding()
    from nonebot_plugin_awmc_helper.core.store import UserBinding

    net_binding = UserBinding(
        platform="OneBot V11",
        user_id="12345678",
        service="net",
        net_sega_id="123456789001",
    )
    await _assert_image_reply(
        app,
        "query_chart",
        "id 199",
        lambda: chart_card_bytes(jp_song, net_binding, SongType.STANDARD, True),
        suffix="\n此歌曲为日服限定",
    )


@requires_assets
@pytest.mark.asyncio
async def test_alias_single_net_routes_jp_view(app: App, songs, monkeypatch):
    """NET 绑定「dx琪露诺是什么歌」：别名单结果同样走日服视图卡。"""
    from maimai_py import SongType

    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.chart_card import chart_card_bytes

    jp_song = _jp_view_song()

    async def fake_jp_map():
        return {199: jp_song}

    monkeypatch.setattr(song_service, "_jp_songs_map", fake_jp_map)
    await _seed_net_binding()
    from nonebot_plugin_awmc_helper.core.store import UserBinding

    net_binding = UserBinding(
        platform="OneBot V11",
        user_id="12345678",
        service="net",
        net_sega_id="123456789001",
    )
    await _assert_image_reply(
        app,
        "search_alias_song",
        "dx琪露诺是什么歌",
        lambda: chart_card_bytes(jp_song, net_binding, SongType.DX, True),
        suffix="您要找的是不是这首？",
    )


# ---------------------------------------------------------------------------
# 数字 id 解析的追加谱面组回退（2026-10-06 居並ぶ MAGiCAL SD 追加实测）：
# CN 视图按根 id 合并缓存必命中，偏好类型 CN 缺而日服视图有时回退日服出卡
# ---------------------------------------------------------------------------


def _inarau_pair():
    """居並ぶ穀物と溜息まじりの運送屋（真实曲，实测值）：CN 视图为 DX 原曲
    （maimai_py 形态，无 standard），日服视图为追加 SD 组后的双组曲。"""
    from mocks import make_diff, make_song
    from maimai_py import SongType, LevelIndex

    cn_song = make_song(
        1154,
        "居並ぶ穀物と溜息まじりの運送屋",
        version=21000,
        diffs=[
            make_diff(
                type=SongType.DX,
                level_index=LevelIndex.MASTER,
                level="12+",
                level_value=12.9,
                version=21000,
                tap_num=697,
                hold_num=70,
                slide_num=123,
                touch_num=26,
                break_num=34,
            )
        ],
    )
    jp_song = make_song(
        1154,
        "居並ぶ穀物と溜息まじりの運送屋",
        version=27002,
        diffs=[
            make_diff(
                type=SongType.STANDARD,
                level_index=LevelIndex.MASTER,
                level="13+",
                level_value=13.8,
                version=27002,
                tap_num=592,
                hold_num=56,
                slide_num=174,
                touch_num=0,
                break_num=93,
            ),
            make_diff(
                type=SongType.DX,
                level_index=LevelIndex.MASTER,
                level="12+",
                level_value=12.9,
                version=21000,
                tap_num=697,
                hold_num=70,
                slide_num=123,
                touch_num=26,
                break_num=34,
            ),
        ],
    )
    return cn_song, jp_song


@pytest.mark.asyncio
async def test_resolve_raw_id_sd_addition_falls_back_to_jp(monkeypatch):
    """SD 形状 id（1154）：CN 命中缺 standard 谱面而日服视图有 → 回退日服
    曲对象并 jp=True（追加组日服先行，CN 数据未跟进）。"""
    from mocks import seed_service
    from maimai_py import SongType

    from nonebot_plugin_awmc_helper.core.songs import song_service

    cn_song, jp_song = _inarau_pair()

    async def fake_jp_map():
        return {1154: jp_song}

    await seed_service(song_service, [cn_song])
    monkeypatch.setattr(song_service, "_jp_songs_map", fake_jp_map)
    try:
        hit = await song_service.resolve_raw_chart(1154)
        assert hit is not None
        song, prefer, jp, utage_diff = hit
        assert song is jp_song
        assert prefer is SongType.STANDARD
        assert jp is True
        assert utage_diff is None
    finally:
        song_service._ready.clear()


@pytest.mark.asyncio
async def test_resolve_raw_id_dx_display_keeps_cn(monkeypatch):
    """DX 展示 id（11154）：CN 命中且偏好类型（DX）在 CN 在列 → 不回退，
    维持 CN 卡（既有口径）。"""
    from mocks import seed_service
    from maimai_py import SongType

    from nonebot_plugin_awmc_helper.core.songs import song_service

    cn_song, _jp_song = _inarau_pair()

    async def fake_jp_map():
        return {}

    await seed_service(song_service, [cn_song])
    monkeypatch.setattr(song_service, "_jp_songs_map", fake_jp_map)
    try:
        hit = await song_service.resolve_raw_chart(11154)
        assert hit is not None
        song, prefer, jp, _utage_diff = hit
        assert song.id == 1154  # inject 克隆歌曲对象，按根 id 断言（非身份）
        assert song.difficulties.standard == []
        assert prefer is SongType.DX
        assert jp is False
    finally:
        song_service._ready.clear()


@pytest.mark.asyncio
async def test_resolve_raw_id_no_fallback_when_jp_lacks_type(monkeypatch):
    """CN 缺偏好类型但日服视图也没有 → 维持 CN 命中（渲染端组回落口径）。"""
    from mocks import make_diff, make_song, seed_service
    from maimai_py import SongType, LevelIndex

    from nonebot_plugin_awmc_helper.core.songs import song_service

    cn_song, _jp_song = _inarau_pair()
    jp_dx_only = make_song(
        1154,
        "居並ぶ穀物と溜息まじりの運送屋",
        version=21000,
        diffs=[
            make_diff(
                type=SongType.DX,
                level_index=LevelIndex.MASTER,
                level="12+",
                level_value=12.9,
            )
        ],
    )

    async def fake_jp_map():
        return {1154: jp_dx_only}

    await seed_service(song_service, [cn_song])
    monkeypatch.setattr(song_service, "_jp_songs_map", fake_jp_map)
    try:
        hit = await song_service.resolve_raw_chart(1154)
        assert hit is not None
        song, prefer, jp, _utage_diff = hit
        assert song.id == 1154
        assert prefer is SongType.STANDARD
        assert jp is False
    finally:
        song_service._ready.clear()


@pytest.mark.asyncio
async def test_entries_completion_lists_added_group(db, monkeypatch):
    """定位层组级并集（2026-10-06 居並ぶ SD 追加实测）：CN 视图缺 standard
    而日服视图有 → 条目展开出 SD/DX 双条目（双谱曲「列出 id 指定」形态），
    不再退化为单 DX 条目直出卡。"""
    from mocks import seed_service
    from maimai_py import SongType

    from nonebot_plugin_awmc_helper.core.songs import song_service

    cn_song, jp_song = _inarau_pair()
    cn_song.aliases = ["骆驼祥子"]
    jp_song.aliases = ["骆驼祥子"]

    async def fake_jp_map():
        return {1154: jp_song}

    await seed_service(song_service, [cn_song])
    monkeypatch.setattr(song_service, "_jp_songs_map", fake_jp_map)
    try:
        entries = await song_service.entries_for_name("骆驼祥子")
        assert [(e[0], e[2]) for e in entries] == [
            (1154, SongType.STANDARD),
            (11154, SongType.DX),
        ]
        assert all(song is jp_song for _, song, _ in entries)
    finally:
        song_service._ready.clear()


@requires_assets
@pytest.mark.asyncio
async def test_lookup_reply_sd_prefix_renders_jp_card(app: App, db, monkeypatch):
    """「标准骆驼祥子是什么歌」：SD 单条目出卡走日服对象并带日服限定标注
    （CN 对象缺 standard 谱面，出卡偏好回退与 resolve_raw_chart 同口径）。"""
    import base64

    import nonebot
    from fake import fake_group_message_event_v11
    from mocks import seed_service
    from maimai_py import SongType
    from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    from nonebot_plugin_awmc_helper.plugins import music_query
    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.render import jp_cover
    from nonebot_plugin_awmc_helper.core.chart_card import (
        JP_ONLY_NOTE,
        chart_card_bytes,
    )

    cn_song, jp_song = _inarau_pair()
    cn_song.aliases = ["骆驼祥子"]
    jp_song.aliases = ["骆驼祥子"]

    async def fake_jp_map():
        return {1154: jp_song}

    async def fake_ensure(_song_id):
        return None

    await seed_service(song_service, [cn_song])
    monkeypatch.setattr(song_service, "_jp_songs_map", fake_jp_map)
    monkeypatch.setattr(jp_cover, "ensure", fake_ensure)

    png = await chart_card_bytes(jp_song, None, SongType.STANDARD, True)
    expected = Message(
        [
            MessageSegment.at(12345678),
            MessageSegment.text(f" {JP_ONLY_NOTE}"),
            MessageSegment.image(f"base64://{base64.b64encode(png).decode()}"),
            MessageSegment.text("您要找的是不是这首？"),
        ]
    )
    matcher = music_query.search_alias_song
    event = fake_group_message_event_v11(message="标准骆驼祥子是什么歌")
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
            {"group_id": 87654321, "user_id": 12345678, "no_cache": True},
            result={"user_id": 12345678, "role": "member", "card": "", "nickname": "t"},
        )
        ctx.should_call_send(event, expected, result=None, bot=bot)
        ctx.should_finished()
