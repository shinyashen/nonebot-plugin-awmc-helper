"""柚子别名服务直连：申请/投票/进行中投票 + SSE 常驻推送。

maimai-py 的 YuzuProvider 只有别名读取，本模块补齐写接口与推送，
端点语义对齐原版 maimaiDX（``core/clients/yuzuchan`` + ``core/alias_sse_push``）。
"""

import json
import asyncio
from dataclasses import field, dataclass
from collections.abc import Callable, Awaitable, AsyncIterator

import httpx
from nonebot import logger

from . import ExtError, ExtNetworkError, get_client
from ..http import create_smart_client
from ...config import plugin_config

YUZU_DOMAIN_MOE = "https://www.yuzuchan.moe"
YUZU_DOMAIN_CN = "https://www.yuzuchan.cn"
"""柚子双域：.moe 主域 / .cn 中转域（awmc_yuzu_proxy 时别名 API 走 CN）。"""

BASE_URL_MOE = f"{YUZU_DOMAIN_MOE}/api/v2"
BASE_URL_CN = f"{YUZU_DOMAIN_CN}/api/v2"
VOTE_URL = f"{YUZU_DOMAIN_MOE}/vote"
"""投票详情页（推送文案附链接）。"""

SSE_RECONNECT_DELAY = 3.0
SSE_RECONNECT_DELAY_MAX = 60.0


def base_url() -> str:
    return BASE_URL_CN if plugin_config.awmc_yuzu_proxy else BASE_URL_MOE


# ---------------------------------------------------------------------------
# 响应模型（对齐原版 yuzuchan models）
# ---------------------------------------------------------------------------


@dataclass
class AliasVote:
    """一条进行中的别名投票。"""

    song_id: int
    apply_alias: str
    tag: str
    agree_votes: int = 0
    votes: int = 0


@dataclass
class AliasPush:
    """SSE 推送的别名申请事件（type=Apply）。"""

    type: str
    status: list[AliasVote] = field(default_factory=list)


@dataclass
class ServerAlias:
    """别名服务器上某曲目已收录的别名。"""

    song_id: int
    name: str
    alias: list[str] = field(default_factory=list)

    def has(self, alias: str) -> bool:
        return alias.lower() in (a.lower() for a in self.alias)


@dataclass
class ApplySongs:
    """按名称查申请记录的结果：可能命中投票（进行中）或已有别名。"""

    type: str  # ongoing / success / ...
    data: list[AliasVote | ServerAlias] = field(default_factory=list)

    @property
    def votes(self) -> list[AliasVote]:
        return [x for x in self.data if isinstance(x, AliasVote)]


def _check(resp: httpx.Response) -> dict | list:
    if 200 <= resp.status_code < 300:
        try:
            return resp.json()
        except ValueError as e:
            raise ExtError("柚子接口返回了无效数据") from e
    if 400 <= resp.status_code < 500:
        try:
            data = resp.json()
        except ValueError:
            data = {}
        message = data.get("message") if isinstance(data, dict) else None
        raise ExtError(str(message or f"柚子接口错误（HTTP {resp.status_code}）"))
    raise ExtError(f"柚子接口服务异常（HTTP {resp.status_code}）")


async def _request(method: str, url: str, **kwargs) -> dict | list:
    """请求 + _check 收口：网络异常包装 ExtNetworkError（上层只 catch ExtError，
    裸 httpx 异常会被记成未捕获而非友好提示）。"""
    try:
        resp = await get_client().request(method, url, **kwargs)
    except httpx.RequestError as e:
        raise ExtNetworkError("柚子接口网络异常，请稍后再试") from e
    return _check(resp)


def _parse_vote(d: dict) -> AliasVote:
    return AliasVote(
        song_id=int(d["song_id"]),
        apply_alias=d.get("apply_alias", ""),
        tag=d.get("tag", ""),
        agree_votes=int(d.get("agree_votes") or 0),
        votes=int(d.get("votes") or 0),
    )


class YuzuClient:
    """柚子别名 REST 客户端。"""

    async def get_status(self) -> list[AliasVote]:
        """进行中的别名投票列表。"""
        data = await _request(
            "GET",
            f"{base_url()}/aliases/maimaidx/votes",
            params={"status": "ongoing"},
        )
        if not isinstance(data, list):
            raise ExtError("柚子投票接口返回了意外结构")
        return [_parse_vote(x) for x in data]

    async def get_alias(self, song_id: int) -> ServerAlias | None:
        """查某曲目在别名服务器已收录的别名（无记录返回 None）。"""
        data = await _request(
            "GET",
            f"{base_url()}/aliases/maimaidx/aliases",
            params={"song_id": song_id},
        )
        if isinstance(data, dict) and "message" in data:
            return None
        if isinstance(data, list):
            data = data[0] if data else None
        if not isinstance(data, dict):
            return None
        return ServerAlias(
            song_id=int(data["song_id"]),
            name=data.get("name", ""),
            alias=list(data.get("alias", [])),
        )

    async def get_apply_songs(self, name: str) -> ApplySongs | None:
        """按名称查申请/收录情况（别名查询投票中提示用）。"""
        data = await _request(
            "GET",
            f"{base_url()}/aliases/maimaidx/songs",
            params={"name": name},
        )
        if not isinstance(data, dict) or "message" in data:
            return None
        items: list[AliasVote | ServerAlias] = []
        for x in data.get("data", []):
            if "tag" in x:
                items.append(_parse_vote(x))
            else:
                items.append(
                    ServerAlias(
                        song_id=int(x["song_id"]),
                        name=x.get("name", ""),
                        is_votable=bool(x.get("is_votable", False)),
                        alias=list(x.get("alias", [])),
                    )
                )
        return ApplySongs(type=str(data.get("type", "")), data=items)

    async def apply_alias(
        self, song_id: int, alias: str, user_id: str, group_id: str
    ) -> str:
        """提交别名公开申请，返回柚子提示文案。"""
        data = await _request(
            "POST",
            f"{base_url()}/aliases/maimaidx/apply",
            json={
                "song_id": song_id,
                "apply_alias": alias,
                "apply_uid": user_id,
                "group_id": group_id,
                "ws_uuid": "",  # 原版用 ws 标识；柚子接口允许空
            },
        )
        if not isinstance(data, dict):
            return "提交成功"
        return str(data.get("message", "提交成功"))

    async def agree_alias(self, tag: str, user_id: str) -> str:
        """为指定投票提交赞成票，返回柚子提示文案。"""
        data = await _request(
            "POST",
            f"{base_url()}/aliases/maimaidx/votes",
            json={"tag": tag, "agree_user": user_id},
        )
        if not isinstance(data, dict):
            return "投票成功"
        return str(data.get("message", "投票成功"))


yuzu_client = YuzuClient()
"""ext 层柚子客户端单例（仅 REST；SSE 见下方常驻协程）。"""


# ---------------------------------------------------------------------------
# SSE 常驻连接（断线指数退避 3s→60s + Last-Event-ID）
# ---------------------------------------------------------------------------


@dataclass
class SSEMessage:
    event: str
    data: str
    id: str | None = None
    retry: int | None = None


async def iter_sse(lines: AsyncIterator[str]) -> AsyncIterator[SSEMessage]:
    """把 HTTP 响应行流解析为 SSE 消息（对齐原版实现）。"""
    event = "message"
    data: list[str] = []
    event_id: str | None = None
    retry: int | None = None

    async for line in lines:
        if not line:
            if data:
                yield SSEMessage(
                    event=event, data="\n".join(data), id=event_id, retry=retry
                )
            event = "message"
            data = []
            event_id = None
            retry = None
            continue
        if line.startswith(":"):
            continue

        field, separator, value = line.partition(":")
        if separator and value.startswith(" "):
            value = value[1:]
        if field == "event":
            event = value
        elif field == "data":
            data.append(value)
        elif field == "id" and "\0" not in value:
            event_id = value
        elif field == "retry" and value.isdecimal():
            retry = int(value)

    if data:
        yield SSEMessage(event=event, data="\n".join(data), id=event_id, retry=retry)


async def run_alias_sse(on_apply: Callable[[AliasPush], Awaitable[None]]) -> None:
    """常驻协程：连接柚子 SSE，收到别名申请事件后回调 on_apply；断线退避重连。"""
    sse_url = f"{base_url()}/events"
    reconnect_delay = SSE_RECONNECT_DELAY
    last_event_id: str | None = None
    timeout = httpx.Timeout(connect=30, read=None, write=30, pool=30)
    async with create_smart_client(timeout=timeout) as session:
        while True:
            try:
                headers = {"Accept": "text/event-stream"}
                if last_event_id is not None:
                    headers["Last-Event-ID"] = last_event_id
                async with session.stream("GET", sse_url, headers=headers) as response:
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "")
                    if (
                        content_type.partition(";")[0].strip().lower()
                        != "text/event-stream"
                    ):
                        raise httpx.RemoteProtocolError(
                            f"服务器返回了非 SSE 响应: {content_type or 'unknown'}"
                        )
                    logger.info("别名推送服务器连接成功")
                    reconnect_delay = SSE_RECONNECT_DELAY
                    async for message in iter_sse(response.aiter_lines()):
                        if message.id is not None:
                            last_event_id = message.id or None
                        if message.retry is not None:
                            reconnect_delay = min(
                                max(message.retry / 1000, 0.1), SSE_RECONNECT_DELAY_MAX
                            )
                        if message.event != "alias":
                            continue
                        try:
                            payload = json.loads(message.data)
                            if payload.get("type") != "Apply":
                                continue
                            push = AliasPush(
                                type="Apply",
                                status=[
                                    _parse_vote(x) for x in payload.get("status", [])
                                ],
                            )
                            await on_apply(push)
                        except ValueError as e:
                            logger.warning(f"收到无效的别名推送事件: {e}")
                        except Exception:
                            logger.exception("处理别名推送事件失败")
            except httpx.HTTPError as e:
                logger.warning(
                    f"别名推送服务器连接异常: {e}，将在 {reconnect_delay:g} 秒后重连"
                )
            # CancelledError 继承 BaseException，不会被下方 Exception 捕获，直接外传
            except Exception:
                logger.exception("别名推送服务器连接失败，稍后重试")

            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, SSE_RECONNECT_DELAY_MAX)


_push_task: asyncio.Task | None = None


def start_alias_push(on_apply: Callable[[AliasPush], Awaitable[None]]) -> None:
    """启动 SSE 常驻任务（已有任务在跑则先取消替换）。"""
    global _push_task
    if _push_task is not None and not _push_task.done():
        _push_task.cancel()
    _push_task = asyncio.create_task(run_alias_sse(on_apply))
