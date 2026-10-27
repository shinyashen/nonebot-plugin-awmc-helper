"""落雪素材站直连（assets2.lxns.net，谱面预览专用，热链公开资源）。

- 谱面：``/maimai/chart/{官方id}.txt``（simai 全曲文本）；
- 音频：``/maimai/music/{id%10000}.mp3``（64kbps 整曲）。

id 规则（2026-10-24 实测，见 ``local/reference/chart-preview-notes.md`` §1）：
谱面文件按**日服官方 id** 组织——CN 根 id 与官方 id 存在 +10000 迁移
（BLACK ROSE 1001 ↔ 11001、ラグトレイン 1355 ↔ 11355），因此 DX 谱面直接取
根 id+10000，SD 谱面先试根 id、404 再试 +10000 兜底；音频按 %10000 取根。
全部无需 token。宿主为落雪的公开素材服务（SEGA 版权内容，热链口径经用户
拍板，见 QUESTIONS.md Q61 结案记录）。
"""

from . import ExtError, ext_request
from ..render.chart_audio import SOUND_NAMES

ASSETS_BASE = "https://assets2.lxns.net"
HIT_SOUND_BASE = "https://maimai.lxns.net"
_DX_ID_OFFSET = 10000


class ChartNotAvailableError(ExtError):
    """素材站无该谱面/音频（404；国服先行曲无谱面、个别老歌缺失）。"""


async def fetch_chart_text(root_id: int, *, dx: bool) -> tuple[str, int]:
    """拉取 simai 谱面文本，返回 (文本, 命中的官方 id)。

    ``root_id`` 为 CN 根 id（lxns 口径）；``dx`` 为谱面类型（DX 谱面官方 id
    恒为根 id+10000，含老歌 DX 版）。SD 侧官方 id 与根 id 相同、个别缺失
    时回退 +10000 兜底（同曲 SD/DX 文件分立）。官方 id 同时供预览页
    ``chart_id`` 参数使用。
    """
    primary = root_id + (_DX_ID_OFFSET if dx else 0)
    text = await _fetch_chart_file(primary)
    if text is not None:
        return text, primary
    fallback = primary + _DX_ID_OFFSET if not dx else root_id
    if fallback != primary:
        text = await _fetch_chart_file(fallback)
        if text is not None:
            return text, fallback
    raise ChartNotAvailableError(
        f"素材站没有该谱面数据（id {root_id} {'DX' if dx else 'SD'}）"
    )


async def fetch_chart_text_by_id(official_id: int) -> str:
    """按官方谱面 id 直接拉取（宴谱 diff_id / 已确认的官方 id）。

    宴谱的谱面文件以 6 位 diff_id 命名（宿主曲 id 的文件是普通谱），
    因此宴谱渲染必须走本函数而不是 ``fetch_chart_text(root_id)``。
    """
    text = await _fetch_chart_file(official_id)
    if text is None:
        raise ChartNotAvailableError(f"素材站没有该谱面数据（官方 id {official_id}）")
    return text


async def _fetch_chart_file(official_id: int) -> str | None:
    resp = await ext_request(
        "GET",
        f"{ASSETS_BASE}/maimai/chart/{official_id}.txt",
        name="落雪谱面数据",
        retries=1,
        timeout=30,
    )
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise ExtError(f"落雪谱面数据拉取失败（HTTP {resp.status_code}）")
    return resp.text


async def fetch_music(root_id: int) -> bytes:
    """拉取整曲 mp3（64kbps 整曲；404 抛 :class:`ChartNotAvailableError`）。"""
    resp = await ext_request(
        "GET",
        f"{ASSETS_BASE}/maimai/music/{root_id % _DX_ID_OFFSET}.mp3",
        name="落雪曲目音频",
        retries=0,
        timeout=120,
    )
    if resp.status_code == 404:
        raise ChartNotAvailableError("素材站没有该曲目的音频")
    if resp.status_code != 200:
        raise ExtError(f"落雪曲目音频拉取失败（HTTP {resp.status_code}）")
    return resp.content


_hit_sounds_cache: dict[str, bytes] | None = None


async def fetch_hit_sounds() -> dict[str, bytes] | None:
    """拉取站内打击音效 wav（11 个，进程内缓存）。

    源为落雪站点静态资源 ``/assets/maimai/chart/{name}.wav``（与谱面预览播放器
    同源采样）；任一文件失败返回 None，渲染端退化为「只有音乐」。
    """
    global _hit_sounds_cache
    if _hit_sounds_cache is not None:
        return _hit_sounds_cache
    sounds: dict[str, bytes] = {}
    for name in SOUND_NAMES:
        try:
            resp = await ext_request(
                "GET",
                f"{HIT_SOUND_BASE}/assets/maimai/chart/{name}.wav",
                name=f"落雪打击音效（{name}）",
                retries=1,
                timeout=30,
            )
        except ExtError:
            return None
        if resp.status_code != 200:
            return None
        sounds[name] = resp.content
    _hit_sounds_cache = sounds
    return sounds
