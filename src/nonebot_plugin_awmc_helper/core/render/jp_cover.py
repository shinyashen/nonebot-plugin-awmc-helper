"""日服封面在线获取：maimaidx.jp 官方曲绘 + localstore 缓存（不动 static 素材目录）。

- 触发时机：日服谱面卡渲染前调用 :func:`ensure`；static 素材与缓存都没有时才拉取；
- URL 契约（01 文档）：``https://maimaidx.jp/maimai-mobile/img/Music/{image_url}``，
  ``image_url`` 为规范表留档的 otoge-db 官方文件名（``song`` 表）；
- maimaidx.jp 在日本官方侧，走智能代理层自动代理优先（内置国外站清单）；
- 官方站点 TLS 只下发叶子证书（缺 GlobalSign 中间件），httpx 严格校验会失败：
  :func:`_ssl_context` 把中间证书并入信任上下文（CA 名含年份，官方轮换后需更新）；
- 拉取失败静默返回 False，渲染维持 0.png 占位行为；并发请求同曲合并为单次下载。
"""

import ssl
from pathlib import Path

from .. import store
from .assets import assets, jp_cache_dir
from .download import DownloadGate, download_to_file

# GlobalSign GCC R46 OV TLS CA 2025 中间证书（AIA: secure.globalsign.com/cacert/
# gsgccr46ovtlsca2025.crt；有效期至 2029-06，链向系统内置的 GlobalSign Root R46）
_INTERMEDIATE_PEM = """\
-----BEGIN CERTIFICATE-----
MIIFfDCCA2SgAwIBAgIRAIRDWJCDb2c5QYLLnJpdyZ8wDQYJKoZIhvcNAQELBQAw
RjELMAkGA1UEBhMCQkUxGTAXBgNVBAoTEEdsb2JhbFNpZ24gbnYtc2ExHDAaBgNV
BAMTE0dsb2JhbFNpZ24gUm9vdCBSNDYwHhcNMjUwOTE3MDI1NTU2WhcNMjkwNjIz
MDAwMDAwWjBUMQswCQYDVQQGEwJCRTEZMBcGA1UEChMQR2xvYmFsU2lnbiBudi1z
YTEqMCgGA1UEAxMhR2xvYmFsU2lnbiBHQ0MgUjQ2IE9WIFRMUyBDQSAyMDI1MIIB
IjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA1JyrGiv+210Lw4LTp9qxx9WC
o6w8HnxcTKr5XwR6WwtKidGXriLqGXtBINGTi4HUZ1Vl3FUIvscLwNcq2DRLwjWs
cYFNClVnuSw4CtwAcfa7Iltz+0FmFeh/KOWv5BfgCxAo9FaeXRG725b2eedo/7fb
0zBc6M/XcfQREVteZ6GovnLE96+T8RzRImvX38Y8vZoulp/XWv3p09C1pgp/53+1
itDl7xbrM4sglGNkeJ5LBN2dOR1sqWCMZ/V4a4cPQwopBtZis1vVh7/k4S6Ysgk0
CTi5vei0RSEIhxoFk48BHSXzTA4FJxqjfauYCZ4M5tmZ/R5VgXOZ4Ck/PifnXQID
AQABo4IBVTCCAVEwDgYDVR0PAQH/BAQDAgGGMBMGA1UdJQQMMAoGCCsGAQUFBwMB
MBIGA1UdEwEB/wQIMAYBAf8CAQAwHQYDVR0OBBYEFGl0Pq/DWwGVSe4UQVqT+rEw
mNqiMB8GA1UdIwQYMBaAFANcq3OBh6jMsKbVlOI2lkn/BZksMHsGCCsGAQUFBwEB
BG8wbTAuBggrBgEFBQcwAYYiaHR0cDovL29jc3AuZ2xvYmFsc2lnbi5jb20vcm9v
dHI0NjA7BggrBgEFBQcwAoYvaHR0cDovL3NlY3VyZS5nbG9iYWxzaWduLmNvbS9j
YWNlcnQvcm9vdHI0Ni5jcnQwNgYDVR0fBC8wLTAroCmgJ4YlaHR0cDovL2NybC5n
bG9iYWxzaWduLmNvbS9yb290cjQ2LmNybDAhBgNVHSAEGjAYMAgGBmeBDAECAjAM
BgorBgEEAaAyCgECMA0GCSqGSIb3DQEBCwUAA4ICAQBEUTiKxe5jEintARUvLBm9
qWZtGiOSV9E+3bntbFFBDBAroqwB6Cj53Zp/W08HwgxaPXdkVaRNYHB/eAatEtSm
1ldtoorfPc+mVlzbwCwfbpIs2uqW5rF78ne37qy2o+iVnJptq9AzPnlC03+zhhB9
JwmjUXVtPuqQZ96tFl0fAT77xGSLzCO8yfEDrxCqdWz2wneShSbCCsC15JB07OgO
StE+MsVBkwe5+PNzAlAr8NZ6f8mzeY/FzaBzlhYw5+c1yyzXJqp+gjRXWrLpD3Ho
hGOvIXIvCBnyVrYI/HPe6DR5w7oteui9Rt0xfUUudaTkt0iz7fc23eGboZ+bpvgT
gbd/kYK6JOrxawMyfBYxrR5zDHIJX0Mws99DNgACKBUfFadKAfwFw0+0airY5WAI
Xs8yhCb5XGwyzVpcB30BrQbWtqdI0PoE9usNvNbH3YFGfuS8oRmAJEgUUQnwOoGK
jMWtHacw0n8QESdRM274LJvLd9nwawYU4svJpf06FtKPqGH3nXefL741NO9KzDAG
PM11YScyJVfYdBDXFM86HU1fBGTKlkLcG/qMJxOqppY4wydRI3koSH6A78nO2QaJ
yqjTOQyCNHaSlmGjdiOvhJ8y1PiazHnuvWBx6z+7JJF2ukqqfjlSARwyfkfnRUIY
la7ZYEqcc56eoPAiElhvrg==
-----END CERTIFICATE-----
"""

_COVER_GATE = DownloadGate()
"""曲绘下载去重（同曲/同键并发合并为单次下载）。"""


def _ssl_context() -> ssl.SSLContext:
    """系统 CA + GlobalSign 中间证书的 TLS 上下文（补齐官方缺失的链）。"""
    ctx = ssl.create_default_context()
    ctx.load_verify_locations(cadata=_INTERMEDIATE_PEM)
    return ctx


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
            url, path, subject=f"jp_cover：{song_id} 曲绘", verify=_ssl_context()
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
            url, path, subject=f"jp_cover：{key} 曲绘", verify=_ssl_context()
        ),
    )
    return path if ok else None
