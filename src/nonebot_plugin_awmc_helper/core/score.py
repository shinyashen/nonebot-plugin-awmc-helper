"""成绩服务：b50 / 全量成绩 / minfo / 牌子 / 玩家信息 的薄封装与异常映射。

所有 maimai-py 异常统一在 core 捕获转用户文案（规划 §5.4），
子插件只收 :class:`UserScoreError`。
"""

import httpx
from maimai_py import (
    Song,
    PlayerSong,
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

from .songs import song_service
from .client import client, divingfish_provider
from .binding import UserBinding, BindingError, binding_service


class UserScoreError(Exception):
    """查分业务错误，message 为面向用户的文案。"""


def _map_error(e: Exception) -> UserScoreError:
    """maimai-py 异常 → 用户文案（统一映射表，含已知坑）。"""
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

    async def get_player(self, binding: UserBinding):
        try:
            return await client.players(
                binding_service.identifier(binding),
                provider=binding_service.provider(binding),
            )
        except BindingError as e:
            raise UserScoreError(str(e)) from e
        except MaimaiPyError as e:
            raise _map_error(e) from e
        except httpx.RequestError as e:
            raise _map_error(e) from e

    async def get_b50(self, binding: UserBinding) -> MaimaiScores:
        """B50（b35 + b15 与总 rating）。"""
        try:
            return await client.bests(
                binding_service.identifier(binding),
                provider=binding_service.provider(binding),
            )
        except BindingError as e:
            raise UserScoreError(str(e)) from e
        except MaimaiPyError as e:
            raise _map_error(e) from e
        except httpx.RequestError as e:
            raise _map_error(e) from e

    async def get_scores_all(self, binding: UserBinding) -> MaimaiScores:
        """全量成绩（牌子 / ap50 / 表格的基础）。"""
        try:
            return await client.scores(
                binding_service.identifier(binding),
                provider=binding_service.provider(binding),
            )
        except BindingError as e:
            raise UserScoreError(str(e)) from e
        except MaimaiPyError as e:
            raise _map_error(e) from e
        except httpx.RequestError as e:
            raise _map_error(e) from e

    async def get_b50_by_username(
        self, username: str
    ) -> tuple["DivingFishPlayer", MaimaiScores]:
        """水鱼公开代查：b50 <水鱼用户名>（无需绑定）。"""
        ident = PlayerIdentifier(username=username)
        try:
            player = await client.players(ident, provider=divingfish_provider)
            bests = await client.bests(ident, provider=divingfish_provider)
        except MaimaiPyError as e:
            raise _map_error(e) from e
        except httpx.RequestError as e:
            raise _map_error(e) from e
        return player, bests  # type: ignore[return-value]

    async def get_minfo(
        self, song: Song, binding: UserBinding | None
    ) -> PlayerSong | None:
        """单曲成绩（未绑定时仅谱面信息）。"""
        ident: PlayerIdentifier | None = None
        if binding is not None:
            try:
                ident = binding_service.identifier(binding)
            except BindingError:
                ident = None
        try:
            await song_service.ensure_loaded()
            return await client.minfo(
                song,
                ident,
                provider=divingfish_provider
                if ident is None
                else binding_service.provider(binding),  # type: ignore[arg-type]
            )
        except MaimaiPyError as e:
            raise _map_error(e) from e
        except httpx.RequestError as e:
            raise _map_error(e) from e

    async def get_plates(self, binding: UserBinding, plate: str) -> MaimaiPlates:
        """牌子进度（判牌语义在 maimai-py 内置）。"""
        try:
            return await client.plates(
                binding_service.identifier(binding),
                plate,
                provider=binding_service.provider(binding),
            )
        except BindingError as e:
            raise UserScoreError(str(e)) from e
        except MaimaiPyError as e:
            raise _map_error(e) from e
        except httpx.RequestError as e:
            raise _map_error(e) from e


score_service = ScoreService()
"""成绩服务单例。"""
