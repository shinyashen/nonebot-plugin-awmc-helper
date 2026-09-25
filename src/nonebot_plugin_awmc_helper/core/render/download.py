"""渲染素材在线下载公共层：单次下载落盘 + 进程内并发去重。

B50 收藏品（best50）与日服曲绘（jp_cover）共用同一套行为：超时三元组、
智能代理 transport（可注入自定义 verify 上下文）、to_thread 写盘、
失败 warning 返回 False（由调用方走各自的回退链）；同键并发请求经
:class:`DownloadGate` 合并为一次下载。
"""

import ssl
import asyncio
from pathlib import Path
from collections.abc import Callable, Hashable, Awaitable

import httpx
from nonebot import logger

from ..http import build_smart_transport

_DOWNLOAD_TIMEOUT = httpx.Timeout(connect=10, read=30, write=10, pool=10)


async def download_to_file(
    url: str,
    path: Path,
    *,
    subject: str,
    verify: ssl.SSLContext | bool = True,
) -> bool:
    """GET url → 落盘 path（父目录自动创建）；成功 True，失败 warning False。"""
    transport = build_smart_transport(verify=verify)
    try:
        async with httpx.AsyncClient(
            timeout=_DOWNLOAD_TIMEOUT,
            follow_redirects=True,
            transport=transport,
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            content = resp.content

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)

        await asyncio.to_thread(_write)
        return True
    except Exception as e:
        logger.warning(f"{subject}拉取失败 {url}（{e}）")
        return False


class DownloadGate:
    """同键并发下载合并：首个调用创建任务，其余 await 同一任务。

    出表带身份检查（晚到的 stale 调用不误删新任务的键）；失败任务同样
    出表——滞留会让该素材进程内永不重试且表无界增长。
    """

    def __init__(self) -> None:
        self._tasks: dict[Hashable, asyncio.Task] = {}

    async def run(self, key: Hashable, factory: Callable[[], Awaitable[bool]]) -> bool:
        task = self._tasks.get(key)
        if task is None:
            task = asyncio.create_task(factory())
            self._tasks[key] = task
        try:
            return await task
        finally:
            if self._tasks.get(key) is task:
                self._tasks.pop(key, None)
