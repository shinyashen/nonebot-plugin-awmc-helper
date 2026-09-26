"""成绩服务：b50 / 全量成绩 / minfo / 牌子 / 玩家信息 的薄封装与异常映射。

所有 maimai-py 异常统一在 core 捕获转用户文案（规划 §5.4），
子插件只收 :class:`UserScoreError`。
"""

import asyncio

import httpx
from nonebot import logger
from maimai_py import (
    Song,
    PlayerSong,
    PlayerBests,
    MaimaiPlates,
    MaimaiScores,
    MaimaiPyError,
    DivingFishPlayer,
    PlayerIdentifier,
    InvalidPlateError,
    PrivacyLimitationError,
    InvalidDeveloperTokenError,
    InvalidPlayerIdentifierError,
)

from .ext import ExtError
from .songs import song_service
from .client import client, divingfish_provider
from ..config import plugin_config
from .binding import (
    SERVICE_NET,
    NET_UNSUPPORTED_HINT,
    UserBinding,
    BindingError,
    binding_service,
)
from .ext.net import NET_ERROR_MESSAGES, NetError


class UserScoreError(Exception):
    """查分业务错误，message 为面向用户的文案。"""


def _map_error(e: Exception) -> UserScoreError:
    """maimai-py / ext 异常 → 用户文案（统一映射表，含已知坑）。"""
    if isinstance(e, NetError):
        return UserScoreError(NET_ERROR_MESSAGES.get(e.code, str(e)))
    if isinstance(e, ExtError):
        return UserScoreError(str(e))
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
        return UserScoreError("机器人开发者令牌无效或缺失，请联系管理员检查部署配置")
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

    async def _run(self, binding: UserBinding | None, make_coro):
        """统一执行 maimai-py 查询：异常映射 + 落雪 token 过期自动续期重试。

        ``make_coro`` 是无参协程工厂，重试时重新装配 identifier（token 已刷新）。
        """
        try:
            return await make_coro()
        except BindingError as e:
            raise UserScoreError(str(e)) from e
        except (MaimaiPyError, httpx.RequestError) as e:
            if binding is not None and await binding_service.refresh_lxns_if_expired(
                binding, e
            ):
                try:
                    return await make_coro()
                except (MaimaiPyError, httpx.RequestError) as e2:
                    raise _map_error(e2) from e2
            raise _map_error(e) from e

    async def get_player(self, binding: UserBinding):
        self._guard_cn(binding)
        await song_service.ensure_loaded()
        return await self._run(
            binding,
            lambda: client.players(
                binding_service.identifier(binding),
                provider=binding_service.provider(binding),
            ),
        )

    async def _get_b50_net(self, binding: UserBinding) -> PlayerBests:
        """日服 B50：NET 抓取 → JP 视图组装（冷却在 core.net_score 维护）。"""
        from .net_score import net_score_service

        platform, user_id = binding.platform, binding.user_id
        remain = net_score_service.cooldown.try_acquire(platform, user_id)
        if remain > 0:
            minutes = plugin_config.awmc_net_cooldown_minutes
            raise UserScoreError(
                f"日服查询过于频繁，请约 {remain // 60 + 1} 分钟后再试"
                f"（NET 数据源冷却 {minutes} 分钟，防止官方风控）"
            )
        try:
            records = await net_score_service.fetch_records(binding)
        except Exception:
            # 抓取失败不占冷却（成功才留 15 分钟窗口）
            net_score_service.cooldown.release(platform, user_id)
            raise
        bests = await net_score_service.build_b50(records)
        if bests.rating <= 0:
            logger.warning(
                "net-score：NET 记录组装后 rating 为 0（登录态可能已失效或页面改版）"
            )
        return bests

    async def get_b50(self, binding: UserBinding) -> "MaimaiScores | PlayerBests":
        """B50（b35 + b15 与总 rating）；NET 数据源走日服组装链路。

        两个返回类型对渲染层 duck-compatible（rating/b35/b15/scores 字段同构）。
        """
        if binding.service == SERVICE_NET:
            return await self._get_b50_net(binding)
        await song_service.ensure_loaded()
        return await self._run(
            binding,
            lambda: client.bests(
                binding_service.identifier(binding),
                provider=binding_service.provider(binding),
            ),
        )

    async def get_scores_all(self, binding: UserBinding) -> MaimaiScores:
        """全量成绩（牌子 / ap50 / 表格的基础）。"""
        self._guard_cn(binding)
        await song_service.ensure_loaded()
        return await self._run(
            binding,
            lambda: client.scores(
                binding_service.identifier(binding),
                provider=binding_service.provider(binding),
            ),
        )

    async def get_b50_by_username(
        self, username: str
    ) -> tuple["DivingFishPlayer", MaimaiScores]:
        """水鱼公开代查：b50 <水鱼用户名>（无需绑定）。"""
        ident = PlayerIdentifier(username=username)
        player, bests = await asyncio.gather(
            self._run(
                None, lambda: client.players(ident, provider=divingfish_provider)
            ),
            self._run(None, lambda: client.bests(ident, provider=divingfish_provider)),
        )
        return player, bests  # type: ignore[return-value]

    async def get_minfo(
        self, song: Song, binding: UserBinding | None
    ) -> PlayerSong | None:
        """单曲成绩（未绑定时仅谱面信息）。

        已绑定且有凭据但任何难度都无成绩时返回 None（= 未游玩，对齐 Hoshino
        原版 ``MusicNotPlayError`` →「您未游玩过曲目」语义）；未绑定时成绩为空
        的 PlayerSong 原样返回，供纯谱面视图渲染。
        """
        ident: PlayerIdentifier | None = None
        if binding is not None:
            self._guard_cn(binding)
            ident = binding_service.identifier_or_none(binding)
        await song_service.ensure_loaded()
        result = await self._run(
            binding,
            lambda: client.minfo(
                song,
                ident,
                provider=divingfish_provider
                if ident is None
                else binding_service.provider(binding),  # type: ignore[arg-type]
            ),
        )
        if ident is not None and result is not None and not result.scores:
            return None
        return result

    async def get_plates(self, binding: UserBinding, plate: str) -> MaimaiPlates:
        """牌子进度（判牌语义在 maimai-py 内置）。"""
        self._guard_cn(binding)
        await song_service.ensure_loaded()
        return await self._run(
            binding,
            lambda: client.plates(
                binding_service.identifier(binding),
                plate,
                provider=binding_service.provider(binding),
            ),
        )


score_service = ScoreService()
"""成绩服务单例。"""
