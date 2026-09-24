#!/usr/bin/env python3
# ruff: noqa: T201 —— 命令行工具，print 即输出
"""wiki 补充源生成器：gamerch 富化的 otoge-db 数据 → 外部源 ``::fill`` 标准 JSON。

**数据来源与置信度（§7.5-C）**：数据取自 [gamerch maimai 攻略wiki]
(https://gamerch.com/maimai/) 的社区编辑内容（人工维护、更新最快，新版本曲目
通常数小时内就有数据），经本仓参考克隆 ``local/repos/otoge-db`` 内的改写版
爬虫（``scripts/maimai/wiki.py``，修复其 DX 谱面判别/结构化找行/缓存）抓取并
充实进 otoge-db 的 ``music-ex.json``；本脚本再做 title join 补上机台内部 id，
产出可被 ``awmc_extra_song_sources`` 消费的标准 JSON。

置信度排序：机台（magical.json, override）> otoge-db（重建基线）> wiki（本源，
**仅补充**）。本源务必以 ``::fill`` 模式配置——wiki 数据人工噪声大，三方对账
（2026-09-25，268 键 wiki 独走均为 wiki 错误）证明其只配填空、不配覆盖。

刷新流程（新版本曲目日/宴轮换日执行）：
  1. ``git -C local/repos/otoge-db pull``（上游当日更新 music-ex.json）；
  2. otoge-db 仓 ``update-wiki-data.py --maimai --noskip``（改写版：磁盘缓存 +
     diffs.txt 增量，只抓新/更新页面，数分钟）；
  3. 本脚本重新生成 wiki.json 并上传服务器 ``extra-sources/``。

无 id 的新曲（maimaiinfo/机台包都还没给 id）无法进入以 id 为主键的规范表，
由 pending 暂存机制兜底（§7.5-A），id 到位后自动归并。

用法：python3 scripts/wiki_to_json.py [music-ex.json] [magical-full.json] [输出.json]
默认输入对应当前开发布局（``local/`` 下），输出 ``local/extra-sources/wiki.json``。
"""

import re
import sys
import json
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DEFAULT_MUSIC_EX = (
    REPO / "local" / "repos" / "otoge-db" / "maimai" / "data" / "music-ex.json"
)
DEFAULT_MACHINE = REPO / "local" / "extra-sources" / "magical-full.json"
DEFAULT_OUT = REPO / "local" / "extra-sources" / "wiki.json"

EXCLUDE_IDS = {"2055"}  # 与机台导出口径一致（ループザルーム：上线即删曲）

_UTAGE_PARTS = ("", "_tap", "_hold", "_slide", "_touch", "_break")

SD_KEYS = ["lev_bas", "lev_adv", "lev_exp", "lev_mas", "lev_remas"]
DX_KEYS = ["dx_lev_bas", "dx_lev_adv", "dx_lev_exp", "dx_lev_mas", "dx_lev_remas"]


def norm_title(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"\s+", "", s)
    return (
        s.replace("~", "~")
        .replace("〜", "~")
        .replace("！", "!")
        .replace("？", "?")
        .replace("：", ":")
        .replace("＆", "&")
        .replace("・", "·")
        .lower()
    )


def strip_prefix(s: str) -> str:
    return re.sub(r"^\[[^\]]+\]", "", s)


def _int(v):
    if v in (None, "", "?", "？", "-", "…"):
        return None
    try:
        return int(str(v).replace(",", ""))
    except ValueError:
        return None


def main():
    music_ex_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_MUSIC_EX
    machine_path = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_MACHINE
    music_ex = json.loads(music_ex_path.read_text(encoding="utf-8"))
    machine = json.loads(machine_path.read_text(encoding="utf-8"))
    out_path = Path(sys.argv[3]) if len(sys.argv) > 3 else DEFAULT_OUT

    # 机台 title 索引（归一化 → 条目列表；宴条目标题自带 [kanji] 前缀，双索引）
    m_by_title: dict[str, list[dict]] = {}
    for song in machine.values():
        m_by_title.setdefault(norm_title(song["title"]), []).append(song)
        stripped = norm_title(strip_prefix(song["title"]))
        if stripped != norm_title(song["title"]):
            m_by_title.setdefault(stripped, []).append(song)

    doc: dict[str, dict] = {}
    skipped = {"no_id": [], "utage_kanji_unknown": []}
    charts_emitted = 0

    for osong in music_ex:
        title = osong["title"]
        kanji = osong.get("kanji")
        base_title = title
        if kanji is not None and title.startswith(f"[{kanji}]"):
            base_title = title[len(kanji) + 2 :].strip()

        key = norm_title(base_title)
        cands = [m for m in m_by_title.get(key, []) if str(m["id"]) not in EXCLUDE_IDS]
        if not cands:
            skipped["no_id"].append(title)
            continue
        msong = cands[0]
        sid = str(msong["id"])

        entry: dict = {"title": msong["title"], "sheets": {}}
        if osong.get("artist"):
            entry["artist"] = osong["artist"]
        if osong.get("bpm"):
            entry["bpm"] = str(osong["bpm"])
        if osong.get("image_url"):
            entry["image_url"] = osong["image_url"]

        sheets: dict[str, dict] = {}
        for kind, keys in (("sd", SD_KEYS), ("dx", DX_KEYS)):
            if not any(k in osong for k in keys):
                continue
            contents = []
            for level_id, key_c in enumerate(keys):
                if key_c not in osong:
                    continue
                content: dict = {"level_id": level_id}
                if osong.get(f"{key_c}_designer"):
                    content["designer"] = osong[f"{key_c}_designer"]
                parts = [
                    osong.get(f"{key_c}_notes_tap"),
                    osong.get(f"{key_c}_notes_hold"),
                    osong.get(f"{key_c}_notes_slide"),
                    osong.get(f"{key_c}_notes_touch") if kind == "dx" else 0,
                    osong.get(f"{key_c}_notes_break"),
                ]
                ints = [_int(v) for v in parts]
                if all(v is not None for v in ints):
                    content["notes"] = ints
                elif any(v is not None for v in ints):
                    continue  # 内訳不全：宁缺毋滥
                iv = osong.get(f"{key_c}_i")
                if iv not in (None, "", "?", "？"):
                    try:
                        content["level"] = [float(iv)]
                    except ValueError:
                        pass
                if len(content) > 1:
                    contents.append(content)
            if contents:
                sheets[kind] = {
                    "version": None,
                    "version_cn": None,
                    "date": None,
                    "contents": contents,
                }

        if kanji is not None and "lev_utage" in osong:
            msheet = msong["sheets"].get("utage") if msong else None
            contents = []
            if msheet is not None:
                # kanji 唯一才映射 level_id；同 kanji 多张（轮换快照）无法从
                # gamerch 获知官方 level_id，跳过等机台包
                known: dict[int, dict] = {
                    c["level_id"]: c
                    for c in msheet["contents"]
                    if c.get("kanji") == kanji
                }
                if len(known) == 1:
                    level_id, mcontent = next(iter(known.items()))
                    content: dict = {"level_id": level_id}
                    if osong.get("lev_utage_designer"):
                        content["designer"] = osong["lev_utage_designer"]
                    if "buddy" in osong:
                        sides = (("left", "notes_left"), ("right", "notes_right"))
                        for side, mkey in sides:
                            if mkey not in mcontent:
                                continue
                            parts = [
                                osong.get(f"lev_utage_{side}_notes{p}")
                                for p in _UTAGE_PARTS
                            ]
                            ints = [_int(v) for v in parts]
                            if all(v is not None for v in ints):
                                content[mkey] = ints[1:]  # 首位是総数，五元组不含
                    else:
                        parts = [osong.get(f"lev_utage_notes{p}") for p in _UTAGE_PARTS]
                        ints = [_int(v) for v in parts]
                        if all(v is not None for v in ints):
                            content["notes"] = ints[1:]  # 首位是総数，五元组不含
                    if osong.get("kanji"):
                        content["kanji"] = osong["kanji"]
                    if osong.get("comment"):
                        content["comment"] = osong["comment"]
                    content["is_buddy"] = "buddy" in osong
                    has_notes = content.get("notes") or content.get("notes_left")
                    if len(content) > 3 or has_notes:
                        contents.append(content)
                else:
                    skipped["utage_kanji_unknown"].append(
                        f"{title}（机台 kanji 命中 {len(known)}）"
                    )
            else:
                skipped["utage_kanji_unknown"].append(f"{title}（机台无宴组）")
            if contents:
                sheets["utage"] = {
                    "version": None,
                    "version_cn": None,
                    "date": None,
                    "contents": contents,
                }

        if not sheets:
            continue
        entry["sheets"] = sheets
        if sid in doc:
            # 同曲多条目（基曲 sd/dx 条目 + 宴谱条目）合并 sheets，不丢弃
            existing = doc[sid]
            for kind, sheet in sheets.items():
                blank = {
                    "version": None,
                    "version_cn": None,
                    "date": None,
                    "contents": [],
                }
                tgt = existing["sheets"].setdefault(kind, blank)
                by_lid = {c["level_id"]: c for c in tgt["contents"]}
                for c in sheet["contents"]:
                    by_lid[c["level_id"]] = {**by_lid.get(c["level_id"], {}), **c}
                tgt["contents"] = [by_lid[k] for k in sorted(by_lid)]
        else:
            doc[sid] = entry
        charts_emitted += sum(len(sh["contents"]) for sh in sheets.values())

    out_path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wiki.json：{len(doc)} 曲 / {charts_emitted} 张谱面（fill）→ {out_path}")
    if skipped["no_id"]:
        print(f"无 id 跳过 {len(skipped['no_id'])}: {skipped['no_id'][:5]}")
    if skipped["utage_kanji_unknown"]:
        print(
            f"宴 kanji 无法定位 {len(skipped['utage_kanji_unknown'])}: "
            f"{skipped['utage_kanji_unknown'][:5]}"
        )


if __name__ == "__main__":
    main()
