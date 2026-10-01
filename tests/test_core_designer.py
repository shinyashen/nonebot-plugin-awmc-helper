"""core/designer 谱师名义等价类测试：声明解析、needle 构建、curated 黄金集。

调研与出处：local/reference/designer-alias-notes.md（2026-10-01 定稿）。
声明解析用 gamerch 三页真实快照；黄金集用部署服务器曲库的 distinct 谱师值
（2026-10-01 dump，存 tests/data/designers_golden.json）。
导入放函数内：顶层 import 会在 nonebug 初始化前触发插件包加载（见 conftest）。
"""

import json
from pathlib import Path

import pytest

SNAP = Path(__file__).parent / "data" / "snapshots" / "gamerch"


def norm_of(text: str) -> str:
    """断言归一口径＝模块 _norm（NFKC+lower+简繁+假名折叠+空白剥离）。"""
    from nonebot_plugin_awmc_helper.core import designer

    return designer._norm(text)


@pytest.fixture(scope="module")
def alias_graph():
    """三页快照 → 原始串别名图（主名 → 别名列表，跨页并集）。"""
    import gzip

    from nonebot_plugin_awmc_helper.core.ext.gamerch import parse_alias_graph

    graph: dict[str, list[str]] = {}
    for name in (
        "534017_譜面製作者一覧DX.html.gz",
        "1003533_譜面製作者一覧DX2.html.gz",
        "534036_譜面製作者一覧ST.html.gz",
    ):
        html = gzip.open(SNAP / name, "rt", encoding="utf-8").read()
        for main, aliases in parse_alias_graph(html).items():
            graph.setdefault(main, [])
            for a in aliases:
                if a not in graph[main]:
                    graph[main].append(a)
    return graph


class TestParseAliasGraph:
    def test_h1校验拒绝非一覧页(self):
        from nonebot_plugin_awmc_helper.core.ext.gamerch import parse_alias_graph

        html = "<html><body><h1 class='content-head'>前前前世</h1></body></html>"
        with pytest.raises(ValueError, match="譜面製作者一覧"):
            parse_alias_graph(html)

    def test_声明全集(self, alias_graph):
        """§3.3 全表：12 主名 14 别名（DX/ST 两页重复声明取并集）。"""
        expect = {
            "はっぴー": ["緑風 犬三郎", "原田ひろゆき"],
            "ぴちネコ": ["ロシアンブラック"],
            "小鳥遊さん": ["Phoenix"],
            "ものくろっく": ["一ノ瀬 リズ"],
            "サファ太": ["-ZONE- SaFaRi"],
            "シチミヘルツ": ["7.3Hz", "7.3GHz"],
            "隅田川星人": ["The ALiEN"],
            "翠楼屋": ["翡翠マナ"],
            "あまくちジンジャー": ["EL DiABLO"],
            "鳩ホルダー": ["The Dove"],
            "Luxizhel": ["BELiZHEL"],
            "ミニミライト": ["Twinrook"],
        }
        for main, aliases in expect.items():
            assert alias_graph.get(main) == aliases, main
        # 无声明的节不误产别名（DX 页 28 人中 16 人无声明）
        assert "Jack" not in alias_graph
        assert alias_graph.get("しろいろ") is None
        assert len(alias_graph) == 12
        assert sum(len(v) for v in alias_graph.values()) == 14

    def test_別名義について小节不误判为人(self, alias_graph):
        """别名条目的小节标题（如「緑風 犬三郎」h3/h4）不产生独立人节。"""
        assert "緑風 犬三郎" not in alias_graph
        assert "The ALiEN" not in alias_graph


class TestNeedles:
    def test_等价类_サファ太(self, alias_graph):
        from nonebot_plugin_awmc_helper.core import designer

        needles = designer.build_needles("サファ太", alias_graph)
        norm = norm_of
        for expect in (
            "サファ太",
            "-ZONE- SaFaRi",
            "Safazhel",
            "さふぁた",
            "Safari",
            "サぴぴぴぴちネファ太太太太コ",
            "safaTAmago",
            "Safata.Hz",
            "Safata.GHz",
            "ボコ太",
        ):
            assert norm(expect) in needles, (expect, needles)

    def test_等价类_鳩ホルダー(self, alias_graph):
        from nonebot_plugin_awmc_helper.core import designer

        needles = designer.build_needles("鳩ホルダー", alias_graph)
        assert norm_of("7sRef -DOVE-") in needles
        assert norm_of("The Dove") in needles
        assert norm_of("鳩サファzhel") in needles
        assert norm_of("カマボコホルダー") in needles

    def test_等价类_シチミヘルツ_隅田川星人(self, alias_graph):
        """七味星人⇒シチミヘルツ＋隅田川星人（用户裁定合作，非サファ太系）。"""
        from nonebot_plugin_awmc_helper.core import designer

        for q in ("シチミヘルツ", "隅田川星人"):
            needles = designer.build_needles(q, alias_graph)
            assert norm_of("七味星人") in needles, (q, needles)
            assert norm_of("超七味星人") in needles, (q, needles)

    def test_等价类_はっぴー(self, alias_graph):
        from nonebot_plugin_awmc_helper.core import designer

        needles = designer.build_needles("はっぴー", alias_graph)
        for expect in (
            "緑風 犬三郎",
            "原田ひろゆき",
            "たかなっぴー",
            "鳩ホルぴー",
            "PANDORA PARADOXXX",
            "緑風 犬童子",
            "シチミッピー",
            "いぬっくまとボコっくま",
        ):
            assert norm_of(expect) in needles, (expect, needles)

    def test_查询别名_单字与中文(self, alias_graph):
        from nonebot_plugin_awmc_helper.core import designer

        needles_lux = designer.build_needles("卢", alias_graph)
        assert norm_of("Luxizhel") in needles_lux
        assert norm_of("Safazhel") in needles_lux
        needles_emo = designer.build_needles("🍋", alias_graph)
        assert norm_of("じゃこレモン") in needles_emo

    def test_片假名输入命中平假名本名(self, alias_graph):
        from nonebot_plugin_awmc_helper.core import designer

        needles = designer.build_needles("ハッピー", alias_graph)
        assert norm_of("はっぴー") in needles

    def test_不拆片段(self, alias_graph):
        """needle 整串包含：Safazhel 不产生 'Safa'/'zhel' 片段 needle。"""
        from nonebot_plugin_awmc_helper.core import designer

        needles = designer.build_needles("サファ太", alias_graph)
        assert "safa" not in needles
        assert "zhel" not in needles

    def test_未知名退化为单元素(self):
        from nonebot_plugin_awmc_helper.core import designer

        needles = designer.build_needles("存在しない譜面師", {})
        assert norm_of("存在しない譜面師") in needles


class TestMatch:
    @pytest.fixture
    def needles_safata(self, alias_graph):
        from nonebot_plugin_awmc_helper.core import designer

        return designer.build_needles("サファ太", alias_graph)

    def test_合作串命中(self, needles_safata):
        from nonebot_plugin_awmc_helper.core import designer

        assert designer.match("サファ太 vs じゃこレモン", needles_safata)
        assert designer.match("JacK on Phoenix & -ZONE- SaFaRi", needles_safata)
        assert designer.match("Safazhel", needles_safata)

    def test_花字与全半角(self, needles_safata):
        from nonebot_plugin_awmc_helper.core import designer

        # 涂鸦名（leet 串；∪ 全半角差异已被 NFKC 折平）
        # ∪＝机器值形态（wiki 作 Ｕ，NFKC 不折叠二者；运行时只对机器值匹配）
        assert designer.match("ﾚよ†ょ／∪ヽ”┠  (十,3､了ﾅﾆ", needles_safata)
        # さふぁた 平假名
        assert designer.match("さふぁた", needles_safata)

    def test_非命中与缺省(self, needles_safata):
        from nonebot_plugin_awmc_helper.core import designer

        assert not designer.match("はっぴー", needles_safata)
        assert not designer.match("翠楼屋", needles_safata)
        assert not designer.match("-", needles_safata)
        assert not designer.match("", needles_safata)

    def test_respects_for致敬过匹配接受(self, alias_graph):
        """「はっぴー respects for 某S氏」查某S氏会命中——放置口径接受。"""
        from nonebot_plugin_awmc_helper.core import designer

        needles = designer.build_needles("某S氏", alias_graph)
        assert designer.match("はっぴー respects for 某S氏", needles)


GOLDEN = json.loads(
    (Path(__file__).parent / "data" / "designers_golden.json").read_text("utf-8")
)


class TestGoldenSet:
    """服务器 distinct 谱师 207 值黄金集（调研 §5.5 口径）。

    查询方＝53 位独立谱师（persons，运行时的真实查询词汇；昵称经
    QUERY_ALIASES 折到代表名，等价）。覆盖率＝值被至少一人可达（调研：
    193/207，放置 14 不计）；放置＝不被任何人命中（防误归属）。
    """

    @pytest.fixture(scope="class")
    def all_needles(self, alias_graph):
        from nonebot_plugin_awmc_helper.core import designer

        return {q: designer.build_needles(q, alias_graph) for q in GOLDEN["persons"]}

    def test_覆盖率下限(self, all_needles):
        """/207 被某人可达 ≥193（等价类实名/别名 + curated 注入，§5.5）。"""
        from nonebot_plugin_awmc_helper.core import designer

        covered = [
            v
            for v in GOLDEN["values"]
            if any(designer.match(v, needles) for needles in all_needles.values())
        ]
        # 193（调研名义值）−maimai TEAM/TEAM DX（放置清单算术遗漏）−
        # みんなでマイマイマー（表位置层，已裁定不做）＝190
        assert len(covered) >= 190, f"覆盖 {len(covered)}/207"

    def test_放置名单不被误归属(self, all_needles):
        """§5.4 放置名单（团体/不明/未公开 14 值）不得被任何谱师命中。"""
        from nonebot_plugin_awmc_helper.core import designer

        for v in GOLDEN["placed"]:
            hits = [
                q for q, needles in all_needles.items() if designer.match(v, needles)
            ]
            assert not hits, (v, hits)

    def test_关键锚点命中(self, all_needles):
        """抽样断言：调研结论中的关键归属在真实值上成立。"""
        from nonebot_plugin_awmc_helper.core import designer

        def hits(value: str) -> list[str]:
            return [
                q
                for q, needles in all_needles.items()
                if designer.match(value, needles)
            ]

        # Cement Forest ⇒ せめんともり（T1）
        assert hits("Cement Forest") == ["せめんともり"]
        # Safazhel ⇒ サファ太 + Luxizhel
        assert {"サファ太", "Luxizhel"} <= set(hits("Safazhel"))
        # Safari ⇒ サファ太（唯一）
        assert hits("Safari") == ["サファ太"]
        # 緑風 犬三郎／原田ひろゆき ⇒ はっぴー
        assert hits("緑風 犬三郎") == ["はっぴー"]
        assert hits("原田ひろゆき") == ["はっぴー"]
        # 翡翠マナ ⇒ 翠楼屋
        assert hits("翡翠マナ") == ["翠楼屋"]
        # 七味星人 ⇒ シチミヘルツ + 隅田川星人（用户裁定合作）
        assert set(hits("七味星人")) == {"シチミヘルツ", "隅田川星人"}
        # ボコ太 ⇒ カマボコ君 + サファ太（R4/R5）
        assert set(hits("ボコ太")) == {"カマボコ君", "サファ太"}
        # JAQ ⇒ Jack；るしえる ⇒ Luxizhel
        assert hits("JAQ") == ["Jack"]
        assert hits("るしえる") == ["Luxizhel"]
        # 7.3GHz ⇒ シチミヘルツ（声明别名实名值）
        assert hits("7.3GHz") == ["シチミヘルツ"]
