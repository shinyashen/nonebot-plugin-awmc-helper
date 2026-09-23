"""用户绑定服务：绑定表 CRUD、PlayerIdentifier 装配、绑定回填会话（TTL）。

与原版的最大差异（规划 §5.3）：maimai-py 不含水鱼 OAuth 设备授权——
水鱼绑定采用「用户名/QQ 公开查询 + Import-Token 凭据」两档；落雪为
「OAuth 授权码换个人 token」或「好友码 + 开发者 token」。
"""

import time
from dataclasses import dataclass

from maimai_py import PlayerIdentifier

from . import store
from .store import UserBinding
from ..config import plugin_config

SERVICE_DIVINGFISH = "divingfish"
SERVICE_LXNS = "lxns"

THEMES = ("prism_plus", "circle")

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

    def consume(self, platform: str, user_id: str) -> str | None:
        sess = self._sessions.pop((platform, user_id), None)
        return sess.kind if sess else None

    def discard(self, platform: str, user_id: str) -> None:
        self._sessions.pop((platform, user_id), None)


pending_bindings = PendingBindingStore()
"""绑定回填会话单例。"""


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

    def identifier(self, binding: UserBinding) -> PlayerIdentifier:
        """按绑定装配 maimai-py PlayerIdentifier（不可查时抛 BindingError）。"""
        # uninfo 对 OneBot v11 不填 Session.platform（该字段仅多平台适配器使用），
        # 插件层统一兜底 "unknown"——与历史迁移数据（platform="qq"）同为 QQ 号语义
        qq = int(binding.user_id) if binding.platform in ("qq", "unknown") else None
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
