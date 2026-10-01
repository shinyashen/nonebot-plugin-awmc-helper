"""猜歌对局核心：状态机、提示循环、开局/作答/揭晓编排（不注册 matcher）。"""

import random
import asyncio

from nonebot import logger
from nonebot.adapters import Bot, Event
from nonebot_plugin_uninfo import Session
from nonebot_plugin_alconna.uniseg import UniMessage

from ...core import store
from ...config import plugin_config
from ...constants import GENRE_TO_ZH, version_zh
from ...core.songs import song_service, chart_of_color
from ...core.types import Song, SongType, LevelIndex
from ...core.utils import group_id_of
from ...core.render import song as song_render
from ...core.render.cover import crop_cover_randomly
from ...core.render.tools import image_to_bytes
from ...core.render.assets import assets

HOT_SAMPLE_THRESHOLD = 10000
GUESS_FEATURE = "guess"


async def _guess_enabled(group_id: str | None) -> bool:
    if group_id is None:
        return False
    return await store.get_switch(
        group_id, GUESS_FEATURE, plugin_config.awmc_guess_enabled
    )


class GuessGame:
    """一局猜歌/猜曲绘。"""

    def __init__(
        self,
        song: Song,
        *,
        pic_mode: bool,
        group_id: str,
        bot: Bot | None,
        event: Event | None,
    ) -> None:
        self.song = song
        self.group_id = group_id
        self.pic_mode = pic_mode
        # 提示循环是独立 task，脱离 handler 上下文，须显式携带 bot/event 发送
        self.bot = bot
        self.event = event
        self.answers = {str(song.id), song.title.lower()}
        self.answers.update(a.lower() for a in song.aliases or [])
        self.hints = [] if pic_mode else self._build_hints()
        self.hint_index = 0
        self.task: asyncio.Task | None = None
        self.winner: str | None = None
        # 揭晓幂等标志：答对/超时/重置并发时只揭晓一次
        self.settled = False

    def _build_hints(self) -> list[str]:
        options = [
            f"它的 Expert 难度是 {self._level(LevelIndex.EXPERT)}",
            f"它的 Master 难度是 {self._level(LevelIndex.MASTER)}",
            f"它的分类是 {GENRE_TO_ZH.get(self.song.genre, self.song.genre.value)}",
            f"它的版本是 {version_zh(self.song.version)}",
            f"它的曲师是 {self.song.artist}",
            f"它{'不' if not self._is_dx() else ''}是 DX 谱面",
            f"它{'没' if not self._has_sd() else ''}有 SD 谱面",
            f"它的 BPM 是 {self.song.bpm}",
        ]
        random.shuffle(options)
        return random.sample(options, 6)

    def _has_sd(self) -> bool:
        return bool(self.song.get_difficulties(SongType.STANDARD))

    def _is_dx(self) -> bool:
        return any(d.type == SongType.DX for d in self.song.get_difficulties())

    def _level(self, index: LevelIndex) -> str:
        d = chart_of_color(self.song, index, prefer=SongType.STANDARD)
        return d.level if d else "无"

    def next_hint(self) -> str | None:
        if self.hint_index < len(self.hints):
            hint = f"提示{self.hint_index + 1}：{self.hints[self.hint_index]}"
            self.hint_index += 1
            return hint
        return None

    def match(self, text: str) -> bool:
        return text.strip().lower() in self.answers


_games: dict[str, GuessGame] = {}
"""进行中的猜歌局（进程内存，键 = group_id）。"""

_start_lock = asyncio.Lock()
"""开局串行锁：判重 → 发送开始消息 → 占位 全程持锁，消除时序错乱与并发双开。"""


def _game_of(group_id: str | None) -> GuessGame | None:
    return _games.get(group_id) if group_id else None


async def _pick_song() -> Song | None:
    """热门题库（曲线样本和 > 10000），无曲线数据时退化为全曲库。"""
    songs = await song_service.get_all()

    def curve_total(song: Song) -> int:
        return sum(
            (d.curve.sample_size or 0) if d.curve is not None else 0
            for d in song.get_difficulties()
        )

    hot = [s for s in songs if curve_total(s) > HOT_SAMPLE_THRESHOLD]
    pool = hot if hot else songs
    return random.choice(pool) if pool else None


async def _cover_hint_bytes(song: Song) -> bytes:
    cover = assets.cover(song.id)
    return image_to_bytes(crop_cover_randomly(cover))


async def _reveal(game: GuessGame, prefix: str) -> None:
    # 答对与超时可能同时醒来，先到先得；无 await 保证判定原子
    if game.settled:
        return
    game.settled = True
    _games.pop(game.group_id, None)
    # 超时路径在本 task 内调用：自 cancel 会让 CancelledError 落在下面的
    # send 上，把「时间到」揭晓一并吞掉，故仅外部调用（答对/重置/关开关）才取消
    if asyncio.current_task() is not game.task and game.task is not None:
        if not game.task.done():
            game.task.cancel()
    suffix = f"（{game.winner}）" if game.winner else ""
    await (
        UniMessage.text(
            f"{prefix}答案是：{game.song.title}（ID {game.song.id}）{suffix}"
        )
        .image(raw=song_render.song_card_bytes(game.song))
        .send(at_sender=True)
    )


async def _game_loop(game: GuessGame) -> None:
    """对局循环：猜歌逐条提示 → 裁剪曲绘 → 计时揭晓；猜曲绘直接计时揭晓。"""
    # 开局「开始」发送期间可能已被抢先揭晓（答对/重置），届时本局已出 _games
    if _games.get(game.group_id) is not game:
        return
    # 循环仅由 _start_game（携带真实 bot/event）启动，测试路径不进入
    assert game.bot is not None
    assert game.event is not None
    try:
        if not game.pic_mode:
            while (hint := game.next_hint()) is not None:
                await UniMessage.text(hint).send(game.event, game.bot, at_sender=True)
                await asyncio.sleep(plugin_config.awmc_guess_interval)
            await (
                UniMessage.image(raw=await _cover_hint_bytes(game.song))
                .text("\n最后提示：曲绘裁剪")
                .send(game.event, game.bot, at_sender=True)
            )
        await asyncio.sleep(plugin_config.awmc_guess_duration)
        await _reveal(game, "时间到！")
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("猜歌对局循环异常")
        if _games.get(game.group_id) is game:
            _games.pop(game.group_id)


async def _start_game(session: Session, pic_mode: bool, bot: Bot, event: Event) -> None:
    group_id = group_id_of(session)
    if group_id is None:
        await UniMessage.text(" 猜歌仅群聊可用").finish(at_sender=True)
    if not await _guess_enabled(group_id):
        await UniMessage.text(
            " 本群已关闭猜歌，请管理员使用「开启mai猜歌」开启"
        ).finish(at_sender=True)
    song = await _pick_song()  # 取曲在锁外：并发取曲只读曲库，无害
    if song is None:
        await UniMessage.text(" 曲库尚未就绪，请稍后再试").finish(at_sender=True)
    async with _start_lock:
        if _game_of(group_id) is not None:
            await UniMessage.text(
                "本群已有进行中的猜歌，请先作答或使用「重置猜歌」"
            ).finish(at_sender=True)
        # 开始/曲绘消息先发，再同步占位并起循环（占位与起 task 之间无 await）：
        # 对局可被作答时「开始」必已发出，不会出现揭晓先于开始消息
        mode_text = "猜曲绘开始" if pic_mode else "猜歌开始"
        await UniMessage.text(f" {mode_text}，直接回复曲目名称/别名/ID 作答").send(
            at_sender=True
        )
        game = GuessGame(
            song, pic_mode=pic_mode, group_id=group_id, bot=bot, event=event
        )
        _games[group_id] = game
        if pic_mode:
            await UniMessage.image(raw=await _cover_hint_bytes(song)).send(
                at_sender=True
            )
        game.task = asyncio.create_task(_game_loop(game))


async def _handle_answer(session: Session, text: str) -> bool:
    """答案命中时揭晓并返回 True。"""
    group_id = group_id_of(session)
    game = _game_of(group_id)
    if game is None or not game.match(text):
        return False
    game.winner = str(session.user.id)
    await _reveal(game, "答对了！")
    return True
