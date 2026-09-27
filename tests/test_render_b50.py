"""render/best50 纯函数单测：ID 归一化、标题截断、段位牌/段位徽章序号、徽章映射。

导入放函数内：顶层 import 会在 nonebug 初始化前触发插件包加载（见 conftest）。
"""

import dataclasses

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
    """截断规则与 Hoshino 一致：>18 列截到 17 列加省略号（与预期图逐字符一致）。"""
    from nonebot_plugin_awmc_helper.core.render.best50 import truncate_title

    assert truncate_title("short") == "short"
    assert truncate_title("BULK UP (GAME EXCLUSIVE ver.)") == "BULK UP (GAME EXC..."
    assert truncate_title("Love's Theme of BADASS~") == "Love's Theme of B..."


def test_truncate_title_cjk():
    """全角按 2 列计：恰好 18 列不截断，超限按宽度保留。"""
    from nonebot_plugin_awmc_helper.core.render.best50 import truncate_title

    assert truncate_title("一か罰一か罰一か罰") == "一か罰一か罰一か罰"
    assert truncate_title("超最終鬼畜妹フランドール・") == "超最終鬼畜妹フラ..."


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
    assert (bbox2[2] - bbox2[0], bbox2[3] - bbox2[1]) == (10, 5)


def test_fit_into_upscale_opt_in():
    """名牌图放大适配：默认不放大，allow_upscale=True 才放大填满槽位。"""
    from PIL import Image

    from nonebot_plugin_awmc_helper.core.render.best50 import _fit_into

    plate = Image.new("RGBA", (396, 66), (255, 0, 0, 255))
    out = _fit_into(plate, (800, 130))  # 默认只缩不放
    bbox = out.getbbox()
    assert (bbox[2] - bbox[0], bbox[3] - bbox[1]) == (396, 66)
    out2 = _fit_into(plate, (800, 130), allow_upscale=True)
    bbox2 = out2.getbbox()
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
