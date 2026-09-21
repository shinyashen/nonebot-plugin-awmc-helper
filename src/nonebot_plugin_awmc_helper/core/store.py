"""统一 SQLite 存储（localstore 数据目录 ``awmc.db``）。

全部运行时数据集中在一个 SQLite 库：
- ``user_binding``：用户绑定（platform + user_id → 查分器凭据/主题）；
- ``group_switch``：群级开关的**显式覆盖**（部署级默认值走 .env 配置）；
- ``local_alias``：本地别名（热更新进曲库别名索引）；
- ``arcade`` 族：机厅 / 机厅别名 / 群订阅 / 人数变更流水；
- ``kv_cache``：拉取数据的持久快照（曲库刷新成功后例行写入，断网降级用）。

不入库的只有三类：素材与预渲染图片（static/ 文件）、.env 配置、纯内存态
（绑定回填会话、进行中的猜歌局）。
"""

import json
import sqlite3
from typing import Any
from pathlib import Path
from datetime import datetime

from sqlmodel import Field, SQLModel, select
from sqlalchemy import UniqueConstraint
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
    service: str = Field(default="divingfish")  # divingfish / lxns
    divingfish_username: str | None = Field(default=None)
    divingfish_import_token: str | None = Field(default=None)
    lxns_friend_code: int | None = Field(default=None)
    lxns_token: str | None = Field(default=None)
    theme: str = Field(default="prism_plus")  # prism_plus / circle
    bound_at: datetime = Field(default_factory=datetime.now)


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
    created_at: datetime = Field(default_factory=datetime.now)


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
    updated_at: datetime = Field(default_factory=datetime.now)


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
    created_at: datetime = Field(default_factory=datetime.now)


class KvCache(SQLModel, table=True):
    """拉取数据的持久快照（key 形如 ``songs_snapshot``），断网降级与冷启动加速。"""

    __tablename__ = "kv_cache"  # type: ignore[reportGeneralTypeIssues]

    key: str = Field(primary_key=True)
    payload: str  # JSON
    updated_at: datetime = Field(default_factory=datetime.now)


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

    ``notes_left``/``notes_right`` 仅 buddy 谱使用，``[tap,hold,slide,touch,break]`` JSON。
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


class SongSourceRaw(SQLModel, table=True):
    """源始 JSON 留档（基建）：otoge-db 无 id，键用 ``title:<曲名>``。"""

    __tablename__ = "song_source_raw"  # type: ignore[reportGeneralTypeIssues]

    source: str = Field(primary_key=True)  # lxns / divingfish / maimaiinfo / otoge-db / extra:<名称>
    song_id: str = Field(primary_key=True)  # 曲 id 字符串或 title:<曲名>
    payload: str  # JSON
    fetched_at: datetime = Field(default_factory=datetime.now)


class SongPending(SQLModel, table=True):
    """信息不足暂存（基建）：主键（机台内部 id）不可得的曲目，补足后批量归并。

    字段级缺失不算 pending（仍入主表、列置空）；pending 只承载阻塞性缺失。
    """

    __tablename__ = "song_pending"  # type: ignore[reportGeneralTypeIssues]

    source: str = Field(primary_key=True)
    key: str = Field(primary_key=True)  # 无 id 时 title:<曲名>，有 id 后为 id 字符串
    reason: str = "missing_id"
    payload: str  # JSON，该源原始条目（归并时的输入）
    first_seen: datetime = Field(default_factory=datetime.now)
    last_seen: datetime = Field(default_factory=datetime.now)
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


def _open_session() -> AsyncSession:
    return AsyncSession(get_engine(), expire_on_commit=False)


# ---------------------------------------------------------------------------
# 通用 CRUD
# ---------------------------------------------------------------------------


async def get_binding(platform: str, user_id: str) -> UserBinding | None:
    async with _open_session() as session:
        return (
            await session.exec(
                select(UserBinding).where(
                    UserBinding.platform == platform, UserBinding.user_id == user_id
                )
            )
        ).first()


async def save_binding(binding: UserBinding) -> None:
    async with _open_session() as session:
        session.add(binding)
        await session.commit()


async def delete_binding(platform: str, user_id: str) -> bool:
    async with _open_session() as session:
        binding = (
            await session.exec(
                select(UserBinding).where(
                    UserBinding.platform == platform, UserBinding.user_id == user_id
                )
            )
        ).first()
        if binding is None:
            return False
        await session.delete(binding)
        await session.commit()
        return True


async def get_group_switch(group_id: str, feature: str) -> bool | None:
    """返回群级覆盖值；未覆盖返回 None（调用方取 .env 默认值）。"""
    async with _open_session() as session:
        row = (
            await session.exec(
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
    async with _open_session() as session:
        row = (
            await session.exec(
                select(GroupSwitch).where(
                    GroupSwitch.group_id == group_id, GroupSwitch.feature == feature
                )
            )
        ).first()
        if row is None:
            row = GroupSwitch(group_id=group_id, feature=feature, enabled=enabled)
        else:
            row.enabled = enabled
        session.add(row)
        await session.commit()


async def add_local_alias(song_id: int, alias: str, created_by: str) -> bool:
    """添加本地别名，重复返回 False。"""
    async with _open_session() as session:
        exists = (
            await session.exec(
                select(LocalAlias).where(
                    LocalAlias.song_id == song_id, LocalAlias.alias == alias
                )
            )
        ).first()
        if exists:
            return False
        session.add(LocalAlias(song_id=song_id, alias=alias, created_by=created_by))
        await session.commit()
        return True


async def remove_local_alias(song_id: int, alias: str) -> bool:
    async with _open_session() as session:
        row = (
            await session.exec(
                select(LocalAlias).where(
                    LocalAlias.song_id == song_id, LocalAlias.alias == alias
                )
            )
        ).first()
        if row is None:
            return False
        await session.delete(row)
        await session.commit()
        return True


async def get_local_aliases() -> list[LocalAlias]:
    async with _open_session() as session:
        return list((await session.exec(select(LocalAlias))).all())


async def kv_get(key: str) -> Any | None:
    async with _open_session() as session:
        row = (await session.exec(select(KvCache).where(KvCache.key == key))).first()
        return json.loads(row.payload) if row else None


async def kv_set(key: str, payload: Any) -> None:
    async with _open_session() as session:
        row = (await session.exec(select(KvCache).where(KvCache.key == key))).first()
        data = json.dumps(payload, ensure_ascii=False)
        if row is None:
            row = KvCache(key=key, payload=data)
        else:
            row.payload = data
            row.updated_at = datetime.now()
        session.add(row)
        await session.commit()


async def kv_updated_at(key: str) -> datetime | None:
    async with _open_session() as session:
        row = (await session.exec(select(KvCache).where(KvCache.key == key))).first()
        return row.updated_at if row else None


# ---------------------------------------------------------------------------
# 机厅 CRUD（M6 使用，表结构先统一定义在此）
# ---------------------------------------------------------------------------


async def get_arcade(arcade_id: int) -> Arcade | None:
    async with _open_session() as session:
        return (
            await session.exec(select(Arcade).where(Arcade.id == arcade_id))
        ).first()


async def get_arcades_by_name(keyword: str) -> list[Arcade]:
    """按名称/地址/别称模糊查找机厅。"""
    async with _open_session() as session:
        arcades = list((await session.exec(select(Arcade))).all())
        aliases = list((await session.exec(select(ArcadeAlias))).all())
    alias_map: dict[int, list[str]] = {}
    for a in aliases:
        alias_map.setdefault(a.arcade_id, []).append(a.alias)
    kw = keyword.lower()
    return [
        a
        for a in arcades
        if kw in a.name.lower()
        or kw in a.address.lower()
        or any(kw in al.lower() for al in alias_map.get(a.id, []))
    ]


async def get_all_arcades() -> list[Arcade]:
    async with _open_session() as session:
        return list((await session.exec(select(Arcade))).all())


async def save_arcade(arcade: Arcade) -> None:
    async with _open_session() as session:
        session.add(arcade)
        await session.commit()


async def upsert_arcade(arcade: Arcade) -> None:
    """按主键存在则覆盖（华立官方数据同步用）。"""
    async with _open_session() as session:
        row = (await session.exec(select(Arcade).where(Arcade.id == arcade.id))).first()
        if row:
            # 保留本地标记与排卡人数
            arcade.is_custom = row.is_custom
            arcade.person = row.person
            arcade.updated_by = row.updated_by
            await session.delete(row)
        session.add(arcade)
        await session.commit()


async def delete_arcade(arcade_id: int) -> bool:
    async with _open_session() as session:
        row = (await session.exec(select(Arcade).where(Arcade.id == arcade_id))).first()
        if row is None:
            return False
        await session.delete(row)
        for sub in (
            await session.exec(
                select(ArcadeSubscription).where(
                    ArcadeSubscription.arcade_id == arcade_id
                )
            )
        ).all():
            await session.delete(sub)
        for al in (
            await session.exec(
                select(ArcadeAlias).where(ArcadeAlias.arcade_id == arcade_id)
            )
        ).all():
            await session.delete(al)
        await session.commit()
        return True


async def remove_arcade_alias_by_name(alias: str) -> bool:
    async with _open_session() as session:
        row = (
            await session.exec(select(ArcadeAlias).where(ArcadeAlias.alias == alias))
        ).first()
        if row is None:
            return False
        await session.delete(row)
        await session.commit()
        return True


async def get_arcade_aliases(arcade_id: int | None = None) -> list[ArcadeAlias]:
    async with _open_session() as session:
        stmt = select(ArcadeAlias)
        if arcade_id is not None:
            stmt = stmt.where(ArcadeAlias.arcade_id == arcade_id)
        return list((await session.exec(stmt)).all())


async def add_arcade_alias(arcade_id: int, alias: str) -> bool:
    async with _open_session() as session:
        exists = (
            await session.exec(select(ArcadeAlias).where(ArcadeAlias.alias == alias))
        ).first()
        if exists:
            return False
        session.add(ArcadeAlias(arcade_id=arcade_id, alias=alias))
        await session.commit()
        return True


async def remove_arcade_alias(arcade_id: int, alias: str) -> bool:
    async with _open_session() as session:
        row = (
            await session.exec(
                select(ArcadeAlias).where(
                    ArcadeAlias.arcade_id == arcade_id, ArcadeAlias.alias == alias
                )
            )
        ).first()
        if row is None:
            return False
        await session.delete(row)
        await session.commit()
        return True


async def get_subscriptions(group_id: str) -> list[int]:
    async with _open_session() as session:
        rows = (
            await session.exec(
                select(ArcadeSubscription).where(
                    ArcadeSubscription.group_id == group_id
                )
            )
        ).all()
        return [r.arcade_id for r in rows]


async def subscribe(group_id: str, arcade_id: int) -> None:
    async with _open_session() as session:
        exists = (
            await session.exec(
                select(ArcadeSubscription).where(
                    ArcadeSubscription.group_id == group_id,
                    ArcadeSubscription.arcade_id == arcade_id,
                )
            )
        ).first()
        if not exists:
            session.add(ArcadeSubscription(group_id=group_id, arcade_id=arcade_id))
            await session.commit()


async def unsubscribe(group_id: str, arcade_id: int) -> None:
    async with _open_session() as session:
        row = (
            await session.exec(
                select(ArcadeSubscription).where(
                    ArcadeSubscription.group_id == group_id,
                    ArcadeSubscription.arcade_id == arcade_id,
                )
            )
        ).first()
        if row:
            await session.delete(row)
            await session.commit()


async def reset_all_persons(operator: str = "自动清零") -> int:
    """全部机厅排卡人数清零（每日 4 点同步后调用），返回受影响机厅数。"""
    async with _open_session() as session:
        arcades = list((await session.exec(select(Arcade))).all())
        for a in arcades:
            a.person = 0
            a.updated_by = operator
            a.updated_at = datetime.now()
            session.add(a)
        await session.commit()
        return len(arcades)


async def add_count_log(
    arcade_id: int, delta: int, machines: int, operator_id: str
) -> None:
    async with _open_session() as session:
        session.add(
            ArcadeCountLog(
                arcade_id=arcade_id,
                delta=delta,
                machines=machines,
                operator_id=operator_id,
            )
        )
        await session.commit()


async def get_count_logs(arcade_id: int, limit: int = 50) -> list[ArcadeCountLog]:
    async with _open_session() as session:
        stmt = (
            select(ArcadeCountLog)
            .where(ArcadeCountLog.arcade_id == arcade_id)
            .order_by(ArcadeCountLog.created_at.desc())  # type: ignore[arg-type]
            .limit(limit)
        )
        return list((await session.exec(stmt)).all())
