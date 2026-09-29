"""成绩服务：b50 / 全量成绩 / minfo / 牌子 / 玩家信息 的薄封装与异常映射。

所有 maimai-py 异常统一在 core 捕获转用户文案（规划 §5.4），
子插件只收 :class:`UserScoreError`。
"""

import asyncio
from typing import TYPE_CHECKING

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

if TYPE_CHECKING:
    from maimai_py import ScoreExtend

from .ext import ExtError
from .calc import build_bests
from .songs import song_service
from .client import client, divingfish_provider, divingfish_public_provider
from .binding import (
    SERVICE_NET,
    SERVICE_LXNS,
    SERVICE_DIVINGFISH,
    NET_UNSUPPORTED_HINT,
    UserBinding,
    BindingError,
    binding_service,
)
from .ext.net import NET_ERROR_MESSAGES, NetError
from .net_score import NetScoreError, net_score_service

__all__ = ["UserScoreError", "build_bests", "score_service"]
"""模块公开面：第三方 b50 变体（pc50 等）从 core.score 一站式导入。"""


class UserScoreError(Exception):
    """查分业务错误，message 为面向用户的文案。"""


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


class ScoreService:
    """成绩查询封装：全部先 ensure_loaded（被动缓存由 maimai-py 保证）。"""

    @staticmethod
    def _guard_cn(binding: UserBinding) -> None:
        """NET 数据源能力拦截：仅 b50 支持，其余指令统一在此拒绝。"""
        if binding.service == SERVICE_NET:
            raise UserScoreError(NET_UNSUPPORTED_HINT)

    async def _run(
        self,
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
        收口——仅对**非落雪**绑定生效，落雪恒走下方续期阶梯。
        入口先做落雪 token 预检（JWT exp 已过/临近则先续期，省掉必败首跳；
        best-effort 不上抛）。续期成功后走 :meth:`_retry_after_refresh` 阶梯
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
                return await self._retry_after_refresh(make_coro, e, notify_slow)
            if status == "dead":
                raise UserScoreError("落雪授权已过期，请重新「绑定落雪」") from e
            raise _map_error(e) from e

    @staticmethod
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

    async def _run_full(self, binding: UserBinding, make, notify_slow=None):
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
            return await self._run(
                binding,
                make(lambda: binding_service.full_identifier(binding)),
                notify_slow=notify_slow,
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

    async def get_player(self, binding: UserBinding, notify_slow=None):
        self._guard_cn(binding)
        await song_service.ensure_loaded()
        return await self._run(
            binding,
            lambda: client.players(
                binding_service.identifier(binding),
                provider=binding_service.provider(binding),
            ),
            notify_slow=notify_slow,
        )

    async def _get_b50_net(self, binding: UserBinding) -> PlayerBests:
        """日服 B50：窗口缓存优先（core.net_score），真实抓取按需触发。"""
        try:
            return await net_score_service.get_b50(binding)
        except NetError as e:
            # NET 抓取层错误（凭据错误/维护等）经映射表转专项文案；
            # 只捕 NetScoreError 会让 handler 的通用兜底吃掉专项提示
            raise UserScoreError(NET_ERROR_MESSAGES.get(e.code, str(e))) from e
        except NetScoreError as e:
            raise UserScoreError(str(e)) from e

    async def get_b50(
        self, binding: UserBinding, notify_slow=None
    ) -> "MaimaiScores | PlayerBests":
        """B50（b35 + b15 与总 rating）；NET 数据源走日服组装链路。

        两个返回类型对渲染层 duck-compatible（rating/b35/b15/scores 字段同构）。
        """
        if binding.service == SERVICE_NET:
            return await self._get_b50_net(binding)
        await song_service.ensure_loaded()

        def make(get_ident, get_provider):
            return lambda: client.bests(get_ident(), provider=get_provider())

        try:
            return await self._run(
                binding,
                make(
                    lambda: binding_service.identifier(binding),
                    lambda: binding_service.provider(binding),
                ),
                notify_slow=notify_slow,
            )
        except PlayerNotAuthorizedError:
            # 1.6.0 起 subject 走 Bearer b50（公开 /query/player 不收 subject）：
            # 未授权用户回退公开键 + 无凭据 provider——无凭据是必须的，否则裸
            # username 会被拼成 username: subject 再走 Bearer，回退必败
            return await self._run(
                binding,
                make(
                    lambda: binding_service.identifier(binding, with_oauth=False),
                    lambda: divingfish_public_provider,
                ),
                notify_slow=notify_slow,
            )

    async def get_minfo_net(
        self, binding: UserBinding, song: Song, song_type: SongType | None = None
    ) -> PlayerSong | None:
        """日服单曲成绩：NET 窗口缓存过滤组装 PlayerSong（未游玩 None）。

        仅 service=net 时由 minfo 调用；score/JP 视图口径，与 CN minfo 语义一致
        （``song_type`` 见 :meth:`get_minfo`）。
        """
        try:
            hit = await net_score_service.get_minfo_scores(binding, song)
        except NetError as e:
            raise UserScoreError(NET_ERROR_MESSAGES.get(e.code, str(e))) from e
        except NetScoreError as e:
            raise UserScoreError(str(e)) from e
        if hit is None or not _has_scores(hit, song_type):
            return None
        return PlayerSong(song=song, scores=hit)

    async def get_scores_all(
        self, binding: UserBinding, notify_slow=None
    ) -> MaimaiScores:
        """全量成绩（牌子 / ap50 / 表格的基础）：凭据按绑定标志确定性路由。

        失败（未授权 / token 已在水鱼侧重置）由 :meth:`_run_full` 单条可行动
        文案收口。
        """
        self._guard_cn(binding)
        await song_service.ensure_loaded()

        def make(get_ident):
            def factory():
                return client.scores(
                    get_ident(), provider=binding_service.provider(binding)
                )

            return factory

        return await self._run_full(binding, make, notify_slow)

    async def get_b50_by_username(
        self, username: str
    ) -> tuple["DivingFishPlayer", MaimaiScores]:
        """水鱼公开代查：b50 <水鱼用户名>（无需绑定）。

        必须用无凭据 provider（:data:`divingfish_public_provider`）：1.6.0 起
        配了 client 凭据的 provider 会把裸 username 拼成 ``username:`` subject
        走 Bearer，对未授权的陌生人必然 consent_required。
        """
        ident = PlayerIdentifier(username=username)
        player, bests = await asyncio.gather(
            self._run(
                None,
                lambda: client.players(ident, provider=divingfish_public_provider),
            ),
            self._run(
                None,
                lambda: client.bests(ident, provider=divingfish_public_provider),
            ),
        )
        return player, bests  # type: ignore[return-value]

    async def get_minfo(
        self,
        song: Song,
        binding: UserBinding | None,
        song_type: SongType | None = None,
        notify_slow=None,
    ) -> PlayerSong | None:
        """单曲成绩（未绑定时仅谱面信息）。

        已绑定且有凭据但无成绩时返回 None（= 未游玩，对齐 Hoshino 原版按条目
        id 查询的 ``MusicNotPlayError`` →「您未游玩过曲目」语义）；未绑定时成绩
        为空的 PlayerSong 原样返回，供纯谱面视图渲染。

        ``song_type``：本次查询指向的谱面类型（数字 id 形状 / 唯一命中条目的
        类型）。给出时**该类型**无成绩即算未游玩——双谱曲「标准谱有分、DX 谱
        没分」时查 DX 谱应提示未游玩，而不是画一张全「未游玩」的空卡；为 None
        时任一类型有成绩即算玩过（名称命中多条目、宴谱条目等场景）。
        """
        if binding is not None and binding.service == SERVICE_NET:
            return await self.get_minfo_net(binding, song, song_type)
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
            result = await self._run(binding, factory, notify_slow=notify_slow)
        except PlayerNotAuthorizedError as e:
            # 单曲只有 OAuth Bearer 形态，无回退路径：未覆盖用户给专项文案
            # （minfo 变体 = 收口常量把「查询成绩」替换为「查询单曲成绩」）
            raise UserScoreError(
                _DF_OAUTH_HINT.replace("查询成绩", "查询单曲成绩")
            ) from e
        if not has_ident or result is None:
            return result
        return result if _has_scores(result.scores, song_type) else None

    async def get_plates(
        self, binding: UserBinding, plate: str, notify_slow=None
    ) -> MaimaiPlates:
        """牌子进度（判牌语义在 maimai-py 内置）：全量成绩，凭据按绑定标志路由。

        失败（未授权 / token 已在水鱼侧重置）由 :meth:`_run_full` 单条可行动
        文案收口。
        """
        self._guard_cn(binding)
        await song_service.ensure_loaded()

        def make(get_ident):
            return lambda: client.plates(
                get_ident(), plate, provider=binding_service.provider(binding)
            )

        return await self._run_full(binding, make, notify_slow)


score_service = ScoreService()
"""成绩服务单例。"""
