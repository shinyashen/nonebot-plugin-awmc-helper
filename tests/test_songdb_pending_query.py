"""song_pending 可查兜底（Q33）测试：gate 解析 / 搜索过滤 / 渲染冒烟 / 指令链路。

场景：MAGiCAL 等新版本曲目在 magical 机台源与 maimaiinfo 均未提供根 id 的窗口期，
曲目只存在于 ``song_pending``（otoge 条目 payload）——验证「歌名 + 至少一张谱面
有具体定数」即可被查歌查到，其余缺失字段在卡面上以 - 呈现。
"""

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
    """注入样例曲库 + 独立临时数据库（CN 视图有数据、pending 表为空）。"""
    from mocks import sample_songs, seed_service

    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.core.songs import song_service

    store.set_db_file(tmp_path / "awmc.db")
    await store.init_db()
    await seed_service(song_service, sample_songs())
    yield
    song_service._ready.clear()
    store.set_db_file(None)


def _otoge_item(**over) -> dict:
    """MAGiCAL 新曲形态的 otoge 条目（定数齐、物量/封面可缺）。"""
    base = {
        "title": "物語はここから",
        "artist": "OSTER project feat. Kanata.N",
        "catcode": "POPS＆アニメ",
        "bpm": "190",
        "version": "27000",
        "image_url": None,
        "dx_lev_bas": "4",
        "dx_lev_bas_i": "4.0",
        "dx_lev_adv": "7",
        "dx_lev_adv_i": "7.5",
        "dx_lev_exp": "10+",
        "dx_lev_exp_i": "10.9",
        "dx_lev_mas": "13",
        "dx_lev_mas_i": "13.5",
        "dx_lev_exp_designer": "譜面作者X",
    }
    base.update(over)
    return base


def test_parse_gate_requires_constant():
    """可查 gate：至少一张谱面有具体定数；无定数/问号定数/空标题均不可查。"""
    from nonebot_plugin_awmc_helper.core.songdb import parse_pending_item

    pending = parse_pending_item(_otoge_item())
    assert pending is not None
    assert [c.level_value for c in pending.charts] == [4.0, 7.5, 10.9, 13.5]
    assert all(c.is_dx for c in pending.charts)
    assert pending.genre == "POPSアニメ"
    assert pending.version == 27000
    assert pending.bpm == "190"

    # 全部定数缺失 → None（继续等 otoge 补数）
    item = _otoge_item()
    for k in list(item):
        if k.endswith("_i"):
            del item[k]
    assert parse_pending_item(item) is None
    # 定数为 "?" 不可解析；但其余谱面仍有时整曲可查
    assert parse_pending_item(_otoge_item(dx_lev_mas_i="?")) is not None
    item = _otoge_item()
    for suffix in ("bas", "adv", "exp", "mas"):
        item[f"dx_lev_{suffix}_i"] = "?"
    assert parse_pending_item(item) is None
    # 空标题不可查
    assert parse_pending_item(_otoge_item(title="")) is None


def test_parse_missing_fields_render_as_none():
    """缺字段 → None（渲染层画 -）：标级 ?、物量缺、曲师/BPM/分类/封面缺。"""
    from nonebot_plugin_awmc_helper.core.songdb import parse_pending_item

    pending = parse_pending_item(
        _otoge_item(
            artist=None,
            bpm=None,
            catcode="未知分类",
            image_url=None,
            dx_lev_mas="?",
            dx_lev_exp_designer=None,
        )
    )
    assert pending is not None
    assert pending.artist is None
    assert pending.bpm is None
    assert pending.genre is None  # catcode 映射不到
    assert pending.image_url is None
    mas = next(c for c in pending.charts if c.level_id == 3)
    assert mas.level is None  # "?" 形标级视为未知
    assert mas.level_value == 13.5
    assert mas.notes is None  # 无物量锚点
    exp = next(c for c in pending.charts if c.level_id == 2)
    assert exp.designer is None
    # 标级 "13+" 与 "13?" 均为可展示形态
    p_plus = parse_pending_item(_otoge_item(dx_lev_mas="13+"))
    p_q = parse_pending_item(_otoge_item(dx_lev_mas="13?"))
    assert p_plus is not None
    assert p_plus.charts[3].level == "13+"
    assert p_q is not None
    assert p_q.charts[3].level == "13?"


def test_parse_notes_partial_missing():
    """物量部分缺失：「0 即 -」约定——缺失列填 0（渲染层画 -），tap 为锚点。"""
    from nonebot_plugin_awmc_helper.core.songdb import parse_pending_item

    pending = parse_pending_item(
        _otoge_item(
            dx_lev_mas_notes_tap="564",
            dx_lev_mas_notes_hold="65",
            dx_lev_mas_notes_slide=None,
            dx_lev_mas_notes_touch=None,
            dx_lev_mas_notes_break="68",
        )
    )
    assert pending is not None
    mas = next(c for c in pending.charts if c.level_id == 3)
    assert mas.notes == (564, 65, 0, 0, 68)
    bas = next(c for c in pending.charts if c.level_id == 0)
    assert bas.notes is None  # tap 缺 → 整行无物量（物化为全 0，画 -）


def test_notes_cell_text_touch_convention():
    """物量单元格约定：SD touch 恒 -、DX 真 0 显示 0、整行全 0 恒 -（Q33 口径）。"""
    from nonebot_plugin_awmc_helper.core.render.nb_chart import notes_cell_text

    # 有值 → 数字（两种谱型一致）
    assert notes_cell_text(40, 3, is_dx=True, row_has_notes=True) == "40"
    assert notes_cell_text(40, 3, is_dx=False, row_has_notes=True) == "40"
    # touch=0：DX 谱真 0（实测 15 张，如ローリンガール exp）→ 0；
    # SD 谱恒 -（touch 为 DX 谱面机制，SD 全量 2336 行无 touch）
    assert notes_cell_text(0, 3, is_dx=True, row_has_notes=True) == "0"
    assert notes_cell_text(0, 3, is_dx=False, row_has_notes=True) == "-"
    # 整行全 0 = 物量未收录：两种谱型 touch 都画 -
    assert notes_cell_text(0, 3, is_dx=True, row_has_notes=False) == "-"
    assert notes_cell_text(0, 3, is_dx=False, row_has_notes=False) == "-"
    # 其余列 0 → -（SD 旧框移植谱 hold=0 等按未收录口径）
    assert notes_cell_text(0, 1, is_dx=True, row_has_notes=True) == "-"
    assert notes_cell_text(0, 0, is_dx=False, row_has_notes=False) == "-"


def test_pending_to_song_placeholder_fields():
    """物化为临时 Song：id=0、缺失字段按「0 即 -」约定、版本未知 logo 缺席。"""
    from maimai_py import SongType

    from nonebot_plugin_awmc_helper.core.songdb import (
        pending_to_song,
        parse_pending_item,
    )

    bare = parse_pending_item(
        _otoge_item(artist=None, bpm=None, catcode="未知分类", version=None)
    )
    assert bare is not None
    assert bare.genre_display is None
    song = pending_to_song(bare)
    assert song.id == 0  # 临时 id，不参与任何 id 键路径
    assert song.bpm == 0  # 「0 即 -」：卡面画 -
    assert song.artist == "-"
    assert song.version == 0  # from_value(0) 为 None → 版本 logo 自然缺席
    assert all(d.version == 0 for d in song.difficulties.dx)
    assert [d.level for d in song.difficulties.dx] == ["4", "7", "10+", "13"]
    # 谱师仅 exp 有（base 条目只有 dx_lev_exp_designer），其余画 -
    assert (
        next(d for d in song.difficulties.dx if d.level_index.value == 2).note_designer
        == "譜面作者X"
    )
    assert (
        next(d for d in song.difficulties.dx if d.level_index.value == 3).note_designer
        == "-"
    )
    assert all(
        d.type == SongType.DX and d.level_value > 0 for d in song.difficulties.dx
    )

    full = parse_pending_item(_otoge_item())
    assert full is not None
    assert full.genre_display == "流行&动漫"
    song = pending_to_song(full)
    assert song.bpm == 190
    assert song.artist == "OSTER project feat. Kanata.N"
    mas = next(d for d in song.difficulties.dx if d.level_index.value == 3)
    assert mas.level == "13"
    assert mas.level_value == 13.5


def test_cover_key_stable_per_title():
    """封面缓存键按标题派生（pending 曲无 id，不能按 id 缓存）。"""
    from nonebot_plugin_awmc_helper.core.songdb import parse_pending_item

    a = parse_pending_item(_otoge_item())
    b = parse_pending_item(_otoge_item(title="別の新曲"))
    assert a is not None
    assert b is not None
    assert a.cover_key == a.cover_key
    assert a.cover_key != b.cover_key


@pytest.mark.asyncio
async def test_pending_search_filters(db):
    """搜索过滤：gate 排除无定数行、reason 限定 missing_id、标题归一子串与定数区间。"""
    from nonebot_plugin_awmc_helper.core.songdb import pending_search, upsert_pending

    await upsert_pending(
        "otoge-db", "title:物語はここから", "missing_id", _otoge_item()
    )
    # 无定数：gate 不过
    item = _otoge_item(title="定数未決の曲")
    for suffix in ("bas", "adv", "exp", "mas"):
        item[f"dx_lev_{suffix}_i"] = None
    await upsert_pending("otoge-db", "title:定数未決の曲", "missing_id", item)
    # 非 missing_id 行不参与
    await upsert_pending(
        "otoge-db", "title:別理由", "other_reason", _otoge_item(title="別理由")
    )

    all_pending = await pending_search()
    assert [p.title for p in all_pending] == ["物語はここから"]

    hit = await pending_search(title="物語")  # 子串
    assert len(hit) == 1
    assert hit[0].key == "title:物語はここから"
    assert hit[0].source == "otoge-db"
    assert not await pending_search(title="ここからじゃない")  # 不含关键词

    in_range = await pending_search(ds_range=(13.0, 14.0))
    assert len(in_range) == 1
    assert not await pending_search(ds_range=(15.0, 16.0))


@pytest.mark.asyncio
async def test_song_service_pending_wrappers(db):
    """SongService 包装层透传（music_query 实际调用入口）。"""
    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.songdb import upsert_pending

    await upsert_pending(
        "otoge-db", "title:物語はここから", "missing_id", _otoge_item()
    )
    assert len(await song_service.pending_by_title_fuzzy("物語")) == 1
    assert len(await song_service.pending_by_level_value(7.0, 8.0)) == 1
    assert await song_service.pending_by_title_fuzzy("没有的歌") == []


@requires_assets
@pytest.mark.asyncio
async def test_pending_card_renders(db):
    """pending 卡渲染冒烟（合并路径）：缺字段与带物量两种形态均出非空 PNG。"""
    from nonebot_plugin_awmc_helper.core.songdb import (
        pending_to_song,
        parse_pending_item,
    )
    from nonebot_plugin_awmc_helper.core.render.nb_chart import song_chart_info

    bare = parse_pending_item(_otoge_item())
    assert bare is not None
    png = song_chart_info(
        pending_to_song(bare),
        calc=False,
        is_full=False,
        best_list=[],
        jp=True,
        id_text="ID —",
        genre_text=bare.genre_display or "-",
    )
    assert png.startswith(b"\x89PNG")
    assert len(png) > 1000

    full = parse_pending_item(
        _otoge_item(
            image_url="x.png",
            dx_lev_bas_notes_tap="192",
            dx_lev_bas_notes_hold="12",
            dx_lev_bas_notes_slide="4",
            dx_lev_bas_notes_touch="4",
            dx_lev_bas_notes_break="4",
        )
    )
    assert full is not None
    png = song_chart_info(
        pending_to_song(full),
        calc=False,
        is_full=False,
        best_list=[],
        jp=True,
        id_text="ID —",
        genre_text=full.genre_display or "-",
    )
    assert png.startswith(b"\x89PNG")


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
    from nonebot_plugin_awmc_helper.core.songdb import upsert_pending

    await upsert_pending(
        "otoge-db", "title:物語はここから", "missing_id", _otoge_item()
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
    await _assert_pending_card(app, "search", "定数查歌 7.5")


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
