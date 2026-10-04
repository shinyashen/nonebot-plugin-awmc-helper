"""用户绑定服务：绑定表 CRUD、PlayerIdentifier 装配、绑定回填会话（TTL）。

与原版的差异（规划 §5.3）：maimai-py 不含水鱼 OAuth 设备授权（由
core/ext/divingfish 直连补齐）——水鱼绑定三档：「绑定水鱼」OAuth 设备码
授权、「绑定水鱼用户名」用户名/QQ 公开查询、「绑定水鱼token」Import-Token；
落雪为「OAuth 授权码换个人 token」或「好友码 + 开发者 token」。
"""

import re
import time
import asyncio
import hashlib
from typing import TYPE_CHECKING, ClassVar
from dataclasses import dataclass

from maimai_py import PlayerIdentifier
from nonebot.log import logger
from sqlalchemy.exc import IntegrityError
from nonebot.adapters import Event

from . import store
from .store import UserBinding
from ..config import plugin_config

# SERVICE_* 单一事实源在 constants.py（store 模型默认值也引用），此处
# 再导出保持既有 `from .binding import SERVICE_*` 消费方不变
from ..constants import (
    THEMES,
    SERVICE_NET,
    SERVICE_LXNS,
    SERVICE_DISPLAY,
    SERVICE_DIVINGFISH,
)
from .session_store import TtlSession, TtlSessionStore

if TYPE_CHECKING:
    from nonebot_plugin_uninfo import Session

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


def extract_at_target(event: "Event | None") -> str | None:
    """消息中被 @ 的目标用户（代查），仅取第一个非全体 at。

    段形状按 OneBot v11（``seg.type == "at"``）判定；其他适配器的 at 段
    类型名不同时会静默退化为查自己，属已知限制。
    """
    message = getattr(event, "message", None)
    if message is None:
        return None
    for seg in message:
        if seg.type == "at" and str(seg.data.get("qq")) != "all":
            return str(seg.data["qq"])
    return None


async def resolve_session_query(
    session: "Session", event: "Event | None"
) -> tuple[UserBinding | None, str | None]:
    """会话级代查目标解析（score_query b50/minfo 等按人查分入口共用）。

    解析查询目标（@目标 或 发送者）→ (绑定或 None, at 目标)；代查链见
    :meth:`BindingService.resolve_query`。发送者路径 ensure 自动建行。
    """
    at_target = extract_at_target(event)
    binding = await binding_service.resolve_query(
        session_keys(session)[0], str(session.user.id), at_target
    )
    return binding, at_target


_UNBOUND_SELF_HINT = (
    " 尚未绑定查分器，请先使用「绑定水鱼」「绑定落雪」或「绑定日服」进行绑定"
)
"""查询入口自身未绑定的统一引导文案（resolve_query_binding 单源）。"""


async def resolve_query_binding(
    session: "Session",
    event: "Event | None",
    *,
    unbound_hint: str | None = None,
) -> "tuple[UserBinding, str | None]":
    """查询目标解析 + 凭据门禁：返回 (绑定, at 目标)。

    「查询他人成绩」功能点的单源（2026-09-30 定案）：:func:`resolve_session_query`
    之上加凭据门禁——目标（代查或自身）无可用凭据时按分支给引导文案并终止：
    代查（有 at）用 ``unbound_hint``（缺省「对方尚未绑定查分器，无法代查」，
    b50 附公开代查指引），自身统一 :data:`_UNBOUND_SELF_HINT`。
    at 未绑定 QQ 用户回退的临时水鱼绑定凭 QQ 可查（``has_usable_credentials``
    为真），不在此拦截，由各查询路径的凭据语义自然收口。

    调用方需要 at 目标 id（如 pc 数据按人取）时用本函数；只要绑定时用
    :func:`query_binding` / :class:`SessionQueryBinding`。
    """
    binding, at_target = await resolve_session_query(session, event)
    if binding is not None and binding_service.has_usable_credentials(binding):
        return binding, at_target
    from nonebot_plugin_alconna.uniseg import UniMessage

    if at_target is not None:
        await UniMessage.text(
            f" {unbound_hint or '对方尚未绑定查分器，无法代查'}"
        ).finish(at_sender=True)
    await UniMessage.text(_UNBOUND_SELF_HINT).finish(at_sender=True)


async def query_binding(
    session: "Session", event: "Event | None", *, unbound_hint: str | None = None
) -> UserBinding:
    """:func:`resolve_query_binding` 的只要绑定形态（handler 体内按需调用，
    供 b50 这类「带参数时跳过绑定解析」的指令避免无谓建行）。"""
    binding, _ = await resolve_query_binding(session, event, unbound_hint=unbound_hint)
    return binding


def SessionQueryBinding(unbound_hint: str | None = None):
    """handler DI 依赖工厂：会话查询绑定（支持 @ 代查）。

    用法 ``binding: UserBinding = SessionQueryBinding()``；= :func:`query_binding`
    的 Depends 形态（:func:`resolve_session_query` + 凭据门禁），与
    :func:`SessionBinding`（仅发送者、恒建行）并列——查询他人成绩类指令
    （完成表/进度/牌子/分数列表等）用它，「我要上N分」等第一人称指令与
    个人设置类维持 :func:`SessionBinding`。
    """

    from nonebot.params import Depends
    from nonebot_plugin_uninfo import UniSession

    async def _get(session=UniSession(), event: "Event | None" = None):
        return await query_binding(session, event, unbound_hint=unbound_hint)

    return Depends(_get)


_AT_SEG = r"\s*\[CQ:at,[^\]]*\]"
r"""单个 at 段（段前空白一并吞掉）。

实测形状（2026-09-30 服务器日志）：QQ 客户端在 @ 之后自动留一个空格，
消息串以「at 段 + 尾随空格」结束（``…[CQ:at,qq=..,name=..] ``）；段数据可带
``name=`` 键。``[^\]]*`` 对段内数据整体吞（nickname 的 ``]`` 已被 CQ 转义）。
"""

_AT_RUN = rf"(?:{_AT_SEG})*\s*"
r"""消息串中可容忍的 at 段连排（段间/段后空白一并吞掉），由 :data:`_AT_SEG`
组合——段形状单源，``at_tolerant`` 锚点与 ``strip_at_segments`` 共用。"""


def strip_at_segments(text: str) -> str:
    """剥除消息串中的 at 段（条件文本清洗用）：段形状与
    :func:`extract_at_target` 同口径（OneBot v11），段间/前后空白一并吞掉。

    用于「at 夹在条件词中间」的形态（如「东方 @某人 50」）——此时 at 段
    落入正则捕获的条件串，昵称里的条件字（雪/神/将…）会污染解析；头部/
    尾部 at 由 :func:`at_tolerant` 锚点吞掉，不经此函数。
    """
    return re.sub(_AT_SEG, "", text)


def at_tolerant(pattern: str) -> str:
    """on_regex 指令的 @ 代查容忍包装：允许消息串中夹带 at 段。

    nonebot 的 regex 规则匹配**含 CQ 码的完整消息串**（``str(msg)``），
    ``^$`` 锚定指令会被前后缀 at 段卡死（「13fc完成表 @某人」完全不触发）。
    本包装把 at 段容忍**拼接进锚点内侧**（``^``/``$`` 由调用方模式自带），
    捕获组不受影响（容忍组非捕获）；at 段形状按 OneBot v11（与
    :func:`extract_at_target` 同口径）。仅用于已接入 @ 代查的 on_regex 指令。
    """
    if pattern.startswith("^"):
        pattern = "^" + _AT_RUN + pattern[1:]
    if pattern.endswith("$"):
        pattern = pattern[:-1] + _AT_RUN + "$"
    return pattern


def _identifier_is_empty(ident: "PlayerIdentifier") -> bool:
    """PlayerIdentifier 是否无任何可用键。maimai_py 私有方法（
    ``PlayerIdentifier._is_empty``）不去依赖，本地等价实现：字段清单与
    maimai_py models.py 同步（qq/username/friend_code/credentials/ref/sub
    全 None 即空），上游加字段时需跟进。"""
    return all(
        v is None
        for v in (
            ident.qq,
            ident.username,
            ident.friend_code,
            ident.credentials,
            ident.ref,
            ident.sub,
        )
    )


def SessionBinding():
    """handler DI 依赖工厂：会话绑定，无则按部署默认查分器自动创建。

    用法 ``binding: UserBinding = SessionBinding()``（nonebot.params.Depends
    惯用大驼峰工厂名）；= ``binding_service.ensure(*session_keys(session))``
    的 Depends 形态，session 由 DI 注入，各子插件查询类 handler 共用。
    """

    from nonebot_plugin_uninfo import UniSession

    async def _get(session=UniSession()):
        return await binding_service.ensure(*session_keys(session))

    from nonebot.params import Depends

    return Depends(_get)


LXNS_PENDING_TTL = 90  # 落雪授权码回填会话无操作超时（秒）


class BindingError(Exception):
    """绑定相关业务错误（message 面向用户）。"""


@dataclass
class PendingSession(TtlSession):
    kind: str


class PendingBindingStore:
    """绑定回填会话表（进程内存；键 = (platform, user_id)）。

    不用 got 独占会话：群内其他指令不受影响（规划 §3.4）。
    超时会话短暂留在 ``_expired``（``EXPIRED_HINT_WINDOW`` 秒内可查），
    供拦截器对超时后仍发码的用户给出重发指引，避免静默无响应。

    ⚠️ 每键单槽（``start`` 互踢）：落雪授权码与水鱼确认码形态同构，拦截
    规则靠 kind 区分路由——正确性依赖「同一用户同时至多一个待回填会话」
    这一不变量；若将来放宽为多会话，拦截规则需重审。
    """

    EXPIRED_HINT_WINDOW = 300

    def __init__(self) -> None:
        self._sessions: TtlSessionStore[tuple[str, str], PendingSession] = (
            TtlSessionStore()
        )
        self._expired: dict[tuple[str, str], tuple[str, float]] = {}

    def start(
        self, platform: str, user_id: str, kind: str, ttl: int = LXNS_PENDING_TTL
    ) -> None:
        now = time.monotonic()
        self._sessions.start(
            (platform, user_id),
            PendingSession(kind=kind, expire_at=now + ttl),
        )
        self._expired.pop((platform, user_id), None)
        # 顺带清扫提示窗外的副表残留（原仅 expired_recently 同键命中才删，
        # 再未触发同键的过期条目会无限滞留；新开回填会话是天然清扫时机）
        stale = [
            key
            for key, (_kind, at) in self._expired.items()
            if now - at > self.EXPIRED_HINT_WINDOW
        ]
        for key in stale:
            del self._expired[key]

    def any_active(self) -> bool:
        """是否可能存在待回填会话（O(1) 空表短路，透传内表判定）。

        供回填拦截 rule 先行短路：无任何会话时免走 uninfo Session 构造
        （L-29）；非空表含过期残留时 True，精确判定走 :meth:`is_active`。
        """
        return self._sessions.any_active()

    def is_active(self, platform: str, user_id: str, kind: str | None = None) -> bool:
        sess, expired = self._sessions.take((platform, user_id))
        if sess is None:
            return False
        if expired:
            # 过期转提示窗副表（超时后仍发码给重发指引）
            self._expired[(platform, user_id)] = (sess.kind, time.monotonic())
            return False
        self._sessions.start((platform, user_id), sess)
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
        self._sessions.discard((platform, user_id))
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
    # 各用户最近一次续期成功时刻（monotonic）：短窗内的续期请求不再重放
    # grant——预检刚刷过的令牌随即 401（落雪侧生效延迟，Q43）会再触发
    # 401 驱动续期，重放 grant 只会白转一次 rt、把生效窗口往后推（Q45
    # 观察到的「白白轮换加剧踩踏」）。按用户键增长，进程内有界。
    _lxns_refresh_at: ClassVar[dict[tuple[str, str], float]] = {}

    # 预检余量：exp 到秒即 401 无宽限（Q43 受控实验），margin 只吸收本地
    # 时钟偏差与「检查到实际发起调用」的耗时；生效延迟由 401 阶梯兜底
    LXNS_PREFLIGHT_MARGIN = 60
    # 视为「刚续期过」的短窗：覆盖 401 阶梯全程（0/5/10s）+ 一次请求余量
    LXNS_RECENT_REFRESH_WINDOW = 60.0

    def _lxns_refresh_lock(self, platform: str, user_id: str) -> asyncio.Lock:
        return self._lxns_refresh_locks.setdefault((platform, user_id), asyncio.Lock())

    async def get(self, platform: str, user_id: str) -> UserBinding | None:
        return await store.get_binding(platform, user_id)

    async def ensure(self, platform: str, user_id: str) -> UserBinding:
        """取绑定；不存在则以部署默认查分器自动创建（对齐原版 auto_create）。

        并发首绑定双 INSERT 竞态：两协程同时 miss 后各自 INSERT，后 commit 方
        撞主键唯一约束——捕 IntegrityError 重读返回既有行（两方装配内容一致，
        均为部署默认查分器）；重读仍无行则原样上抛（非竞态异常不吞）。
        """
        binding = await store.get_binding(platform, user_id)
        if binding is None:
            binding = UserBinding(
                platform=platform,
                user_id=user_id,
                service=plugin_config.awmc_default_provider,
            )
            try:
                await store.save_binding(binding)
            except IntegrityError:
                binding = await store.get_binding(platform, user_id)
                if binding is None:
                    raise
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
        （score-updater 对 service=net/lxns 且已完成 OAuth 授权的用户同样凭
        本摘要装配水鱼导分目标——2026-09-28 写路径强制 OAuth 后的主要用途）；
        调用方需要 service 语义时自行先行判断。
        ⚠️ 本摘要可从公开标识（QQ/用户名）派生，**不构成已授权证据**：存量
        「仅 QQ」行的 consent 至多只读（补齐快照不含 write）、普遍缺失，
        拿去写入必败——导分等写路径须以 ``divingfish_oauth`` 标志为准判定
        可写（2026-09-28 二次修订，修复仅 QQ 行被误当凭据装配导致整链失败）。
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
        路径、忽略 credentials。「用户名 + credentials（ref: subject）」组合
        上游 1.6.0 起可安全使用：带 subject 前缀的 credentials 字符串被
        ``DivingFishProvider._oauth_subject`` 原样透传为 OAuth subject；无
        前缀的 credentials（如 Import-Token）则按服务端密码/令牌登录语义
        处理，全量成绩一律走 :meth:`full_identifier`（Q50 确定性路由）。
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
        if _identifier_is_empty(ident):
            if (
                binding.service == SERVICE_DIVINGFISH
                and binding.divingfish_import_token
            ):
                # 仅绑 Import-Token 且非 QQ 平台：全量可查，但 b50/单曲无公开键
                raise BindingError(
                    "水鱼 b50/单曲查询需要用户名或 QQ 号：请使用"
                    "「绑定水鱼用户名 <用户名>」补充绑定，"
                    "或使用「b50 <水鱼用户名>」查询"
                )
            raise BindingError(
                "尚未绑定查分器，请先使用「绑定水鱼」或「绑定落雪」进行绑定"
            )
        return ident

    def full_identifier(self, binding: UserBinding) -> PlayerIdentifier:
        """装配**全量成绩**查询键（scores/plates 等 records 类查询）。

        10-01 后定稿（Q50）：按绑定标志**确定性路由**，零换票浪费、零回落阶梯。
        水鱼侧 subject（OAuth ref 摘要）与 Import-Token 并存时的优先级路由表：

        - ``divingfish_oauth=True``（完成过设备码授权，consent 在手）：
          **subject ＞ token**——OAuth subject 全量/单曲/写全凭据，token 死了
          也不受影响（分支 1 短路，不走 token）；
        - ``divingfish_oauth=False``：**token ＞ subject**——10-01 后 token 仅剩
          全量只读一项能力，纯 token 用户直走（实测 27/28 存活），不做无谓的
          换票尝试（分支 2）；
        - 两侧都缺：subject 尝试——仅 QQ 档的水鱼迁移快照或然命中（实测命中
          率低），败则由 score 层单条可行动文案收口（分支 3）。

        不做跨凭据回落：token 已重置（400「导入token有误」）与未授权
        （consent_required）都是「重新授权 / 换绑 token」的文案场景。
        b50/单曲公开查询不经本方法（:meth:`identifier`）。落雪与公开键相同。
        """
        if binding.service == SERVICE_NET:
            raise BindingError(NET_UNSUPPORTED_HINT)
        if binding.service == SERVICE_DIVINGFISH:
            qq = self.qq_of(binding)
            subject = self.divingfish_subject(binding)
            if binding.divingfish_oauth and subject:
                return PlayerIdentifier(qq=qq, credentials=subject)
            if binding.divingfish_import_token:
                return PlayerIdentifier(
                    qq=qq, credentials=binding.divingfish_import_token
                )
            if subject:
                return PlayerIdentifier(qq=qq, credentials=subject)
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
        短窗防重放：刚续期成功后 ``LXNS_RECENT_REFRESH_WINDOW`` 内的再续期
        请求直接按 ``"refreshed"`` 返回、不重放 grant（预检与 401 驱动双入口
        汇聚时的防白转守卫，见 :attr:`_lxns_refresh_at` 注）。
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
            recent = self._lxns_refresh_at.get((binding.platform, binding.user_id))
            if (
                recent is not None
                and time.monotonic() - recent < self.LXNS_RECENT_REFRESH_WINDOW
            ):
                # 刚续期过：库里 token 就是刚签发的（15 分钟内有效），按已
                # 续期处理让调用方走重试阶梯等落雪侧生效即可
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
                # 暂时性失败按 skip 回原错误处理，但留 debug 痕迹供诊断
                logger.debug(
                    f"落雪 token 续期暂时性失败"
                    f"（{binding.platform}:{binding.user_id}）",
                    exc_info=True,
                )
                return "skip"
            binding.lxns_token = token.access_token
            if token.refresh_token:
                binding.lxns_refresh_token = token.refresh_token
            if token.friend_code:
                binding.lxns_friend_code = token.friend_code
            await store.save_binding(binding)
            self._lxns_refresh_at[(binding.platform, binding.user_id)] = (
                time.monotonic()
            )
            return "refreshed"

    async def preflight_lxns(self, binding: UserBinding) -> None:
        """落雪 token 主动续期预检（查询/导分入口在装配凭据前调用）。

        JWT ``exp`` 已过或临近（:data:`LXNS_PREFLIGHT_MARGIN`）就先续期，
        省掉闲置超期后首查的必败 401（exp 到秒即失效，Q43 受控实验）；
        新令牌的落雪侧生效延迟不由本方法解决，仍由调用方的 401 重试阶梯
        兜底。best-effort：无凭据、解码失败（非 JWT，回退 401 驱动链路）、
        续期失败（旧 token 可能仍在有效期内）一律静默返回，不得使查询失败。
        """
        from .ext import lxns as lxns_ext

        if not binding.lxns_token:
            return
        exp = lxns_ext.token_expiry(binding.lxns_token)
        if exp is None or time.time() < exp - self.LXNS_PREFLIGHT_MARGIN:
            return
        try:
            await self.refresh_lxns(binding)
        except Exception:
            logger.debug("落雪 token 预检续期失败（忽略，走原链路）", exc_info=True)

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
            # 有效，自动续期（refresh_lxns）全靠它（2026-09-26
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
