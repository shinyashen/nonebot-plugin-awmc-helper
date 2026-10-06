"""统一 SQLite 存储（localstore 数据目录 ``awmc.db``）。

全部运行时数据集中在一个 SQLite 库：
- ``user_binding``：用户绑定（platform + user_id → 查分器凭据/主题）；
- ``group_switch``：群级开关的**显式覆盖**（部署级默认值走 .env 配置）；
- ``local_alias``：本地别名（热更新进曲库别名索引）；
- ``arcade`` 族：机厅 / 机厅别名 / 群订阅 / 人数变更流水；
- ``kv_cache``：拉取数据的持久快照（曲库刷新成功后例行写入，断网降级用）。

不入库的只有三类：素材与预渲染图片（static/ 文件）、.env 配置、纯内存态
（绑定回填会话、进行中的猜歌局）。

所有时间字段统一 naive 本地时（`NaiveDatetime` 标注）：sqlmodel≥0.0.43 的
DateTime 绑定强制要求 tzinfo，显式标注以维持既有存储格式与读写行为。

SQLModel 全局 metadata 边界（三仓共知）：SQLModel 表默认注册到同一全局
metadata，任一仓 ``create_all`` 会把其他仓已加载模型的**空表**也建出来。
表名三仓约定不重名（现状已满足：主仓 arcade 族 / arcade 仓 *_entry 族 /
score-updater wechat_binding/play_count 族），空表无行、无实际影响；根治
需独立 MetaData（SQLModel 支持有限，先调研，见主仓
``local/code-review-3rd-deferred-structure.md`` 搁置项）。新表命名保持
跨仓不重名。
"""

import json
import sqlite3
from typing import Any, cast
from pathlib import Path
from datetime import datetime

from nonebot import logger
from pydantic import NaiveDatetime
from sqlmodel import Field, SQLModel, col, delete, select
from sqlalchemy import (
    Table,
    Column,
    MetaData,
    TextClause,
    UniqueConstraint,
    or_,
    func,
    text,
    update,
    inspect,
)
from sqlalchemy.exc import OperationalError as SAOperationalError
from sqlalchemy.dialects import sqlite
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from nonebot_plugin_localstore import get_data_dir
from sqlmodel.ext.asyncio.session import AsyncSession

from ..constants import DX_ID_OFFSET, DEFAULT_THEME, SERVICE_DIVINGFISH

# ---------------------------------------------------------------------------
# 表定义
# ---------------------------------------------------------------------------


class UserBinding(SQLModel, table=True):
    """用户绑定：(platform, user_id) → 查分器凭据与显示偏好。"""

    __tablename__ = "user_binding"  # type: ignore[reportGeneralTypeIssues]

    platform: str = Field(primary_key=True)
    user_id: str = Field(primary_key=True)
    service: str = Field(default=SERVICE_DIVINGFISH)  # divingfish / lxns / net
    divingfish_username: str | None = Field(default=None)
    divingfish_import_token: str | None = Field(default=None)
    # 水鱼 OAuth 设备码绑定：映射本体在授权服务器侧（ref 摘要换票），
    # 本地只存「已完成 consent」标志与水鱼用户 ID（诊断/展示用）
    divingfish_oauth: bool = Field(default=False)
    divingfish_sub: str | None = Field(default=None)
    lxns_friend_code: int | None = Field(default=None)
    lxns_token: str | None = Field(default=None)
    # 落雪 OAuth refresh_token（maimai-py 暂无刷新流程，先落库防迁移丢失）
    lxns_refresh_token: str | None = Field(default=None)
    # 日服 NET 凭据（SEGA ID + 密码；NET 无第三方 API，仅能凭账号登录官方站）
    net_sega_id: str | None = Field(default=None)
    net_password: str | None = Field(default=None)
    theme: str = Field(default=DEFAULT_THEME)  # prism_plus / circle
    bound_at: NaiveDatetime = Field(default_factory=datetime.now)


class GroupSwitch(SQLModel, table=True):
    """群级开关的显式覆盖：未覆盖的群取 .env 部署级默认值。"""

    __tablename__ = "group_switch"  # type: ignore[reportGeneralTypeIssues]

    group_id: str = Field(primary_key=True)
    feature: str = Field(primary_key=True)  # alias_push / guess
    enabled: bool


class LocalAlias(SQLModel, table=True):
    """本地别名，唯一约束防重；查询时热并入曲库别名索引。"""

    __tablename__ = "local_alias"  # type: ignore[reportGeneralTypeIssues]
    __table_args__ = (UniqueConstraint("song_id", "alias", name="uq_local_alias"),)

    id: int | None = Field(default=None, primary_key=True)
    song_id: int = Field(index=True)
    alias: str = Field(index=True)
    created_by: str = ""
    created_at: NaiveDatetime = Field(default_factory=datetime.now)


class Arcade(SQLModel, table=True):
    """机厅：华立官方数据（is_custom=False，id 为官方 id）或自定义（id≥10000）。

    ``person`` 为本地排卡人数（每日 4 点同步后清零），机器数 ``machines`` 来自华立。
    """

    __tablename__ = "arcade"  # type: ignore[reportGeneralTypeIssues]

    id: int = Field(primary_key=True)
    name: str = Field(index=True)
    address: str = ""
    province: str = ""
    mall: str = ""
    machines: int = 0
    person: int = 0
    is_custom: bool = False
    updated_by: str = ""
    updated_at: NaiveDatetime = Field(default_factory=datetime.now)


class ArcadeAlias(SQLModel, table=True):
    """机厅别称，用于 `<店名>多少人` 等指令的模糊定位。"""

    __tablename__ = "arcade_alias"  # type: ignore[reportGeneralTypeIssues]
    __table_args__ = (UniqueConstraint("alias", name="uq_arcade_alias"),)

    id: int | None = Field(default=None, primary_key=True)
    arcade_id: int = Field(index=True)
    alias: str


class ArcadeSubscription(SQLModel, table=True):
    """群订阅：订阅后人数指令只对本群订阅的机厅生效。"""

    __tablename__ = "arcade_subscription"  # type: ignore[reportGeneralTypeIssues]

    group_id: str = Field(primary_key=True)
    arcade_id: int = Field(primary_key=True)


class ArcadeCountLog(SQLModel, table=True):
    """人数变更流水：记录操作人与时间。"""

    __tablename__ = "arcade_count_log"  # type: ignore[reportGeneralTypeIssues]

    id: int | None = Field(default=None, primary_key=True)
    arcade_id: int = Field(index=True)
    delta: int
    machines: int  # 变更后的机台数
    operator_id: str
    created_at: NaiveDatetime = Field(default_factory=datetime.now)


class KvCache(SQLModel, table=True):
    """拉取数据的持久快照（key 形如 ``songs_snapshot``），断网降级与冷启动加速。"""

    __tablename__ = "kv_cache"  # type: ignore[reportGeneralTypeIssues]

    key: str = Field(primary_key=True)
    payload: str  # JSON
    updated_at: NaiveDatetime = Field(default_factory=datetime.now)


# ---------------------------------------------------------------------------
# 歌曲规范表（01-Data-structure.md 1:1；日服全集 ∪ 国服信息，见 song-db-design §4）
# ---------------------------------------------------------------------------


class SongRow(SQLModel, table=True):
    """歌曲主表：id 0–9999（宴折基曲），并集 ≈1900 行。"""

    __tablename__ = "song"  # type: ignore[reportGeneralTypeIssues]

    id: int = Field(primary_key=True)
    title: str = Field(index=True)
    artist: str = ""
    genre: str = ""  # 日文流派名（maimai_py Genre 值域）
    bpm: str = ""  # 01 文档 str（各源 int/str 混存 → 统一 TEXT）
    image_url: str | None = None  # otoge-db 官方封面哈希名


class SongSheetGroup(SQLModel, table=True):
    """谱面组（sd/dx/utage）：组内谱面 version 完全一致（song-db-design §2.3）。

    ``version`` = 日服更新版本；``version_cn`` = 国服更新版本，NULL = 国服未上线；
    ``date`` = 日服更新（宴为复活）日期，8 位 int。
    """

    __tablename__ = "song_sheet_group"  # type: ignore[reportGeneralTypeIssues]

    song_id: int = Field(primary_key=True)
    kind: str = Field(primary_key=True)  # sd / dx / utage
    version: int | None = None
    version_cn: int | None = None
    date: int | None = None


class SongChart(SQLModel, table=True):
    """谱面：level_id sd/dx 取 0–4，宴取 6 位机台内部 id 的右起第 5 位。

    ``notes_left``/``notes_right`` 仅 buddy 谱使用，为 ``[tap,hold,slide,touch,break]``
    的 JSON。
    """

    __tablename__ = "song_chart"  # type: ignore[reportGeneralTypeIssues]

    song_id: int = Field(primary_key=True)
    kind: str = Field(primary_key=True)
    level_id: int = Field(primary_key=True)
    designer: str | None = None
    notes_tap: int = 0
    notes_hold: int = 0
    notes_slide: int = 0
    notes_touch: int = 0
    notes_break: int = 0
    kanji: str | None = None  # 仅 utage
    comment: str | None = None  # 仅 utage（otoge-db 原生字段）
    is_buddy: bool = False  # 仅 utage
    notes_left: str | None = None  # JSON
    notes_right: str | None = None  # JSON


class SongChartLevel(SQLModel, table=True):
    """定数历史（``level`` 数组的关系化，只存变化点，读取方 carry-forward）。

    ``version`` = 该定数自此版本起生效；语义为日服序列（song-db-design §4.4），
    国服当前定数经 §5.3 规则推导，不入本表。
    """

    __tablename__ = "song_chart_level"  # type: ignore[reportGeneralTypeIssues]

    song_id: int = Field(primary_key=True)
    kind: str = Field(primary_key=True)
    level_id: int = Field(primary_key=True)
    version: int = Field(primary_key=True)
    level_value: float | None = None  # 宴为标级推导值（+/无+ → .7/.0）


class DanCourse(SQLModel, table=True):
    """段位表（gallery.yaml 解析入库）：一个版本一张普通/真段位表。"""

    __tablename__ = "dan_course"  # type: ignore[reportGeneralTypeIssues]

    gallery_id: str = Field(primary_key=True)  # magical-dan / circle-plus-dan-intl…
    kind: str = Field(index=True)  # normal / shin（random 段位不建 course）
    version: int = Field(index=True)  # maimai_py Version 码


class DanGrade(SQLModel, table=True):
    """段位（一个段位表内的一段）：段名 + 血量规则五元组。"""

    __tablename__ = "dan_grade"  # type: ignore[reportGeneralTypeIssues]

    gallery_id: str = Field(primary_key=True)
    dan_id: str = Field(primary_key=True)  # 段位种名 id（1dan..ura_kaiden）
    sort: int = Field(index=True)  # 段位序（初段 0 起依官方顺序）
    name_ja: str  # 日文正名（初段…裏皆伝）
    life: int  # 初始血量
    damage_great: int  # GREAT 扣血
    damage_good: int  # GOOD 扣血
    damage_miss: int  # MISS 扣血
    clear_bonus: int  # 每曲 CLEAR 回复


class DanSheet(SQLModel, table=True):
    """段位课题曲：gallery sheetExpr 三段键 + 规范表 join 结果。

    ``song_id`` 为 JP 视图曲 id，标题无法对上（削除曲等）为 NULL——渲染层
    仅标题展示 + 默认封面兜底。
    """

    __tablename__ = "dan_sheet"  # type: ignore[reportGeneralTypeIssues]

    gallery_id: str = Field(primary_key=True)
    dan_id: str = Field(primary_key=True)
    idx: int = Field(primary_key=True)  # 曲序 0..3
    title: str  # gallery 原题（日服原题）
    kind: str  # std / dx
    difficulty: str  # basic..remaster
    song_id: int | None = Field(default=None, index=True)


class DanRandom(SQLModel, table=True):
    """随机段位档位（全版本同值，单表即全量）：显示等级区间 + 实测定数区间。"""

    __tablename__ = "dan_random"  # type: ignore[reportGeneralTypeIssues]

    dan_id: str = Field(primary_key=True)  # random_expert_1..4 / random_master_1..4
    difficulty: str  # expert / master
    name_ja: str  # 初級/中級/上級/超上級
    level_range: str  # 显示等级区间原文（"14~14+"）
    ds_lo: float  # 定数区间下缘
    ds_hi: float  # 定数区间上缘
    ds_source: str  # measured（BUDDiES 时代社区实测）/ derived（标级跨度推导）
    life: int
    damage_great: int
    damage_good: int
    damage_miss: int
    clear_bonus: int


class SongAlias(SQLModel, table=True):
    """远端别名持久化快照（yuzu/lxns/munet 拉取后整源替换）：离线重启时兜底可用。"""

    __tablename__ = "song_alias"  # type: ignore[reportGeneralTypeIssues]

    source: str = Field(primary_key=True)  # 键域 = core.provider.ALIAS_SOURCES
    song_id: int = Field(primary_key=True)
    alias: str = Field(primary_key=True)


class SongSourceRaw(SQLModel, table=True):
    """源始 JSON 留档（基建）：otoge-db 无 id，键用 ``title:<曲名>``。"""

    __tablename__ = "song_source_raw"  # type: ignore[reportGeneralTypeIssues]

    source: str = Field(
        primary_key=True
    )  # lxns / divingfish / maimaiinfo / otoge-db / extra:<名称>
    song_id: str = Field(primary_key=True)  # 曲 id 字符串或 title:<曲名>
    payload: str  # JSON
    fetched_at: NaiveDatetime = Field(default_factory=datetime.now)


class SongPending(SQLModel, table=True):
    """信息不足暂存（基建）：主键（机台内部 id）不可得的曲目，补足后批量归并。

    字段级缺失不算 pending（仍入主表、列置空）；pending 只承载阻塞性缺失。
    """

    __tablename__ = "song_pending"  # type: ignore[reportGeneralTypeIssues]

    source: str = Field(primary_key=True)
    key: str = Field(primary_key=True)  # 无 id 时 title:<曲名>，有 id 后为 id 字符串
    reason: str = "missing_id"
    payload: str  # JSON，该源原始条目（归并时的输入）
    first_seen: NaiveDatetime = Field(default_factory=datetime.now)
    last_seen: NaiveDatetime = Field(default_factory=datetime.now)
    attempts: int = 0
    """归并重试计数（``flush_pending`` 每轮未归并 +1、``upsert_pending`` +1）：
    消费方为 MuNET 批次补充（``munet._PENDING_MAX_ATTEMPTS`` 阈值跳过超限
    候选，防每晚空转）；用户可见的 pending 查歌路径不受该计数限制。"""


# ---------------------------------------------------------------------------
# 引擎管理
# ---------------------------------------------------------------------------

_engine: AsyncEngine | None = None
_db_file: Path | None = None


def db_file() -> Path:
    global _db_file
    if _db_file is None:
        _db_file = get_data_dir("nonebot_plugin_awmc_helper") / "awmc.db"
    return _db_file


def create_plugin_engine(data_dir: Path | str, filename: str) -> AsyncEngine:
    """生态通用引擎工厂：``<data_dir>/<filename>`` 的 aiosqlite 异步引擎。

    把「db_file 拼路径 + create_async_engine」样板从本仓的 localstore 默认
    目录/``set_db_file`` 测试重定向机制中解耦出来，供 awmc 生态同构插件
    复用——机厅仓（nonebot-plugin-awmc-arcade）与传分仓
    （nonebot-plugin-awmc-score-updater）的引擎管理样板与本仓逐行同构，
    后续换用本工厂与 :func:`init_plugin_db`（先主仓落地，兄弟仓择机切换）。
    返回的引擎不入本仓全局单例，生命周期归调用方（用完 ``dispose()``）。
    """
    return create_async_engine(f"sqlite+aiosqlite:///{Path(data_dir) / filename}")


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        path = db_file()
        _engine = create_plugin_engine(path.parent, path.name)
    return _engine


def set_db_file(path: Path | None) -> None:
    """测试用：重置引擎并指向指定文件；传 None 恢复 localstore 默认路径。"""
    global _engine, _db_file
    _engine = None
    _db_file = path


async def init_plugin_db(engine: AsyncEngine, metadata: MetaData) -> None:
    """生态通用幂等建表：对 engine 执行 create_all，「already exists」按幂等成功忽略。

    create_all 的存在性检查与 CREATE 之间存在竞态：多进程同时初始化同一个
    库文件（如 pytest-xdist 共享默认路径）时会收到 "table already exists"，
    对 SQLite 而言即幂等成功，忽略之；本轮事务内自己已建的表由下次调用补齐。
    参数收 metadata 而非绑死 SQLModel.metadata：调用方传各自的表元数据
    （三仓现均用 SQLModel 全局 metadata，连带建出其他仓空表的语义见模块
    docstring）。

    ⚠️ 竞态可能**连续多轮**命中（N 个 worker 的 check/CREATE 交错），单轮
    重试在 xdist 高并发下仍可再次撞 already-exists（2026-10-06 CI py3.14
    实锤），故有界循环重试至收敛；非竞态 OperationalError 照常上抛。
    """
    for _ in range(3):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(metadata.create_all)
            return
        except (SAOperationalError, sqlite3.OperationalError) as e:
            if "already exists" not in str(e):
                raise
    raise RuntimeError("create_all 连续多轮 already-exists 未收敛（异常高并发）")


async def init_db() -> None:
    """建表（幂等）+ 旧库补列。

    模型新增**简单列**（str/bool/int/float/时间，带常量默认或可空）由
    :func:`_migrate_columns` 的自动迁移覆盖，无需手工登记 DDL；回填/类型
    变更等复杂迁移走同函数的 ``_MIGRATE_COLUMNS`` 特例通道。
    """
    await init_plugin_db(get_engine(), SQLModel.metadata)
    await _migrate_columns()


# SQLite 方言实例（列类型 → DDL 片段编译用；无状态，模块级复用）
_SQLITE_DIALECT = sqlite.dialect()


def _own_tables() -> dict[str, Table]:
    """本模块定义的表（表名 → Table）。

    SQLModel 全局 metadata 三仓共享（见模块 docstring），自动迁移只对本
    模块定义的模型负责；兄弟仓的表由兄弟仓自己的初始化流程补列。
    """
    tables: dict[str, Table] = {}
    for cls in globals().values():
        if (
            isinstance(cls, type)
            and issubclass(cls, SQLModel)
            and cls.__module__ == __name__
        ):
            # __tablename__/__table__ 由 SQLModel 运行时注入，静态类型上
            # 不可见，故经 getattr 取
            name = getattr(cls, "__tablename__")
            tables[name] = cast(Table, getattr(cls, "__table__"))
    return tables


def _sqlite_default_literal(value: Any) -> str | None:
    """Python 简单标量 → SQLite DEFAULT 字面量；不可渲染返回 None。

    None 视为「无默认值」而非 ``DEFAULT NULL``（可空列旧行读 NULL 即模型
    语义，无需显式子句）。
    """
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return None


def _column_default_sql(column: Column) -> str | None:
    """模型列可自动迁移的服务端默认值片段；拿不到返回 None。

    优先 ``server_default``（模型显式声明；str 按 SQLAlchemy 语义原样内联，
    引号转义由声明方负责）；否则取 Python 侧常量默认（``Field(default=常量)``
    生成的标量 ColumnDefault）。``default_factory``（callable）无法变成
    服务端默认，返回 None。
    """
    server_default = column.server_default
    if server_default is not None:
        arg = getattr(server_default, "arg", None)
        if isinstance(arg, TextClause):
            return str(arg)
        if isinstance(arg, str):
            return arg
        return _sqlite_default_literal(arg)
    default = column.default
    if default is not None and getattr(default, "is_scalar", False):
        return _sqlite_default_literal(getattr(default, "arg"))
    return None


def _auto_add_column_sql(table: str, column: Column) -> str | None:
    """按模型列定义生成 ``ALTER TABLE … ADD COLUMN``；无法自动迁移返回 None。

    列类型以对 SQLite 方言的编译结果为准（str→VARCHAR、bool→BOOLEAN、
    int→INTEGER、NaiveDatetime→DATETIME、float→FLOAT）。SQLite 约束：
    ADD COLUMN 带 NOT NULL 必须同时给非 NULL 常量默认值——NOT NULL 列
    （如 default_factory 时间的必填列）凑不齐即不可自动迁移，走特例通道。
    """
    default_sql = _column_default_sql(column)
    if not column.nullable and default_sql is None:
        return None
    parts = [
        f"ALTER TABLE {table} ADD COLUMN {column.name}",
        column.type.compile(_SQLITE_DIALECT),
    ]
    if not column.nullable:
        parts.append("NOT NULL")
    if default_sql is not None:
        parts.append(f"DEFAULT {default_sql}")
    return " ".join(parts)


_MIGRATE_COLUMNS: dict[str, dict[str, str]] = {}
"""复杂迁移特例通道（表名 → 列名 → 手写 DDL）。

自动迁移（:func:`_auto_add_column_sql`）已覆盖「模型新增简单列」场景——
给模型加带常量默认或可空的简单列**无需再在此登记**。本表只留自动迁移
不适用的特例：数据回填、类型变更、带子查询的 DDL 等；同一列在此登记时
手写 DDL 优先于自动推导。截至 2026-10-04 为空：原 5 条
（lxns_refresh_token / net_sega_id / net_password / divingfish_sub 为可空
VARCHAR、divingfish_oauth 为 bool NOT NULL DEFAULT 0）已逐条核对，全部
可由自动迁移等价生成，迁入自动路径。
"""


async def _migrate_columns() -> None:
    """旧库补列：create_all 不会修改已存在的表，靠本函数对齐模型列集。

    单事务多表一次遍历：inspect 取本仓各表现有列集后，``_MIGRATE_COLUMNS``
    特例 DDL 优先执行（列名不再出现在模型列集亦可，如回填临时列），其余
    缺失列按模型定义自动生成 ADD COLUMN；已存在的列不重复添加（幂等）。
    自动迁移不可为的缺失列（如 default_factory 时间的 NOT NULL 列）
    log.warning 提醒登记特例通道或调整模型定义，不中断建库。
    """
    tables = _own_tables()
    async with get_engine().begin() as conn:

        def _existing(sync_conn):
            inspector = inspect(sync_conn)
            present = set(inspector.get_table_names())
            return {
                name: (
                    {col["name"] for col in inspector.get_columns(name)}
                    if name in present
                    else None  # 表不存在（create_all 已先行提交建出，防御竞态）
                )
                for name in tables
            }

        schema = await conn.run_sync(_existing)
        for name, existing in schema.items():
            if existing is None:
                continue
            manual = _MIGRATE_COLUMNS.get(name, {})
            for col_name, ddl in manual.items():
                if col_name not in existing:
                    await conn.execute(text(ddl))
            for column in tables[name].columns:
                if column.name in existing or column.name in manual:
                    continue
                ddl = _auto_add_column_sql(name, column)
                if ddl is None:
                    # error 而非 warning：旧库该列将永久缺失，运行期触该列的
                    # SQL 才爆 no such column，建库期就该高优暴露
                    logger.error(
                        "store：列 %s.%s 无法自动迁移（NOT NULL 且无可用的"
                        "服务端默认值，如 default_factory 时间列），请登记"
                        " _MIGRATE_COLUMNS 特例通道或将列改为可空/常量默认",
                        name,
                        column.name,
                    )
                    continue
                await conn.execute(text(ddl))


def session() -> AsyncSession:
    """会话上下文（core 内模块与测试统一入口；AsyncSession 本身即异步 CM）。"""
    return AsyncSession(get_engine(), expire_on_commit=False)


async def _delete_row(model: type[SQLModel], *conds: Any) -> bool:
    """「查行 → 删 → 提交」DAO 底座：命中删除返回 True，未命中 False。

    带级联的删除（如 delete_arcade）留在各自函数体内——级联必须与主行
    同一事务提交，不并入本助手。
    """
    async with session() as db:
        row = (await db.exec(select(model).where(*conds))).first()
        if row is None:
            return False
        await db.delete(row)
        await db.commit()
        return True


async def _add_if_absent(model: type[SQLModel], *conds: Any, row: SQLModel) -> bool:
    """「exists → 否则 add」DAO 底座：已存在返回 False，否则写入提交返回 True。

    保持既有先查询后写入的语义，不用 on_conflict_do_nothing（sqlite 方言
    风险最小；唯一约束仍兜底并发双写）。
    """
    async with session() as db:
        exists = (await db.exec(select(model).where(*conds))).first()
        if exists:
            return False
        db.add(row)
        await db.commit()
        return True


# ---------------------------------------------------------------------------
# 通用 CRUD
# ---------------------------------------------------------------------------


async def get_binding(platform: str, user_id: str) -> UserBinding | None:
    async with session() as db:
        return (
            await db.exec(
                select(UserBinding).where(
                    UserBinding.platform == platform, UserBinding.user_id == user_id
                )
            )
        ).first()


async def get_lxns_refreshable_bindings() -> list[UserBinding]:
    """全部持有 refresh_token 的绑定（落雪令牌每日保活任务用）。"""
    async with session() as db:
        return list(
            await db.exec(
                select(UserBinding).where(
                    col(UserBinding.lxns_refresh_token).is_not(None)
                )
            )
        )


async def save_binding(binding: UserBinding) -> None:
    async with session() as db:
        db.add(binding)
        await db.commit()


async def delete_binding(platform: str, user_id: str) -> bool:
    return await _delete_row(
        UserBinding, UserBinding.platform == platform, UserBinding.user_id == user_id
    )


async def get_group_switch(group_id: str, feature: str) -> bool | None:
    """返回群级覆盖值；未覆盖返回 None（调用方取 .env 默认值）。"""
    async with session() as db:
        row = (
            await db.exec(
                select(GroupSwitch).where(
                    GroupSwitch.group_id == group_id, GroupSwitch.feature == feature
                )
            )
        ).first()
        return row.enabled if row else None


async def get_switch(group_id: str, feature: str, default: bool) -> bool:
    """生效开关：群级显式覆盖优先，否则取部署级默认值。"""
    override = await get_group_switch(group_id, feature)
    return default if override is None else override


async def set_group_switch(group_id: str, feature: str, enabled: bool) -> None:
    async with session() as db:
        row = (
            await db.exec(
                select(GroupSwitch).where(
                    GroupSwitch.group_id == group_id, GroupSwitch.feature == feature
                )
            )
        ).first()
        if row is None:
            row = GroupSwitch(group_id=group_id, feature=feature, enabled=enabled)
        else:
            row.enabled = enabled
        db.add(row)
        await db.commit()


async def add_local_alias(song_id: int, alias: str, created_by: str) -> bool:
    """添加本地别名，重复返回 False。"""
    return await _add_if_absent(
        LocalAlias,
        LocalAlias.song_id == song_id,
        LocalAlias.alias == alias,
        row=LocalAlias(song_id=song_id, alias=alias, created_by=created_by),
    )


async def get_local_aliases() -> list[LocalAlias]:
    async with session() as db:
        return list((await db.exec(select(LocalAlias))).all())


async def get_utage_kanji() -> dict[int, set[str]]:
    """宴谱汉字映射（根 id → {kanji}）：谱面类型前缀剥离的动态依据。"""
    async with session() as db:
        rows = (
            await db.exec(
                select(SongChart.song_id, SongChart.kanji).where(
                    col(SongChart.kind) == "utage",
                    col(SongChart.kanji).is_not(None),  # type: ignore[arg-type]
                )
            )
        ).all()
    result: dict[int, set[str]] = {}
    for song_id, kanji in rows:
        if kanji:
            result.setdefault(song_id, set()).add(kanji)
    return result


async def save_song_aliases(source: str, items: dict[int, list[str]]) -> None:
    """整源替换远端别名快照（单事务；items 为根 id → 别名列表）。

    远端数据存在同曲完全重复的别名（柚子源实测），入库前按 (song_id, alias)
    精确去重，否则整源写入触发唯一约束整体失败。
    """
    async with session() as db:
        # SQLModel 已弃用 db.execute，delete 一律走 exec
        await db.exec(delete(SongAlias).where(col(SongAlias.source) == source))
        seen: set[tuple[int, str]] = set()
        for song_id, aliases in items.items():
            for alias in aliases:
                if (song_id, alias) in seen:
                    continue
                seen.add((song_id, alias))
                db.add(SongAlias(source=source, song_id=song_id, alias=alias))
        await db.commit()


async def song_image_url(song_id: int) -> str | None:
    """曲绘文件名（otoge-db 官方图名；日服封面在线拉取用），无则 None。"""
    async with session() as db:
        row = (await db.exec(select(SongRow).where(col(SongRow.id) == song_id))).first()
    return row.image_url if row else None


async def song_image_urls(song_ids: list[int]) -> dict[int, str]:
    """批量曲绘文件名映射（仅含**有**文件名的 id；批量补齐封面用）。"""
    unique = list(dict.fromkeys(song_ids))
    if not unique:
        return {}
    async with session() as db:
        rows = (
            await db.exec(
                select(SongRow.id, SongRow.image_url).where(col(SongRow.id).in_(unique))
            )
        ).all()
    return {sid: url for sid, url in rows if url}


async def load_song_aliases(sources: list[str]) -> dict[int, list[str]]:
    """读取若干源的别名快照（根 id → 别名列表）。"""
    async with session() as db:
        rows = (
            await db.exec(select(SongAlias).where(col(SongAlias.source).in_(sources)))
        ).all()
    merged: dict[int, list[str]] = {}
    for row in rows:
        merged.setdefault(row.song_id, []).append(row.alias)
    return merged


async def list_song_titles() -> set[str]:
    """规范表全部曲名（批次补充的 title-diff 候选过滤用）。"""
    async with session() as db:
        rows = (await db.exec(select(SongRow))).all()
    return {row.title for row in rows if row.title}


async def song_kind_index() -> dict[int, tuple[str, frozenset[str]]]:
    """全库歌曲的 (title, 已有谱面组 kind 集)，批次「标题+组类型」差分用。

    返回 ``{song_id: (title, kinds)}``（title 为空的不返回）；kinds 含
    sd/dx/utage 全量，消费方按需取用。谱面组追加候选的判定依据是
    otoge 侧分组字段与这里的 kind 集合做差。
    """
    async with session() as db:
        song_rows = (await db.exec(select(SongRow))).all()
        group_rows = (await db.exec(select(SongSheetGroup))).all()
    kinds: dict[int, set[str]] = {}
    for group in group_rows:
        kinds.setdefault(group.song_id, set()).add(group.kind)
    return {
        row.id: (row.title, frozenset(kinds.get(row.id, ())))
        for row in song_rows
        if row.title
    }


async def song_group_facts(song_ids: list[int]) -> dict[int, dict]:
    """指定曲的标题与 sd/dx 组版本/日期现值（MuNET 批次既有曲校正用）。

    返回 ``{song_id: {"title": str, "groups": {kind: {"version", "date"}}}}``；
    只取 sd/dx（宴组不参与 MuNET 批次校正），库中不存在的曲不返回。
    """
    async with session() as db:
        song_rows = (
            await db.exec(select(SongRow).where(col(SongRow.id).in_(song_ids)))
        ).all()
        group_rows = (
            await db.exec(
                select(SongSheetGroup).where(
                    col(SongSheetGroup.song_id).in_(song_ids),
                    col(SongSheetGroup.kind).in_(("sd", "dx")),
                )
            )
        ).all()
    out: dict[int, dict] = {
        row.id: {"title": row.title, "groups": {}} for row in song_rows
    }
    for group in group_rows:
        if (info := out.get(group.song_id)) is not None:
            info["groups"][group.kind] = {
                "version": group.version,
                "date": group.date,
            }
    return out


async def upsert_song_aliases(source: str, items: dict[int, list[str]]) -> int:
    """增量写入远端别名快照（不整源替换，已存在的 (song_id, alias) 跳过）。

    版本批次补充等小批量写入用；全量走查用 :func:`save_song_aliases` 整源替换。
    返回新写入行数。
    """
    added = 0
    async with session() as db:
        existing = {
            (row.song_id, row.alias)
            for row in (
                await db.exec(select(SongAlias).where(col(SongAlias.source) == source))
            ).all()
        }
        for song_id, aliases in items.items():
            for alias in aliases:
                if (song_id, alias) in existing:
                    continue
                existing.add((song_id, alias))
                db.add(SongAlias(source=source, song_id=song_id, alias=alias))
                added += 1
        await db.commit()
    return added


async def prune_song_aliases(source: str, keep_song_ids: set[int]) -> int:
    """删除某源中 song_id 不在 keep 集内的别名行，返回删除行数。

    断点续走的走查不能整源替换（会裁剪此前各晚增量），改「增量 upsert +
    目标集外清理」达到与远端对齐的等价语义；keep 集 = 本轮走查覆盖的根 id。
    """
    async with session() as db:
        result = await db.exec(
            delete(SongAlias).where(
                col(SongAlias.source) == source,
                col(SongAlias.song_id).not_in(keep_song_ids),
            )
        )
        await db.commit()
    return int(result.rowcount or 0)


async def list_alias_walk_targets() -> list[int]:
    """MuNET 别名全量走查的组级 id 集（升序）。

    MuNET 条目 id 沿官方机台内部 id 编码（§2.2）：SD 组=曲 id、SD+DX 双组曲的
    DX 组=曲 id+10000、**仅 DX 组的曲（无 sd 组）MuNET 直接用曲 id**（如
    パズルリボン DX-only = 1449）。宴谱挂在基曲条目上，无需单独 id。
    """
    async with session() as db:
        songs = (await db.exec(select(SongRow))).all()
        groups = (await db.exec(select(SongSheetGroup))).all()
    kinds: dict[int, set[str]] = {}
    for g in groups:
        kinds.setdefault(g.song_id, set()).add(g.kind)
    targets: set[int] = set()
    for row in songs:
        song_kinds = kinds.get(row.id, set())
        if not song_kinds:
            continue  # 无组歌曲不在 MuNET 上
        targets.add(row.id)  # SD 组 / 仅 DX 组 / 仅宴组都用曲 id
        if "sd" in song_kinds and "dx" in song_kinds:
            targets.add(row.id + DX_ID_OFFSET)  # 双组曲的 DX 组
    return sorted(targets)


async def kv_get(key: str) -> Any | None:
    async with session() as db:
        row = (await db.exec(select(KvCache).where(KvCache.key == key))).first()
        return json.loads(row.payload) if row else None


async def kv_set(key: str, payload: Any) -> None:
    async with session() as db:
        row = (await db.exec(select(KvCache).where(KvCache.key == key))).first()
        data = json.dumps(payload, ensure_ascii=False)
        if row is None:
            row = KvCache(key=key, payload=data)
        else:
            row.payload = data
            row.updated_at = datetime.now()
        db.add(row)
        await db.commit()


# ---------------------------------------------------------------------------
# 机厅 CRUD（M6 使用，表结构先统一定义在此）
# ---------------------------------------------------------------------------


async def get_arcade(arcade_id: int) -> Arcade | None:
    async with session() as db:
        return (await db.exec(select(Arcade).where(Arcade.id == arcade_id))).first()


async def get_arcade_by_ids(ids: list[int]) -> list[Arcade]:
    """按 id 批量取机厅（保持入参顺序，缺失跳过）。"""
    if not ids:
        return []
    async with session() as db:
        rows = list(
            (await db.exec(select(Arcade).where(col(Arcade.id).in_(ids)))).all()
        )
    by_id = {a.id: a for a in rows}
    return [by_id[i] for i in ids if i in by_id]


async def get_arcades_by_name(keyword: str) -> list[Arcade]:
    """按名称/地址/别称模糊查找机厅（名称/地址/别称均走 SQL LIKE）。"""
    # 用户输入面：转义 LIKE 通配符（% _ \），否则单输 % 命中全表
    kw = keyword.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    async with session() as db:
        rows = list(
            (
                await db.exec(
                    select(Arcade).where(
                        or_(
                            col(Arcade.name).ilike(f"%{kw}%", escape="\\"),
                            col(Arcade.address).ilike(f"%{kw}%", escape="\\"),
                        )
                    )
                )
            ).all()
        )
        alias_rows = list(
            (
                await db.exec(
                    select(ArcadeAlias).where(
                        col(ArcadeAlias.alias).ilike(f"%{kw}%", escape="\\")
                    )
                )
            ).all()
        )
        if alias_rows:
            seen = {r.id for r in rows}
            extra_ids = [a.arcade_id for a in alias_rows if a.arcade_id not in seen]
            if extra_ids:
                rows += list(
                    (
                        await db.exec(
                            select(Arcade).where(col(Arcade.id).in_(extra_ids))
                        )
                    ).all()
                )
    return rows


async def get_all_arcades() -> list[Arcade]:
    async with session() as db:
        return list((await db.exec(select(Arcade))).all())


async def save_arcade(arcade: Arcade) -> None:
    async with session() as db:
        db.add(arcade)
        await db.commit()


async def update_arcade_count(
    arcade_id: int,
    field: str,
    mode: str,
    amount: int,
    *,
    updated_by: str | None = None,
    touch: bool = True,
) -> int | None:
    """原子更新机厅排卡人数/机台数，返回更新后的值（行不存在返回 None）。

    「+1」类高频打卡走**相对增量**单语句 UPDATE，而非读快照→内存算→整行
    回写——后者的并发窗口里两人同时 +1 会各写同一个值（丢更新），整行回写
    还会顺带覆盖其它列的并发变更。``mode``：inc（钳下限 0 前的相对加）、
    dec（下限钳 0 的相对减）、set（绝对值；并发下最后写入者胜，语义自洽）。
    SQLite ≥3.35 支持单语句 RETURNING（Python 3.12 自带的 sqlite3 均满足）。

    ``updated_by``/``touch``：默认记录操作人与时间；传 None/False 可只改数值
    （如管理侧改机台数不产生「最近上报人」语义）。
    """
    fields_map = {"person": Arcade.person, "machines": Arcade.machines}
    if field not in fields_map:
        raise ValueError(f"未知更新字段：{field}")
    column = col(fields_map[field])
    if mode == "inc":
        expr = column + amount
    elif mode == "dec":
        expr = func.max(column - amount, 0)
    elif mode == "set":
        expr = amount
    else:
        raise ValueError(f"未知更新模式：{mode}")
    values: dict[str, Any] = {field: expr}
    if updated_by is not None:
        values["updated_by"] = updated_by
    if touch:
        values["updated_at"] = datetime.now()
    async with session() as db:
        # db.exec 对 UPDATE…RETURNING 返回 Row 元组（SQLModel 的 exec 是
        # select 语义的薄封装），取首列；不用 db.execute 是为免全库唯二
        # 触发 SQLModel 的「请用 exec」弃用告警
        row = (
            await db.exec(
                update(Arcade)
                .where(col(Arcade.id) == arcade_id)
                .values(**values)
                .returning(column)
            )
        ).first()
        await db.commit()
    return row[0] if row is not None else None


async def upsert_arcades(arcades: list["Arcade"]) -> None:
    """按主键批量覆盖（华立官方同步用；单事务，保留本地字段）。

    存量行一次 ``in_()`` 取回建 dict——华立全量同步上千机厅，逐行 SELECT 是 N+1。
    """
    async with session() as db:
        ids = [arcade.id for arcade in arcades]
        existing: dict[int, Arcade] = {}
        if ids:
            rows = (await db.exec(select(Arcade).where(col(Arcade.id).in_(ids)))).all()
            existing = {row.id: row for row in rows}
        for arcade in arcades:
            row = existing.get(arcade.id)
            if row:
                arcade.is_custom = row.is_custom
                arcade.person = row.person
                arcade.updated_by = row.updated_by
                arcade.updated_at = row.updated_at
                await db.delete(row)
            db.add(arcade)
        await db.commit()


async def delete_arcade(arcade_id: int) -> bool:
    async with session() as db:
        row = (await db.exec(select(Arcade).where(Arcade.id == arcade_id))).first()
        if row is None:
            return False
        await db.delete(row)
        for sub in (
            await db.exec(
                select(ArcadeSubscription).where(
                    ArcadeSubscription.arcade_id == arcade_id
                )
            )
        ).all():
            await db.delete(sub)
        for al in (
            await db.exec(select(ArcadeAlias).where(ArcadeAlias.arcade_id == arcade_id))
        ).all():
            await db.delete(al)
        await db.commit()
        return True


async def remove_arcade_alias_by_name(alias: str) -> bool:
    return await _delete_row(ArcadeAlias, ArcadeAlias.alias == alias)


async def get_arcade_aliases_by_ids(arcade_ids: set[int]) -> list[ArcadeAlias]:
    """按机厅 id 集合取别名（订阅列表匹配用，免全表拉取后 Python 过滤）。"""
    if not arcade_ids:
        return []
    async with session() as db:
        return list(
            (
                await db.exec(
                    select(ArcadeAlias).where(
                        col(ArcadeAlias.arcade_id).in_(arcade_ids)
                    )
                )
            ).all()
        )


async def add_arcade_alias(arcade_id: int, alias: str) -> bool:
    return await _add_if_absent(
        ArcadeAlias,
        ArcadeAlias.alias == alias,
        row=ArcadeAlias(arcade_id=arcade_id, alias=alias),
    )


async def get_subscriptions(group_id: str) -> list[int]:
    async with session() as db:
        rows = (
            await db.exec(
                select(ArcadeSubscription).where(
                    ArcadeSubscription.group_id == group_id
                )
            )
        ).all()
        return [r.arcade_id for r in rows]


async def subscribe(group_id: str, arcade_id: int) -> None:
    await _add_if_absent(
        ArcadeSubscription,
        ArcadeSubscription.group_id == group_id,
        ArcadeSubscription.arcade_id == arcade_id,
        row=ArcadeSubscription(group_id=group_id, arcade_id=arcade_id),
    )


async def unsubscribe(group_id: str, arcade_id: int) -> None:
    await _delete_row(
        ArcadeSubscription,
        ArcadeSubscription.group_id == group_id,
        ArcadeSubscription.arcade_id == arcade_id,
    )


async def reset_all_persons(operator: str = "自动清零") -> int:
    """全部机厅排卡人数清零（每日 4 点同步后调用），返回受影响机厅数。"""
    async with session() as db:
        result = await db.exec(
            update(Arcade).values(
                person=0, updated_by=operator, updated_at=datetime.now()
            )
        )
        await db.commit()
        return result.rowcount


async def add_count_log(
    arcade_id: int, delta: int, machines: int, operator_id: str
) -> None:
    async with session() as db:
        db.add(
            ArcadeCountLog(
                arcade_id=arcade_id,
                delta=delta,
                machines=machines,
                operator_id=operator_id,
            )
        )
        await db.commit()
