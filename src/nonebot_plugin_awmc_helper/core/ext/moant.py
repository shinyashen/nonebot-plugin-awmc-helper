"""第三方谱面镜像兜底（dw.moant.cn:34225，落雪 404 时补谱面文本）。

源为个人维护的 Flask 服务（unionapp 同款接入方式），仅作落雪 assets2
缺失谱面的兜底——落雪仍是第一源，404 时才请求本源；热链单谱面取文本，
绝不批量镜像。simai 方言与落雪一致（2026-11-03 对拍 51 对样本 note 数
100% 一致，调研记录见 ``local/reference/godserver-notes.md`` §5.4）。
仅补谱面文本：落雪缺失的 chart 其音频根 id 全部可从落雪取到，音频/
打击音效不换源。buddy 宴谱本源只有 1P 一张谱，2P 命中不到时由
preview 的后缀定位自然抛错（允许的行为）。

id 规则与落雪一致（「官方谱面 id」）：SD=曲根 id，DX=根 id+10000，
宴=100000+档位*10000+曲根 id（宴按传入的 diff_id 直取）。
"""

from . import ExtError, ext_request
from ...constants import DX_ID_OFFSET

MOANT_BASE = "https://dw.moant.cn:34225"
FALLBACK_FILE = "maidata.txt"


async def fetch_chart_text(root_id: int, *, dx: bool) -> tuple[str, int] | None:
    """按根 id 拉取 simai 谱面文本，返回 (文本, 命中的官方 id)。

    候选 id 与落雪同构：DX 谱面官方 id 恒为根 id+10000；SD 先试根 id、
    再试 +10000 兜底。双探均无谱面返回 None（不抛异常——调用方以落雪
    的 ChartNotAvailableError 维持「无谱面」语义）。
    """
    primary = root_id + (DX_ID_OFFSET if dx else 0)
    text = await _fetch_chart_file(primary)
    if text is not None:
        return text, primary
    fallback = primary + DX_ID_OFFSET if not dx else root_id
    if fallback != primary:
        text = await _fetch_chart_file(fallback)
        if text is not None:
            return text, fallback
    return None


async def fetch_chart_text_by_id(official_id: int) -> str | None:
    """按官方谱面 id 直接拉取（宴谱 diff_id 同构路径）。"""
    return await _fetch_chart_file(official_id)


async def _fetch_chart_file(official_id: int) -> str | None:
    """拉取单个谱面文件；404/占位响应返回 None，其余 HTTP 错误抛 ExtError。"""
    resp = await ext_request(
        "GET",
        f"{MOANT_BASE}/api/chartFile",
        params={"musicId": str(official_id), "file": FALLBACK_FILE},
        name="moant 兜底谱面",
        retries=1,
        timeout=15,
    )
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise ExtError(f"moant 兜底谱面拉取失败（HTTP {resp.status_code}）")
    # 防御性去 BOM（上游实测无 BOM，text/plain 各实现不保证）
    text = resp.text.lstrip("\ufeff")
    # 必须含谱面正文键才算有效，占位头/空壳按无谱面处理
    if "&inote_" not in text:
        return None
    return text
