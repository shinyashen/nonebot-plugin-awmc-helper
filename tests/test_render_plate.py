"""core/render 牌子渲染：plate_progress 进度总览卡（R7）与 plate_table_draw
各牌种达标判定边界。"""

import pytest
from mocks import requires_assets


@pytest.fixture
async def songs(tmp_path):
    from mocks import seed_service

    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.core.songs import song_service

    store.set_db_file(tmp_path / "awmc.db")
    await store.init_db()
    await seed_service(song_service)
    yield
    song_service._ready.clear()
    store.set_db_file(None)


@requires_assets
def test_plate_progress_card_smoke():
    import io
    from pathlib import Path

    from PIL import Image

    from nonebot_plugin_awmc_helper.core.render.plate_progress import (
        plate_progress_bytes,
    )

    def slot(li, cleared, total, n_items):
        items = [(231 + (i * 37) % 700, li, 13.0 - i * 0.1) for i in range(n_items)]
        return {"level_index": li, "cleared": cleared, "total": total, "items": items}

    slots = [slot(li, 40 - li * 5, 52 - li * 4, 3 + li * 5) for li in (3, 2, 1, 0)]
    png = plate_progress_bytes(
        "樱",
        "极",
        service="DivingFish",
        slots=slots,
        total_count=52,
        completed_count=28,
    )
    im = Image.open(io.BytesIO(png))
    assert im.size[0] == 1400
    assert im.size[1] > 900

    # 牌名繁体映射：樱极 → 櫻極.png 存在于素材包
    assert Path("static/mai/plate_version/櫻極.png").exists()

    # 日服增量包牌头：回 归一后的素材查找走 简→繁 映射（廻），丸 直查
    from nonebot_plugin_awmc_helper.core.render.assets import Assets

    assert Assets.plate_version("回", "将") is not None
    assert Assets.plate_version("丸", "极") is not None


@pytest.mark.asyncio
async def test_plate_qualified_kinds(songs):
    """各牌种达标判定边界：舞舞须 FSD/FSDp（曾把方向写反漏 FSDp、误纳 Sync/FS/FSP）。

    判定单源于 core.plates.plate_score_ok（完成表盖章与本地判牌共用）。
    """
    from maimai_py import FCType, FSType, RateType, SongType, LevelIndex, ScoreExtend

    from nonebot_plugin_awmc_helper.core.plates import plate_score_ok as _qualified

    def score(fs=None, fc=None, ach=100.0):
        return ScoreExtend(
            id=1,
            level="13+",
            level_index=LevelIndex.MASTER,
            achievements=ach,
            fc=fc,
            fs=fs,
            dx_score=None,
            dx_rating=250.0,
            play_count=1,
            play_time=None,
            rate=RateType.SSSP,
            type=SongType.STANDARD,
            title="t",
            level_value=13.0,
            level_dx_score=0,
            dx_star=None,
            version=15000,
        )

    # 舞舞：仅 FSD/FSDp 达标
    assert _qualified("舞舞", score(fs=FSType.FSD))
    assert _qualified("舞舞", score(fs=FSType.FSDP))
    for fs in (FSType.SYNC, FSType.FS, FSType.FSP):
        assert not _qualified("舞舞", score(fs=fs))
    assert not _qualified("舞舞", score(fs=None))
    # 极/神：枚举值越小越强
    assert _qualified("极", score(fc=FCType.FC))
    assert _qualified("极", score(fc=FCType.APP))
    assert not _qualified("极", score())
    assert _qualified("神", score(fc=FCType.AP))
    assert _qualified("神", score(fc=FCType.APP))
    assert not _qualified("神", score(fc=FCType.FC))
    # 者/将：达成率阈值
    assert _qualified("者", score(ach=80.0))
    assert not _qualified("者", score(ach=79.9))
    # 将 = ≥SSS（100.0；S=97/S+=98/SS=99/SS+=99.5/SSS=100/SSS+=100.5 大将）
    assert _qualified("将", score(ach=100.0))
    assert not _qualified("将", score(ach=99.9999))
    assert not _qualified("将", None)


def test_plate_latest_header_fallback(tmp_path, monkeypatch):
    """未牌头图回退（2026-10-05 定案）：素色框缓存存在即返回，缺失返回 None。

    未 无牌图（现行代不会有当版本牌字）：Assets.plate_version 对「未」落
    官方デフォルト素色框（jp_cover 启动预缓存）；monkeypatch 打在 assets
    模块函数上（render/__init__ 把 assets 名字绑成了单例实例）。
    """
    import importlib

    from PIL import Image

    assets_mod = importlib.import_module(
        "nonebot_plugin_awmc_helper.core.render.assets"
    )
    seeded = tmp_path / "UI_Plate_default.png"
    Image.new("RGBA", (396, 63), "#aacfff").save(seeded)
    monkeypatch.setattr(assets_mod, "default_plate_path", lambda: seeded)

    from nonebot_plugin_awmc_helper.core.render import assets

    img = assets.plate_version("未", "将")
    assert img is not None
    assert img.size == (396, 63)
    # 缓存缺失 → None（完成表/进度头图静默跳过贴图，不抛）
    monkeypatch.setattr(
        assets_mod, "default_plate_path", lambda: tmp_path / "missing.png"
    )
    assert assets.plate_version("未", "将") is None


@requires_assets
@pytest.mark.asyncio
async def test_plate_template_name_split_by_view(songs, tmp_path, monkeypatch):
    """底图名分口径（2026-10-05 修同名冲突）：CN 裸名、JP 带 -jp 后缀。

    共用牌字（华 等）两口径区间/曲集都异——CN 华（20000-20999）含 20000
    段、JP 华（20500-20999）不含——同名底图会让日服查询读到 CN 预渲染
    网格、叠章错位（未牌预览改造时发现的历史隐患）。
    """
    from mocks import make_diff, make_song, sample_songs, seed_service

    from nonebot_plugin_awmc_helper.core.songs import song_service
    from nonebot_plugin_awmc_helper.core.render import table_template
    from nonebot_plugin_awmc_helper.core.render.plate_table_draw import (
        draw_plate_table,
    )

    # 测试注入集当 JP 视图（真实 jp_all 走 songdb 规范表，与 tables 测试同规）
    real_get_all = song_service.get_all

    async def fake_jp_all():
        return await real_get_all()

    monkeypatch.setattr(song_service, "jp_all", fake_jp_all)

    out = tmp_path / "plate_table"
    monkeypatch.setattr(table_template, "plate_table_dir", lambda: out)

    # 注入构造曲：20050 仅 CN 华含、20600 两口径都含 → 两口径必出不同网格
    extra = [
        make_song(8011, "（构造）DX 早期曲", diffs=[make_diff(version=20050)]),
        make_song(8012, "（构造）DX PLUS 曲", diffs=[make_diff(version=20600)]),
    ]
    await seed_service(song_service, [*sample_songs(), *extra])

    n_cn = await table_template.generate_plate_template("华", "将", song_service)
    n_jp = await table_template.generate_plate_template(
        "华", "将", song_service, jp=True
    )
    assert n_cn == 2
    assert n_jp == 1
    assert (out / "华将.png").exists()
    assert (out / "华将-jp.png").exists()
    assert (out / "华将.png").read_bytes() != (out / "华将-jp.png").read_bytes()

    # 读图同规：JP 底图缺失时 JP 查询视为缺失（None → fallback 现场生成），
    # CN 查询不受影响照读裸名
    (out / "华将-jp.png").unlink()
    base = {"version": "华", "kind": "将", "play_result": [], "entries": [], "page": 1}
    assert draw_plate_table(jp=True, **base) is None
    assert draw_plate_table(jp=False, **base) is not None
