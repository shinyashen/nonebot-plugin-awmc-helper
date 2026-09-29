"""查分服务门面：b50 / 全量成绩 / minfo / 牌子 / 玩家信息 / RA 排名 统一入口。

方法按绑定的数据源路由到 :mod:`core.sources` 注册表中的适配器：能力门禁
（如 NET 不支持全量成绩）由适配器基类默认实现统一产生「暂不支持」文案，
曲库视图选择经 :meth:`ScoreService.view_of`，本模块不再写任何 service 分支。
异常映射与落雪续期管线同样在 core.sources（规划 §5.4：所有 maimai-py / ext
异常在适配层捕获转用户文案，子插件只收 :class:`UserScoreError`）。

子插件与第三方 b50 变体一律从本模块导入（:data:`__all__`）。
"""

from maimai_py import (
    Song,
    SongType,
    PlayerSong,
    PlayerBests,
    MaimaiPlates,
    MaimaiScores,
)

from .calc import build_bests
from .songdb import Scope
from .binding import SERVICE_DIVINGFISH, UserBinding
from .sources import Capability, UserScoreError, source_of

__all__ = ["UserScoreError", "build_bests", "score_service"]
"""模块公开面：第三方 b50 变体（pc50 等）从 core.score 一站式导入。"""


class ScoreService:
    """查分服务门面：签名与原实现一致，实现按数据源分派到适配器。"""

    @staticmethod
    def _src(binding: UserBinding):
        return source_of(binding.service)

    # ---- 数据源能力 / 视图查询（帮助标注、插件视图切换、抓取提示用） ----

    def view_of(self, service: str) -> Scope:
        """数据源对应的曲库视图：涉及曲库视图的功能统一经此取用。"""
        return source_of(service).view

    def supports(self, service: str, cap: Capability) -> bool:
        """数据源是否声明支持某能力域（软降级/文案判断用；硬门禁走方法调用）。"""
        return cap in source_of(service).capabilities

    def needs_fetch(self, binding: UserBinding) -> bool:
        """查询前是否需要真实抓取（NET 窗口缓存过期等；handler 发提示用）。"""
        return self._src(binding).needs_fetch(binding)

    def player_profile(self, binding: UserBinding):
        """数据源玩家身份（NET 官方资料，供卡面渲染；其余源 None）。"""
        return self._src(binding).player_profile(binding)

    # ---- 查分操作（按数据源路由） ----

    async def get_player(self, binding: UserBinding, notify_slow=None):
        return await self._src(binding).get_player(binding, notify_slow)

    async def get_b50(
        self, binding: UserBinding, notify_slow=None
    ) -> "MaimaiScores | PlayerBests":
        """B50（b35 + b15 与总 rating）；NET 数据源走日服组装链路。

        两个返回类型对渲染层 duck-compatible（rating/b35/b15/scores 字段同构）。
        """
        return await self._src(binding).get_b50(binding, notify_slow)

    async def get_minfo(
        self,
        song: Song,
        binding: UserBinding | None,
        song_type: SongType | None = None,
        notify_slow=None,
    ) -> PlayerSong | None:
        """单曲成绩（未绑定时仅谱面信息，路由到水鱼公开路径）。

        已绑定且有凭据但无成绩时返回 None（= 未游玩，对齐 Hoshino 原版按条目
        id 查询的 ``MusicNotPlayError`` →「您未游玩过曲目」语义）；未绑定时成绩
        为空的 PlayerSong 原样返回，供纯谱面视图渲染。

        ``song_type``：本次查询指向的谱面类型（数字 id 形状 / 唯一命中条目的
        类型）。给出时**该类型**无成绩即算未游玩——双谱曲「标准谱有分、DX 谱
        没分」时查 DX 谱应提示未游玩，而不是画一张全「未游玩」的空卡；为 None
        时任一类型有成绩即算玩过（名称命中多条目、宴谱条目等场景）。
        """
        src = source_of(binding.service if binding is not None else SERVICE_DIVINGFISH)
        return await src.get_minfo(song, binding, song_type, notify_slow)

    async def get_scores_all(
        self, binding: UserBinding, notify_slow=None
    ) -> MaimaiScores:
        """全量成绩（牌子 / ap50 / 表格的基础）：凭据按绑定标志确定性路由。"""
        return await self._src(binding).get_scores_all(binding, notify_slow)

    async def get_plates(
        self, binding: UserBinding, plate: str, notify_slow=None
    ) -> MaimaiPlates:
        """牌子进度（判牌语义在 maimai-py 内置）。"""
        return await self._src(binding).get_plates(binding, plate, notify_slow)

    async def get_my_ranking(
        self, binding: UserBinding, notify_slow=None
    ) -> "tuple[object, int] | None":
        """RA 榜个人定位：返回 (榜内条目, 名次)；未上榜 None（仅水鱼支持）。"""
        return await self._src(binding).get_my_ranking(binding, notify_slow)

    async def get_b50_by_username(self, username: str) -> "tuple[object, MaimaiScores]":
        """水鱼公开代查：b50 <水鱼用户名>（无需绑定）。"""
        return await source_of(SERVICE_DIVINGFISH).get_b50_public(username)


score_service = ScoreService()
"""成绩服务单例。"""
