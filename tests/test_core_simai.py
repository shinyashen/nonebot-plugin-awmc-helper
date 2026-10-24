"""core/simai 解析器测试。

造数唯一取材来源 = tests/data/snapshots/simai/ 真实谱面快照（落雪
assets2 抓取）；物量期望值 = 水鱼快照实测口径（对拍详情见
local/reference/chart-preview-notes.md §2）。插件模块按项目测试惯例
在用例内 import（nonebot 于 session fixture 初始化）。
"""

from pathlib import Path

import pytest

SNAPSHOTS = Path(__file__).parent / "data" / "snapshots" / "simai"


def _load(song_id: str) -> str:
    return (SNAPSHOTS / f"{song_id}.txt").read_text()


@pytest.fixture
def simai():
    from nonebot_plugin_awmc_helper.core import simai as simai_mod

    return simai_mod


def test_parse_headers(simai):
    song = simai.parse_simai(_load("8"))
    assert song.title == "True Love Song [SD]"
    assert song.music_id == 8
    assert song.bpm == 150.0
    assert song.pv_start == 15.6
    assert sorted(song.difficulties) == [2, 3, 4, 5]


def test_counts_match_mizukibo_snapshot(simai):
    """物量对拍（水鱼快照口径）：SD 4 列 = tap/hold/slide/break。"""
    song = simai.parse_simai(_load("8"))
    master = song.difficulties[5]
    c = master.counts()
    assert [c["tap"], c["hold"], c["slide"], c["break"]] == [263, 14, 19, 6]
    assert not master.warnings


def test_counts_dx_five_columns(simai):
    """DX 5 列 = tap/hold/slide/touch/break（ラグトレイン MASTER）。"""
    song = simai.parse_simai(_load("11355"))
    c = song.difficulties[5].counts()
    # tap 552 = 水鱼 551 + 1（无星链头 tap 的边缘方言残差，见笔记 §2）
    assert [c["tap"], c["hold"], c["slide"], c["touch"], c["break"]] == [
        552,
        84,
        96,
        30,
        11,
    ]
    assert not song.difficulties[5].warnings


def test_counts_utage(simai):
    """宴谱单难度（[蛸]チルノ）：后缀 _2。"""
    song = simai.parse_simai(_load("100199"))
    assert sorted(song.difficulties) == [2]
    c = song.difficulties[2].counts()
    assert [c["tap"], c["hold"], c["slide"], c["touch"], c["break"]] == [
        58,
        217,
        27,
        0,
        7,
    ]
    assert not song.difficulties[2].warnings


def test_timing_first_note_and_chart_end(simai):
    """TH MASTER：(150){1},, = 两小节空拍，首个 note 在 3.2s。"""
    song = simai.parse_simai(_load("8"))
    master = song.difficulties[5]
    assert master.notes[0].time == pytest.approx(3.2)
    assert master.chart_end > master.note_end - 5  # 正文含尾部空拍
    assert master.note_end == pytest.approx(89.6, abs=0.5)


def test_bpm_change_events(simai):
    """中途 (BPM) 记入 bpm_events（秒坐标）。"""
    song = simai.parse_simai(_load("8"))
    events = song.difficulties[5].bpm_events
    assert events[0] == (0.0, 150.0)


def test_hold_shape_and_each(simai):
    """hold 的类型与 each 判定（EXPERT 首行 3h/6h 并发 → each）。"""
    song = simai.parse_simai(_load("8"))
    expert = song.difficulties[4]
    holds = [n for n in expert.notes if isinstance(n, simai.SimaiHold)]
    assert holds, "EXPERT 应含 hold"
    # 首对 hold：3h[4:3]/6h[4:3] = 3/4 小节 @150bpm = 1.2s，同刻并发 → each
    first = holds[0]
    assert first.duration == pytest.approx(1.2, abs=0.02)
    assert first.is_each
    # ADVANCED 首个 hold 为单点（6h 独占一步）→ 非 each
    adv_first = next(
        n for n in song.difficulties[3].notes if isinstance(n, simai.SimaiHold)
    )
    assert not adv_first.is_each


def test_touch_notes_parsed(simai):
    """DX 谱裸 touch（无 T 前缀）与 touch 长条。"""
    song = simai.parse_simai(_load("11001"))
    touches = [n for n in song.difficulties[5].notes if isinstance(n, simai.SimaiTouch)]
    assert touches, "BLACK ROSE MASTER 应含 touch"
    assert {t.sensor for t in touches} <= {"A", "B", "C", "D", "E"}
    assert any(t.duration > 0 for t in touches), "应含 touch 长条（Ch[..]）"


def test_ex_and_break_modifiers(simai):
    """x = EX 修饰、b = 破坏标记透传。"""
    song = simai.parse_simai(_load("11355"))
    taps = [n for n in song.difficulties[5].notes if isinstance(n, simai.SimaiTap)]
    assert any(n.is_ex for n in taps), "ラグトレイン MASTER 含 EX tap"
    slides = [n for n in song.difficulties[5].notes if isinstance(n, simai.SimaiSlide)]
    assert any(n.is_break for n in slides), "含破坏滑"
    assert any(n.shape != "-" for n in slides), "含非直线滑"


def test_unknown_token_surfaces_as_warning(simai):
    """未知 token 不抛异常，记 warning 跳步。"""
    song = simai.parse_simai("&bpm=120\n&inote_5=\n(120){4}1,1,9x,1,\n")
    diff = next(iter(song.difficulties.values()))
    assert diff.warnings, "未知 token 应记 warning"
    assert diff.counts()["tap"] == 3  # 坏步跳过，其余保留
