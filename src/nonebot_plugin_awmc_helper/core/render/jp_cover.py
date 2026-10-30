"""日服官方图片在线获取：曲绘/NET 玩家资料图 + localstore 缓存（不动 static 素材目录）。

- 触发时机：渲染经唯一入口 :func:`get_cover_path` 取封面（static → 缓存 →
  在线链逐级尝试）；:func:`ensure`/:func:`ensure_many` 是其 bool/批量适配；
- URL 契约（01 文档）：``https://maimaidx.jp/maimai-mobile/img/Music/{image_url}``，
  ``image_url`` 为规范表留档的 otoge-db 官方文件名（``song`` 表）；
- maimaidx.jp 在日本官方侧，走智能代理层自动代理优先（内置国外站清单）；
- 官方站点 TLS 只下发叶子证书（缺 GlobalSign 中间件），httpx 严格校验会失败：
  :func:`_ssl_context` 把中间证书并入信任上下文（CA 名含年份，官方轮换后需更新）；
- 拉取失败静默返回 False，渲染维持 0.png 占位行为；并发请求同曲合并为单次下载；
- **otoge-db 仓库兜底（2026-10-30）**：官方站上了日本 IP 门（维护后仅日本 IP 可
  访问）且订阅机房节点被拒——官方拉取失败时回退 otoge-db 仓库同名缩图
  （190×190，raw.githubusercontent 走智能代理可达），落同一缓存路径。
"""

import asyncio
from pathlib import Path
from collections.abc import Iterable

from nonebot import logger

from .. import store
from ..http import maimaidx_ssl_context
from .assets import assets, jp_cache_dir, default_plate_path
from .download import DownloadGate, download_to_file

_COVER_GATE = DownloadGate()
"""曲绘下载去重（同曲/同键并发合并为单次下载）。"""

_PROBE_COUNT = 3
"""批量补齐的探针数：任一成功即认为官方站可达；单曲 404 不熔断。"""

_DEFAULT_PLATE_URL = (
    "https://maimaidx.jp/maimai-mobile/img/NamePlate/b919c327669240b8.png"
)
"""官方デフォルト素色框（NET 收藏品页装备初始框的预览图）。

2026-09-28 账号实测固定哈希、与曲绘同 host 免登录可拉（396×63）；哈希可能
随官方改版轮换——404 时未牌头图退化为缺省跳过，下次启动重试新地址。"""


def _has_local(song_id: int, cache: Path) -> bool:
    # static 封面路径单源 assets.cover_candidates（首环）；缓存路径随调用方
    return (
        assets.cover_candidates(song_id)[0].exists()
        or (cache / f"{song_id}.png").exists()
    )


_MUSIC_IMG_URL = "https://maimaidx.jp/maimai-mobile/img/Music/{image_url}"

_LXNS_JACKET_URL = "https://assets.lxns.net/maimai/jacket/{song_id}.png"
"""落雪资产站曲绘（400×400，按曲目 id 寻址）：国内直连、无官方站 IP 门，
覆盖 otoge 未收录曲；pending 曲无 id 不适用。"""

_OTOGE_JACKET_URL = (
    "https://raw.githubusercontent.com/zvuc/otoge-db/main/maimai/jacket/{image_url}"
)
"""otoge-db 仓库曲绘兜底：190×190 缩图（官方 680×680），哈希名与
song.image_url 一致；GitHub raw 经智能代理可达、标准证书链。"""

_LXNS_TAG, _NET_TAG, _OTOGE_TAG = "lxns", "官方NET", "otoge 兜底"


async def _download_cover(url: str, path: Path, *, subject: str, verify) -> bool:
    return await _COVER_GATE.run(
        (subject, url),
        lambda: download_to_file(url, path, subject=subject, verify=verify),
    )


async def _fetch_cover_chain(
    song_id: int | str, image_url: str, path: Path, *, with_lxns: bool
) -> bool:
    """落雪资产站 →（官方 NET）→ otoge-db 仓库缩图，逐级落同一缓存路径。

    落雪置首：国内直连无 IP 门、400×400 优于 otoge 缩图；官方 680×680
    保持在链上（SEGA 解封即自动回到最优画质）。
    ``with_lxns``：按 id 寻址的落雪源仅主表曲可用（pending 曲无 id）。
    """
    attempts = []
    if with_lxns:
        attempts.append((_LXNS_JACKET_URL.format(song_id=song_id), True, _LXNS_TAG))
    attempts.append(
        (_MUSIC_IMG_URL.format(image_url=image_url), maimaidx_ssl_context(), _NET_TAG)
    )
    attempts.append((_OTOGE_JACKET_URL.format(image_url=image_url), True, _OTOGE_TAG))
    for url, verify, tag in attempts:
        subject = f"jp_cover：{song_id} 曲绘" + (f"（{tag}）" if tag else "")
        if await _download_cover(url, path, subject=subject, verify=verify):
            return True
    return False


async def get_cover_path(song_id: int, cache_dir: Path | None = None) -> Path:
    """曲绘解析**唯一入口**（2026-10-30 定案）：所有需要封面的渲染统一经此。

    static 素材 → 缓存 → 在线链（落雪 → 官方 NET → otoge 仓库）→ 占位 0.png，
    逐级尝试；在线命中即落缓存（后续走缓存环）。落雪层按曲目 id 寻址、**不
    依赖 image_url**——otoge 未收录的 CN 先行曲同样覆盖；NET/otoge 两层需要
    otoge 哈希名，无 image_url 的曲跳过。
    ``cache_dir`` 供测试注入缓存目录，默认取 localstore 的 ``jp_covers``。
    """
    static, cached, placeholder = assets.cover_candidates(song_id)
    if static.exists():
        return static
    if cached.exists():
        return cached
    image_url = await store.song_image_url(song_id)
    if await _fetch_cover_chain(song_id, image_url, cached, with_lxns=True):
        return cached
    return placeholder


async def ensure(song_id: int, cache_dir: Path | None = None) -> bool:
    """确保曲绘本地可得（bool 适配：底图缺失集等批量语义用）。

    本就有图或拉取成功返回 True；无 image_url 的曲恒 False。
    """
    got = await get_cover_path(song_id, cache_dir)
    placeholder = assets.cover_candidates(song_id)[-1]
    return got != placeholder and got.exists()


def has_local(song_id: int, cache: Path | None = None) -> bool:
    """曲绘本地是否可得（static 素材或缓存；sidecar 愈合检查用）。"""
    return _has_local(song_id, cache or jp_cache_dir())


def any_local(song_ids: Iterable[int], cache: Path | None = None) -> bool:
    """任一 id 的曲绘本地可得（sidecar 愈合检查：缺口被他路补齐即重渲）。"""
    return any(_has_local(i, cache or jp_cache_dir()) for i in song_ids)


async def ensure_many(
    song_ids: Iterable[int], *, cache_dir: Path | None = None, parallelism: int = 8
) -> list[int]:
    """批量补齐曲绘缓存（底图内嵌曲绘用；卡片路径逐卡 ensure，底图一版整批）。

    返回**有封面文件名但仍未落盘**的 id（下载失败集，调用方留档供后续重
    试）。快照未提供文件名的曲无法在线补（删除曲在 NET 已无封面索引），
    直接排除且**不参与探针**——曾以首曲探针命中此类曲、本地即刻 False 被
    误判「站点不可达」毒死整批（2026-10-05 服务器实测）。static/缓存已命
    中的 id 零开销跳过；未命中集先以前 :data:`_PROBE_COUNT` 首顺序探针，
    任一成功才信号量限流并发拉取（官方站不可达时整批放弃，否则数百曲 ×
    下载超时是分钟级空等）。``cache_dir`` 供测试注入。
    """
    cache = cache_dir or jp_cache_dir()
    ids = list(dict.fromkeys(song_ids))
    if not ids:
        return []
    url_map = await store.song_image_urls(ids)
    missing = [i for i in ids if not _has_local(i, cache)]
    no_url = [i for i in missing if i not in url_map]
    todo = [i for i in missing if i in url_map]
    if no_url:
        logger.info(
            f"jp_cover：{len(no_url)} 曲快照未提供封面文件名，暂用占位图"
            f"（删除曲或快照未追加，待曲库刷新收敛）：{no_url}"
        )
    if not todo:
        return []
    probe, ok = todo[:_PROBE_COUNT], False
    for sid in probe:
        if await ensure(sid, cache_dir=cache):
            ok = True
    if not ok:
        logger.warning(
            f"jp_cover：探针 {len(probe)} 曲均拉取失败，判定官方站不可达，"
            f"跳过剩余 {len(todo) - len(probe)} 曲"
        )
        return todo
    sem = asyncio.Semaphore(parallelism)

    async def _one(song_id: int) -> None:
        async with sem:
            await ensure(song_id, cache_dir=cache)

    rest = [i for i in todo if not _has_local(i, cache)]
    await asyncio.gather(*(_one(sid) for sid in rest))
    return [i for i in todo if not _has_local(i, cache)]


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
    ok = await _fetch_cover_chain(key, image_url, path, with_lxns=False)
    return path if ok else None


async def ensure_default_plate(cache_dir: Path | None = None) -> Path | None:
    """官方デフォルト素色框落盘缓存（未牌完成表/进度头图占位；启动即拉）。

    固定文件名落盘（:func:`assets.default_plate_path` 单源）；已有缓存不重拉，
    失败返回 None（渲染侧头图回退跳过，下次启动重试）。``cache_dir`` 供测试
    注入，默认取 localstore 收藏品 plate 类目录。
    """
    path = default_plate_path()
    if cache_dir is not None:
        path = cache_dir / path.name
    if path.exists():
        return path
    ok = await _COVER_GATE.run(
        "default_plate",
        lambda: download_to_file(
            _DEFAULT_PLATE_URL,
            path,
            subject="jp_cover：デフォルト素色框",
            verify=maimaidx_ssl_context(),
        ),
    )
    return path if ok else None


async def ensure_asset(url: str, cache_dir: Path | None = None) -> Path | None:
    """NET 官方资料图落盘缓存（B50 卡头像/段位认定/でらっクラス徽章用）。

    官方哈希文件名全局唯一（同名即同图，天然去重），缓存只取 URL 末段
    （防路径成分落到缓存目录外）；与曲绘同 host 同缓存目录；失败返回
    None（渲染走各自回退）。
    """
    name = Path(url).name
    cache = cache_dir or jp_cache_dir()
    path = cache / name
    if path.exists():
        return path
    ok = await _COVER_GATE.run(
        f"asset:{name}",
        lambda: download_to_file(
            url,
            path,
            subject=f"jp_cover：NET 资料图 {name}",
            verify=maimaidx_ssl_context(),
        ),
    )
    return path if ok else None


async def asset_bytes(url: str | None, cache_dir: Path | None = None) -> bytes | None:
    """NET 官方资料图 URL → bytes（落盘缓存；无 URL/下载失败均 None）。"""
    if not url:
        return None
    path = await ensure_asset(url, cache_dir)
    return path.read_bytes() if path is not None else None


async def net_player_assets(player) -> dict:
    """NET 玩家身份素材并发落盘 → B50 系卡面（b50/ap50/pc50）的显式注入参数。

    ``player`` 为窗口缓存里的 :class:`NetPlayer`（缺失/页面改版兜底为 None
    时全 None，卡面走缺省渲染）。返回键与 ``best50_bytes`` 的身份参数一致：
    ``icon_image/course_image/class_image/nameplate_image``。
    """
    keys = ("icon_image", "course_image", "class_image", "nameplate_image")
    if player is None:
        return dict.fromkeys(keys)
    icon, course, klass, plate = await asyncio.gather(
        asset_bytes(player.icon_url),
        asset_bytes(player.course_url),
        asset_bytes(player.class_url),
        asset_bytes(player.nameplate_url),
    )
    return {
        "icon_image": icon,
        "course_image": course,
        "class_image": klass,
        "nameplate_image": plate,
    }
