"""awmc.fortune：今日运势（今日mai）。

qqhash 人品值 + 宜/忌（FORTUNE 13 项中 11 项参与判定）+ 每日随机推荐曲，
同人同日结果稳定（对齐原版 qqhash 语义）。
"""

import time
import zlib
import random

from nonebot import on_command
from nonebot.plugin import PluginMetadata
from nonebot_plugin_uninfo import Session, UniSession
from nonebot_plugin_alconna.uniseg import UniMessage

from ...core.help import CommandSpec, help_registry
from ...core.songs import song_service
from ...core.utils import handle_errors
from ...core.render import song as song_render

__plugin_meta__ = PluginMetadata(
    name="awmc.fortune",
    description="舞萌DX 今日运势",
    usage="今日mai｜今日舞萌｜今日运势",
    type="application",
    homepage="https://github.com/shinyashen/nonebot-plugin-awmc-helper",
)

FORTUNE = [
    "拼机",
    "推分",
    "越级",
    "下埋",
    "夜勤",
    "练底力",
    "练手法",
    "打旧框",
    "干饭",
    "抓绝赞",
    "收歌",
]

today_fortune = on_command("今日mai", aliases={"今日舞萌", "今日运势"}, block=True)


def qqhash(qq: int) -> int:
    """与原版一致的日稳定哈希（同日同 QQ 结果相同）。"""
    days = (
        int(time.strftime("%d", time.localtime()))
        + 31 * int(time.strftime("%m", time.localtime()))
        + 77
    )
    return (days * qq) >> 8


@today_fortune.handle()
@handle_errors("查询失败，请稍后再试")
async def _(session: Session = UniSession()):
    user_id = str(session.user.id)
    # 非数字 user_id 用 crc32（内建 hash 受 PYTHONHASHSEED 盐化，重启即变，
    # 与「同人同日结果稳定」矛盾）
    seed = int(user_id) if user_id.isdigit() else zlib.crc32(user_id.encode()) % (10**8)
    fortune_hash = qqhash(seed)
    daily_random = random.Random(fortune_hash)

    rp = fortune_hash % 100
    h = fortune_hash
    lines = [f" 今日人品值：{rp}"]
    for i in range(len(FORTUNE)):
        wm = h & 3
        h >>= 2
        if wm == 3:
            lines.append(f"宜 {FORTUNE[i]}")
        elif wm == 0:
            lines.append(f"忌 {FORTUNE[i]}")

    # 推荐曲与运势同源种子：同人同日稳定；random() 收口选曲（含宴谱，保持
    # 原语义——按谱面均匀而非按曲均匀，接受该差异换取 core 单源）
    hit = await song_service.random(exclude_utage=False, rng=daily_random)
    if hit is not None:
        song, _diff = hit
        ds = "/".join(f"{d.level_value:.1f}" for d in song.get_difficulties())
        lines.append("打机时不要大力拍打或滑动哦")
        lines.append(f"今日推荐歌曲：ID.{song.id} - {song.title}（定数 {ds}）")
        await (
            UniMessage.text("\n".join(lines))
            .image(raw=song_render.song_card_bytes(song))
            .finish(at_sender=True)
        )
    await UniMessage.text("\n".join(lines)).finish(at_sender=True)


# ---------------------------------------------------------------- 帮助声明

help_registry.declare(
    plugin="awmc.fortune",
    title="今日运势",
    category="fun",
    commands=[
        CommandSpec(
            matcher=today_fortune,
            name="今日mai",
            aliases=("今日舞萌", "今日运势"),
            brief="今日人品值+宜/忌+随机推荐曲（同人同日结果稳定）",
        ),
    ],
)
