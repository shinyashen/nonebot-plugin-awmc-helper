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


def handle_errors(fallback: str = "出错了，请稍后再试或联系管理员。") -> Callable:
    """子插件 handler 的统一异常兜底。

    NoneBot 的 MatcherException（Finished/Rejected/Paused/Skipped 等）
    在 matcher 控制流程中抛出，必须原样透传；
    其余异常转友好文案并记录日志，不向用户泄漏堆栈。
    """

    def decorator(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except MatcherException:
                raise
            except Exception:
                logger.exception("awmc-helper 处理指令时出现未捕获异常")
                await UniMessage.text(fallback).finish()

        return wrapper

    return decorator
