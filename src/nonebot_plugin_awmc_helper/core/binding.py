"""用户绑定服务：绑定表 CRUD、PlayerIdentifier 装配、绑定回填会话（TTL）。

与原版的最大差异（规划 §5.3）：maimai-py 不含水鱼 OAuth 设备授权——
水鱼绑定采用「用户名/QQ 公开查询 + Import-Token 凭据」两档；落雪为
「OAuth 授权码换个人 token」或「好友码 + 开发者 token」。
"""

import time
import asyncio
import hashlib
from typing import TYPE_CHECKING, ClassVar
from dataclasses import dataclass

from maimai_py import PlayerIdentifier
from nonebot.log import logger

from . import store
from .store import UserBinding
from ..config import plugin_config
from ..constants import THEMES, SERVICE_DISPLAY

if TYPE_CHECKING:
    from nonebot_plugin_uninfo import Session

SERVICE_DIVINGFISH = "divingfish"
SERVICE_LXNS = "lxns"
SERVICE_NET = "net"  # 日服 maimai でらっくす NET（官方站直连，凭据 = SEGA ID + 密码）

# NET 数据源仅覆盖 b50；其余指令在 score/binding 层统一拦截
NET_UNSUPPORTED_HINT = "日服数据源（NET）暂不支持该指令，敬请期待后续版本"

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


LXNS_PENDING_TTL = 90  # 落雪授权码回填会话无操作超时（秒）


class BindingError(Exception):
    """绑定相关业务错误（message 面向用户）。"""


@dataclass
class PendingSession:
    kind: str
    expires: float


class PendingBindingStore:
    """绑定回填会话表（进程内存；键 = (platform, user_id)）。

    不用 got 独占会话：群内其他指令不受影响（规划 §3.4）。
    超时会话短暂留在 ``_expired``（``EXPIRED_HINT_WINDOW`` 秒内可查），
    供拦截器对超时后仍发码的用户给出重发指引，避免静默无响应。
    """

    EXPIRED_HINT_WINDOW = 300

    def __init__(self) -> None:
        self._sessions: dict[tuple[str, str], PendingSession] = {}
        self._expired: dict[tuple[str, str], tuple[str, float]] = {}

    def start(
        self, platform: str, user_id: str, kind: str, ttl: int = LXNS_PENDING_TTL
    ) -> None:
        self._sessions[(platform, user_id)] = PendingSession(
            kind=kind, expires=time.monotonic() + ttl
        )
        self._expired.pop((platform, user_id), None)

    def is_active(self, platform: str, user_id: str, kind: str | None = None) -> bool:
        sess = self._sessions.get((platform, user_id))
        if sess is None:
            return False
        if sess.expires < time.monotonic():
            del self._sessions[(platform, user_id)]
            self._expired[(platform, user_id)] = (sess.kind, time.monotonic())
            return False
        return kind is None or sess.kind == kind

    def expired_recently(
        self, platform: str, user_id: str, kind: str | None = None
    ) -> bool:
        """是否存在刚过期（提示窗口内）的回填会话，用于超时指引。"""
        entry = self._expired.get((platform, user_id))
        if entry is None:
            return False
        expired_kind, at = entry
        if time.monotonic() - at > self.EXPIRED_HINT_WINDOW:
            del self._expired[(platform, user_id)]
            return False
        return kind is None or expired_kind == kind

    def discard(self, platform: str, user_id: str) -> None:
        self._sessions.pop((platform, user_id), None)
        self._expired.pop((platform, user_id), None)


pending_bindings = PendingBindingStore()
"""绑定回填会话单例。"""


def service_display(binding: "UserBinding") -> str:
    """绑定数据源 → 查分器站点显示名（未知键原样返回，各卡面共用）。"""
    return SERVICE_DISPLAY.get(binding.service, binding.service)


class BindingService:
    """绑定表操作 + 凭据装配。"""

    # 落雪续期按用户互斥：refresh_token 一次性轮换，并发续期的后到者
    # 重放旧 rt 会 invalid_grant（落雪甚至可能据此撤销授权）
    _lxns_refresh_locks: ClassVar[dict[tuple[str, str], asyncio.Lock]] = {}

    def _lxns_refresh_lock(self, platform: str, user_id: str) -> asyncio.Lock:
        return self._lxns_refresh_locks.setdefault((platform, user_id), asyncio.Lock())

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
        """切换查分器：目标数据源未配置凭据时拒绝。"""
        if service == SERVICE_LXNS and not (
            binding.lxns_token or binding.lxns_friend_code
        ):
            raise BindingError("尚未绑定落雪查分器，无法切换数据源")
        if service == SERVICE_NET and not binding.net_sega_id:
            raise BindingError("尚未绑定日服 NET，无法切换数据源")
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

    def divingfish_subject(self, binding: UserBinding) -> str | None:
        """水鱼 OAuth subject（ref 摘要）：``sha256(f"{client_id}:{external_id}")``。

        external_id 必须与 Developer-Token 时代 ``/dev/*`` 调用**实际传入**的参数
        原值完全一致——水鱼迁移快照按原值等值建映射，差一字节即 ``consent_required``
        且与「未授权」不可区分。本插件公开键口径为「用户名 > QQ 号」，此处同样
        用户名优先、否则取 QQ 号（仅 QQ 系平台）。OAuth 未配置或无可用标识时
        返回 None（调用方回退旧路径）。

        ⚠️ 不检查 ``binding.service``：service 仅是查询偏好，水鱼凭据与它无关
        （导分插件对 service=net/lxns 的用户同样要装配水鱼 OAuth 目标，
        2026-09-28 写路径强制 OAuth 后成为主用途）；调用方需要 service 语义
        时自行先行判断。
        """
        client_id = plugin_config.awmc_divingfish_oauth_client_id
        if not (client_id and plugin_config.awmc_divingfish_oauth_client_secret):
            return None
        external_id = binding.divingfish_username or (
            str(qq) if (qq := self.qq_of(binding)) else None
        )
        if external_id is None:
            return None
        return (
            "ref:" + hashlib.sha256(f"{client_id}:{external_id}".encode()).hexdigest()
        )

    def identifier(
        self, binding: UserBinding, *, with_oauth: bool = True
    ) -> PlayerIdentifier:
        """装配 maimai-py PlayerIdentifier（**公开查询键**语义：bests/players/minfo）。

        水鱼公开键优先级：绑定用户名 > QQ 号——maimai_py 的
        ``PlayerIdentifier._as_diving_fish`` 中 qq 优先于 username，同时携带
        会在「聊天 QQ ≠ 水鱼账号 QQ」时静默查错账号，故有用户名时不带 qq。
        OAuth 已配置时把 subject 放进 credentials（``with_oauth=False`` 可取
        纯公开键，供 scores/plates 的未授权回退使用）：minfo 单曲只有 Bearer
        形态（无公开/Import-Token 形态），靠它走 OAuth；b50/players 走公开
        路径、忽略 credentials。注意「用户名 + credentials」组合**不能**进入
        maimai_py 的全量查询（会被视为密码登录），全量请用 :meth:`full_identifier`。
        Import-Token 不是公开查询键，全量成绩请用 :meth:`full_identifier`。

        NET 数据源不走 maimai-py（官方站直连），到此即说明上游未拦截，
        给出能力边界提示而非「尚未绑定」的误导文案。
        """
        if binding.service == SERVICE_NET:
            raise BindingError(NET_UNSUPPORTED_HINT)
        qq = self.qq_of(binding)
        if binding.service == SERVICE_DIVINGFISH:
            subject = self.divingfish_subject(binding) if with_oauth else None
            if binding.divingfish_username:
                ident = PlayerIdentifier(
                    username=binding.divingfish_username,
                    credentials=subject,
                )
            elif subject:
                ident = PlayerIdentifier(qq=qq, credentials=subject)
            else:
                ident = PlayerIdentifier(qq=qq)
        else:
            ident = PlayerIdentifier(
                friend_code=binding.lxns_friend_code,
                qq=qq,
                credentials=binding.lxns_token or None,
            )
        if ident._is_empty():
            if (
                binding.service == SERVICE_DIVINGFISH
                and binding.divingfish_import_token
            ):
                # 仅绑 Import-Token 且非 QQ 平台：全量可查，但 b50/单曲无公开键
                raise BindingError(
                    "水鱼 b50/单曲查询需要用户名或 QQ 号：请使用「绑定水鱼 <用户名>」"
                    "补充绑定，或使用「b50 <水鱼用户名>」查询"
                )
            raise BindingError(
                "尚未绑定查分器，请先使用「绑定水鱼」或「绑定落雪」进行绑定"
            )
        return ident

    def full_identifier(self, binding: UserBinding) -> PlayerIdentifier:
        """装配**全量成绩**查询键（scores/plates 等 records 类查询）。

        与公开键的差异在水鱼，迁移期（developer-token 2026-10-01 日落）顺序：

        1. Import-Token 最优先——maimai_py 把 ``username + credentials`` 视为
           「用户名 + 密码」登录，token 必须独占 credentials（score-updater 的
           credentials-only 写路径同理）；读/写/传分三条路径均实证可用；
        2. 无 token 时尝试 OAuth subject——补齐名单内的用户免迁移直通，
           未覆盖用户由 score 层捕获 PlayerNotAuthorizedError 回退公开键，
           给出可行动的迁移文案而非裸「未授权」；
        3. 公开键兜底（developer 端点已日落，将得到 410 → 迁移文案）。

        b50/单曲公开查询不经本方法。落雪与公开键相同
        （token 优先的语义已含在 identifier）。
        """
        if binding.service == SERVICE_NET:
            raise BindingError(NET_UNSUPPORTED_HINT)
        if binding.service == SERVICE_DIVINGFISH:
            if binding.divingfish_import_token:
                ident = PlayerIdentifier(
                    qq=self.qq_of(binding),
                    credentials=binding.divingfish_import_token,
                )
                return ident
            subject = self.divingfish_subject(binding)
            if subject:
                return PlayerIdentifier(qq=self.qq_of(binding), credentials=subject)
        return self.identifier(binding)

    def identifier_or_none(self, binding: UserBinding) -> PlayerIdentifier | None:
        """同 identifier，但无凭据时返回 None（不抛错）。"""
        try:
            return self.identifier(binding)
        except BindingError:
            return None

    def has_usable_credentials(self, binding: UserBinding) -> bool:
        """当前数据源是否已有可用凭据（NET 凭 SEGA ID，CN 源凭 PlayerIdentifier）。

        指令入口的「先绑定」检查用这个，不要用 identifier_or_none——
        它对 NET 恒为 None（NET 不走 maimai-py identifier），会误判未绑定。
        水鱼仅绑 Import-Token 也算可用（全量可查；b50/单曲公开键缺失时
        由 identifier 的专项文案引导，见上）。
        """
        if binding.service == SERVICE_NET:
            return bool(binding.net_sega_id)
        if binding.service == SERVICE_DIVINGFISH and binding.divingfish_import_token:
            return True
        return self.identifier_or_none(binding) is not None

    async def resolve_query(
        self, platform: str, sender_id: str, at_target: str | None
    ) -> UserBinding | None:
        """代查目标绑定解析（score_query b50/minfo 等按人查分入口共用）。

        - 无 at：发送者绑定，``ensure`` 自动建行（对齐原版 auto_create）；
        - 有 at：目标绑定行**只读**（代查不给对方落库建行）；目标无行且
          平台 user_id 可作 QQ 号时，回退「水鱼按 QQ 公开查询」的临时绑定
          （对齐 Hoshino：at 未绑定用户 → 默认水鱼凭 QQ 直查，无需对方
          在本 bot 绑定）；其余（非 QQ 平台）返回 None，由调用方降级。
        临时绑定不落库：仅内存对象，查询即弃。
        """
        if at_target is None:
            return await self.ensure(platform, sender_id)
        binding = await self.get(platform, at_target)
        if binding is None and platform in QQ_PLATFORMS:
            binding = UserBinding(
                platform=platform, user_id=at_target, service=SERVICE_DIVINGFISH
            )
        return binding

    async def refresh_lxns(self, binding: UserBinding) -> str:
        """落雪令牌续期核心（全部落雪链路共用：主插件查询、第三方传分、每日保活）。

        返回三态：
        - ``"refreshed"``：已续期并落库，调用方须重试原操作（重新装配凭据）；
        - ``"dead"``：refresh_token 已被落雪判定失效（invalid_grant），只能
          重新「绑定落雪」；
        - ``"skip"``：无凭据 / OAuth 未配置 / 网络等暂时性失败，调用方按原
          错误处理。

        并发安全：按 (platform, user_id) 加锁 + 锁内重读库中凭据——rt 一次性
        轮换，并发续期的后到者重放旧 rt 会 invalid_grant（落雪甚至可能据此
        撤销授权），库中 token 与进入时不同即说明别处刚刷新过，直接采用新
        凭据返回、不重放旧 rt。落库必须在锁内完成（save 的 commit 与后到者
        的锁内重读走不同连接，先释放锁会让重读赶在 commit 生效前看到旧
        token，误判「没人刷新过」而重放已轮换作废的旧 rt → invalid_grant）。
        与默认查分器 ``service`` 无关——凭据齐备即可续期（2026-09-26 放宽，
        原要求 service == lxns）。
        """
        from .ext import lxns as lxns_ext

        # service 仅是默认查分器偏好；落雪凭据有效性与其无关
        if not binding.lxns_token:
            return "skip"
        if not binding.lxns_refresh_token or not lxns_ext.oauth_configured():
            return "skip"
        entry_token = binding.lxns_token
        async with self._lxns_refresh_lock(binding.platform, binding.user_id):
            fresh = await self.get(binding.platform, binding.user_id)
            if (
                fresh is not None
                and fresh.lxns_token
                and fresh.lxns_token != entry_token
            ):
                binding.lxns_token = fresh.lxns_token
                binding.lxns_refresh_token = fresh.lxns_refresh_token
                if fresh.lxns_friend_code:
                    binding.lxns_friend_code = fresh.lxns_friend_code
                return "refreshed"
            try:
                token = await lxns_ext.refresh_token(binding.lxns_refresh_token)
            except lxns_ext.LxnsGrantError as e:
                logger.warning(
                    f"落雪 refresh_token 已失效，需重新绑定"
                    f"（{binding.platform}:{binding.user_id}）：{e}"
                )
                return "dead"
            except Exception:
                return "skip"
            binding.lxns_token = token.access_token
            if token.refresh_token:
                binding.lxns_refresh_token = token.refresh_token
            if token.friend_code:
                binding.lxns_friend_code = token.friend_code
            await store.save_binding(binding)
            return "refreshed"

    async def refresh_lxns_if_expired(
        self, binding: UserBinding, exc: Exception
    ) -> bool:
        """（按需续期兼容包装）落雪个人 token 过期时用 refresh_token 续期并
        落库（对齐原版 maimaiDX 的 _on_unauthorized 自动刷新），成功返回 True。

        仅当异常为 maimai-py 的 401 合流异常（InvalidPlayerIdentifierError，
        落雪 user API 未授权与玩家不存在在其内部合流）时才尝试；状态细分与
        并发语义见 :meth:`refresh_lxns`，新代码建议直接用后者。
        """
        from maimai_py import InvalidPlayerIdentifierError

        if not isinstance(exc, InvalidPlayerIdentifierError):
            return False
        return await self.refresh_lxns(binding) == "refreshed"

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

    async def bind_divingfish_oauth(
        self, binding: UserBinding, sub: str | None = None
    ) -> None:
        """标记水鱼 OAuth consent 已建立（设备码绑定成功后调用）。

        映射本体在授权服务器侧（ref 摘要换票），本地仅存标志与水鱼用户 ID
        （sub，诊断/展示/将来 sub: 换票备胎）；不改 service——OAuth 写凭据
        与默认查分器偏好无关。
        """
        binding.divingfish_oauth = True
        if sub is not None:
            binding.divingfish_sub = sub
        await store.save_binding(binding)

    async def bind_lxns(
        self,
        binding: UserBinding,
        *,
        token: str | None,
        friend_code: int | None,
        refresh_token: str | None = None,
    ) -> None:
        binding.service = SERVICE_LXNS
        if token is not None:
            binding.lxns_token = token
        if friend_code is not None:
            binding.lxns_friend_code = friend_code
        if refresh_token is not None:
            # OAuth 换发的 refresh_token 必须落库：access_token 仅 15 分钟
            # 有效，自动续期（refresh_lxns_if_expired）全靠它（2026-09-26
            # 修复：此前 OAuth 绑定路径从未落库，导致绑定 15 分钟后必失效）
            binding.lxns_refresh_token = refresh_token
        await store.save_binding(binding)

    async def bind_net(
        self, binding: UserBinding, *, sega_id: str, password: str
    ) -> None:
        """绑定日服 NET：SEGA ID + 密码落库（敏感级别高于查分器 token，
        bind 插件侧引导私聊操作）。"""
        binding.service = SERVICE_NET
        binding.net_sega_id = sega_id
        binding.net_password = password
        await store.save_binding(binding)


binding_service = BindingService()
"""绑定服务单例。"""
