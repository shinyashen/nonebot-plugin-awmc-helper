"""minfo 单曲成绩卡（Hoshino core/image/info.py::song_play_data 的移植）。

使用素材包主题底图 ``{theme}/play_info.png``（1200×900），左侧曲绘 + 曲目信息、
右侧按难度 5 行成绩（评级/FC/FS 徽章、DX 分与星数、未游玩谱灰行）。

与基准的差异（有意为之）：
- 版本 logo 用 :func:`nb_chart.fit_version_logo` 等比适配（防各世代画布拉伸）；
- ``service`` 为空（未绑定纯谱面视图）时省略「Data from …」句（嵌入署名行）；
- 难度槽定数小字统一 21pt 垂直居中（Hoshino 已玩 20pt/未玩 25pt 两档）。
"""

from PIL import ImageDraw
from maimai_py import Song, Genre, SongType, ScoreExtend

from .fonts import FONT_HAN, FONT_NUM, FONT_RODIN, font
from .tools import (
    TEXT_BLUE,
    CIRCLE_PINK,
    credit_text,
    image_to_bytes,
    truncate_hoshino,
)
from .assets import assets
from .nb_chart import LOGO_SIZE, major_diffs, chart_version_of, paste_version_logo
from ...constants import (
    RATE_FILE,
    SYNC_FILE,
    COMBO_FILE,
    DEFAULT_THEME,
    SERVICE_DISPLAY,
    chart_display_id,
)

# 分类徽章（pic/info_*.png，320×320 覆盖在曲绘上；宴会場无素材，跳过）
_GENRE_FILE: dict[Genre, str] = {
    Genre.POPSアニメ: "info_anime.png",
    Genre.maimai: "info_maimai.png",
    Genre.niconicoボーカロイド: "info_niconico.png",
    Genre.東方Project: "info_touhou.png",
    Genre.ゲームバラエティ: "info_game.png",
    Genre.オンゲキCHUNITHM: "info_ongeki.png",
}

_TEXT_COLOR = TEXT_BLUE
_CIRCLE_COLOR = CIRCLE_PINK


def song_play_data(
    song: Song,
    play_result: list[ScoreExtend],
    *,
    service: str | None = None,
    theme: str = DEFAULT_THEME,
    prefer_type: SongType | None = None,
) -> bytes:
    """谱面游玩成绩卡。

    ``play_result``：该曲成绩（maimai_py ``PlayerSong.scores``）；只取主类型
    谱面的成绩入槽，未游玩槽画灰行。``service``：数据源署名（未绑定省略）。
    """
    color = _CIRCLE_COLOR if theme == "circle" else _TEXT_COLOR
    im = assets.canvas("play_info.png", theme)
    dr = ImageDraw.Draw(im)

    # logo
    im.alpha_composite(assets.pic("logo.png", theme).resize(LOGO_SIZE), (42, 34))
    # 曲绘（assets.cover 自带回退链）
    im.alpha_composite(assets.cover(song.id).resize((300, 300)), (100, 260))
    # 分类徽章（素材缺失时跳过，如宴会場）
    genre_file = _GENRE_FILE.get(song.genre)
    if genre_file and (genre_img := assets.pic_optional(genre_file)):
        im.alpha_composite(genre_img, (100, 260))
    # 类型徽章与版本 logo 均跟随卡片主类型
    prefer_sd = prefer_type == SongType.STANDARD and bool(song.difficulties.standard)
    major_type = (
        SongType.STANDARD
        if prefer_sd
        else (SongType.DX if song.difficulties.dx else SongType.STANDARD)
    )
    # 版本 logo（等比适配槽位，项目内既定做法）：版本口径与查歌卡同源——
    # 主类型谱面组首谱面版本，组空/缺版本回落曲级（nb_chart.chart_version_of）
    paste_version_logo(im, chart_version_of(song, prefer_sd), (295, 205, 183, 90))
    if badge := assets.type_badge(
        "SD" if major_type == SongType.STANDARD else "DX", (55, 20)
    ):
        im.alpha_composite(badge, (350, 560))

    # 曲目信息（截断规则对齐 Hoshino：宽 >L 才截、截后保留 ≤L-1 列）
    dr.text(
        (255, 595),
        truncate_hoshino(song.artist, 58),
        font=font(12, FONT_HAN),
        fill=color,
        anchor="mm",
    )
    dr.text(
        (255, 622),
        truncate_hoshino(song.title, 38),
        font=font(18, FONT_HAN),
        fill=color,
        anchor="mm",
    )
    # 主类型谱面组：展示 id 与难度行共用
    diffs = major_diffs(song, prefer_type)
    card_id = chart_display_id(song, diffs[0]) if diffs else song.id
    dr.text(
        (160, 720), str(card_id), font=font(22, FONT_RODIN), fill=color, anchor="mm"
    )
    dr.text(
        (380, 720), str(song.bpm), font=font(22, FONT_RODIN), fill=color, anchor="mm"
    )

    # 按难度行成绩（只取主类型谱面的成绩入槽）
    by_slot = {s.level_index: s for s in play_result if s.type == major_type}
    slots = [d.level_index for d in diffs]
    step_y = 100
    # 难度槽定数小字：五档统一 21pt 并垂直居中于 d_N 色条（70×30 @ (650,235)）。
    # NB 原版已玩 20pt / 未玩 25pt 两档且 y 略偏上（用户要求统一并居中）
    level_y = 251
    for num, level_index in enumerate(slots):
        y = step_y * num
        im.alpha_composite(assets.pic(f"d_{num}.png"), (650, 235 + y))
        score = by_slot.get(level_index)
        diff = next(d for d in diffs if d.level_index == level_index)
        if score is None:
            # 定数小字为白色（NB DrawText 默认色），「未游玩」才是主题色
            dr.text(
                (685, level_y + y),
                f"{diff.level_value}",
                font=font(21, FONT_RODIN),
                fill=(255, 255, 255, 255),
                anchor="mm",
            )
            dr.text(
                (800, 302 + y),
                "未游玩",
                font=font(30, FONT_HAN),
                fill=color,
                anchor="mm",
            )
            continue
        im.alpha_composite(
            assets.pic("ra_dx.png", theme).resize((102, 44)), (850, 272 + y)
        )
        dx_score = score.dx_score or 0
        # DX 星取库算值（ScoreExtend.dx_star，阈值同现算；0 分为 None → 不画星）
        star = score.dx_star or 0
        if star:
            im.alpha_composite(
                assets.pic(f"UI_GAM_Gauge_DXScoreIcon_0{star}.png").resize((32, 19)),
                (851, 296 + y),
            )
        dr.text(
            (916, 304 + y),
            f"{dx_score}/{score.level_dx_score}",
            font=font(13, FONT_NUM),
            fill=color,
            anchor="mm",
        )
        im.alpha_composite(assets.pic("fcfs.png"), (965, 265 + y))
        if score.fc:
            im.alpha_composite(
                assets.pic(
                    f"UI_CHR_PlayBonus_{COMBO_FILE[score.fc.name.lower()]}.png"
                ).resize((65, 65)),
                (960, 261 + y),
            )
        if score.fs:
            im.alpha_composite(
                assets.pic(
                    f"UI_CHR_PlayBonus_{SYNC_FILE[score.fs.name.lower()]}.png"
                ).resize((65, 65)),
                (1025, 261 + y),
            )
        rate_name = RATE_FILE.get(score.rate.name, "D") if score.rate else "D"
        if rank_img := assets.pic_optional(f"UI_TTR_Rank_{rate_name}.png", theme):
            im.alpha_composite(rank_img.resize((100, 45)), (737, 272 + y))
        dr.text(
            (500, 295 + y),
            f"{score.achievements or 0:.4f}%",
            font=font(30, FONT_RODIN),
            fill=color,
            anchor="lm",
        )
        dr.text(
            (685, level_y + y),
            f"{diff.level_value}",
            font=font(21, FONT_RODIN),
            fill=(255, 255, 255, 255),
            anchor="mm",
        )
        dr.text(
            (915, 283 + y),
            str(int(score.dx_rating or 0)),
            font=font(18, FONT_NUM),
            fill=color,
            anchor="mm",
        )
    if len(slots) == 4:
        dr.text(
            (800, 302 + step_y * 4),
            "没有该难度",
            font=font(30, FONT_HAN),
            fill=color,
            anchor="mm",
        )

    # 底部署名行（Hoshino 同款：Data from 嵌入行内、20pt；未绑定省略该句）
    dr.text(
        (600, 830),
        credit_text(service and SERVICE_DISPLAY.get(service, service)),
        font=font(20, FONT_RODIN),
        fill=color,
        anchor="mm",
        stroke_width=3,
        stroke_fill=(255, 255, 255, 255),
    )
    return image_to_bytes(im)
