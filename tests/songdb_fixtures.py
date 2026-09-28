"""歌曲库测试样例数据构造（真实快照版，2026-09-29 取材）。

全部真实条目裁剪自 ``tests/data/snapshots/``（字段与值未手改，溯源见该目录
meta.json / README.md）；payload 形状对齐 2026-09-21 实测的四源结构。

「当前态」路径（国服当前进度、限曲身份等会随版本推进过期的语义）同样以快照
为准构造；快照无原型的（国服限定曲——当前真实数据里已不存在）以构造条目补位，
并在本文件内以「构造」注明。MAGiCAL 新曲物語はここから的定数揭晓形态取自
MuNET 真实条目（2026-09-29 GetById）。
"""

import copy
import json
from typing import Any
from pathlib import Path
from functools import cache

_SNAP = Path(__file__).parent / "data" / "snapshots"


@cache
def _snapshot(name: str) -> Any:
    return json.loads((_SNAP / name).read_text(encoding="utf-8"))


def _fresh(name: str) -> Any:
    """快照不可变：每次返回独立副本，允许用例就地改写。"""
    return copy.deepcopy(_snapshot(name))


def make_all_data() -> dict[str, dict]:
    """maimaiinfo all_data：覆盖 SD/DX/宴、定数末值校正（11396）、from=未知（12）、
    increments 新曲（10267）、buddy 宴（111355）、同名多义（Link 131/383）、
    双谱曲（ネコ日和。30/10030）、滞留下架（青春コンプレックス 11634）、日服限定
    （10199 チルノ DX）等场景的真实条目。"""
    return _fresh("maimaiinfo_all_data.json")


def make_dschange() -> dict:
    """dschange：8（UNiVERSE PLUS 变更点）/ 239（多段变更、Re:MASTER CiRCLE PLUS
    变更未进国服）/ 11396（all_data 末值校正 dschange 的真实滞后样本）/
    30+10030（PRiSM 变更）+ ``__increments__`` CiRCLE PLUS 段（裁至 10267/854）。"""
    return _fresh("maimaiinfo_dschange.json")


def make_otoge_live() -> list[dict]:
    """otoge-db 现役：12 条真实条目（Link×2 同名多义、[協]ラグトレイン buddy 左右
    物量、物語はここから MAGiCAL 无 id → pending）。"""
    return _fresh("otoge_live.json")


def make_otoge_deleted() -> list[dict]:
    """otoge-db 下架记录：True Love Song（记录在案但已复活 → 非下架）+
    青春コンプレックス三体（2026-08-07 同日下架，buddy 体带左右物量）。"""
    return _fresh("otoge_deleted.json")


# ---------------------------------------------------------------------------
# 国服侧（落雪/水鱼）：真实快照 + 构造条目
# ---------------------------------------------------------------------------

# 构造条目（快照无原型）：国服限定曲。当前真实数据里国服曲目全部能在日服侧找到
# 对应（水鱼 id 空间与日服一致），「国服独有」路径只剩健壮性意义，保留构造覆盖。
_CN_ONLY_LXNS = {
    "id": 9002,
    "title": "（构造）国服限定样例",
    "artist": "构造条目",
    "genre": "舞萌",
    "bpm": 120,
    "difficulties": {
        "standard": [
            {
                "level": "7",
                "level_value": 7.2,
                "difficulty": 0,
                "note_designer": "构造",
                "version": 25500,
                "notes": {"tap": 120, "hold": 30, "slide": 15, "break": 5},
            }
        ],
        "dx": [],
        "utage": [],
    },
}

_CN_ONLY_DF = {
    "id": "9002",
    "title": "（构造）国服限定样例",
    "ds": [7.2],
    "level": ["7"],
    "basic_info": {
        "title": "（构造）国服限定样例",
        "artist": "构造条目",
        "genre": "舞萌",
        "bpm": 120,
        "from": "maimai でらっくす PRiSM PLUS",
    },
}


def make_lxns(
    with_new: bool = False,
    with_utage_new: bool = False,
    drop_ids: set[int] | None = None,
    disable_ids: set[int] | None = None,
) -> dict:
    """落雪 song/list（真实条目）；``with_new`` 追加双源在列的真实非宴新曲
    華の集落、秋のお届け(1301，CN 22005)，``with_utage_new`` 追加真实宴轮换
    新曲 [回]ハム太郎とっとこうた(111113)，``drop_ids``/``disable_ids`` 按
    **落雪 raw id**（宴为 11xxxx 命名空间）模拟整曲消失/打标留列表。"""
    songs = _fresh("lxns_songs.json")["songs"]
    if with_new:
        songs += _fresh("lxns_new_song.json")["songs"]
    if with_utage_new:
        songs += _fresh("lxns_utage_new.json")["songs"]
    songs.append(copy.deepcopy(_CN_ONLY_LXNS))
    if drop_ids:
        songs = [s for s in songs if s["id"] not in drop_ids]
    for s in songs:
        if disable_ids and s["id"] in disable_ids:
            s["disabled"] = True
    return {"songs": songs}


def make_divingfish(
    with_new: bool = False,
    with_utage_new: bool = False,
    drop_ids: set[int] | None = None,
) -> list[dict]:
    """水鱼 music_data（真实条目，id 为日服命名空间字符串）；参数语义同
    :func:`make_lxns`。"""
    data = _fresh("divingfish_music.json")
    if with_new:
        data += _fresh("divingfish_new_song.json")
    if with_utage_new:
        data += _fresh("divingfish_utage_new.json")
    data.append(copy.deepcopy(_CN_ONLY_DF))
    if drop_ids:
        data = [d for d in data if int(d["id"]) not in drop_ids]
    return data


# ---------------------------------------------------------------------------
# song_pending：MAGiCAL 新曲的真实形态（物語はここから，otoge 27000 无 id）
# ---------------------------------------------------------------------------


def make_pending_item(**over: Any) -> dict:
    """真实 MAGiCAL 新曲 otoge 条目（定数未揭形态：无 ``*_i``/物量/谱师）。

    定数揭晓形态按 MuNET 真实条目（2026-09-29 GetById，id 2020）补
    ``dx_lev_*_i``/物量/谱师——见 :func:`make_pending_revealed`。
    """
    base = next(s for s in _fresh("otoge_live.json") if s["title"] == "物語はここから")
    base.update(over)
    return base


def make_pending_revealed(**over: Any) -> dict:
    """定数揭晓形态：MuNET 真实定数（4.0/7.5/10.9/13.5）+ 真实物量/谱师。"""
    item = make_pending_item(
        dx_lev_bas_i="4.0",
        dx_lev_adv_i="7.5",
        dx_lev_exp_i="10.9",
        dx_lev_mas_i="13.5",
        dx_lev_exp_designer="けんけん法師",
        dx_lev_mas_designer="Luxizhel",
        dx_lev_bas_notes_tap="192",
        dx_lev_bas_notes_hold="12",
        dx_lev_bas_notes_slide="4",
        dx_lev_bas_notes_touch="4",
        dx_lev_bas_notes_break="4",
        dx_lev_mas_notes_tap="564",
        dx_lev_mas_notes_hold="65",
        dx_lev_mas_notes_slide="80",
        dx_lev_mas_notes_touch="32",
        dx_lev_mas_notes_break="68",
    )
    item.update(over)
    return item


def make_munet_entry() -> dict:
    """MuNET GetById 真实条目（物語はここから，addVersion 27，含别名与逐谱定数）。"""
    return _fresh("munet_monogatari.json")
