"""指令帮助注册表（M10）：全插件指令帮助的单源数据与页面装配。

设计要点（DEVELOPMENT_PLAN §8 M10，2026-09-29 定稿）：

- **注册键 = matcher 对象**，声明块写在各子插件 ``matchers.py`` 末尾（对象在
  作用域内），文案与 matcher 定义永不漂移；
- 三级树「类别 → 插件 → 指令」：类别表由主插件定义，第三方声明未知类别键时
  自动创建（并列展示，来源由树的插件层体现）；
- **指南（guide）**：跨插件流程一等实体（如「导分」「查分上手」），步骤按名
  引用已注册指令、不复制文案；渲染容忍缺失（指令所属插件被停用 → 照常渲染并
  标注不可用），反向索引（指令详情页回链所属指南）渲染时现算；
- 消歧顺序：**指令名 > 类别名 > 指南名**；
- 本模块只做纯数据与装配（``page_entries``/``page_text``），发送与降级链
  （转发 → 整图 → 纯文本）留在调用方 matcher 侧——降级要用 ``finish`` 走
  事件上下文（at_sender），core 不假设发送场景。

第三方接入：``require("nonebot_plugin_awmc_helper")`` 后
``from nonebot_plugin_awmc_helper.core.help import help_registry`` 声明自己的
插件块与指南（指引见 docs/subplugin-dev-guide.md）。
"""

from pathlib import Path
from dataclasses import field, dataclass

from nonebot import logger
from nonebot.matcher import Matcher
from nonebot_plugin_alconna.uniseg import UniMessage

# ---------------------------------------------------------------- 数据模型


@dataclass
class CommandSpec:
    """单条指令的帮助声明（声明处直接引用 matcher 对象作键）。"""

    matcher: Matcher
    name: str
    aliases: tuple[str, ...] = ()
    # 一句话简介：类别页/总览用；空则详情页也不展示该行
    brief: str = ""
    # 详细用法：详情页正文（多行纯文本）
    detail: str = ""
    example: str = ""
    # 适用场景标注（如「仅私聊」「群管」）；空 = 无限制
    scope: str = ""
    # True = 不进普通帮助列表（超管/内部指令）；SUPERUSER 查询时经「管理」
    # 节点可见，按名查询仍可命中
    hidden: bool = False


@dataclass
class GuideStep:
    """指南步骤：文案 + 按名引用指令；附图为独立纯图节点（可与文案同步骤）。"""

    text: str = ""
    commands: tuple[str, ...] = ()
    image: Path | None = None


@dataclass
class Guide:
    """指南（跨插件流程）：步骤按名字引用指令，文案单源。"""

    key: str
    title: str
    aliases: tuple[str, ...] = ()
    # 概览（本指南覆盖的指令清单/能做什么）与前置条件；均为必填引导——
    # 防止第三方注册出空壳流程
    intro: str = ""
    prerequisites: str = ""
    steps: list[GuideStep] = field(default_factory=list)
    source: str = ""  # 注册方插件名（展示来源）


@dataclass
class PluginHelp:
    """子插件声明块：一个子插件一次 ``declare`` 集中声明（拍板项⑤）。"""

    plugin: str  # PluginMetadata.name（awmc.xxx / 第三方自取名）
    title: str  # 展示名
    category: str  # 类别 key（未知键自动创建）
    description: str = ""
    commands: list[CommandSpec] = field(default_factory=list)


@dataclass
class Category:
    key: str
    title: str
    order: int = 100
    builtin: bool = True  # 主插件定义 vs 第三方自动创建


# -------------------------------------------------------- 内置类别与指南

# 内置类别表：key/标题/序（对齐原 HELP_TEXT 分组次序）。第三方勿占用这些 key。
BUILTIN_CATEGORIES: tuple[Category, ...] = (
    Category("basic", "基础", 5),
    Category("bind", "绑定/设置", 10),
    Category("query", "查歌", 20),
    Category("alias", "别名", 30),
    Category("score", "查分", 40),
    Category("tools", "工具", 50),
    Category("table", "表格", 60),
    Category("fun", "娱乐", 70),
    Category("arcade", "排卡", 80),
    # 超管/内部指令收纳（仅 SUPERUSER 可见，不进普通总览）
    Category("manage", "管理", 90),
)


def _builtin_guide_score() -> Guide:
    """内建指南「查分上手」：跨 bind/score_query 两个子插件的完整流程。"""
    return Guide(
        key="查分上手",
        title="查分上手",
        aliases=("查分指南",),
        intro=(
            "从零开始绑定数据站并查看自己的成绩。"
            "按顺序完成前三步即可使用 b50、minfo 等全部查分指令。"
        ),
        prerequisites=(
            "需要一个水鱼或落雪账号（至少其一）。"
            "全程发给 bot 即可，绑定类指令大多仅限私聊。"
        ),
        steps=[
            GuideStep(
                text="绑定水鱼（二选一，推荐 OAuth 授权）：",
                commands=("绑定水鱼", "绑定水鱼token"),
            ),
            GuideStep(
                text="绑定落雪（可选，推荐两个都绑以互通数据）：",
                commands=("绑定落雪",),
            ),
            GuideStep(
                text="确认绑定状态、选定默认数据源后即可查分：",
                commands=("我的绑定", "数据源", "b50"),
            ),
            GuideStep(
                text="进阶：单曲成绩与全局信息——",
                commands=("minfo", "ginfo"),
            ),
        ],
        source="awmc.base",
    )


# ---------------------------------------------------------------- 注册表


class HelpRegistry:
    """帮助注册表单例。子插件 import 时声明，渲染时查询。"""

    def __init__(self) -> None:
        self.categories: dict[str, Category] = {c.key: c for c in BUILTIN_CATEGORIES}
        # 声明块按 (plugin, category) 存：同一插件可就近入多个类别
        # （拍板⑦ 规则化双支持），每块独立整替
        self.blocks: list[PluginHelp] = []
        self._block_index: dict[tuple[str, str], PluginHelp] = {}
        self.guides: dict[str, Guide] = {}
        self._commands: dict[str, CommandSpec] = {}  # matcher 对象 → 声明
        self._name_index: dict[str, CommandSpec] = {}  # 名称/别名 → 声明
        self._guide_alias: dict[str, str] = {}  # 展示名/别名 → guide key

    # ---- 声明 API（子插件 matchers.py 末尾调用） ----

    def declare(
        self,
        *,
        plugin: str,
        title: str,
        category: str,
        description: str = "",
        commands: list[CommandSpec] | tuple[CommandSpec, ...] = (),
    ) -> None:
        """注册一个子插件的帮助块。

        同一插件可多次调用（不同 category 各成一块，就近入多类）；同
        ``(plugin, category)`` 重复声明（插件重载/测试）整块替换。
        """
        if category not in self.categories:
            order = 100 + len(self.categories)
            self.categories[category] = Category(
                category, category, order, builtin=False
            )
            logger.info(f"帮助注册表：第三方新增类别「{category}」")
        block = PluginHelp(
            plugin=plugin,
            title=title,
            category=category,
            description=description,
            commands=list(commands),
        )
        key = (plugin, category)
        if key in self._block_index:
            idx = self.blocks.index(self._block_index[key])
            self.blocks[idx] = block
        else:
            self.blocks.append(block)
        self._block_index[key] = block
        for spec in block.commands:
            self._commands[id(spec.matcher)] = spec
            for key in (spec.name, *spec.aliases):
                existed = self._name_index.get(key)
                if existed is not None and existed is not spec:
                    logger.warning(
                        f"帮助注册表：指令名「{key}」冲突"
                        f"（{existed.name} @ {self._plugin_of(existed)} 与 "
                        f"{spec.name} @ {plugin}），保留先注册者"
                    )
                    continue
                self._name_index[key] = spec

    def declare_guide(self, guide: Guide) -> None:
        """注册一条指南；key/别名建索引，重名按先注册保留。

        必填引导校验：无步骤、或 intro 与 prerequisites 双空 = 空壳流程，
        直接拒收（原创设计无先例，注册面收紧防第三方糊弄）。
        """
        if not guide.steps or (not guide.intro and not guide.prerequisites):
            raise ValueError(
                f"指南「{guide.key}」缺少 intro/prerequisites 或 steps（防空壳流程）"
            )
        if guide.key in self.guides:
            logger.warning(f"帮助注册表：指南「{guide.key}」重复声明，保留先注册者")
            return
        self.guides[guide.key] = guide
        for key in (guide.key, *guide.aliases):
            existed = self._guide_alias.get(key)
            if existed is not None and existed != guide.key:
                logger.warning(f"帮助注册表：指南别名「{key}」冲突，保留先注册者")
                continue
            self._guide_alias[key] = guide.key

    def load_builtin_guide(self) -> None:
        """装载内建指南（base 装配时调用一次；幂等）。"""
        if "查分上手" not in self.guides:
            self.declare_guide(_builtin_guide_score())

    # ---- 查询 API ----

    def spec_of(self, matcher: Matcher) -> CommandSpec | None:
        return self._commands.get(id(matcher))

    def _plugin_of(self, spec: CommandSpec) -> str:
        for block in self.blocks:
            if any(s is spec for s in block.commands):
                return block.plugin
        return "?"

    def guides_for(self, command_name: str) -> list[tuple[Guide, int]]:
        """反向索引：指令名出现在哪些指南的第几步（渲染时现算，容忍删改）。"""
        hits: list[tuple[Guide, int]] = []
        for guide in self.guides.values():
            for idx, step in enumerate(guide.steps, start=1):
                if command_name in step.commands:
                    hits.append((guide, idx))
        return hits

    def lookup_command(self, name: str) -> CommandSpec | None:
        """按名称/别名查指令声明（指南步骤引用解析用）。"""
        return self._name_index.get(name)

    def resolve(self, query: str, *, include_hidden: bool = False) -> "Page":
        """按「指令 > 类别 > 指南」消歧；query 为空 = 总览。

        ``include_hidden``：SUPERUSER 视角。hidden 指令对普通用户按未命中
        处理（不进列表也不可按名查），对超管全部可见。
        """
        query = query.strip()
        if not query:
            return OverviewPage(include_hidden=include_hidden)
        spec = self._name_index.get(query)
        if spec is not None and (include_hidden or not spec.hidden):
            return CommandPage(spec=spec)
        # 类别（key 或别名：标题同键；大小写不敏感仅对 ASCII 名义）
        for cat in self.categories.values():
            if query == cat.key or query == cat.title:
                return CategoryPage(category=cat, include_hidden=include_hidden)
        # 指南
        guide_key = self._guide_alias.get(query)
        if guide_key is not None:
            return GuidePage(guide=self.guides[guide_key])
        return NotFoundPage(query=query)


# ---------------------------------------------------------------- 页面模型


@dataclass
class OverviewPage:
    include_hidden: bool = False


@dataclass
class CategoryPage:
    category: Category
    include_hidden: bool = False


@dataclass
class CommandPage:
    spec: CommandSpec


@dataclass
class GuidePage:
    guide: Guide


@dataclass
class ManagePage:
    """超管专属：全部 hidden 指令清单。"""


@dataclass
class NotFoundPage:
    query: str


Page = OverviewPage | CategoryPage | CommandPage | GuidePage | ManagePage | NotFoundPage


# ---------------------------------------------------------------- 页面装配

# 总览/类别页的行前缀（纯文本节点，可复制可搜索）
_LINE_PREFIX = "·"


def _names(spec: CommandSpec) -> str:
    return "/".join((spec.name, *spec.aliases))


def _command_line(spec: CommandSpec) -> str:
    tags = f"（{spec.scope}）" if spec.scope else ""
    body = f"{_names(spec)}{tags}"
    return (
        f"{_LINE_PREFIX} {body} —— {spec.brief}"
        if spec.brief
        else f"{_LINE_PREFIX} {body}"
    )


def _visible(spec: CommandSpec, include_hidden: bool) -> bool:
    return include_hidden or not spec.hidden


def _overview_blocks(reg: HelpRegistry, include_hidden: bool) -> list[str]:
    blocks = ["舞萌DX 插件指令总览\n发送「舞萌帮助 <条目>」查看类别/指令/指南详情"]
    for cat in sorted(reg.categories.values(), key=lambda c: c.order):
        if cat.key == "manage":
            continue
        lines: list[str] = []
        for block in reg.blocks:
            if block.category != cat.key:
                continue
            visible = [s for s in block.commands if _visible(s, include_hidden)]
            if visible:
                lines.append(f"【{block.title}】")
                lines.extend(_command_line(s) for s in visible)
        if lines:
            blocks.append(f"━━ {cat.title} ━\n" + "\n".join(lines))
    if reg.guides:
        lines = [
            f"{_LINE_PREFIX} {g.title} —— {g.intro.splitlines()[0]}（舞萌帮助 {g.key}）"
            for g in reg.guides.values()
        ]
        blocks.append("━━ 指南 ━\n" + "\n".join(lines))
    if include_hidden:
        manage = _manage_blocks(reg)
        if manage:
            blocks.append(manage)
    return blocks


def _category_blocks(
    reg: HelpRegistry, cat: Category, include_hidden: bool
) -> list[str]:
    blocks: list[str] = []
    for block in reg.blocks:
        if block.category != cat.key:
            continue
        visible = [s for s in block.commands if _visible(s, include_hidden)]
        if not visible:
            continue
        lines = [f"【{block.title}】"]
        if block.description:
            lines.append(block.description)
        lines.extend(_command_line(s) for s in visible)
        blocks.append("\n".join(lines))
    header = f"━━ {cat.title} ━"
    if not blocks:
        blocks = ["（该类别暂无可见指令；发送「舞萌帮助」查看总览）"]
    return [header, *blocks]


def _command_blocks(reg: HelpRegistry, spec: CommandSpec) -> list[str]:
    lines = [_names(spec)]
    if spec.scope:
        lines.append(f"适用：{spec.scope}")
    if spec.detail:
        lines.append(spec.detail)
    if spec.example:
        lines.append(f"例：{spec.example}")
    if backlinks := reg.guides_for(spec.name):
        refs = "、".join(f"{g.title}（第 {idx} 步）" for g, idx in backlinks)
        lines.append(
            f"属于指南：{refs} —— 发送「舞萌帮助 {backlinks[0][0].key}」查看完整流程"
        )
    return ["\n".join(lines)]


def _guide_entries(reg: HelpRegistry, guide: Guide) -> "list[str | UniMessage]":
    """指南页：头节点（简介+前置）+ 一步一节点；附图步骤为独立纯图节点。"""
    header = f"📖 指南：{guide.title}\n{guide.intro}"
    if guide.prerequisites:
        header += f"\n\n前置条件：{guide.prerequisites}"
    if guide.source and guide.source != "awmc.base":
        header += f"\n（来自 {guide.source}）"
    entries: "list[str | UniMessage]" = [header]
    for idx, step in enumerate(guide.steps, start=1):
        if step.text or step.commands:
            lines = [f"第 {idx} 步：{step.text}".rstrip()]
            for name in step.commands:
                spec = reg.lookup_command(name)
                if spec is None:
                    lines.append(f"{_LINE_PREFIX} {name}（当前不可用：未启用或未声明）")
                else:
                    tags = f"（{spec.scope}）" if spec.scope else ""
                    body = (
                        f"{_names(spec)}{tags} —— {spec.brief}"
                        if spec.brief
                        else f"{_names(spec)}{tags}"
                    )
                    lines.append(f"{_LINE_PREFIX} {body}")
            entries.append("\n".join(lines))
        if step.image is not None:  # 纯图节点（可与文案同步骤，core.forward 惯例）
            entries.append(UniMessage.image(path=step.image))
    return entries


def _manage_blocks(reg: HelpRegistry) -> "str | None":
    lines: list[str] = []
    for block in reg.blocks:
        hidden = [s for s in block.commands if s.hidden]
        if hidden:
            lines.append(f"【{block.title}】")
            lines.extend(_command_line(s) for s in hidden)
    return "\n".join(lines) if lines else None


def page_entries(reg: HelpRegistry, page: Page) -> "list[str | UniMessage]":
    """把页面装配为转发节点序列（str=文本节点，UniMessage=含图节点）。

    ``NotFoundPage`` 由调用方先行处理，传入即断言失败。
    """
    if isinstance(page, OverviewPage):
        return _overview_blocks(reg, page.include_hidden)
    if isinstance(page, CategoryPage):
        return _category_blocks(reg, page.category, page.include_hidden)
    if isinstance(page, CommandPage):
        return _command_blocks(reg, page.spec)
    if isinstance(page, GuidePage):
        return _guide_entries(reg, page.guide)
    if isinstance(page, ManagePage):
        manage = _manage_blocks(reg)
        return [manage] if manage else ["（无 hidden 指令）"]
    raise ValueError(
        f"page_entries 不支持 {type(page).__name__}（NotFoundPage 由调用方处理）"
    )


def page_text(reg: HelpRegistry, page: Page) -> str:
    """页面的纯文本形态（转发失败降级为整图的素材；节点间以空行衔接）。"""
    return "\n\n".join(
        entry if isinstance(entry, str) else "（含图节点，详见转发/在线帮助）"
        for entry in page_entries(reg, page)
    )


# 进程级单例：子插件与第三方共用的注册入口
help_registry = HelpRegistry()
help_registry.load_builtin_guide()
