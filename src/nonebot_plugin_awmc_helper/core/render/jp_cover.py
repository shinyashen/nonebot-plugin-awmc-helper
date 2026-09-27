"""日服官方图片在线获取：曲绘/玩家头像 + localstore 缓存（不动 static 素材目录）。

- 触发时机：日服谱面卡渲染前调用 :func:`ensure`；static 素材与缓存都没有时才拉取；
- URL 契约（01 文档）：``https://maimaidx.jp/maimai-mobile/img/Music/{image_url}``，
  ``image_url`` 为规范表留档的 otoge-db 官方文件名（``song`` 表）；
- maimaidx.jp 在日本官方侧，走智能代理层自动代理优先（内置国外站清单）；
- 官方站点 TLS 只下发叶子证书（缺 GlobalSign 中间件），httpx 严格校验会失败：
  :func:`_ssl_context` 把中间证书并入信任上下文（CA 名含年份，官方轮换后需更新）；
- 拉取失败静默返回 False，渲染维持 0.png 占位行为；并发请求同曲合并为单次下载。
"""

from pathlib import Path

from .. import store
from ..http import maimaidx_ssl_context
from .assets import assets, jp_cache_dir
from .download import DownloadGate, download_to_file

_COVER_GATE = DownloadGate()
"""曲绘下载去重（同曲/同键并发合并为单次下载）。"""


def _has_local(song_id: int, cache: Path) -> bool:
    # static 封面路径单源 assets.cover_candidates（首环）；缓存路径随调用方
    return (
        assets.cover_candidates(song_id)[0].exists()
        or (cache / f"{song_id}.png").exists()
    )


_MUSIC_IMG_URL = "https://maimaidx.jp/maimai-mobile/img/Music/{image_url}"


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
    path = cache / f"{song_id}.png"
    url = _MUSIC_IMG_URL.format(image_url=image_url)
    return await _COVER_GATE.run(
        song_id,
        lambda: download_to_file(
            url,
            path,
            subject=f"jp_cover：{song_id} 曲绘",
            verify=maimaidx_ssl_context(),
        ),
    )


async def ensure_image(
    image_url: str, key: str, cache_dir: Path | None = None
) -> Path | None:
    """按**显式封面文件名**拉取曲绘（pending 曲无 id，不能走 song 表查 URL）。

    ``key`` 为缓存文件名（不含扩展名）——调用方保证无 id 冲突（如标题摘要）。
    成功返回落盘路径；失败返回 None（渲染走占位图）。
    """
    cache = cache_dir or jp_cache_dir()
    path = cache / f"{key}.png"
    if path.exists():
        return path
    url = _MUSIC_IMG_URL.format(image_url=image_url)
    ok = await _COVER_GATE.run(
        key,
        lambda: download_to_file(
            url, path, subject=f"jp_cover：{key} 曲绘", verify=maimaidx_ssl_context()
        ),
    )
    return path if ok else None


async def ensure_icon(file_name: str, cache_dir: Path | None = None) -> Path | None:
    """日服玩家头像落盘缓存（NET 首页身份区，img/Icon/{官方哈希文件名}）。

    B50 卡 NET 头像用：按官方文件名缓存（同名即同图，天然去重），
    与曲绘同 host 同缓存目录；失败返回 None（渲染走 QQ 头像/缺省回退）。
    ``file_name`` 只取末段（官方页哈希名；防 URL 带路径成分落到缓存目录外）。
    """
    file_name = Path(file_name).name
    cache = cache_dir or jp_cache_dir()
    path = cache / file_name
    if path.exists():
        return path
    url = f"https://maimaidx.jp/maimai-mobile/img/Icon/{file_name}"
    ok = await _COVER_GATE.run(
        f"icon:{file_name}",
        lambda: download_to_file(
            url,
            path,
            subject=f"jp_cover：NET 头像 {file_name}",
            verify=maimaidx_ssl_context(),
        ),
    )
    return path if ok else None
