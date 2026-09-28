"""核心层通用工具：分页、统一异常兜底装饰器。"""

from typing import Any, TypeVar, cast
from functools import wraps
from collections.abc import Callable, Awaitable

from nonebot import logger
from nonebot.adapters import Bot, Event, Message
from nonebot.exception import MatcherException
from nonebot_plugin_alconna.uniseg import UniMessage

T = TypeVar("T")
# 与 alconna SendWrapper 协议同款泛型（send 原类型原样返回）
_TM = TypeVar("_TM", bound=str | Message | UniMessage)


def group_id_of(session) -> str | None:
    """会话的群 id（非群聊返回 None；uninfo 语义，各子插件共用）。"""
    from nonebot_plugin_uninfo import SceneType

    if session.scene and session.scene.type == SceneType.GROUP:
        return str(session.scene.id)
    return None


def user_id_of(session) -> str:
    """会话的用户 id（字符串口径，各子插件共用）。"""
    return str(session.user.id)


def parse_page(args: str | None, default: int = 1) -> int:
    """指令参数 → 页码（缺省/非数字回默认值）。"""
    if args and str(args).isdigit():
        return int(args)
    return default


def paginate(data: list[T], page: int, per_page: int) -> tuple[list[T], int]:
    """切片分页，返回 (当前页数据, 总页数)；页码越界时返回空列表。"""
    total = max(1, -(-len(data) // per_page))
    page = max(1, page)
    start = (page - 1) * per_page
    return data[start : start + per_page], total


def _install_at_space_send_wrapper() -> None:
    """注册 alconna 发送包装器（导入期设置，全局生效）。

    - 群聊：alconna 插入 at 后，若紧邻的是文本且不以空格开头，补一个分隔空格
      （已有前导空格的文案不会重复添加）；
    - 私聊：不插 at，也就不需要空格——去掉文案的前导空格，避免私聊消息
      以空白开头；
    - 依赖 nonebot_plugin_alconna 的 current_send_wrapper 上下文，低版本缺失时
      静默跳过（退化为不带空格的行为）。
    """
    try:
        from nonebot_plugin_alconna.uniseg import At, Text
        from nonebot_plugin_alconna.uniseg.message import current_send_wrapper
    except ImportError:  # pragma: no cover
        return

    # 签名对齐 alconna 的 SendWrapper 协议（泛型 _TM），运行时 send 恒为 UniMessage
    async def send_wrapper(bot: Bot, event: Event, send: _TM) -> _TM:
        msg = cast(UniMessage, send)
        private = getattr(event, "message_type", None) == "private"
        first = msg[0] if len(msg) else None
        if private:
            if isinstance(first, Text) and first.text.startswith(" "):
                first.text = first.text.lstrip(" ")
            return send
        if (
            len(msg) >= 2
            and isinstance(first, At)
            and isinstance(msg[1], Text)
            and not msg[1].text.startswith(" ")
        ):
            msg.insert(1, Text(" "))
        return send

    current_send_wrapper.set(send_wrapper)


_install_at_space_send_wrapper()


def handle_errors(
    fallback: str = "出错了，请稍后再试或联系管理员。",
    except_with_message: tuple[type[Exception], ...] = (),
) -> Callable:
    """子插件 handler 的统一异常兜底。

    - NoneBot 的 MatcherException（Finished/Rejected/Paused/Skipped 等）
      在 matcher 控制流程中抛出，必须原样透传；
    - ``except_with_message`` 中的业务异常（如 UserScoreError）把 str(e)
      作为用户文案发送；
    - 其余异常转友好文案并记录日志，不向用户泄漏堆栈。
    """

    def decorator(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except MatcherException:
                raise
            except except_with_message as e:
                await UniMessage.text(f" {e}").finish(at_sender=True)
            except Exception:
                logger.exception("awmc-helper 处理指令时出现未捕获异常")
                await UniMessage.text(f" {fallback}").finish(at_sender=True)

        return wrapper

    return decorator


def slow_notice(text: str = " 比预期时间要长，再稍等一下…"):
    """慢查询中途一次性提示的回调工厂（须在 handler 栈内触发；发送失败静默）。

    供 ``score_service`` 的 ``notify_slow`` 参数使用：落雪令牌续期后等待
    生效超过预期（进入 10s 档）时给用户一句非技术提示。
    """

    async def notify() -> None:
        from nonebot_plugin_alconna.uniseg import UniMessage

        try:
            await UniMessage.text(text).send(at_sender=True)
        except Exception:
            pass  # 提示属锦上添花，任何发送问题都不影响查询本身

    return notify


_admin_perm = None
"""群管权限单例缓存（经 :func:`group_admin` 延迟装配，避免与 nonebot
初始化次序纠缠；不暴露模块级名字防第三方 import 到 None）。"""


def group_admin():
    """群管权限（SUPERUSER ∨ 群管/群主）。"""
    global _admin_perm
    if _admin_perm is None:
        from nonebot.permission import SUPERUSER
        from nonebot_plugin_uninfo import ADMIN

        _admin_perm = SUPERUSER | ADMIN()
    return _admin_perm


async def notify_superusers(text: str) -> None:
    """向全部 SUPERUSER 经 OneBot v11 主动私聊推送（多适配器部署其余适配器
    不覆盖；发送失败静默记 debug，不影响主流程）。"""
    from nonebot import get_driver
    from nonebot_plugin_alconna.uniseg import Target, UniMessage, SupportAdapter

    for user_id in get_driver().config.superusers:
        try:
            await UniMessage.text(text).send(
                target=Target.user(user_id, adapter=SupportAdapter.onebot11)
            )
        except Exception as e:  # 平台不支持/未连接等一律跳过
            logger.debug(f"通知发送失败（superuser={user_id}）：{e}")


async def ensure_group_admin(session, bot, event, *, feature: str) -> str:
    """「仅群聊可用 → 群管权限 → 权限不足」门禁三连（各子插件开关类 handler 共用）。

    通过返回群 id；非群聊 finish「仅群聊可用」、无权限 finish「权限不足」
    （finish 抛 MatcherException 终止 handler，不返回）。``feature`` 用于
    群聊文案（如「猜歌开关」）。
    """
    from nonebot_plugin_alconna.uniseg import UniMessage

    group_id = group_id_of(session)
    if group_id is None:
        await UniMessage.text(f" {feature}仅群聊可用").finish(at_sender=True)
        raise AssertionError("unreachable")  # finish 必抛，窄化 str | None
    if not await group_admin()(bot, event):
        await UniMessage.text(" 权限不足：仅群管理员可用").finish(at_sender=True)
    return group_id
