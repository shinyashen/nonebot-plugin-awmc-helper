"""render/best50 纯函数单测：ID 归一化、标题截断、段位牌/段位徽章序号、徽章映射。

导入放函数内：顶层 import 会在 nonebug 初始化前触发插件包加载（见 conftest）。
"""

import dataclasses

import pytest
from mocks import requires_assets


def _score(song_id: int, type_):
    from maimai_py import Score, RateType, LevelIndex, ScoreExtend

    base = Score(
        id=song_id,
        level="14",
        level_index=LevelIndex.MASTER,
        achievements=100.5,
        fc=None,
        fs=None,
        dx_score=2000,
        dx_rating=300,
        play_count=None,
        play_time=None,
        rate=RateType.SSS,
        type=type_,
    )
    return ScoreExtend(
        **dataclasses.asdict(base),
        title="t",
        level_value=14.0,
        level_dx_score=3000,
        dx_star=None,
        version=22000,
    )


def test_game_song_id_lxns_dx_needs_10000():
    """落雪成绩 DX 谱 id 缺 10000 位（1315 → 11315）。"""
    from maimai_py import SongType

    from nonebot_plugin_awmc_helper.core.render.best50 import game_song_id

    assert game_song_id(_score(1315, SongType.DX)) == 11315


def test_game_song_id_divingfish_dx_already_full():
    """水鱼成绩已是全 id（10231），不重复加 10000。"""
    from maimai_py import SongType

    from nonebot_plugin_awmc_helper.core.render.best50 import game_song_id

    assert game_song_id(_score(10231, SongType.DX)) == 10231


def test_game_song_id_standard_unchanged():
    """SD 谱 id 不动。"""
    from maimai_py import SongType

    from nonebot_plugin_awmc_helper.core.render.best50 import game_song_id

    assert game_song_id(_score(231, SongType.STANDARD)) == 231


def test_truncate_title_ascii():
    """行卡标题截断（tools.truncate_hoshino）：>18 列截到 17 列加省略号。"""
    from nonebot_plugin_awmc_helper.core.render.tools import truncate_hoshino

    assert truncate_hoshino("short", 18) == "short"
    assert truncate_hoshino("BULK UP (GAME EXCLUSIVE ver.)", 18) == (
        "BULK UP (GAME EXC..."
    )
    assert truncate_hoshino("Love's Theme of BADASS~", 18) == "Love's Theme of B..."


def test_truncate_title_cjk():
    """全角按 2 列计：恰好 18 列不截断，超限按宽度保留。"""
    from nonebot_plugin_awmc_helper.core.render.tools import truncate_hoshino

    assert truncate_hoshino("一か罰一か罰一か罰", 18) == "一か罰一か罰一か罰"
    assert truncate_hoshino("超最終鬼畜妹フランドール・", 18) == "超最終鬼畜妹フラ..."


def test_dani_plate_num_skips_after_10():
    """段位 >10 文件号跳一位（Hoshino 同款）。"""
    from nonebot_plugin_awmc_helper.core.render.best50 import dani_plate_num

    assert dani_plate_num(0) == "00"
    assert dani_plate_num(10) == "10"
    assert dani_plate_num(11) == "12"
    assert dani_plate_num(15) == "16"


def test_ra_badge_num_thresholds():
    """边界与 Hoshino 一致（rating < limit 才归下一段，14500 归 10 号牌）。"""
    from nonebot_plugin_awmc_helper.core.render.best50 import ra_badge_num

    assert ra_badge_num(999) == "01"
    assert ra_badge_num(14499) == "09"
    assert ra_badge_num(14500) == "10"
    assert ra_badge_num(15500) == "11"
    assert ra_badge_num(20000) == "11"
    # circle 主题 16000–16999 用 12 号牌，其余与 prism 相同
    assert ra_badge_num(15999, "circle") == "11"
    assert ra_badge_num(16500, "circle") == "12"
    assert ra_badge_num(17000, "circle") == "11"


def test_ra_star_num_matches_hoshino():
    from nonebot_plugin_awmc_helper.core.render.best50 import ra_star_num

    assert ra_star_num(14000) == "01"
    assert ra_star_num(15800) == "04"
    assert ra_star_num(16750) == "04"


def test_ra_star_num_low_rating_clamped():
    """rating<14000（调用方守卫失效时）不取负下标：钳到 0 号档（1 星）。"""
    from nonebot_plugin_awmc_helper.core.render.best50 import ra_star_num

    assert ra_star_num(0) == "01"
    assert ra_star_num(13999) == "01"


@requires_assets
def test_utage_bg_no_highlight_pixels_falls_back(monkeypatch, tmp_path):
    """底图素材无高饱和像素（纯灰白图）时不 IndexError：回退原图不动色相。"""
    from PIL import Image as PILImage

    from nonebot_plugin_awmc_helper.core.render import best50
    from nonebot_plugin_awmc_helper.core.render.assets import Assets

    pic_dir = tmp_path / "mai" / "pic"
    pic_dir.mkdir(parents=True)
    # 全图低饱和（S<128）→ 高亮像素 Counter 为空（修复前 most_common(1)[0] 越界）
    PILImage.new("RGBA", (64, 64), (200, 200, 200, 255)).save(
        pic_dir / "b50_score_basic.png"
    )

    best50._utage_score_bg.cache_clear()
    monkeypatch.setattr(Assets, "static_path", staticmethod(lambda: tmp_path))
    try:
        bg = best50._utage_score_bg()
        assert bg.size == (64, 64)
        assert bg.convert("RGB").getpixel((0, 0)) == (200, 200, 200)
    finally:
        # 防污染后续用例：真实素材路径下的缓存必须重算
        best50._utage_score_bg.cache_clear()


@pytest.mark.asyncio
async def test_qq_avatar_uses_smart_client(monkeypatch):
    """QQ 头像走 create_smart_client（统一国内外分流），不再裸 httpx.AsyncClient。"""
    import io

    from PIL import Image as PILImage

    from nonebot_plugin_awmc_helper.core.render import best50

    seen = {}

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, params=None):
            seen["url"] = url
            seen["params"] = params

            class _Resp:
                def raise_for_status(self):
                    return None

                @property
                def content(self):
                    buf = io.BytesIO()
                    PILImage.new("RGBA", (4, 4), (255, 0, 0, 255)).save(buf, "PNG")
                    return buf.getvalue()

            return _Resp()

    def fake_smart_client(**kwargs):
        # 真工厂是同步函数，返回支持异步上下文的 client
        seen["kwargs"] = kwargs
        return _FakeClient()

    monkeypatch.setattr(best50, "create_smart_client", fake_smart_client)
    img = await best50._qq_avatar(123456)
    assert img is not None
    assert img.size == (4, 4)
    assert seen["kwargs"] == {"timeout": 10}
    assert seen["url"] == "https://q1.qlogo.cn/g"


def test_combo_sync_icon_files_cover_all_enum_members():
    """FC/FS 全枚举有素材映射，含落雪 sync → Sync。"""
    from maimai_py import FCType, FSType

    from nonebot_plugin_awmc_helper.constants import SYNC_FILE, COMBO_FILE

    assert set(COMBO_FILE) == {fc.name.lower() for fc in FCType}
    assert set(SYNC_FILE) == {fs.name.lower() for fs in FSType}
    assert SYNC_FILE["sync"] == "Sync"
    assert COMBO_FILE["fcp"] == "FCp"


def _render_row(score):
    """渲染单张行卡（264×109，fc/fs/星留空以排除徽章干扰）。"""
    from PIL import Image, ImageDraw

    from nonebot_plugin_awmc_helper.core.render.best50 import draw_score_row

    im = Image.new("RGBA", (264, 109), (255, 255, 255, 255))
    draw_score_row(im, ImageDraw.Draw(im), 0, 0, score, "prism_plus")
    return im


def _dominant_color(im):
    """行卡高饱和像素众数 RGB（即色带主色，白色文字/留白不参与）。

    阈值取 100 而非 128：目标色 #EB77ED 的 8-bit 饱和度恰为 127。
    """
    from collections import Counter

    rgb = im.convert("RGB")
    counter = Counter()
    for px, color in zip(
        rgb.convert("HSV").get_flattened_data(), rgb.get_flattened_data()
    ):
        if px[1] >= 100 and px[2] >= 128:
            counter[color] += 1
    return counter.most_common(1)[0][0]


@requires_assets
def test_utage_score_row_tinted_eb77ed():
    """宴谱行卡：UTAGE 换程序染色 #EB77ED 底图与同色 ID 字。

    库把宴谱 level_index 记为 BASIC（divingfish provider 硬编码），
    修复前会误用绿色 BASIC 底图。
    """
    from maimai_py import SongType, LevelIndex

    from nonebot_plugin_awmc_helper.core.render.best50 import UTAGE_BAND_COLOR

    utage = dataclasses.replace(
        _score(100001, SongType.UTAGE), level_index=LevelIndex.BASIC
    )
    im = _render_row(utage)
    dominant = _dominant_color(im)
    assert all(abs(a - b) <= 2 for a, b in zip(dominant, UTAGE_BAND_COLOR))
    # 灰色圆等中性区域不动（V 缩放只作用于有彩度像素）
    assert im.convert("RGB").getpixel((182, 96)) == (231, 231, 231)


@requires_assets
def test_basic_score_row_bg_not_tinted():
    """普通 BASIC 行卡仍是绿底：染色只对 type=UTAGE 生效，其余难度不变。"""
    from maimai_py import SongType, LevelIndex

    basic = dataclasses.replace(
        _score(100001, SongType.STANDARD), level_index=LevelIndex.BASIC
    )
    dominant = _dominant_color(_render_row(basic))
    # BASIC 绿 #81D955，与宴谱粉紫 #EB77ED 相差悬殊
    assert all(abs(a - b) <= 2 for a, b in zip(dominant, (129, 217, 85)))


@requires_assets
def test_utage_bg_cached_and_alpha_preserved():
    """宴谱底图进程内缓存（同对象复用），透明度通道与源 BASIC 底图逐字节一致。"""
    from PIL import Image

    from nonebot_plugin_awmc_helper.core.render.assets import assets
    from nonebot_plugin_awmc_helper.core.render.best50 import _utage_score_bg

    bg = _utage_score_bg()
    assert bg is _utage_score_bg()
    src_alpha = Image.open(
        assets.static_path() / "mai" / "pic" / "b50_score_basic.png"
    ).getchannel("A")
    assert bg.getchannel("A").tobytes() == src_alpha.tobytes()


def test_fit_into_scales_and_centers():
    """NET 官方徽章图等比适配槽位：只缩不放、居中、画布透明。"""
    from PIL import Image

    from nonebot_plugin_awmc_helper.core.render.best50 import _fit_into

    wide = Image.new("RGBA", (100, 50), (255, 0, 0, 255))  # 2:1 → 装进 80x32
    out = _fit_into(wide, (80, 32))
    assert out.size == (80, 32)
    scaled = out.getbbox()  # 非透明内容区
    assert scaled is not None
    inner_w, inner_h = scaled[2] - scaled[0], scaled[3] - scaled[1]
    assert (inner_w, inner_h) == (64, 32)  # min(0.8, 0.64) = 0.64
    assert (scaled[0], scaled[1]) == ((80 - 64) // 2, 0)  # 垂直已满、水平居中

    tiny = Image.new("RGBA", (10, 5), (0, 255, 0, 255))  # 小图不放大
    out2 = _fit_into(tiny, (80, 32))
    bbox2 = out2.getbbox()
    assert bbox2 is not None
    assert (bbox2[2] - bbox2[0], bbox2[3] - bbox2[1]) == (10, 5)


def test_fit_into_upscale_opt_in():
    """名牌图放大适配：默认不放大，allow_upscale=True 才放大填满槽位。"""
    from PIL import Image

    from nonebot_plugin_awmc_helper.core.render.best50 import _fit_into

    plate = Image.new("RGBA", (396, 66), (255, 0, 0, 255))
    out = _fit_into(plate, (800, 130))  # 默认只缩不放
    bbox = out.getbbox()
    assert bbox is not None
    assert (bbox[2] - bbox[0], bbox[3] - bbox[1]) == (396, 66)
    out2 = _fit_into(plate, (800, 130), allow_upscale=True)
    bbox2 = out2.getbbox()
    assert bbox2 is not None
    w, h = bbox2[2] - bbox2[0], bbox2[3] - bbox2[1]
    assert (w, h) == (780, 130)  # min(800/396, 130/66) ≈ 1.9697 → 780×130


def test_score_list_height_formula():
    """分数列表高度公式（NB 版式算式下沉 core）：非末页整 80 四段，末页按实条数。"""
    from nonebot_plugin_awmc_helper.core.render.score import score_list_height

    assert score_list_height(200, 1, 3) == 16 * 109 + 130 * 4  # 非末页整 80
    assert score_list_height(161, 3, 3) == 4 * 109 + 130 * 1  # 末页 1 条：4 行 1 段
    assert score_list_height(181, 3, 3) == 5 * 109 + 130 * 2  # 末页 21 条：5 行 2 段
    assert score_list_height(160, 2, 2) == 16 * 109 + 130 * 4  # 末页恰整 80


@requires_assets
def test_score_row_sub_hook_swaps_subline():
    """副行提取器替换「定数 -> 单曲Ra」行；返回 None 回退默认（导分 pc 列表复用）。"""
    from PIL import Image, ImageDraw
    from maimai_py import SongType

    from nonebot_plugin_awmc_helper.core.render.best50 import draw_score_row

    def render(sub_of):
        im = Image.new("RGBA", (260, 114), (255, 255, 255, 255))
        draw_score_row(
            im,
            ImageDraw.Draw(im),
            0,
            0,
            _score(231, SongType.DX),
            "prism_plus",
            sub_of=sub_of,
        )
        return im.tobytes()

    default = render(None)
    assert render(lambda s: None) == default  # 提取器返回 None → 默认定数行
    assert render(lambda s: f"pc: {s.id}") != default  # 自定义文字生效
    assert render(lambda s: f"pc: {s.id}") == render(lambda s: f"pc: {s.id}")


def test_cover_song_id_utage_uses_root_id():
    """宴谱 diff id 取模即根 id＝宿主曲（id = 100000 + level_id×10000 + 根 id，
    步进恰 10000）：宿主封面同曲同图，纯宴谱曲（998/851）素材包也按根 id
    收录官方曲绘（与 otoge-db jacket 比对确认），不存在需要特判的例外。"""
    from maimai_py import SongType

    from nonebot_plugin_awmc_helper.core.render.best50 import cover_song_id

    assert cover_song_id(_score(100998, SongType.UTAGE)) == 998  # [宴]Oshama
    assert cover_song_id(_score(140227, SongType.UTAGE)) == 227  # Garakuta 某
    assert cover_song_id(_score(161852, SongType.UTAGE)) == 1852  # 惣菜 龙
    # 常规谱：落雪缺位 id 与水鱼全 id 落到同一封面文件
    assert cover_song_id(_score(10231, SongType.DX)) == 231
    assert cover_song_id(_score(231, SongType.STANDARD)) == 231


@requires_assets
@pytest.mark.asyncio
async def test_b50_footer_color_follows_theme(monkeypatch):
    """页脚颜色随入参主题：circle 用户页脚应为 CIRCLE_PINK（修复前恒 prism 蓝）。"""
    from PIL import ImageDraw

    from nonebot_plugin_awmc_helper.core.render import best50
    from nonebot_plugin_awmc_helper.core.render.tools import CIRCLE_PINK

    seen = []
    real_text = ImageDraw.ImageDraw.text

    def spy_text(self, xy, *args, **kwargs):
        if tuple(xy) == (700, 1570):
            seen.append(kwargs.get("fill"))
        return real_text(self, xy, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", spy_text)

    await best50.draw_b50_nb("t", 15000, 7500, 7500, [], [], theme="circle")
    assert seen == [CIRCLE_PINK]


@requires_assets
@pytest.mark.asyncio
async def test_b50_render_smoke():
    """B50 与成绩列表绘图冒烟。"""
    import dataclasses

    from maimai_py import Score, RateType, SongType, LevelIndex, ScoreExtend

    from nonebot_plugin_awmc_helper.core.render.score import DrawScore
    from nonebot_plugin_awmc_helper.core.render.best50 import best50_bytes

    base = Score(
        id=1,
        level="13",
        level_index=LevelIndex.MASTER,
        achievements=99.5,
        fc=None,
        fs=None,
        dx_score=2000,
        dx_rating=250,
        play_count=None,
        play_time=None,
        rate=RateType.SSP,
        type=SongType.DX,
    )
    sc = ScoreExtend(
        **dataclasses.asdict(base),
        title="RenderSong",
        level_value=13.0,
        level_dx_score=2400,
        dx_star=4,
        version=25000,
    )
    png = await best50_bytes("tester", 250, 250, 0, [sc], [])
    assert png.startswith(b"\x89PNG")
    assert len(png) > 1000
    listing = DrawScore(280 + 4 * 109 + 130, service=None).draw_score_list(
        "13", [sc], 1, 1
    )
    assert listing.startswith(b"\x89PNG")


@requires_assets
@pytest.mark.asyncio
async def test_flat_layout_bytes():
    """flat 版式（条件50）：10 行等距铺满原跨度、不裁高、尺寸与标准卡一致。"""
    from maimai_py import Score, RateType, SongType, LevelIndex, ScoreExtend

    from nonebot_plugin_awmc_helper.core.render.best50 import (
        _flat_row_y,
        best50_flat_bytes,
    )

    # 几何定稿（karenbot-combo-notes §8.4）：首行 y=235、末行 y=1313
    # （行卡底 1313+109=1422，与标准卡最后一行对齐）
    assert _flat_row_y(0) == 235
    assert _flat_row_y(9) == 1313

    base = Score(
        id=1,
        level="13",
        level_index=LevelIndex.MASTER,
        achievements=99.5,
        fc=None,
        fs=None,
        dx_score=2000,
        dx_rating=250,
        play_count=None,
        play_time=None,
        rate=RateType.SSP,
        type=SongType.DX,
    )
    sc = ScoreExtend(
        **dataclasses.asdict(base),
        title="RenderSong",
        level_value=13.0,
        level_dx_score=2400,
        dx_star=4,
        version=25000,
    )
    png = await best50_flat_bytes(
        "tester", 250, [sc], label="辉50 · 1 条 · 合计 RA 250"
    )
    assert png.startswith(b"\x89PNG")
    assert len(png) > 1000


@requires_assets
@pytest.mark.asyncio
async def test_flat_label_forces_trophy(monkeypatch):
    """flat 版式 label 强制覆盖数据源称号：force 标记必须传到 _draw_header
    （回归：落雪称号曾压过「条件 · 条数 · 合计RA」口径条，2026-10-01）。"""
    from nonebot_plugin_awmc_helper.core.render import best50

    captured: dict = {}

    async def fake_header(im, draw, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(best50, "_draw_header", fake_header)
    from maimai_py import SongType

    label = "辉50 · 1 条 · 合计 RA 250"
    await best50.draw_b50_flat("tester", 250, [_score(199, SongType.DX)], label=label)
    assert captured["force_trophy_name"] is True
    assert captured["trophy_name"] == best50.truncate_hoshino(label, 36)
