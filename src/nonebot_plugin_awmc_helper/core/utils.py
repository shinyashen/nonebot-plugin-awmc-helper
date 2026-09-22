"""核心层通用工具：分页、统一异常兜底装饰器。"""

from typing import Any, TypeVar
from functools import wraps
from collections.abc import Callable, Awaitable

from nonebot import logger
from nonebot.exception import MatcherException
from nonebot_plugin_alconna.uniseg import UniMessage

T = TypeVar("T")


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

    async def send_wrapper(bot, target, message):
        private = getattr(target, "message_type", None) == "private"
        first = message[0] if len(message) else None
        if private:
            if isinstance(first, Text) and first.text.startswith(" "):
                first.text = first.text.lstrip(" ")
            return message
        if (
            len(message) >= 2
            and isinstance(first, At)
            and isinstance(message[1], Text)
            and not message[1].text.startswith(" ")
        ):
            message.insert(1, Text(" "))
        return message

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
