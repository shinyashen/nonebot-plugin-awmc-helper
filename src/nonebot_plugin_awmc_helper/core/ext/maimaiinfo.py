"""maimaiinfo 直连：日服骨架 + 定数历史（Dale2003/maimaiinfo，static/ 最新文件）。

- ``all_data.json``：键 = 组级机台内部 id（SD=曲id、DX=id+10000、
  宴=100000+level_id*10000+曲id）；
- ``dschange.json``：定数的全版本值序列（含旧框段；2026-10-04 起新版本值
  内联进各曲目历史，顶层 ``__increments__`` 增量段已废——解析器对旧格式
  增量段保持兼容，song-db-design §2.9）；
- 只认 static/ 最新文件（old/、*_1021 快照不读）；
- 宴的 ds/level 尾部垃圾一律不采信（§2.8）。
"""

from . import fetch_github_raw

_fetch_json = fetch_github_raw(
    "https://raw.githubusercontent.com/Dale2003/maimaiinfo/main/static",
    source="maimaiinfo",
)


async def fetch_all_data() -> dict[str, dict]:
    """日服曲库骨架（键=内部 id 字符串）。"""
    return await _fetch_json("all_data.json")


async def fetch_dschange() -> dict:
    """定数历史（新格式全内联；旧格式含 ``__increments__`` 增量段）。"""
    return await _fetch_json("dschange.json")
