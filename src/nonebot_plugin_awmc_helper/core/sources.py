"""数据源适配层：能力域（Capability）+ 按源适配器注册表。

三个查分数据源（水鱼 / 落雪 / 日服 NET）统一在此注册。「某数据源支持什么」
的唯一事实是适配器**覆写了哪些方法**——未覆写的能力走 :class:`SourceBase`
默认实现，统一给「暂不支持」的用户文案，门禁无需在调用侧散写 service 判断
（原 ``ScoreService._guard_cn`` 由此退役）。

- :class:`Capability`：查分能力域，一个成员 = 查分门面的一个操作。帮助适用
  性标注（core/help）、绑定成功提示的「支持指令」清单（plugins/bind）均经
  :func:`support_note` / :func:`command_hints` 从注册表派生，不手写副本；
- ``view``：数据源对应的曲库视图（``Scope``）——涉及曲库视图的功能（NET 出
  卡换日服谱面对象、随机曲池、minfo JP 解析）统一经 ``ScoreService.view_of``
  取用；**群娱乐功能（猜歌/今日运势）刻意不跟随个人绑定的视图**（2026-09-29
  定案：与个人数据源解耦，维持国服口径）；
- 查询管线（异常映射、落雪续期阶梯、Q50 全量凭据路由）为本模块共享助手，
  水鱼/落雪适配器共用；NET 适配器走 core.net_score 的官方站直连组装链路。

新增数据源：实现一个 :class:`SourceBase` 子类（覆写它支持的能力方法、声明
``view`` 与 ``capabilities``）→ :data:`SOURCES` 注册一行 → 绑定/凭据装配挂进
core.binding。能力门禁、拦截文案、帮助标注、能力清单随之自动生效；想临时
下线某能力，把对应方法改回抛 :meth:`SourceBase.unsupported` 即可。
"""

import asyncio
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar

import httpx
from maimai_py import (
    Song,
    SongType,
    PlayerSong,
    PlayerBests,
    MaimaiPlates,
    MaimaiScores,
    MaimaiPyError,
    RateLimitError,
    DivingFishPlayer,
    PlayerIdentifier,
    InvalidPlateError,
    PrivacyLimitationError,
    PlayerNotAuthorizedError,
    InvalidDeveloperTokenError,
    InvalidPlayerIdentifierError,
)
from nonebot.log import logger

from .ext import ExtError
from .ext import divingfish as df_ext
from .songs import song_service
from .client import (
    client,
    divingfish_provider,
    divingfish_public_provider,
)
from .songdb import Scope
from .binding import (
    SERVICE_NET,
    SERVICE_LXNS,
    SERVICE_DIVINGFISH,
    UserBinding,
    BindingError,
    binding_service,
)
from .ext.net import NET_ERROR_MESSAGES, NetError
from .net_score import NetScoreError, net_score_service

if TYPE_CHECKING:
    from maimai_py import ScoreExtend

__all__ = [
    "SOURCES",
    "Capability",
    "SourceBase",
    "UserScoreError",
    "command_hints",
    "source_of",
    "support_note",
]


class UserScoreError(Exception):
    """查分业务错误，message 为面向用户的文案。

    原定义在 core.score；随适配层下沉（适配器直接抛），core.score re-export
    保持子插件与第三方的导入面不变。
    """


_DF_OAUTH_HINT = (
    "该水鱼账号未授权本 bot 查询成绩：\n请发送「绑定水鱼」完成授权（推荐，约 1 分钟）"
)
"""全量路径**未授权**（consent_required）收口文案：subject 路径的失败形态
（oauth=1 用户 consent 被撤、或无 token 档的公开键 subject 尝试未命中快照）。"""

_DF_TOKEN_HINT = (
    "水鱼 Import-Token 已失效（可能已在水鱼侧重置）：\n"
    "请发送「绑定水鱼token <新token>」换绑\n"
    "（获取：水鱼个人页 → 设置 → Import-Token），\n"
    "或发送「绑定水鱼」改用授权绑定（推荐，全功能可用）"
)
"""全量路径 **token 失效**（400「导入token有误」）收口文案：路由到 token 的
绑定专属——两类失败凭路由天然可分，各自给最直接的行动指引。"""


class Capability(StrEnum):
    """查分能力域：一个成员 = 查分门面的一个操作，各数据源按域声明支持。"""

    B50 = "b50"  # b50 大图 / 推分推荐 / mai什么加分
    MINFO = "minfo"  # 单曲成绩卡
    SCORES_ALL = "scores_all"  # 全量成绩：ap50 / 完成表 / 进度 / 分数列表
    PLATES = "plates"  # 牌子完成表 / 牌子进度
    PLAYER = "players"  # 查分器玩家资料（b50 卡头 / 排名的前置）
    MY_RANKING = "my_ranking"  # 水鱼 RA 榜个人排名


CAP_LABELS: dict[Capability, str] = {
    Capability.B50: "b50",
    Capability.MINFO: "单曲成绩",
    Capability.SCORES_ALL: "全量成绩",
    Capability.PLATES: "牌子进度",
    Capability.PLAYER: "玩家信息",
    Capability.MY_RANKING: "RA 排名",
}
"""能力域 → 中文标签（拦截文案用；b50 本身即指令名不译）。"""

CAP_COMMAND_HINTS: dict[Capability, str] = {
    Capability.B50: "b50",
    Capability.MINFO: "minfo",
    Capability.SCORES_ALL: "ap50",
}
"""能力域 → 代表指令名（绑定成功提示「支持指令：…」的拼装源）。

只收**用户可直接发起**的能力：PLAYER 等纯内部前置不进清单。某能力域对新
数据源开放为用户指令时，在此补一行即可（command_hints 自动跟进）。
"""


# ---------------------------------------------------------------- 共享查询管线


def _has_scores(
    scores: "list[ScoreExtend] | None", song_type: SongType | None = None
) -> bool:
    """成绩是否覆盖目标谱面类型：``song_type=None`` 时任一类型有成绩即可。

    双谱曲的成绩来自同一曲（SD/DX 同根 id），「未游玩」判定必须能按类型收窄
    ——否则标准谱有分而 DX 谱没分的曲会画出全「未游玩」的空卡。
    """
    if not scores:
        return False
    return any(s.type == song_type for s in scores) if song_type else True


def _map_error(e: Exception) -> UserScoreError:
    """maimai-py / ext 异常 → 用户文案（统一映射表，含已知坑）。"""
    if isinstance(e, NetError):
        return UserScoreError(NET_ERROR_MESSAGES.get(e.code, str(e)))
    if isinstance(e, ExtError):
        return UserScoreError(str(e))
    if isinstance(e, PlayerNotAuthorizedError):
        # 水鱼 OAuth 未授权（consent_required 与「用户不存在」服务端有意不可区分）；
        # scores/plates 层有单条文案收口，b50 有公开键回退，此处是漏网兜底
        return UserScoreError(_DF_OAUTH_HINT)
    if isinstance(e, RateLimitError):
        return UserScoreError("水鱼今日查询配额已用完（按 UTC 日重置），请明天再试")
    if isinstance(e, InvalidPlayerIdentifierError):
        return UserScoreError(
            "没有找到这个玩家，请确认绑定信息（水鱼用户名/QQ、落雪好友码或个人 Token）"
        )
    if isinstance(e, PrivacyLimitationError):
        return UserScoreError(
            "该玩家未授权第三方查询数据：\n"
            "水鱼请到 个人页 → 隐私设置 关闭「禁止第三方查询」；\n"
            "落雪请在 落雪查分器 → 隐私设置 中允许通过好友码查询"
        )
    if isinstance(e, InvalidDeveloperTokenError):
        # 1.6.0 起 dev 端点及其 410 映射已从库中删除，此异常只剩 OAuth 应用凭据
        # 缺失/无效（换票被拒、scope 未获批）一类部署问题
        return UserScoreError("水鱼 OAuth 应用凭据无效或缺失，请联系管理员检查部署配置")
    if isinstance(e, InvalidPlateError):
        return UserScoreError(
            "牌子名称有误，请检查版本与牌种（如：真将 / 樱极 / 舞舞）"
        )
    if isinstance(e, httpx.RequestError):
        return UserScoreError("查分器网络异常，请稍后再试")
    if isinstance(e, MaimaiPyError):
        return UserScoreError(f"查询失败：{e}")
    return UserScoreError("查询失败，请稍后再试")


async def _run(
    binding: UserBinding | None,
    make_coro,
    notify_slow=None,
    *,
    propagate_identifier_error: bool = False,
):
    """统一执行 maimai-py 查询：异常映射 + 落雪 token 过期自动续期重试。

    ``make_coro`` 是无参协程工厂，重试时重新装配 identifier（token 已刷新）。
    ``propagate_identifier_error``：401 合流异常（InvalidPlayerIdentifierError，
    水鱼即 token 已重置/凭据身份不存在）不做映射直接上抛，由调用方单条文案
    收口——仅对**非落雪**绑定生效，落雪恒走续期阶梯。
    入口先做落雪 token 预检（JWT exp 已过/临近则先续期，省掉必败首跳；
    best-effort 不上抛）。续期成功后走 :func:`_retry_after_refresh` 阶梯
    （落雪对新令牌的生效有短延迟，见 local/QUESTIONS.md Q43）；
    ``notify_slow`` 在等待超过预期时被调用一次（handler 传发送回调，供
    用户侧提示）。
    """
    if binding is not None:
        await binding_service.preflight_lxns(binding)
    try:
        return await make_coro()
    except BindingError as e:
        raise UserScoreError(str(e)) from e
    except PlayerNotAuthorizedError:
        # 水鱼 OAuth 未授权：不在 _run 内吃掉——它常意味着换一条凭据路径
        # 仍有戏（b50 回退公开键），由调用方决定回退或映射文案
        raise
    except (MaimaiPyError, httpx.RequestError) as e:
        if binding is None:
            raise _map_error(e) from e
        if not isinstance(e, InvalidPlayerIdentifierError):
            raise _map_error(e) from e
        if propagate_identifier_error and binding.service != SERVICE_LXNS:
            raise
        status = await binding_service.refresh_lxns(binding)
        if status == "refreshed":
            return await _retry_after_refresh(make_coro, e, notify_slow)
        if status == "dead":
            raise UserScoreError("落雪授权已过期，请重新「绑定落雪」") from e
        raise _map_error(e) from e


async def _retry_after_refresh(make_coro, first: Exception, notify_slow=None):
    """续期成功后的阶梯重试：落雪侧新令牌生效有短延迟（实测通常 ≤10s、
    偶发长至数分钟，Q43），立即 → 5s → 10s 三级覆盖绝大多数窗口。

    仅对 401 合流异常（InvalidPlayerIdentifierError）继续阶梯——续期成功
    后玩家身份不会变，再次 401 即生效延迟；其余异常立即映射。等待进入
    10s 一档时经 ``notify_slow`` 提示一次（超过预期）；最终仍失败给非
    技术兜底文案（不向用户暴露令牌/续期细节，技术细节进日志）。
    """
    last = first
    notified = False
    for delay in (0, 5, 10):
        if delay >= 10 and notify_slow is not None and not notified:
            notified = True
            try:
                await notify_slow()
            except Exception:
                logger.debug("慢查询提示发送失败（不影响查询）")
        if delay:
            await asyncio.sleep(delay)
        try:
            return await make_coro()
        except InvalidPlayerIdentifierError as e:
            last = e
        except (MaimaiPyError, httpx.RequestError) as e2:
            raise _map_error(e2) from e2
    logger.warning(f"落雪续期后阶梯重试（0/5/10s）仍 401：{last!r}")
    raise UserScoreError("落雪查分器暂时无法访问，请一分钟后再试") from last


async def _run_full(binding: UserBinding, make, notify_slow=None):
    """全量成绩路径收口（get_scores_all/get_plates 共用）：完整凭据一跳。

    ``make(get_ident)`` 收到的是标识**装配函数**而非实例：工厂每次调用
    重新装配 identifier，401 续期后的阶梯重试才能用上新 token（预检救活
    首跳，生效延迟窗口内的重试靠这里保证不拿旧凭据硬撞）。

    10-01 后定稿（Q50）：全量成绩没有公开键形态（developer 端点已从库中
    删除，``full_identifier`` 又按绑定标志确定性路由 subject/token），故
    不做任何跨凭据/公开键回落——失败按异常类型区分收口：未授权
    （PlayerNotAuthorizedError）与 token 已重置（「导入token有误」400）各自
    给最直接的行动指引。单曲查询无公开键形态也无需凭据回退（get_minfo
    自带专项文案）。
    """
    try:
        return await _run(
            binding,
            make(lambda: binding_service.full_identifier(binding)),
            notify_slow,
            propagate_identifier_error=True,
        )
    except PlayerNotAuthorizedError as e:
        raise UserScoreError(_DF_OAUTH_HINT) from e
    except InvalidPlayerIdentifierError as e:
        if binding.service != SERVICE_DIVINGFISH:
            raise _map_error(e) from e  # 落雪：维持「没有找到」既有语义
        # 路由到 token 的绑定 401 合流＝token 已在水鱼侧重置（授权缺失走
        # PlayerNotAuthorizedError 分支，两类失败凭路由天然可分）
        raise UserScoreError(_DF_TOKEN_HINT) from e


async def _net_call(coro):
    """NET 抓取层调用 → 统一映射 NetError/NetScoreError 为用户文案。

    NetError 是凭据错误/维护等抓取层语义（映射表转专项文案）；只捕
    NetScoreError 会让 handler 的通用兜底吃掉专项提示（L-1）。
    """
    try:
        return await coro
    except NetError as e:
        raise UserScoreError(NET_ERROR_MESSAGES.get(e.code, str(e))) from e
    except NetScoreError as e:
        raise UserScoreError(str(e)) from e


# ---------------------------------------------------------------- 适配器


class SourceBase:
    """数据源适配器基类：能力默认全部「暂不支持」，支持者覆写对应方法。

    ``capabilities`` 显式声明支持域（供帮助标注/绑定文案查询，免实例探测）；
    与「覆写了哪些方法」的一致性由 test_core_sources 守护——覆写漏声明或
    声明漏覆写都会在测试期暴露。
    """

    key: ClassVar[str] = ""
    zh_name: ClassVar[str] = ""
    # 拦截文案用全名 / 帮助标注用短名（如「日服数据源（NET）」/「日服 NET」）
    short_zh: ClassVar[str] = ""
    view: ClassVar[Scope] = "cn"
    capabilities: ClassVar[frozenset[Capability]] = frozenset()

    def unsupported(self, cap: Capability) -> UserScoreError:
        """能力未开放的用户文案（模板单源；数据源可覆写给专项措辞）。"""
        return UserScoreError(
            f"{self.zh_name}暂不支持{CAP_LABELS[cap]}，敬请期待后续版本"
        )

    # ---- 可选能力（默认实现一律拒绝；签名与查分门面方法一致） ----

    async def get_b50(
        self, binding: UserBinding, notify_slow=None
    ) -> "MaimaiScores | PlayerBests":
        raise self.unsupported(Capability.B50)

    async def get_minfo(
        self,
        song: Song,
        binding: UserBinding | None,
        song_type: SongType | None = None,
        notify_slow=None,
    ) -> PlayerSong | None:
        raise self.unsupported(Capability.MINFO)

    async def get_scores_all(
        self, binding: UserBinding, notify_slow=None
    ) -> MaimaiScores:
        raise self.unsupported(Capability.SCORES_ALL)

    async def get_plates(
        self, binding: UserBinding, plate: str, notify_slow=None
    ) -> MaimaiPlates:
        raise self.unsupported(Capability.PLATES)

    async def get_player(self, binding: UserBinding, notify_slow=None):
        raise self.unsupported(Capability.PLAYER)

    async def get_my_ranking(
        self, binding: UserBinding, notify_slow=None
    ) -> "tuple[df_ext.RankUser, int] | None":
        """RA 榜个人定位：返回 (榜内条目, 名次)；未上榜 None。"""
        raise self.unsupported(Capability.MY_RANKING)

    # ---- 附属钩子（抓取提示 / 卡面身份，默认空实现） ----

    def needs_fetch(self, binding: UserBinding) -> bool:
        """查询前是否需要真实抓取（handler 据此先发「正在抓取」提示）。"""
        return False

    def player_profile(self, binding: UserBinding):
        """卡面渲染用的数据源玩家身份（NET 官方资料等；默认无）。"""
        return None


class ProberSource(SourceBase):
    """水鱼/落雪共享基座：maimai_py 查询管线（公开键 + Q50 全量凭据路由）。

    b50 的未授权公开键回退（无凭据 provider）对落雪绑定实际不可达（落雪
    隐私限制走 PrivacyLimitationError 映射），保留在基座属行为等价迁移。
    """

    async def get_player(self, binding: UserBinding, notify_slow=None):
        await song_service.ensure_loaded()
        return await _run(
            binding,
            lambda: client.players(
                binding_service.identifier(binding),
                provider=binding_service.provider(binding),
            ),
            notify_slow,
        )

    async def get_b50(
        self, binding: UserBinding, notify_slow=None
    ) -> "MaimaiScores | PlayerBests":
        await song_service.ensure_loaded()

        def make(get_ident, get_provider):
            return lambda: client.bests(get_ident(), provider=get_provider())

        try:
            return await _run(
                binding,
                make(
                    lambda: binding_service.identifier(binding),
                    lambda: binding_service.provider(binding),
                ),
                notify_slow,
            )
        except PlayerNotAuthorizedError:
            # 1.6.0 起 subject 走 Bearer b50（公开 /query/player 不收 subject）：
            # 未授权用户回退公开键 + 无凭据 provider——无凭据是必须的，否则裸
            # username 会被拼成 username: subject 再走 Bearer，回退必败
            return await _run(
                binding,
                make(
                    lambda: binding_service.identifier(binding, with_oauth=False),
                    lambda: divingfish_public_provider,
                ),
                notify_slow,
            )

    async def get_minfo(
        self,
        song: Song,
        binding: UserBinding | None,
        song_type: SongType | None = None,
        notify_slow=None,
    ) -> PlayerSong | None:
        await song_service.ensure_loaded()

        # 有无公开键只用于结果过滤（装配期判定，凭据刷新不改变有无）；
        # 工厂内每次尝试重新装配 ident，401 续期后阶梯重试才用得上新 token
        has_ident = (
            binding_service.identifier_or_none(binding) is not None
            if binding is not None
            else False
        )

        def factory():
            ident = (
                binding_service.identifier_or_none(binding)
                if binding is not None
                else None
            )
            return client.minfo(
                song,
                ident,
                provider=divingfish_provider
                if ident is None
                else binding_service.provider(binding),  # type: ignore[arg-type]
            )

        try:
            result = await _run(binding, factory, notify_slow)
        except PlayerNotAuthorizedError as e:
            # 单曲只有 OAuth Bearer 形态，无回退路径：未覆盖用户给专项文案
            # （minfo 变体 = 收口常量把「查询成绩」替换为「查询单曲成绩」）
            raise UserScoreError(
                _DF_OAUTH_HINT.replace("查询成绩", "查询单曲成绩")
            ) from e
        if not has_ident or result is None:
            return result
        return result if _has_scores(result.scores, song_type) else None

    async def get_scores_all(
        self, binding: UserBinding, notify_slow=None
    ) -> MaimaiScores:
        """全量成绩（牌子 / ap50 / 表格的基础）：凭据按绑定标志确定性路由。"""
        await song_service.ensure_loaded()

        def make(get_ident):
            def factory():
                return client.scores(
                    get_ident(), provider=binding_service.provider(binding)
                )

            return factory

        return await _run_full(binding, make, notify_slow)

    async def get_plates(
        self, binding: UserBinding, plate: str, notify_slow=None
    ) -> MaimaiPlates:
        """牌子进度（判牌语义在 maimai-py 内置）：全量成绩，凭据按绑定标志路由。"""
        await song_service.ensure_loaded()

        def make(get_ident):
            return lambda: client.plates(
                get_ident(), plate, provider=binding_service.provider(binding)
            )

        return await _run_full(binding, make, notify_slow)


class DivingFishSource(ProberSource):
    """水鱼适配器：全能力 + RA 榜定位 + 公开代查。"""

    key = SERVICE_DIVINGFISH
    zh_name = "水鱼数据源"
    short_zh = "水鱼"
    capabilities = frozenset(Capability)

    async def get_b50_public(
        self, username: str
    ) -> "tuple[DivingFishPlayer, MaimaiScores]":
        """水鱼公开代查：b50 <水鱼用户名>（无需绑定）。

        必须用无凭据 provider（:data:`divingfish_public_provider`）：1.6.0 起
        配了 client 凭据的 provider 会把裸 username 拼成 ``username:`` subject
        走 Bearer，对未授权的陌生人必然 consent_required。
        """
        ident = PlayerIdentifier(username=username)
        player, bests = await asyncio.gather(
            _run(
                None,
                lambda: client.players(ident, provider=divingfish_public_provider),
            ),
            _run(
                None,
                lambda: client.bests(ident, provider=divingfish_public_provider),
            ),
        )
        return player, bests  # type: ignore[return-value]

    async def get_my_ranking(
        self, binding: UserBinding, notify_slow=None
    ) -> "tuple[df_ext.RankUser, int] | None":
        ident = binding_service.identifier_or_none(binding)
        if ident is None or (ident.username is None and ident.qq is None):
            raise UserScoreError("请先绑定水鱼查分器后再查询排名")
        # query/player 响应含 username（DivingFishPlayer.name），qq 查询同样可用
        player = await self.get_player(binding, notify_slow=notify_slow)
        users = await df_ext.rating_ranking()
        for i, u in enumerate(users):
            if u.username.lower() == player.name.lower():
                return u, i + 1
        return None


class LxnsSource(ProberSource):
    """落雪适配器：全能力（RA 榜为水鱼侧榜单，不声明 MY_RANKING）。"""

    key = SERVICE_LXNS
    zh_name = "落雪数据源"
    short_zh = "落雪"
    capabilities = frozenset(
        {
            Capability.B50,
            Capability.MINFO,
            Capability.SCORES_ALL,
            Capability.PLATES,
            Capability.PLAYER,
        }
    )


class NetSource(SourceBase):
    """日服 NET 适配器：官方站直连（core.ext.net 抓取 + core.net_score 组装）。

    一次抓取的全量成绩在冷却窗口内被 b50/minfo/全量共享（0 请求秒回）；
    b35/b15 划分用日服现行版本（``current_version_jp``），成绩与曲库口径
    均为日服，故 ``view = "jp"``。

    全量成绩（SCORES_ALL）已接入（2026-09-30）：ap50 / 完成表 / 进度 /
    分数列表等曲库消费方维持**国服视图网格**——日服限定曲不在国服网格、
    自然缺席，共享曲定数显示国服口径（插件「国服为主、日服补充」既定口径；
    日服专属底图未立项）。牌子（PLATES）与玩家信息（PLAYER）保持门禁：
    前者牌单/素材未定（用户 2026-09-30 拍板暂不接入），后者无 maimai_py
    Player 形态、卡面身份走 needs_fetch/player_profile 注入链路。
    """

    key = SERVICE_NET
    zh_name = "日服数据源（NET）"
    short_zh = "日服 NET"
    view = "jp"
    capabilities = frozenset({Capability.B50, Capability.MINFO, Capability.SCORES_ALL})

    async def get_b50(
        self, binding: UserBinding, notify_slow=None
    ) -> "MaimaiScores | PlayerBests":
        return await _net_call(net_score_service.get_b50(binding))

    async def get_minfo(
        self,
        song: Song,
        binding: UserBinding | None,
        song_type: SongType | None = None,
        notify_slow=None,
    ) -> PlayerSong | None:
        """NET 窗口缓存过滤组装 PlayerSong（未游玩 None）。

        ``binding`` 恒非 None（门面把无绑定请求路由到水鱼公开路径）；
        ``song_type`` 见 :class:`ProberSource.get_minfo`。
        """
        hit = await _net_call(net_score_service.get_minfo_scores(binding, song))
        if hit is None or not _has_scores(hit, song_type):
            return None
        return PlayerSong(song=song, scores=hit)

    async def get_scores_all(
        self, binding: UserBinding, notify_slow=None
    ) -> MaimaiScores:
        """日服全量成绩：窗口缓存组装成绩 → MaimaiScores 同构包装。

        字段手工装配镜像 maimai_py ``MaimaiScores.configure`` 的字段契约——
        不走 configure 本体：其 b35/b15 划分按国服版本缓存（CN 视图），
        NET 侧拆分已在 :meth:`net_score.NetScoreService.bests_of` 用日服
        现行版本完成。
        """
        scores, _ = await _net_call(net_score_service.get_scores(binding))
        bests = net_score_service.bests_of(scores)
        ms = MaimaiScores(client)
        ms.scores = scores
        ms.scores_b35 = bests.scores_b35
        ms.scores_b15 = bests.scores_b15
        ms.rating = bests.rating
        ms.rating_b35 = bests.rating_b35
        ms.rating_b15 = bests.rating_b15
        return ms

    def needs_fetch(self, binding: UserBinding) -> bool:
        return net_score_service.needs_fetch(binding)

    def player_profile(self, binding: UserBinding):
        return net_score_service.player_of(binding)


SOURCES: dict[str, SourceBase] = {
    s.key: s for s in (DivingFishSource(), LxnsSource(), NetSource())
}
"""数据源适配器注册表（进程单例；key 与 core.binding 的 SERVICE_* 一致）。"""


def source_of(service: str) -> SourceBase:
    """service 键 → 适配器；未知键回落水鱼（与 binding_service.provider 同口径）。"""
    return SOURCES.get(service) or SOURCES[SERVICE_DIVINGFISH]


def command_hints(service: str) -> str:
    """数据源支持的用户指令清单文案（如「b50、minfo」），声明序输出。"""
    src = source_of(service)
    return "、".join(
        hint for cap, hint in CAP_COMMAND_HINTS.items() if cap in src.capabilities
    )


def support_note(cap: Capability) -> "str | None":
    """能力域 → 帮助适用性标注；全源支持时 None。

    单源支持标「仅水鱼数据源」；部分源缺失标「日服 NET 暂不支持」
    （第三方声明未知的 capability 值由调用方忽略，不在此校验）。
    """
    supported = [s for s in SOURCES.values() if cap in s.capabilities]
    if len(supported) == len(SOURCES):
        return None
    if len(supported) == 1:
        return f"仅{supported[0].short_zh}数据源"
    missing = "、".join(
        s.short_zh for s in SOURCES.values() if cap not in s.capabilities
    )
    return f"{missing} 暂不支持"
