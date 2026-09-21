"""歌曲库测试样例数据构造（形状对齐 2026-09-21 实测的四源 payload）。"""

from typing import Any

# ---------------------------------------------------------------------------
# maimaiinfo：all_data（日服骨架）+ dschange（定数历史）
# ---------------------------------------------------------------------------


def _info_basic(
    title: str, artist: str = "Artist", genre: str = "舞萌", bpm: str = "150", **kw: Any
) -> dict:
    return {"title": title, "artist": artist, "genre": genre, "bpm": bpm, **kw}


def make_all_data() -> dict[str, dict]:
    """覆盖：SD/DX/宴、定数末值校正、from=未知、increments 新曲、otoge 已下架仍滞留。"""
    return {
        # SD：定数历史有变化点（FESTiVAL 4.0→4.4），all_data 4.5（校正 dschange 旧值）
        "8": {
            "id": "8",
            "title": "Test Song SD",
            "type": "SD",
            "ds": [4.5, 6.0, 9.0, 11.5],
            "level": ["4", "6", "9", "11"],
            "charts": [
                {"notes": [63, 23, 8, 2], "charter": "譜面-100号"},
                {"notes": [85, 27, 6, 4], "charter": "-"},
                {"notes": [110, 56, 9, 2], "charter": "ニャイン"},
                {"notes": [263, 14, 19, 6], "charter": "オレ"},
            ],
            "basic_info": _info_basic("Test Song SD", **{"from": "maimai でらっくす"}),
        },
        # DX：5 谱面（含 Re:MASTER）
        "10021": {
            "id": "10021",
            "title": "Test Song DX",
            "type": "DX",
            "ds": [3.0, 6.5, 9.5, 12.5, 13.8],
            "level": ["3", "6", "9", "12", "13+"],
            "charts": [
                {"notes": [40, 10, 5, 0, 3], "charter": "DX-A"},
                {"notes": [80, 20, 15, 8, 5], "charter": "DX-B"},
                {"notes": [150, 30, 25, 12, 8], "charter": "DX-C"},
                {"notes": [250, 45, 40, 20, 12], "charter": "DX-D"},
                {"notes": [350, 60, 55, 30, 20], "charter": "DX-E"},
            ],
            "basic_info": _info_basic(
                "Test Song DX", **{"from": "maimai でらっくす PLUS"}
            ),
        },
        # 宴：level/ds 尾部垃圾（只信首元素），定数由标级串 12? 推导 12.0
        "100018": {
            "id": "100018",
            "title": "[宴]Test Party",
            "type": "SD",  # 宴条目的 type 字段不可靠（实测 UTAGE 仅 12/1939）
            "ds": [8.4, 11.0, 13.8],
            "level": ["12?", "13.9", "14.6"],
            "charts": [{"notes": [200, 40, 30, 0, 10], "charter": "宴-A"}],
            "basic_info": _info_basic(
                "[宴]Test Party", **{"from": "maimai でらっくす BUDDiES"}
            ),
        },
        # from=未知 且无 dschange：日服版本不可知（version=None）
        "12": {
            "id": "12",
            "title": "Unknown From Song",
            "type": "SD",
            "ds": [5.0, 7.0, 9.5],
            "level": ["5", "7", "9"],
            "charts": [
                {"notes": [70, 20, 10, 3], "charter": "X1"},
                {"notes": [100, 30, 12, 5], "charter": "X2"},
                {"notes": [180, 40, 20, 8], "charter": "X3"},
            ],
            "basic_info": _info_basic("Unknown From Song", **{"from": "未知"}),
        },
        # __increments__ 新曲（CiRCLE PLUS 26500 登场）
        "10777": {
            "id": "10777",
            "title": "Increment Song",
            "type": "DX",
            "ds": [3.0, 6.5, 10.2, 13.0, 14.5],
            "level": ["3", "6", "10", "13", "14"],
            "charts": [
                {"notes": [50, 10, 5, 2, 2], "charter": "I-A"},
                {"notes": [90, 22, 16, 9, 5], "charter": "I-B"},
                {"notes": [160, 33, 26, 13, 9], "charter": "I-C"},
                {"notes": [260, 48, 42, 21, 13], "charter": "I-D"},
                {"notes": [360, 63, 58, 31, 21], "charter": "I-E"},
            ],
            "basic_info": _info_basic(
                "Increment Song", **{"from": "maimai でらっくす CiRCLE PLUS"}
            ),
        },
        # buddy 宴（左右物量来自 otoge-db）
        "110355": {
            "id": "100355",
            "title": "[協]Rotate Together",
            "type": "SD",
            "ds": [13.9],
            "level": ["13+"],
            "charts": [{"notes": [300, 50, 40, 10, 15], "charter": "B-A"}],
            "basic_info": _info_basic(
                "[協]Rotate Together", **{"from": "maimai でらっくす BUDDiES PLUS"}
            ),
        },
        # maimaiinfo 滞留：otoge 已下架（应置 version=None）
        "42": {
            "id": "10042",
            "title": "Stale Song",
            "type": "SD",
            "ds": [10.0, 12.0],
            "level": ["10", "12"],
            "charts": [
                {"notes": [90, 20, 10, 4], "charter": "S1"},
                {"notes": [160, 35, 22, 9], "charter": "S2"},
            ],
            "basic_info": _info_basic(
                "Stale Song", **{"from": "maimai でらっくす CiRCLE"}
            ),
        },
        # 日服限定（国服源完全没有）
        "10555": {
            "id": "10555",
            "title": "JP Only Song",
            "type": "DX",
            "ds": [4.0, 7.2, 10.2, 13.4],
            "level": ["4", "7", "10", "13"],
            "charts": [
                {"notes": [60, 12, 8, 4, 3], "charter": "J-A"},
                {"notes": [110, 25, 18, 10, 6], "charter": "J-B"},
                {"notes": [190, 40, 30, 18, 10], "charter": "J-C"},
                {"notes": [300, 55, 48, 28, 16], "charter": "J-D"},
            ],
            "basic_info": _info_basic(
                "JP Only Song", **{"from": "maimai でらっくす UNiVERSE"}
            ),
        },
    }


def make_dschange() -> dict:
    """主段（key 8 diff0 有变化点且末值旧于 all_data）+ __increments__。"""
    # 实测：dschange 的版本名是英文风格（与 all_data `from` 的日文风格不同）
    dx_versions = [
        "maimai DX",
        "maimai DX PLUS",
        "maimai DX Splash",
        "maimai DX Splash PLUS",
        "maimai DX UNiVERSE",
        "maimai DX UNiVERSE PLUS",
        "maimai DX FESTiVAL",
        "maimai DX FESTiVAL PLUS",
        "maimai DX BUDDiES",
        "maimai DX BUDDiES PLUS",
        "maimai DX PRiSM",
        "maimai DX PRiSM PLUS",
        "maimai DX CiRCLE",
        "maimai DX CiRCLE PLUS",
    ]

    def seq(values: dict[str, float], since: str = "maimai DX") -> dict:
        # 实测：dschange 每谱面的字典自该曲登场版本起记录（§2.9）；
        # 显式版本键的值「自该版本起持续生效」（定数变更不会回退，除非显式再给新键）
        names = dx_versions[dx_versions.index(since) :]
        out = {}
        for name in names:
            value = values.get("__base__")
            for key, val in values.items():
                if key != "__base__" and dx_versions.index(key) <= dx_versions.index(
                    name
                ):
                    value = val
            out[name] = value
        return out

    return {
        "8": {
            "name": "Test Song SD",
            "id": "8",
            "ds": [
                seq(
                    {"__base__": 4.0, "maimai DX FESTiVAL": 4.4}
                ),  # 末值 4.4 ≠ all_data 4.5
                seq({"__base__": 6.0}),
                seq({"__base__": 9.0}),
                seq({"__base__": 11.5}),
            ],
        },
        "10021": {
            "name": "Test Song DX",
            "id": "10021",
            "ds": [
                seq({"__base__": 3.0}, since="maimai DX PLUS"),
                seq({"__base__": 6.5}, since="maimai DX PLUS"),
                seq({"__base__": 9.5}, since="maimai DX PLUS"),
                seq(
                    {"__base__": 12.3, "maimai DX CiRCLE": 12.5},
                    since="maimai DX PLUS",
                ),
                seq({"__base__": 13.8}, since="maimai DX PLUS"),
            ],
        },
        "__increments__": [
            {
                "version": "maimai DX CiRCLE PLUS",
                "label": "CiRCLE PLUS",
                "songs": {
                    "10777": {
                        "name": "Increment Song",
                        "ds": [3.0, 6.5, 10.2, 13.0, 14.5],
                    }
                },
            }
        ],
    }


# ---------------------------------------------------------------------------
# otoge-db：现役 + 下架
# ---------------------------------------------------------------------------


def make_otoge_live() -> list[dict]:
    return [
        {
            "title": "Test Song SD",
            "catcode": "maimai",
            "version": "20000",
            "bpm": "150",
            "image_url": "abc123.png",
            "release": "000000",
            "lev_bas": "4",
            "lev_bas_i": "4.5",
            "date_added": "20120711",
            "date_updated": "000000",
        },
        {
            "title": "Test Song DX",
            "catcode": "maimai",
            "version": "20500",
            "bpm": "150",
            "image_url": "def456.png",
            "release": "230914",  # dx 日期规则：release 优先
            "lev_bas": "3",
            "dx_lev_bas": "3",
            "date_added": "20130911",
            "date_updated": "20230913",
        },
        {
            "title": "[宴]Test Party",
            "catcode": "宴会場",
            "version": "24000",
            "bpm": "150",
            "image_url": "utu789.png",
            "release": "230622",  # 宴日期规则：release（复活日）优先
            "lev_utage": "12?",
            "lev_utage_designer": "宴-A",
            "kanji": "宴",
            "comment": "パーティーだ！",
            "date_added": "20230601",
            "date_updated": "20230622",
        },
        {
            "title": "[協]Rotate Together",
            "catcode": "宴会場",
            "version": "24500",
            "bpm": "150",
            "image_url": "buddy.png",
            "release": "000000",
            "lev_utage": "13+",
            "kanji": "協",
            "comment": "せーの！",
            "buddy": "○",
            "lev_utage_left_notes": "200",
            "lev_utage_left_notes_tap": "150",
            "lev_utage_left_notes_hold": "20",
            "lev_utage_left_notes_slide": "25",
            "lev_utage_left_notes_touch": "0",
            "lev_utage_left_notes_break": "5",
            "lev_utage_right_notes": "180",
            "lev_utage_right_notes_tap": "130",
            "lev_utage_right_notes_hold": "25",
            "lev_utage_right_notes_slide": "20",
            "lev_utage_right_notes_touch": "0",
            "lev_utage_right_notes_break": "5",
            "date_added": "20240913",
            "date_updated": "000000",
        },
        {
            # otoge 独有（无 id）→ song_pending
            "title": "[狂]Otoge Only Uta",
            "catcode": "宴会場",
            "version": "24500",
            "lev_utage": "13?",
            "kanji": "狂",
            "comment": "only in otoge",
            "date_added": "20250101",
            "release": "000000",
        },
        {
            # 同名多义：SD 条目（otoge-db 实测重复 title 仅 2 组之一）
            "title": "Link",
            "catcode": "maimai",
            "version": "10000",
            "lev_bas": "3",
            "date_added": "20120621",
            "release": "000000",
        },
        {
            "title": "Link",
            "catcode": "maimai",
            "version": "20000",
            "dx_lev_bas": "5",
            "date_added": "20190711",
            "release": "000000",
        },
        {
            # 下架记录里有它但仍在现役 = 复活，不算下架
            "title": "[宴]Rotated Out",
            "catcode": "宴会場",
            "version": "24000",
            "lev_utage": "12+",
            "kanji": "宴",
            "date_added": "20240101",
            "release": "260815",
        },
    ]


def make_otoge_deleted() -> list[dict]:
    return [
        {"title": "Stale Song", "deleted_date": "20260901"},  # 现役列表无 → 当前下架
        {
            "title": "[宴]Rotated Out",
            "deleted_date": "20260801",
        },  # 已复活（仍在 live）→ 非下架
        {
            "title": "Test Song SD",
            "deleted_date": "20250101",
        },  # 在下架记录但现役复活 → 非下架
    ]


# ---------------------------------------------------------------------------
# 落雪 song/list + 水鱼 music_data（国服）
# ---------------------------------------------------------------------------


def _cn_diff(
    level: str,
    level_value: float,
    difficulty: int,
    version: int,
    notes: dict,
    designer: str,
) -> dict:
    return {
        "level": level,
        "level_value": level_value,
        "difficulty": difficulty,
        "note_designer": designer,
        "version": version,
        "notes": notes,
    }


def make_lxns(
    with_9001: bool = False,
    drop_ids: set[int] | None = None,
    disable_ids: set[int] | None = None,
) -> dict:
    """落雪列表；``drop_ids`` 模拟国服下架（整曲消失）。"""
    songs: list[dict] = [
        {
            "id": "8",
            "title": "Test Song SD",
            "artist": "Artist",
            "genre": "舞萌",
            "bpm": 150,
            "difficulties": {
                "standard": [
                    # CN 定数 = 日服历史 ≤ 国服当前版本的末值（§5.3）：diff0 → 4.5
                    _cn_diff(
                        "4",
                        4.5,
                        0,
                        20000,
                        {"tap": 63, "hold": 23, "slide": 8, "break": 2},
                        "譜面-100号",
                    ),
                    _cn_diff(
                        "6",
                        6.0,
                        1,
                        20000,
                        {"tap": 85, "hold": 27, "slide": 6, "break": 4},
                        "-",
                    ),
                    _cn_diff(
                        "9",
                        9.0,
                        2,
                        20000,
                        {"tap": 110, "hold": 56, "slide": 9, "break": 2},
                        "ニャイン",
                    ),
                    _cn_diff(
                        "12",
                        11.5,
                        3,
                        20000,
                        {"tap": 263, "hold": 14, "slide": 19, "break": 6},
                        "オレ",
                    ),
                ],
                "dx": [],
                "utage": [],
            },
        },
        {
            "id": "10021",
            "title": "Test Song DX",
            "artist": "Artist",
            "genre": "舞萌",
            "bpm": 150,
            "difficulties": {
                "standard": [],
                "dx": [
                    _cn_diff(
                        "3",
                        3.0,
                        0,
                        20000,
                        {"tap": 40, "hold": 10, "slide": 5, "touch": 0, "break": 3},
                        "DX-A",
                    ),
                    _cn_diff(
                        "6",
                        6.5,
                        1,
                        20000,
                        {"tap": 80, "hold": 20, "slide": 15, "touch": 8, "break": 5},
                        "DX-B",
                    ),
                    _cn_diff(
                        "9+",
                        9.5,
                        2,
                        20000,
                        {"tap": 150, "hold": 30, "slide": 25, "touch": 12, "break": 8},
                        "DX-C",
                    ),
                    # 日服 CiRCLE 变 12.5 未进国服 → CN 为变更前 12.3（§2.11）
                    _cn_diff(
                        "12",
                        12.3,
                        3,
                        20000,
                        {"tap": 250, "hold": 45, "slide": 40, "touch": 20, "break": 12},
                        "DX-D",
                    ),
                    _cn_diff(
                        "14",
                        13.8,
                        4,
                        20000,
                        {"tap": 350, "hold": 60, "slide": 55, "touch": 30, "break": 20},
                        "DX-E",
                    ),
                ],
                "utage": [],
            },
        },
        {
            # 宴（CN 数据齐全：kanji/固定 description 不采用）
            "id": "100018",
            "title": "[宴]Test Party",
            "artist": "Artist",
            "genre": "宴会場",
            "bpm": 150,
            "difficulties": {
                "standard": [],
                "dx": [],
                "utage": [
                    {
                        "level": "12",
                        "level_value": 12.9,
                        "difficulty": 0,
                        "note_designer": "宴-A",
                        "version": 24000,
                        "kanji": "宴",
                        "description": "LET'S PARTY!!",
                        "is_buddy": False,
                        "notes": {
                            "tap": 200,
                            "hold": 40,
                            "slide": 30,
                            "touch": 0,
                            "break": 10,
                        },
                    }
                ],
            },
        },
        {
            # buddy 宴（left/right 物量）
            "id": "110355",
            "title": "[協]Rotate Together",
            "artist": "Artist",
            "genre": "宴会場",
            "bpm": 150,
            "difficulties": {
                "standard": [],
                "dx": [],
                "utage": [
                    {
                        "level": "13+",
                        "level_value": 13.9,
                        "difficulty": 0,
                        "note_designer": "B-A",
                        "version": 24500,
                        "kanji": "協",
                        "description": "DUET",
                        "is_buddy": True,
                        "notes": {
                            "tap": 280,
                            "hold": 45,
                            "slide": 45,
                            "touch": 0,
                            "break": 10,
                            "left": {
                                "tap": 150,
                                "hold": 20,
                                "slide": 25,
                                "touch": 0,
                                "break": 5,
                            },
                            "right": {
                                "tap": 130,
                                "hold": 25,
                                "slide": 20,
                                "touch": 0,
                                "break": 5,
                            },
                        },
                    }
                ],
            },
        },
        {
            # 国服限定（日侧无）
            "id": "9002",
            "title": "CN Only Song",
            "artist": "CN Artist",
            "genre": "舞萌",
            "bpm": 128,
            "difficulties": {
                "standard": [
                    _cn_diff(
                        "7",
                        7.2,
                        0,
                        25000,
                        {"tap": 120, "hold": 30, "slide": 15, "break": 5},
                        "CN-A",
                    ),
                ],
                "dx": [],
                "utage": [],
            },
        },
    ]
    if with_9001:
        songs.append(
            {
                "id": "9001",
                "title": "Brand New Song",
                "artist": "New",
                "genre": "舞萌",
                "bpm": 100,
                "difficulties": {
                    "standard": [
                        _cn_diff(
                            "5",
                            5.5,
                            0,
                            25500,
                            {"tap": 90, "hold": 20, "slide": 10, "break": 4},
                            "N-A",
                        ),
                    ],
                    "dx": [],
                    "utage": [],
                },
            }
        )
    if drop_ids:
        songs = [s for s in songs if int(s["id"]) % 10000 not in drop_ids]
    for s in songs:
        if disable_ids and int(s["id"]) % 10000 in disable_ids:
            s["disabled"] = True  # 落雪对删除/下架曲打标留在列表
    return {"songs": songs}


def make_divingfish(
    with_9001: bool = False, drop_ids: set[int] | None = None
) -> list[dict]:
    """水鱼 music_data（id 为字符串；from 经 divingfish_to_version 映射）。"""

    def entry(
        raw_id: str, title: str, from_name: str, ds: list[float], level: list[str]
    ) -> dict:
        return {
            "id": raw_id,
            "title": title,
            "ds": ds,
            "level": level,
            "basic_info": {
                "title": title,
                "artist": "Artist",
                "genre": "舞萌",
                "bpm": 150,
                "from": from_name,
            },
        }

    data = [
        entry(
            "8",
            "Test Song SD",
            "maimai でらっくす",
            [4.5, 6.0, 9.0, 11.5],
            ["4", "6", "9", "12"],
        ),
        entry(
            "10021",
            "Test Song DX",
            "maimai でらっくす",
            [3.0, 6.5, 9.5, 12.3, 13.8],
            ["3", "6", "9", "12", "13+"],
        ),
        entry("100018", "[宴]Test Party", "maimai でらっくす BUDDiES", [12.9], ["12"]),
        entry(
            "110355",
            "[協]Rotate Together",
            "maimai でらっくす BUDDiES PLUS",
            [13.9],
            ["13+"],
        ),
        entry("9002", "CN Only Song", "maimai でらっくす PRiSM", [7.2], ["7"]),
    ]
    if with_9001:
        data.append(
            entry(
                "9001", "Brand New Song", "maimai でらっくす PRiSM PLUS", [5.5], ["5"]
            )
        )
    if drop_ids:
        data = [d for d in data if int(d["id"]) % 10000 not in drop_ids]
    return data
