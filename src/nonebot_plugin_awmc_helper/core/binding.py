"""用户绑定服务：绑定表 CRUD、PlayerIdentifier 装配、绑定回填会话（TTL）。

与原版的最大差异（规划 §5.3）：maimai-py 不含水鱼 OAuth 设备授权——
水鱼绑定采用「用户名/QQ 公开查询 + Import-Token 凭据」两档；落雪为
「OAuth 授权码换个人 token」或「好友码 + 开发者 token」。
"""

import time
from typing import TYPE_CHECKING
from dataclasses import dataclass

from maimai_py import PlayerIdentifier

from . import store
from .store import UserBinding
from ..config import plugin_config
from ..constants import THEMES, SERVICE_DISPLAY

if TYPE_CHECKING:
    from nonebot_plugin_uninfo import Session

SERVICE_DIVINGFISH = "divingfish"
SERVICE_LXNS = "lxns"

# user_id 可作 QQ 号的平台：qq 为 Hoshino 迁移数据的历史键；OneBot v11 是
# uninfo 单平台适配器（Session.platform 恒为 None，适配器标识即平台语义）
QQ_PLATFORMS = frozenset({"qq", "OneBot V11"})


def session_keys(session: "Session") -> tuple[str, str]:
    """绑定键 (platform, user_id)：platform 优先 uninfo 的 platform（多平台
    适配器才有值），否则取适配器标识（OneBot v11 → "OneBot V11"）。

    不要用 "unknown" 兜底平台语义：uninfo 的 platform 缺失不代表平台未知，
    adapter 名才是稳定的单平台标识（对比 NB maimaidx 直接 event.user_id）。
    适配器取值必须走 ``.value``：Python ≤3.10 的 mixin 枚举 ``str()`` 返回
    "SupportAdapter.onebot11" 成员形态，3.11+ 才是 "OneBot V11" 值形态。
    """
    platform = getattr(session, "platform", None)
    if platform is None:
        adapter = getattr(session, "adapter", None)
        platform = getattr(adapter, "value", adapter)
    return str(platform or "unknown").strip(), str(session.user.id)


LXNS_PENDING_TTL = 1200  # 落雪授权码回填会话 20 分钟


class BindingError(Exception):
    """绑定相关业务错误（message 面向用户）。"""


@dataclass
class PendingSession:
    kind: str
    expires: float


class PendingBindingStore:
    """绑定回填会话表（进程内存；键 = (platform, user_id)）。

    不用 got 独占会话：群内其他指令不受影响（规划 §3.4）。
    """

    def __init__(self) -> None:
        self._sessions: dict[tuple[str, str], PendingSession] = {}

    def start(
        self, platform: str, user_id: str, kind: str, ttl: int = LXNS_PENDING_TTL
    ) -> None:
        self._sessions[(platform, user_id)] = PendingSession(
            kind=kind, expires=time.monotonic() + ttl
        )

    def is_active(self, platform: str, user_id: str, kind: str | None = None) -> bool:
        sess = self._sessions.get((platform, user_id))
        if sess is None:
            return False
        if sess.expires < time.monotonic():
            del self._sessions[(platform, user_id)]
            return False
        return kind is None or sess.kind == kind

    def discard(self, platform: str, user_id: str) -> None:
        self._sessions.pop((platform, user_id), None)


pending_bindings = PendingBindingStore()
"""绑定回填会话单例。"""


def service_display(binding: "UserBinding") -> str:
    """绑定数据源 → 查分器站点显示名（未知键原样返回，各卡面共用）。"""
    return SERVICE_DISPLAY.get(binding.service, binding.service)


class BindingService:
    """绑定表操作 + 凭据装配。"""

    async def get(self, platform: str, user_id: str) -> UserBinding | None:
        return await store.get_binding(platform, user_id)

    async def ensure(self, platform: str, user_id: str) -> UserBinding:
        """取绑定；不存在则以部署默认查分器自动创建（对齐原版 auto_create）。"""
        binding = await store.get_binding(platform, user_id)
        if binding is None:
            binding = UserBinding(
                platform=platform,
                user_id=user_id,
                service=plugin_config.awmc_default_provider,
            )
            await store.save_binding(binding)
        return binding

    async def unbind(self, platform: str, user_id: str) -> bool:
        return await store.delete_binding(platform, user_id)

    async def set_service(self, binding: UserBinding, service: str) -> None:
        """切换查分器：未配置落雪凭据时拒绝。"""
        if service == SERVICE_LXNS and not (
            binding.lxns_token or binding.lxns_friend_code
        ):
            raise BindingError("尚未绑定落雪查分器，无法切换数据源")
        binding.service = service
        await store.save_binding(binding)

    async def set_theme(self, binding: UserBinding, theme: str) -> None:
        if theme not in THEMES:
            raise BindingError("主题参数错误：0 = prism_plus，1 = circle")
        binding.theme = theme
        await store.save_binding(binding)

    def qq_of(self, binding: UserBinding) -> int | None:
        """QQ 号（头像回退等展示用途）：仅 QQ 系平台的 user_id 可作 QQ 号。"""
        if binding.platform in QQ_PLATFORMS and binding.user_id.isdigit():
            return int(binding.user_id)
        return None

    def identifier(self, binding: UserBinding) -> PlayerIdentifier:
        """按绑定装配 maimai-py PlayerIdentifier（不可查时抛 BindingError）。"""
        qq = self.qq_of(binding)
        if binding.service == SERVICE_DIVINGFISH:
            ident = PlayerIdentifier(
                qq=qq,
                username=binding.divingfish_username,
                credentials=binding.divingfish_import_token or None,
            )
        else:
            ident = PlayerIdentifier(
                friend_code=binding.lxns_friend_code,
                qq=qq,
                credentials=binding.lxns_token or None,
            )
        if ident._is_empty():
            raise BindingError(
                "尚未绑定查分器，请先使用「绑定水鱼」或「绑定落雪」进行绑定"
            )
        return ident

    def identifier_or_none(self, binding: UserBinding) -> PlayerIdentifier | None:
        """同 identifier，但无凭据时返回 None（不抛错）。"""
        try:
            return self.identifier(binding)
        except BindingError:
            return None

    async def refresh_lxns_if_expired(
        self, binding: UserBinding, exc: Exception
    ) -> bool:
        """落雪个人 token 过期时用 refresh_token 续期并落库（对齐原版
        maimaiDX 的 _on_unauthorized 自动刷新），成功返回 True。

        仅当「落雪源 + 带 token + 有 refresh_token + OAuth 已配置」且异常为
        maimai-py 的 401 合流异常（InvalidPlayerIdentifierError，落雪 user API
        未授权与玩家不存在在其内部合流）时才尝试；调用方刷新成功后需重试原查询。
        """
        from maimai_py import InvalidPlayerIdentifierError

        from .ext import lxns as lxns_ext

        if not isinstance(exc, InvalidPlayerIdentifierError):
            return False
        if binding.service != SERVICE_LXNS or not binding.lxns_token:
            return False
        if not binding.lxns_refresh_token or not lxns_ext.oauth_configured():
            return False
        try:
            token = await lxns_ext.refresh_token(binding.lxns_refresh_token)
        except Exception:
            return False
        binding.lxns_token = token.access_token
        if token.refresh_token:
            binding.lxns_refresh_token = token.refresh_token
        if token.friend_code:
            binding.lxns_friend_code = token.friend_code
        await store.save_binding(binding)
        return True

    def provider(self, binding: UserBinding):
        """按绑定取数据源 provider（与 core.client 的单例同源）。"""
        from .client import lxns_provider, divingfish_provider

        return lxns_provider if binding.service == SERVICE_LXNS else divingfish_provider

    async def bind_divingfish_username(
        self, binding: UserBinding, username: str
    ) -> None:
        binding.service = SERVICE_DIVINGFISH
        binding.divingfish_username = username
        await store.save_binding(binding)

    async def bind_divingfish_token(self, binding: UserBinding, token: str) -> None:
        binding.service = SERVICE_DIVINGFISH
        binding.divingfish_import_token = token
        await store.save_binding(binding)

    async def bind_lxns(
        self, binding: UserBinding, *, token: str | None, friend_code: int | None
    ) -> None:
        binding.service = SERVICE_LXNS
        if token is not None:
            binding.lxns_token = token
        if friend_code is not None:
            binding.lxns_friend_code = friend_code
        await store.save_binding(binding)


binding_service = BindingService()
"""绑定服务单例。"""
