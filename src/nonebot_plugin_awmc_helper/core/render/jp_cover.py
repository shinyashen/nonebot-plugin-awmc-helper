"""日服封面在线获取：maimaidx.jp 官方曲绘 + localstore 缓存（不动 static 素材目录）。

- 触发时机：日服谱面卡渲染前调用 :func:`ensure`；static 素材与缓存都没有时才拉取；
- URL 契约（01 文档）：``https://maimaidx.jp/maimai-mobile/img/Music/{image_url}``，
  ``image_url`` 为规范表留档的 otoge-db 官方文件名（``song`` 表）；
- maimaidx.jp 在日本官方侧，走智能代理层自动代理优先（内置国外站清单）；
- 拉取失败静默返回 False，渲染维持 0.png 占位行为；并发请求同曲合并为单次下载。
"""

import asyncio
from pathlib import Path

from nonebot import logger

from .. import store
from ..ext import get_client
from .assets import jp_cache_dir
from ...config import plugin_config

_inflight: dict[int, asyncio.Task[bool]] = {}


def _static_cover(song_id: int) -> Path:
    return Path(plugin_config.awmc_static_path) / "mai" / "cover" / f"{song_id}.png"


def _has_local(song_id: int, cache: Path) -> bool:
    return _static_cover(song_id).exists() or cache.joinpath(f"{song_id}.png").exists()


def _write_cover(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


async def ensure(song_id: int, cache_dir: Path | None = None) -> bool:
    """确保曲绘本地可得（static 或缓存）；本就有图或拉取成功返回 True。

    ``cache_dir`` 供测试注入缓存目录，默认取 localstore 的 ``jp_covers``。
    """
    cache = cache_dir or jp_cache_dir()
    if _has_local(song_id, cache):
        return True
    image_url = await store.song_image_url(song_id)
    if not image_url:
        return False
    task = _inflight.get(song_id)
    if task is None:
        path = cache / f"{song_id}.png"
        task = asyncio.create_task(_download(song_id, image_url, path))
        _inflight[song_id] = task
    return await task


async def _download(song_id: int, image_url: str, path: Path) -> bool:
    url = f"https://maimaidx.jp/maimai-mobile/img/Music/{image_url}"
    try:
        resp = await get_client().get(url)
        resp.raise_for_status()
        await asyncio.to_thread(_write_cover, path, resp.content)
        logger.debug(f"jp_cover：{song_id} 曲绘已缓存（{len(resp.content)}B）")
        return True
    except Exception as e:
        logger.debug(f"jp_cover：{song_id} 曲绘拉取失败（{e}）")
        return False
    finally:
        _inflight.pop(song_id, None)
