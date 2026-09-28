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
