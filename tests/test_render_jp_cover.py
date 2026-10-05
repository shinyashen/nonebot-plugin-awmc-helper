"""core/render/jp_cover：日服封面在线拉取测试（respx mock + 临时缓存目录）。

``render/__init__`` 把 ``assets`` 名字绑定成了单例实例，取 assets 模块须经
importlib（否则 monkeypatch 目标是实例而非模块）。
"""

import importlib
from pathlib import Path

import respx
import pytest

PNG = b"\x89PNG-fake-bytes"

URL = "https://maimaidx.jp/maimai-mobile/img/Music/c4ec.png"

DEFAULT_PLATE_URL = (
    "https://maimaidx.jp/maimai-mobile/img/NamePlate/b919c327669240b8.png"
)


@pytest.fixture
def mock():
    with respx.mock(assert_all_called=False) as mock:
        yield mock


@pytest.fixture
async def jp_env(tmp_path: Path, monkeypatch):
    """隔离环境：独立 static 目录 / 缓存目录 / 曲库文件（含建表）。"""
    from nonebot_plugin_awmc_helper.core import store
    from nonebot_plugin_awmc_helper.config import plugin_config

    assets_mod = importlib.import_module(
        "nonebot_plugin_awmc_helper.core.render.assets"
    )
    static = tmp_path / "static"
    (static / "mai" / "cover").mkdir(parents=True)
    monkeypatch.setattr(plugin_config, "awmc_static_path", static)
    cache = tmp_path / "jp_covers"
    monkeypatch.setattr(assets_mod, "jp_cache_dir", lambda: cache)
    store.set_db_file(tmp_path / "awmc.db")
    await store.init_db()
    yield store, static, cache
    store.set_db_file(None)


async def _seed_song(store, song_id: int = 2019, image_url: str | None = "c4ec.png"):
    async with store.session() as session:
        session.add(store.SongRow(id=song_id, title="Cryogenic", image_url=image_url))
        await session.commit()


@pytest.mark.asyncio
async def test_ensure_downloads_and_caches(jp_env, mock):
    """无本地素材时按 image_url 拉取官方曲绘并落盘缓存。"""
    store, _static, cache = jp_env
    await _seed_song(store)
    mock.get(url=URL).respond(200, content=PNG)

    from nonebot_plugin_awmc_helper.core.render import assets, jp_cover

    assert await jp_cover.ensure(2019, cache_dir=cache) is True
    assert (cache / "2019.png").read_bytes() == PNG
    assert (cache / "2019.png") in assets.cover_candidates(2019)


@pytest.mark.asyncio
async def test_ensure_skips_when_static_exists(jp_env, mock):
    """static 已有曲绘时不发起任何请求。"""
    store, static, cache = jp_env
    await _seed_song(store)
    (static / "mai" / "cover" / "2019.png").write_bytes(PNG)
    route = mock.get(url=URL).respond(200, content=PNG)

    from nonebot_plugin_awmc_helper.core.render import jp_cover

    assert await jp_cover.ensure(2019, cache_dir=cache) is True
    assert route.call_count == 0


@pytest.mark.asyncio
async def test_ensure_silent_on_fetch_failure(jp_env, mock):
    """拉取失败静默回退（返回 False、无落盘），渲染维持占位图。"""
    store, _static, cache = jp_env
    await _seed_song(store)
    mock.get(url=URL).respond(500)

    from nonebot_plugin_awmc_helper.core.render import assets, jp_cover

    assert await jp_cover.ensure(2019, cache_dir=cache) is False
    assert not (cache / "2019.png").exists()
    # 渲染候选链仍然可用（全缺时落到占位 0.png 或空）
    assert all(
        not p.exists() or p.name == "0.png" for p in assets.cover_candidates(2019)
    )


@pytest.mark.asyncio
async def test_ensure_false_when_song_unknown(jp_env, mock):
    """规范表无此曲（无 image_url）时直接 False，不发起请求。"""
    _store, _static, cache = jp_env
    route = mock.get(url=URL).respond(200, content=PNG)

    from nonebot_plugin_awmc_helper.core.render import jp_cover

    assert await jp_cover.ensure(999999, cache_dir=cache) is False
    assert route.call_count == 0


def test_ssl_context_includes_intermediate():
    """TLS 上下文并入 GlobalSign 中间证书（maimaidx.jp 官方缺链的修复）。"""
    from nonebot_plugin_awmc_helper.core.http import maimaidx_ssl_context

    subjects = str(maimaidx_ssl_context().get_ca_certs())
    assert "GlobalSign GCC R46 OV TLS CA 2025" in subjects


@pytest.mark.asyncio
async def test_ensure_default_plate_downloads_and_caches(jp_env, mock):
    """官方デフォルト素色框：首拉落盘固定名（未牌头图），已有缓存不重拉。"""
    _store, _static, cache = jp_env
    route = mock.get(url=DEFAULT_PLATE_URL).respond(200, content=PNG)

    from nonebot_plugin_awmc_helper.core.render import jp_cover

    path = await jp_cover.ensure_default_plate(cache_dir=cache)
    assert path == cache / "UI_Plate_default.png"
    assert path.read_bytes() == PNG
    assert route.call_count == 1

    again = await jp_cover.ensure_default_plate(cache_dir=cache)
    assert again == path
    assert route.call_count == 1


@pytest.mark.asyncio
async def test_ensure_default_plate_silent_on_failure(jp_env, mock):
    """素色框拉取失败返回 None、无落盘（未牌头图渲染侧回退跳过贴图）。"""
    _store, _static, cache = jp_env
    mock.get(url=DEFAULT_PLATE_URL).respond(500)

    from nonebot_plugin_awmc_helper.core.render import jp_cover

    assert await jp_cover.ensure_default_plate(cache_dir=cache) is None
    assert not (cache / "UI_Plate_default.png").exists()


@pytest.mark.asyncio
async def test_ensure_many_batch(jp_env, mock):
    """批量补齐：已命中零开销跳过，未命中限流拉取落盘，返回仍缺失集。"""
    store, _static, cache = jp_env
    for sid, name in ((1, "a1.png"), (2, "b2.png"), (3, "c3.png")):
        await _seed_song(store, sid, image_url=name)
    cache.mkdir(exist_ok=True)
    (cache / "1.png").write_bytes(PNG)  # 1 已命中
    mock.get(url="https://maimaidx.jp/maimai-mobile/img/Music/b2.png").respond(
        200, content=PNG
    )
    route3 = mock.get(url="https://maimaidx.jp/maimai-mobile/img/Music/c3.png").respond(
        200, content=PNG
    )

    from nonebot_plugin_awmc_helper.core.render import jp_cover

    assert await jp_cover.ensure_many([1, 2, 3], cache_dir=cache) == []
    assert (cache / "2.png").read_bytes() == PNG
    assert (cache / "3.png").read_bytes() == PNG
    assert route3.call_count == 1


@pytest.mark.asyncio
async def test_ensure_many_probe_failure_skips_batch(jp_env, mock):
    """探针（前 3 首有文件名的曲）全失败 → 整批放弃并返回缺失集（防超时空等）。"""
    store, _static, cache = jp_env
    await _seed_song(store, 4, image_url="d4.png")
    await _seed_song(store, 5, image_url="d5.png")
    mock.get(url="https://maimaidx.jp/maimai-mobile/img/Music/d4.png").respond(500)
    route5 = mock.get(url="https://maimaidx.jp/maimai-mobile/img/Music/d5.png").respond(
        500, content=PNG
    )

    from nonebot_plugin_awmc_helper.core.render import jp_cover

    assert await jp_cover.ensure_many([4, 5], cache_dir=cache) == [4, 5]
    assert not (cache / "4.png").exists()
    assert route5.call_count == 1  # 探针轮次


@pytest.mark.asyncio
async def test_ensure_many_no_url_never_probes(jp_env, mock):
    """快照无封面文件名的曲（删除曲）：排除出探针与拉取，不毒整批。

    2026-10-05 服务器实测：探针首曲落在无文件名曲（id12055 删除曲）上，
    本地即刻 False 被误判「官方站不可达」，整批封面全落占位图。
    """
    store, _static, cache = jp_env
    await _seed_song(store, 2055, image_url=None)  # 删除曲：无文件名
    await _seed_song(store, 269, image_url="x9f.png")
    mock.get(url="https://maimaidx.jp/maimai-mobile/img/Music/x9f.png").respond(
        200, content=PNG
    )

    from nonebot_plugin_awmc_helper.core.render import jp_cover

    assert await jp_cover.ensure_many([2055, 269], cache_dir=cache) == []
    assert (cache / "269.png").read_bytes() == PNG
    # 无文件名曲未发起任何请求（music/x9f 之外的路径未 mock，命中即报错）
    assert not (cache / "2055.png").exists()


@pytest.mark.asyncio
async def test_ensure_many_single_404_does_not_bail(jp_env, mock):
    """探针中单曲 404：不熔断（其余探针成功即继续），404 曲进缺失集。"""
    store, _static, cache = jp_env
    for sid, name in ((8, "h8.png"), (9, "h9.png"), (10, "h10.png")):
        await _seed_song(store, sid, image_url=name)
    mock.get(url="https://maimaidx.jp/maimai-mobile/img/Music/h8.png").respond(404)
    mock.get(url="https://maimaidx.jp/maimai-mobile/img/Music/h9.png").respond(
        200, content=PNG
    )
    route10 = mock.get(
        url="https://maimaidx.jp/maimai-mobile/img/Music/h10.png"
    ).respond(200, content=PNG)

    from nonebot_plugin_awmc_helper.core.render import jp_cover

    assert await jp_cover.ensure_many([8, 9, 10], cache_dir=cache) == [8]
    assert (cache / "9.png").exists()
    assert route10.call_count == 1


@pytest.mark.asyncio
async def test_ensure_many_all_cached_no_request(jp_env, mock):
    """全部命中 static/缓存时不发起任何请求。"""
    _store, static, cache = jp_env
    cache.mkdir(exist_ok=True)
    cover_dir = static / "mai" / "cover"
    (cover_dir / "7.png").write_bytes(PNG)
    (cache / "8.png").write_bytes(PNG)

    from nonebot_plugin_awmc_helper.core.render import jp_cover

    await jp_cover.ensure_many([7, 8], cache_dir=cache)
    assert not mock.routes or all(r.call_count == 0 for r in mock.routes)
