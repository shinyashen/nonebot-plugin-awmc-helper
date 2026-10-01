"""谱师名义等价类：别名义图（gamerch）＋ curated 归属 ＋ 查询别名 → needle 集。

「谱师查歌 / 随心配 S-10」覆盖一个谱师参与过的全部谱面（本名/马甲/合作串/
合成名义），调研与出处见 local/reference/designer-alias-notes.md：

- **L1 声明别名**：gamerch 譜面製作者一覧三页的结构化声明（``別名義「…」``），
  经 :func:`ext.gamerch.build_alias_graph` 抓取解析，kv 持久化（TTL 复用
  ``awmc_gamerch_max_age``）；本模块只做内存缓存与 best-effort 读取。
- **L3 curated**：wiki 无法自动归属的名义串（谐音合成、花字、变体）→ 成员
  等价类名，硬编码 :data:`CURATED`，每条注释出处（2026-10-01 取证）。
- **L3+ 查询别名**：中文/社区昵称 → 等价类代表名（**纯查询侧**——昵称永不
  出现于 note_designer，只参与词表注册与查询解析），:data:`QUERY_ALIASES`。

匹配原语：查询名 → :func:`build_needles` 等价类 needle 集 →
``any(needle in normalize_text(note_designer))``；needle 整串包含、不拆片段
（片段双关如 たかなっぴー 只能靠 CURATED 整串归属，见笔记 §5.2 引言）。
"""

import re
import json
import time

from . import store
from .ext import gamerch
from ..constants import normalize_text

_KANA_FOLD_RE = re.compile(r"[ぁ-ゖ]")
_KV_TTL_FALLBACK_HOURS = 24
# 进程内别名图缓存（含装入时刻）；负缓存防 gamerch 持续不可达时反复重试
_alias_graph_memo: dict[str, list[str]] | None = None
_alias_graph_at: float = 0.0
_alias_graph_failed_at: float = 0.0


def fold_kana(text: str) -> str:
    """平假名 → 片假名（单射，零歧义）；供 needle/署名双侧统一折叠。

    价值在查询侧（输入「ハッピー」命中 はっぴー）与未来数据的脚本变体；
    对现有未覆盖值无新增归属（笔记 §5.2 引言实测）。
    """
    return _KANA_FOLD_RE.sub(lambda m: chr(ord(m.group()) + 0x60), text)


# ---------------------------------------------------------------------------
# L3 curated：名义串 → 成员等价类代表名（designer-alias-notes.md §5.1–5.3）
# ---------------------------------------------------------------------------
# 置信三档已在笔记定档；此处全部收录（T3 收录无风险——只是查询多命中）。
CURATED: dict[str, tuple[str, ...]] = {
    # ---- T1 确认（wiki 结构化证据：节表/脚注/正文/fanbook） ----
    "Safari": ("サファ太",),  # 534017 サファ太节表
    "JAQ": ("Jack",),  # 534036 Jack 节表（FFT 行）
    "Cement Forest": ("せめんともり",),  # 节表 + Aegisfortia 正文直译推理
    "るしえる": ("Luxizhel",),  # 节表 + BELiZHEL 读み正文
    "僕の檸檬本当上手": ("じゃこレモン",),  # 534017 じゃこレモン节表
    "ずんだポップ": ("メロンポップ",),  # 534017 メロンポップ节表
    "“Carpe diem” ＊ HAN∀BI": ("華火職人",),  # 534036 華火職人节表
    "PANDORA PARADOXXX": ("はっぴー",),  # Re:MASTER 谱名义；脚注+fanbook
    "PANDORA BOXXX": ("サファ太",),  # MASTER 谱名义；脚注+fanbook
    "BLaCK rOSE dIsEASe pATiENT": ("ぴちネコ",),  # fanbook（Ariake MAS）
    "舞舞10年ズ ～ファイナル～": ("チャン＠DP皆伝", "はっぴー"),  # 942456 脚注
    # 脚注 leet 解码「はなびとさふぁた」；∪ 为机器值形态（wiki 作 Ｕ，§5.6）
    "ﾚよ†ょ／∪ヽ”┠ (十,3､了ﾅﾆ": ("華火職人", "サファ太"),
    "譜面ボーイズからの挑戦状": ("玉子豆腐", "LabiLabi", "小鳥遊さん"),  # 脚注
    "Starlight Disco Festa": ("mai-Star",),  # 歌曲页正文直书
    # ---- T2 拼字/谐音无争议或用户裁定 ----
    "Safazhel": ("サファ太", "Luxizhel"),
    "鳩サファzhel": ("鳩ホルダー", "サファ太", "Luxizhel"),
    "鳩ホルぴー": ("鳩ホルダー", "はっぴー"),
    "たかなっぴー": ("小鳥遊さん", "はっぴー"),
    "カマボコホルダー": ("カマボコ君", "鳩ホルダー"),
    "あまくちヘルツ": ("あまくちジンジャー", "シチミヘルツ"),
    "サぴぴぴぴちネファ太太太太コ": ("サファ太", "ぴちネコ"),
    "さふぁた": ("サファ太",),
    "Safata.Hz": ("サファ太", "シチミヘルツ"),  # Hz=シチミヘルツ（用户判读）
    "Safata.GHz": ("サファ太", "シチミヘルツ"),
    "LuxiHertz": ("Luxizhel", "シチミヘルツ"),
    "SHICHIMI☆CAT": ("シチミヘルツ",),  # namu.wiki 佐证单人；☆CAT≠ぴちネコ
    "SAFARi☆CAT": ("サファ太",),
    "safaTAmago": ("サファ太", "玉子豆腐"),
    "7.3連発華火": ("シチミヘルツ", "華火職人"),
    "隅田川華火大会": ("隅田川星人", "華火職人"),
    "緑風 犬童子": ("はっぴー",),  # 緑風 犬三郎家族（R2）
    "譜面男子学院 中堅 小鳥 遊": ("小鳥遊さん",),
    "ものくロシェ": ("ものくろっく", "ロシェ＠ペンギン"),
    "Hz-R.Arrow": ("シチミヘルツ", "Redarrow"),
    "シチミッピー": ("シチミヘルツ", "はっぴー"),  # ッピー=はっぴー（用户裁定）
    "7sRef -DOVE-": ("鳩ホルダー",),  # -DOVE-＝The Dove 花字（用户裁定）
    "project raputa": ("あまくちジンジャー", "佑"),  # 脚注+评论双源（用户裁定）
    "七味星人": ("シチミヘルツ", "隅田川星人"),  # しちみ+星人（用户裁定合作）
    "超七味星人": ("シチミヘルツ", "隅田川星人"),
    # ---- T3 已采纳推断/单源/源冲突 ----
    "R-blacX of JacQ": ("ぴちネコ", "Jack"),  # BlackJack 双关（已采纳）
    "チェシャ猫とハートのジャック": ("ぴちネコ", "Jack"),  # Alice 角色（已采纳）
    "Luxiいぬ": ("Luxizhel", "はっぴー"),  # 犬元素（R2）
    "いぬっくまとボコっくま": ("はっぴー", "カマボコ君"),  # 犬+ボコ（R2/R5）
    "ボコ太": ("カマボコ君", "サファ太"),  # 蟹棒+太（R4/R5+namu.wiki）
    "SΛFΛRI/RΦCHER": ("サファ太", "ロシェ＠ペンギン"),
    "Sukiyaki vs Happy": ("すきやき奉行", "はっぴー"),
    "小鳥遊チミ": ("小鳥遊さん", "シチミヘルツ"),  # チミ＝シチミ（用户判读）
    "しちみりこりす": ("シチミヘルツ", "きょむりん"),
    "ブレーンバスター": ("じゃこレモン",),  # 源冲突（wiki rowspan 归位）
}

# ---------------------------------------------------------------------------
# L3+ 查询别名：昵称 → 等价类代表名（designer-alias-notes.md §9 用户填写）
# ---------------------------------------------------------------------------
QUERY_ALIASES: dict[str, str] = {
    "哈皮": "はっぴー",
    "happy": "はっぴー",
    "狗": "はっぴー",
    "杰克": "Jack",
    "谱面100号": "譜面-100号",
    "谱面一百号": "譜面-100号",
    "100号": "譜面-100号",
    "dp皆传": "チャン＠DP皆伝",
    "某s": "某S氏",
    "企鹅": "ロシェ＠ペンギン",
    "roshe": "ロシェ＠ペンギン",
    "科技厨房": "Techno Kitchen",
    "桃子猫": "ぴちネコ",
    "小鸟游": "小鳥遊さん",
    "鸟": "小鳥遊さん",
    "寿喜烧奉行": "すきやき奉行",
    "寿喜烧": "すきやき奉行",
    "奉行": "すきやき奉行",
    "沙发太": "サファ太",
    "华火职人": "華火職人",
    "7.3Hz": "シチミヘルツ",
    "7.3GHz": "シチミヘルツ",
    "7.3赫兹": "シチミヘルツ",
    "阿玛莉莉丝": "アマリリス",
    "川哥": "隅田川星人",
    "红箭": "Redarrow",
    "翠": "翠楼屋",
    "甜口姜": "あまくちジンジャー",
    "柠檬": "じゃこレモン",
    "lemon": "じゃこレモン",
    "🍋": "じゃこレモン",
    "蟹棒": "カマボコ君",
    "甜瓜": "メロンポップ",
    "鸠": "鳩ホルダー",
    "九鸟": "鳩ホルダー",
    "泸溪河": "Luxizhel",
    "卢溪河": "Luxizhel",
    "卢": "Luxizhel",
    "鲁比": "Ruby",
    "水泥森林": "せめんともり",
    "发光海狸": "発光ビーバー",
    "海狸": "発光ビーバー",
    "maistar": "mai-Star",
}


_WS_RE = re.compile(r"\s+")


def _norm(text: str) -> str:
    """别名/署名统一归一（NFKC+lower+简繁 + 假名折叠 + 空白剥离）。

    空白剥离与多源核对口径一致（designer-alias-notes.md §5.6）——涂鸦名/
    长串名义的空格差异在 wiki 与机器数据间普遍存在，无语义。
    """
    return _WS_RE.sub("", fold_kana(normalize_text(text)))


def resolve_class(name: str) -> "set[str]":
    """查询名（原始串）→ 等价类成员集（归一后形态）。

    解析顺序：查询别名（L3+）→ 别名图主名/别名（L1）→ curated 成员名
    （L3）；自身也入集。未知名返回仅含自身的单元素集（原样包含式）。
    """
    n = _norm(name)
    members = {n}
    rep = QUERY_ALIASES.get(n) or QUERY_ALIASES.get(name)
    if rep:
        members.add(_norm(rep))
    if _alias_graph_memo:
        for main, aliases in _alias_graph_memo.items():
            nm = _norm(main)
            if n == nm or n in (_norm(a) for a in aliases):
                members.add(nm)
                members.update(_norm(a) for a in aliases)
                break
    # curated 成员名也把自身声明别名并入（如 はっぴー 的緑風 犬三郎在 L1 图中）
    return members


def build_needles(
    name: str, alias_graph: "dict[str, list[str]] | None" = None
) -> "set[str]":
    """查询名 → 等价类 needle 集（整串包含式，含 curated 名义串注入）。

    ``alias_graph``：显式传入 L1 图（测试注入）；缺省用
    :func:`get_alias_graph` 的进程内缓存（不触发网络）。
    needle 含片假名折叠变体：署名侧原形/折叠形均可命中。
    """
    if alias_graph is not None:
        use_memo(alias_graph)
    members = resolve_class(name)
    # 反向注入：该名参与的 curated 名义串 W（members ∩ CURATED[W] 非空）
    needles = set(members)
    folded = {fold_kana(m) for m in members}
    needles |= folded
    norm_curated = {
        _norm(w): (w, members_key)
        for w, members_key in ((w, {_norm(m) for m in ms}) for w, ms in CURATED.items())
    }
    for w_norm, (w_raw, w_members) in norm_curated.items():
        if w_members & members:
            needles.add(w_norm)
            needles.add(fold_kana(w_norm))
    return needles


def match(designer: str, needles: "set[str]") -> bool:
    """署名串（原始形态）是否命中任一 needle（原形或假名折叠形）。"""
    if not designer or designer == "-":
        return False
    n = _norm(designer)
    return any(needle in n for needle in needles)


def use_memo(alias_graph: "dict[str, list[str]]") -> None:
    """注入进程内别名图缓存（测试与调用方预热）。"""
    global _alias_graph_memo, _alias_graph_at
    _alias_graph_memo = alias_graph
    _alias_graph_at = time.time()


async def get_alias_graph(
    *, fetch: bool = False, max_age_hours: int | None = None
) -> "dict[str, list[str]] | None":
    """L1 别名图读取：进程内 memo → kv（TTL）→（仅 ``fetch``）gamerch 抓取。

    ``fetch=False``（默认）纯读：不发起网络请求——词表注册（combo 词法
    路径）与谱师匹配不得在闲聊路径上触发抓取（与曲库加载同一哲学）；
    kv 无缓存时返回 None（词表退化为曲库实名 + QUERY_ALIASES）。
    ``fetch=True`` 供曲库刷新路径（songdb 刷新点）顺带抓取，任何失败
    返回 None 不抛异常（gamerch 不可达不影响主流程）；失败负缓存 5 分钟。
    TTL 缺省取 gamerch 配置的 ``awmc_gamerch_max_age``。
    """
    global _alias_graph_memo, _alias_graph_at, _alias_graph_failed_at
    from ..config import plugin_config

    ttl = (
        max_age_hours
        if max_age_hours is not None
        else plugin_config.awmc_gamerch_max_age
    )
    if _alias_graph_memo is not None and time.time() - _alias_graph_at < ttl * 3600:
        return _alias_graph_memo
    if not fetch:
        # 纯读：kv 有 TTL 内缓存则升温 memo，否则放弃（不抓取）
        raw = await store.kv_get(gamerch.ALIAS_GRAPH_KV)
        if raw:
            try:
                payload = json.loads(raw)
            except ValueError:
                return None
            graph = {k: v for k, v in payload.items() if not k.startswith("__")}
            fetched_at = float(payload.get("__fetched_at__", 0))
            if fetched_at and time.time() - fetched_at < ttl * 3600:
                use_memo(graph)
                return graph
        return None
    # 失败负缓存：5 分钟内不重试
    if _alias_graph_failed_at and time.time() - _alias_graph_failed_at < 300:
        return None
    try:
        graph = await gamerch.build_alias_graph(gamerch.get_client(), max_age=ttl)
    except Exception:
        _alias_graph_failed_at = time.time()
        return None
    use_memo(graph)
    return graph
