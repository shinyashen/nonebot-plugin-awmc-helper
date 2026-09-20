"""awmc.random_song：随机谱面子插件。

指令（对齐原版）：
- `来个/随个/给个 [dx|sd|标准][色]<等级>`（如 随个紫13+ / 来个dx14）
- `mai什么`（随机曲目）
"""

from nonebot import on_regex, on_command
from maimai_py import SongType, LevelIndex
from nonebot.params import RegexGroup
from nonebot.plugin import PluginMetadata
from nonebot.adapters import Bot
from nonebot_plugin_alconna.uniseg import UniMessage

from ...constants import ZH_TO_GENRE, COLOR_TO_LEVEL_INDEX
from ...core.songs import song_service
from ...core.utils import handle_errors
from ...core.render import song as song_render

__plugin_meta__ = PluginMetadata(
    name="awmc.random_song",
    description="舞萌DX 随机谱面：随个/来个/mai什么",
    usage="来个紫13+｜随个dx14｜给个标准10｜mai什么",
    type="application",
    homepage="https://github.com/shinyashen/nonebot-plugin-awmc-helper",
)

random_chart = on_regex(
    r"(?i)^[随来给]个((?:dx|sd|标准))?([绿黄红紫白]?)([0-9]+\+?)$",
    block=True,
)
mai_what = on_command("mai什么", block=True)


@random_chart.handle()
@handle_errors("随机失败，请稍后再试")
async def _(groups: tuple = RegexGroup()):
    type_raw, color, level = groups
    song_type = None
    if type_raw:
        song_type = (
            SongType.STANDARD if type_raw.lower() in ("sd", "标准") else SongType.DX
        )
    level_index = COLOR_TO_LEVEL_INDEX.get(color or "")
    got = await song_service.random(
        song_type=song_type, level=level, level_index=level_index, exclude_utage=True
    )
    if got is None:
        await UniMessage.text("没有符合条件的谱面，换一个试试吧").finish()
    song, diff = got
    await UniMessage.image(raw=song_render.random_song_bytes(song, diff)).finish()


@mai_what.handle()
@handle_errors("随机失败，请稍后再试")
async def _(bot: Bot):
    got = await song_service.random(exclude_utage=True)
    if got is None:
        await UniMessage.text("曲库为空，请稍后再试").finish()
    song, _diff = got
    await UniMessage.image(raw=song_render.song_card_bytes(song)).finish()


_ = ZH_TO_GENRE, LevelIndex
