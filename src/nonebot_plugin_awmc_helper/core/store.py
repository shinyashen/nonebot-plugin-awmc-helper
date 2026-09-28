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
"""

import json
import sqlite3
from typing import Any
from pathlib import Path
from datetime import datetime

from pydantic import NaiveDatetime
from sqlmodel import Field, SQLModel, col, delete, select
from sqlalchemy import UniqueConstraint, or_, func, text, update, inspect
from sqlalchemy.exc import OperationalError as SAOperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from nonebot_plugin_localstore import get_data_dir
from sqlmodel.ext.asyncio.session import AsyncSession

# ---------------------------------------------------------------------------
# 表定义
# ---------------------------------------------------------------------------


class UserBinding(SQLModel, table=True):
    """用户绑定：(platform, user_id) → 查分器凭据与显示偏好。"""

    __tablename__ = "user_binding"  # type: ignore[reportGeneralTypeIssues]

    platform: str = Field(primary_key=True)
    user_id: str = Field(primary_key=True)
    service: str = Field(default="divingfish")  # divingfish / lxns / net
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
    theme: str = Field(default="prism_plus")  # prism_plus / circle
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


class SongAlias(SQLModel, table=True):
    """远端别名持久化快照（yuzu/lxns 拉取后整源替换）：离线重启时兜底可用。"""

    __tablename__ = "song_alias"  # type: ignore[reportGeneralTypeIssues]

    source: str = Field(primary_key=True)  # yuzu / lxns
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


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(f"sqlite+aiosqlite:///{db_file()}")
    return _engine


def set_db_file(path: Path | None) -> None:
    """测试用：重置引擎并指向指定文件；传 None 恢复 localstore 默认路径。"""
    global _engine, _db_file
    _engine = None
    _db_file = path


async def init_db() -> None:
    """建表（create_all 起步；字段变更时在此追加简易迁移）。

    create_all 的存在性检查与 CREATE 之间存在竞态：多进程同时初始化同一个
    库文件（如 pytest-xdist 共享默认路径）时会收到 "table already exists"，
    对 SQLite 而言即幂等成功，忽略之；本轮事务内自己已建的表由下次调用补齐。
    """
    try:
        async with get_engine().begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
    except (SAOperationalError, sqlite3.OperationalError) as e:
        if "already exists" not in str(e):
            raise
    await _migrate_columns()


_MIGRATE_COLUMNS: dict[str, dict[str, str]] = {
    # 旧库无 lxns_refresh_token 列（落雪 OAuth 刷新令牌，2026-09-22 起落库）
    "user_binding": {
        "lxns_refresh_token": (
            "ALTER TABLE user_binding ADD COLUMN lxns_refresh_token VARCHAR"
        ),
        # 日服 NET 凭据（2026-09-26 起支持数据源 net）
        "net_sega_id": "ALTER TABLE user_binding ADD COLUMN net_sega_id VARCHAR",
        "net_password": "ALTER TABLE user_binding ADD COLUMN net_password VARCHAR",
        # 水鱼 OAuth 设备码绑定（2026-09-28 起：写路径强制 OAuth）。
        # BOOL 必须带 NOT NULL DEFAULT 0：无默认时存量行读出 NULL 而非 False，
        # 与新建行不均匀，将来 `is True` 类判定会漏掉 NULL 行
        "divingfish_oauth": (
            "ALTER TABLE user_binding ADD COLUMN"
            " divingfish_oauth BOOL NOT NULL DEFAULT 0"
        ),
        "divingfish_sub": "ALTER TABLE user_binding ADD COLUMN divingfish_sub VARCHAR",
    },
}


async def _migrate_columns() -> None:
    """为旧库补新列：create_all 不会修改已存在的表，SQLite 靠存在性检查幂等。"""
    async with get_engine().begin() as conn:

        def _existing(sync_conn):
            inspector = inspect(sync_conn)
            return {
                table: {col["name"] for col in inspector.get_columns(table)}
                for table in _MIGRATE_COLUMNS
            }

        schema = await conn.run_sync(_existing)
        for table, columns in _MIGRATE_COLUMNS.items():
            for name, ddl in columns.items():
                if name not in schema[table]:
                    await conn.execute(text(ddl))


def session() -> AsyncSession:
    """会话上下文（core 内模块与测试统一入口；AsyncSession 本身即异步 CM）。"""
    return AsyncSession(get_engine(), expire_on_commit=False)


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
    async with session() as db:
        binding = (
            await db.exec(
                select(UserBinding).where(
                    UserBinding.platform == platform, UserBinding.user_id == user_id
                )
            )
        ).first()
        if binding is None:
            return False
        await db.delete(binding)
        await db.commit()
        return True


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
    async with session() as db:
        exists = (
            await db.exec(
                select(LocalAlias).where(
                    LocalAlias.song_id == song_id, LocalAlias.alias == alias
                )
            )
        ).first()
        if exists:
            return False
        db.add(LocalAlias(song_id=song_id, alias=alias, created_by=created_by))
        await db.commit()
        return True


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
    kw = keyword.lower()
    async with session() as db:
        rows = list(
            (
                await db.exec(
                    select(Arcade).where(
                        or_(
                            col(Arcade.name).ilike(f"%{kw}%"),
                            col(Arcade.address).ilike(f"%{kw}%"),
                        )
                    )
                )
            ).all()
        )
        alias_rows = list(
            (
                await db.exec(
                    select(ArcadeAlias).where(col(ArcadeAlias.alias).ilike(f"%{kw}%"))
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
    column = {"person": Arcade.person, "machines": Arcade.machines}[field]
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
                .where(Arcade.id == arcade_id)
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
    async with session() as db:
        row = (
            await db.exec(select(ArcadeAlias).where(ArcadeAlias.alias == alias))
        ).first()
        if row is None:
            return False
        await db.delete(row)
        await db.commit()
        return True


async def get_arcade_aliases(arcade_id: int | None = None) -> list[ArcadeAlias]:
    async with session() as db:
        stmt = select(ArcadeAlias)
        if arcade_id is not None:
            stmt = stmt.where(ArcadeAlias.arcade_id == arcade_id)
        return list((await db.exec(stmt)).all())


async def add_arcade_alias(arcade_id: int, alias: str) -> bool:
    async with session() as db:
        exists = (
            await db.exec(select(ArcadeAlias).where(ArcadeAlias.alias == alias))
        ).first()
        if exists:
            return False
        db.add(ArcadeAlias(arcade_id=arcade_id, alias=alias))
        await db.commit()
        return True


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
    async with session() as db:
        exists = (
            await db.exec(
                select(ArcadeSubscription).where(
                    ArcadeSubscription.group_id == group_id,
                    ArcadeSubscription.arcade_id == arcade_id,
                )
            )
        ).first()
        if not exists:
            db.add(ArcadeSubscription(group_id=group_id, arcade_id=arcade_id))
            await db.commit()


async def unsubscribe(group_id: str, arcade_id: int) -> None:
    async with session() as db:
        row = (
            await db.exec(
                select(ArcadeSubscription).where(
                    ArcadeSubscription.group_id == group_id,
                    ArcadeSubscription.arcade_id == arcade_id,
                )
            )
        ).first()
        if row:
            await db.delete(row)
            await db.commit()


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
