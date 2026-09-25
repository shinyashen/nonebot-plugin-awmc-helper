"""猜歌指令入口：猜歌 / 猜曲绘 / 重置猜歌 / 群开关 与 priority=0 答案拦截。"""

from nonebot import on_regex, on_command, on_message
from nonebot.rule import Rule
from nonebot.params import RegexGroup
from nonebot.adapters import Bot, Event
from nonebot_plugin_uninfo import Session, UniSession
from nonebot_plugin_alconna.uniseg import UniMessage

from .game import GUESS_FEATURE, _reveal, _game_of, _start_game, _handle_answer
from ...core import store
from ...core.utils import group_admin, group_id_of, handle_errors

guess = on_command("猜歌", block=True)
guess_pic = on_command("猜曲绘", block=True)
guess_reset = on_command("重置猜歌", block=True)
guess_switch = on_regex(r"^(开启|关闭)mai猜歌$", block=True)


async def _is_guess_answer(bot: Bot, event: Event) -> bool:
    """priority=0 答案拦截规则：群内存在对局且消息为非空文本。"""
    from nonebot_plugin_uninfo import get_session

    session = await get_session(bot, event)
    if session is None:
        return False
    if _game_of(group_id_of(session)) is None:
        return False
    return bool(event.get_plaintext().strip())


# priority=0 让答案判定先于常规指令，但 block=False 不吞事件：
# 对局期间同群其他指令（含 重置猜歌 / 开关）照常响应
guess_answer = on_message(rule=Rule(_is_guess_answer), priority=0, block=False)


@guess_answer.handle()
@handle_errors()
async def _(bot: Bot, event: Event):
    from nonebot_plugin_uninfo import get_session

    session = await get_session(bot, event)
    assert session is not None
    await _handle_answer(session, event.get_plaintext())


@guess.handle()
@handle_errors()
async def _(bot: Bot, event: Event, session: Session = UniSession()):
    await _start_game(session, pic_mode=False, bot=bot, event=event)


@guess_pic.handle()
@handle_errors()
async def _(bot: Bot, event: Event, session: Session = UniSession()):
    await _start_game(session, pic_mode=True, bot=bot, event=event)


@guess_reset.handle()
@handle_errors()
async def _(session: Session = UniSession()):
    group_id = group_id_of(session)
    game = _game_of(group_id)
    if game is None:
        await UniMessage.text(" 当前没有进行中的猜歌").finish(at_sender=True)
    await _reveal(game, "已强制结束。")


@guess_switch.handle()
@handle_errors()
async def _(
    bot: Bot,
    event: Event,
    session: Session = UniSession(),
    groups: tuple = RegexGroup(),
):
    group_id = group_id_of(session)
    if group_id is None:
        await UniMessage.text(" 猜歌开关仅群聊可用").finish(at_sender=True)
    if not await group_admin()(bot, event):
        await UniMessage.text(" 权限不足：仅群管理员可用").finish(at_sender=True)
    enabled = groups[0] == "开启"
    await store.set_group_switch(group_id, GUESS_FEATURE, enabled)
    if not enabled:
        game = _game_of(group_id)
        if game is not None:
            await _reveal(game, "猜歌已关闭。")
    state = "开启" if enabled else "关闭"
    await UniMessage.text(f" 已{state}本群猜歌").finish(at_sender=True)
