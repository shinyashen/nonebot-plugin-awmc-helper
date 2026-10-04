"""帮助注册表 → Markdown（docs/commands.md 生成器，M10 后置项）。

注册表（core/help.py + 各子插件声明块）是唯一事实源：docs/commands.md 由
scripts/gen_commands.py 生成本模块的输出，tests/test_gen_commands.py 与文档
逐字节锁死防漂移。本模块只做纯渲染（注册表 → markdown 文本），不加载插件、
不碰文件。

- 动态部分：类别按 order 排序 → ``## 类别`` → 插件分组 ``### 【标题】（目录名）``
  → ``指令 | 说明`` 两列表格。hidden/SUPERUSER 指令行尾标【SUPERUSER】、群管
  行尾标【管理员】（手册图例见文档头），scope 其他值（仅私聊/QQ 平台等）原样
  内联，capability 适用性标注复用 core/help.py 的 ``_source_tag`` 派生（单源）；
  说明列展示 detail（无 detail 回落 brief）——与聊天帮助详情页同文案单源；
- 静态部分（文档头图例、每类尾注、条件组合查询专章）为下方模块常量，内容自
  手工版 commands.md 原样搬运（含换行），不重写；改文案时注意单源位置：
  条件词表权威在 core/combo.py，NET 说明对应 core/ext/net + core/sources。
- 范围（拍板 A）：仅主插件块（``plugin`` 以 ``awmc.`` 开头）；第三方块与
  第三方指南（``source`` 不以 ``awmc.`` 开头）不进主插件手册。
"""

from .help import PluginHelp, CommandSpec, HelpRegistry, _source_tag

# ------------------------------------------------------------ 静态模板段

_MD_PROLOGUE = """\
# 指令手册

> 本文件由 `uv run poe gen-commands` 从帮助注册表生成，请勿手工编辑。

命令前缀遵循 NoneBot 配置 `COMMAND_START`（默认无前缀或 `/` 均可）。
标注【管理员】需群管/群主/SUPERUSER，【SUPERUSER】需机器人超级用户。"""

# 每个类别表格后的行为补充段（手册原样）；键 = 类别 key
_CATEGORY_NOTES: dict[str, str] = {
    "query": "单首出谱面卡，2–5 首文本列表，更多出列表图（25/页）。",
    "alias": "别名申请事件（SSE）会推送到已开启推送的群（合并转发优先）。",
    "score": (
        "> 日服 NET 数据源（`数据源 2`）支持 `b50` / `b40` / `minfo` / `ap50` "
        "与完成表、进度、\n"
        "> 分数列表等全量成绩功能；牌子查询按数据源牌单收口：国服可查至「彩」（PRiSM\n"
        "> PLUS）代、日服可查至「回」（CiRCLE PLUS）代，越界会给出明确提示。\n"
        "> NET 查询采用窗口缓存：一次抓取（约 5-15 秒）后窗口内（默认 15 分钟，\n"
        "> `AWMC_NET_COOLDOWN_MINUTES` 可调）重复查询秒回，防止官方风控。\n"
        "> 水鱼公开查询的凭据优先级：绑定用户名 > QQ 号"
        "（聊天 QQ 与水鱼账号 QQ 不一致时以用户名为准）。"
    ),
    "table": (
        "条件串写法见「条件组合查询」；等级（13/13+）、定数（14.5）、评价值\n"
        "（fc/sss+/fdx…）为条件词的特例形态，触发文本与旧版完全一致。"
    ),
    "arcade": (
        "「舞萌帮助 排卡」查看排卡类别页（不设开关门禁，便于发现开启入口）。\n"
        "\n"
        "每日 4 点自动同步华立官方机厅数据并清零排卡人数。\n"
        "除帮助与开关指令外，排卡指令在群内均受「开启排卡」门禁（对齐 Hoshino 原版\n"
        "`maimaiDX排卡` Service 默认关闭、按群开通），私聊按部署默认值。"
    ),
}

# 条件组合查询专章（手册原样；条件词权威单源在 core/combo.py，
# 指令侧声明见 score_query「条件50」/ tables「<条件>进度|完成表|定数表」）。
# 长行以相邻字面量拼接拆源码行，渲染内容与手工版逐字一致
_COMBO_CHAPTER = "\n".join(
    (
        "## 条件组合查询",
        "",
        "在查分与表格指令的条件串位置写条件词即可组合过滤，条件可任意叠加",
        "（同类「或」、跨类「且」），例：`辉50`、`东方50`、`雪辉dx50`、`紫谱将50`、",
        "`祝将50`、`14+sss+进度`、`东方fc完成表`、`14.5定数`、`dx2024b50`、",
        "`拟合理想50`、`音击50`。`@某人` 代查同普通用法。",
        "",
        "| 条件类 | 条件词 |",
        "|---|---|",
        "| 版本 | 版本字（辉/雪辉/真超檄…多代连写；真含初代、舞=旧作全集） |",
        "| 世代/新旧 | dx、旧框、新版本、旧版本、新歌；回到过去："
        "dx2024/舞萌dx2024/dx无印（新旧分界移到该年，定数取时点值） |",
        "| 分类 | 东方、中二（中二节奏/chunithm）、音击（ongeki）、"
        "音击中二（中二音击，大类）、流行动漫、其他游戏、maimai、"
        "v家/术力口/niconico/ボカロ |",
        "| 谱面 | 紫谱、白谱、绿、黄、红、13级、14+级、14.5定数、"
        "谱师名、宴谱、标准、dx谱 |",
        "| 成绩 | fc/全连/极、fcp、ap/神、理论/ap+/app、舞舞/fdx/fsd、fs、fsp、"
        "将/鸟/sss、大将/鸟加/sss+、纯\\<档\\>/仅\\<档\\>（严格等于）、"
        "霸/clear（≥A）、牛逼（≥100.8）、越级（<95）、一星~五星、寸、"
        "锁/锁血/名刀/血压 |",
        "| 修改 | 理想（升一档重算 RA）、拟合（拟合定数重算，缺失回实际定数） |",
        "",
        "- **条件50**：跨等级条件平铺展示；纯成绩类按 35/15 拆分",
        "- **条件40**：旧系数 b40（25+15）；回到过去恒拆分",
        "- 组合语义：同类「或」（如 牛逼越级=任一命中）、跨类「且」"
        "（如 东方神=东方曲 ∩ AP）",
        "- 等级/定数在 50 尾缀须带「级」「定数」字样（`13级50`）；"
        "进度/完成表/分数列表等中文尾缀裸数字即可（`13+sss+进度`）",
        "- 谱师名直接写实名（如 まぐランド）；未命中任何条件词时静默不响应",
    )
)

# 专章插在哪个类别节之后（手册原位置：查分之后、工具之前）
_COMBO_AFTER_CATEGORY = "score"

_GUIDES_NOTE = "跨插件完整流程发送「舞萌帮助 <指南名>」查看。"

# ---------------------------------------------------------------- 行内渲染


def _visible(spec: CommandSpec, include_hidden: bool) -> bool:
    return include_hidden or not spec.hidden


def _esc(text: str) -> str:
    """表格单元格转义：GFM 管道（代码片段内的 | 也会切列）。"""
    return text.replace("|", "\\|")


def _cell_text(text: str) -> str:
    """单元格正文：管道转义 + 多行压 ``<br>``（表格内保留分行）。"""
    return _esc(text).replace("\n", "<br>")


def _permission_tags(spec: CommandSpec) -> str:
    """行尾权限标注：hidden/SUPERUSER 与群管；与文档头图例对应。"""
    tags = ""
    if spec.hidden or "SUPERUSER" in spec.scope:
        tags += "【SUPERUSER】"
    if "群管" in spec.scope:
        tags += "【管理员】"
    return tags


def _scope_inline(spec: CommandSpec) -> str:
    """非权限类 scope（仅私聊/QQ 平台等）原样内联；权限词转行尾标注。"""
    rest = spec.scope.replace("群管", "").replace("SUPERUSER", "").strip("，、;； ")
    return f"（{rest}）" if rest else ""


def _desc_cell(spec: CommandSpec) -> str:
    """说明列：capability 标注 + scope 内联 + detail（无则 brief）+ 例 + 权限标注。"""
    head = ""
    if note := _source_tag(spec):
        head += f"{note}；"
    head += _scope_inline(spec)
    cell = head + _cell_text(spec.detail or spec.brief)
    if spec.example:
        cell += f"<br>例：{_esc(spec.example)}"
    return cell + _permission_tags(spec)


def _cmd_cell(spec: CommandSpec) -> str:
    """指令列：主名 + 别名（均转义，别名以 / 分隔）。"""
    name = f"`{_esc(spec.name)}`"
    if not spec.aliases:
        return name
    return name + "（" + " / ".join(f"`{_esc(a)}`" for a in spec.aliases) + "）"


def _table(block: PluginHelp, include_hidden: bool) -> str:
    rows = [
        f"| {_cmd_cell(s)} | {_desc_cell(s)} |"
        for s in block.commands
        if _visible(s, include_hidden)
    ]
    if not rows:
        return ""
    return "\n".join(("| 指令 | 说明 |", "|---|---|", *rows))


def _plugin_dir(block: PluginHelp) -> str:
    """awmc.xxx → xxx（第三方块不进手册，无须处理其他前缀）。"""
    return block.plugin.removeprefix("awmc.")


def _guides_section(reg: HelpRegistry) -> str:
    """指南小节：intro/prerequisites + 步骤紧凑形态（步骤按名引用指令）。"""
    parts = ["## 指南", _GUIDES_NOTE]
    for guide in reg.guides.values():
        if not guide.source.startswith("awmc."):
            continue  # 第三方指南不进主插件手册（拍板 A 同口径）
        lines = [guide.intro]
        if guide.prerequisites:
            lines.append(f"前置条件：{guide.prerequisites}")
        for idx, step in enumerate(guide.steps, start=1):
            refs = " / ".join(f"`{_esc(n)}`" for n in step.commands)
            head = f"{idx}. {step.text}".rstrip()
            lines.append(f"{head}{refs}" if refs else head)
        parts.append(f"### {guide.title}\n" + "\n".join(lines))
    return "\n\n".join(parts)


# ---------------------------------------------------------------- 入口


def registry_markdown(reg: HelpRegistry, *, include_hidden: bool = True) -> str:
    """帮助注册表 → docs/commands.md 全文（调用方负责写文件）。"""
    parts: list[str] = [_MD_PROLOGUE]
    for cat in sorted(reg.categories.values(), key=lambda c: c.order):
        blocks = [
            b
            for b in reg.blocks
            if b.category == cat.key and b.plugin.startswith("awmc.")
        ]
        if not blocks:
            continue
        parts.append(f"## {cat.title}")
        for block in blocks:
            parts.append(f"### 【{block.title}】（{_plugin_dir(block)}）")
            if block.description:
                parts.append(block.description)
            if table := _table(block, include_hidden):
                parts.append(table)
        if note := _CATEGORY_NOTES.get(cat.key):
            parts.append(note)
        if cat.key == _COMBO_AFTER_CATEGORY:
            parts.append(_COMBO_CHAPTER)
    if guides := _guides_section(reg):
        parts.append(guides)
    return "\n\n".join(parts) + "\n"
