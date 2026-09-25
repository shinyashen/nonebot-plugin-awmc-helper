"""minfo 单曲成绩卡（Hoshino core/image/info.py::song_play_data 的移植）。

使用素材包主题底图 ``{theme}/play_info.png``（1200×900），左侧曲绘 + 曲目信息、
右侧按难度 5 行成绩（评级/FC/FS 徽章、DX 分与星数、未游玩谱灰行）。

与基准的差异（有意为之）：
- 版本 logo 用 :func:`nb_chart.fit_version_logo` 等比适配（防各世代画布拉伸）；
- ``service`` 为空（未绑定纯谱面视图）时省略「Data from …」句（嵌入署名行）；
- 难度槽定数小字统一 21pt 垂直居中（Hoshino 已玩 20pt/未玩 25pt 两档）。
"""

from maimai_py import Song, Genre, SongType, ScoreExtend

from ..calc import dx_star_ratio
from .fonts import FONT_HAN, FONT_NUM, FONT_RODIN, font
from .tools import (
    TEXT_BLUE,
    credit_text,
    image_to_bytes,
    truncate_hoshino,
)
from .assets import assets
from .nb_chart import major_diffs, version_image, fit_version_logo
from ...constants import (
    RATE_FILE,
    SYNC_FILE,
    COMBO_FILE,
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
_CIRCLE_COLOR = (249, 62, 172, 255)


def song_play_data(
    song: Song,
    play_result: list[ScoreExtend],
    *,
    service: str | None = None,
    theme: str = "prism_plus",
    prefer_type: SongType | None = None,
) -> bytes:
    """谱面游玩成绩卡。

    ``play_result``：该曲成绩（maimai_py ``PlayerSong.scores``）；只取主类型
    谱面的成绩入槽，未游玩槽画灰行。``service``：数据源署名（未绑定省略）。
    """
    from PIL import Image, ImageDraw

    base = assets.static_path() / "mai" / "pic"
    color = _CIRCLE_COLOR if theme == "circle" else _TEXT_COLOR
    im = Image.open(base / theme / "play_info.png").convert("RGBA")
    dr = ImageDraw.Draw(im)

    # logo
    im.alpha_composite(
        Image.open(base / theme / "logo.png").resize((249, 120)), (42, 34)
    )
    # 曲绘（assets.cover 自带回退链）
    im.alpha_composite(assets.cover(song.id).resize((300, 300)), (100, 260))
    # 分类徽章（素材缺失时跳过，如宴会場）
    genre_file = _GENRE_FILE.get(song.genre)
    if genre_file and (base / genre_file).exists():
        im.alpha_composite(Image.open(base / genre_file).convert("RGBA"), (100, 260))
    # 版本 logo（等比适配槽位，项目内既定做法）
    diffs = major_diffs(song, prefer_type)
    chart_version = diffs[0].version if diffs and diffs[0].version else song.version
    version_img = version_image(chart_version)
    if version_img is not None:
        logo = fit_version_logo(version_img, (183, 90))
        im.alpha_composite(
            logo, (295 + (183 - logo.width) // 2, 205 + (90 - logo.height) // 2)
        )
    # 类型徽章（跟随卡片主类型）
    prefer_sd = prefer_type == SongType.STANDARD and bool(song.difficulties.standard)
    major_type = (
        SongType.STANDARD
        if prefer_sd
        else (SongType.DX if song.difficulties.dx else SongType.STANDARD)
    )
    type_path = base / f"{'SD' if major_type == SongType.STANDARD else 'DX'}.png"
    if type_path.exists():
        im.alpha_composite(Image.open(type_path).resize((55, 20)), (350, 560))

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
        im.alpha_composite(
            Image.open(base / f"d_{num}.png").convert("RGBA"), (650, 235 + y)
        )
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
            Image.open(base / theme / "ra_dx.png").resize((102, 44)), (850, 272 + y)
        )
        dx_score = score.dx_score or 0
        star = dx_star_ratio(dx_score, score.level_dx_score)
        if star:
            im.alpha_composite(
                Image.open(base / f"UI_GAM_Gauge_DXScoreIcon_0{star}.png").resize(
                    (32, 19)
                ),
                (851, 296 + y),
            )
        dr.text(
            (916, 304 + y),
            f"{dx_score}/{score.level_dx_score}",
            font=font(13, FONT_NUM),
            fill=color,
            anchor="mm",
        )
        im.alpha_composite(
            Image.open(base / "fcfs.png").convert("RGBA"), (965, 265 + y)
        )
        if score.fc:
            im.alpha_composite(
                Image.open(
                    base / f"UI_CHR_PlayBonus_{COMBO_FILE[score.fc.name.lower()]}.png"
                ).resize((65, 65)),
                (960, 261 + y),
            )
        if score.fs:
            im.alpha_composite(
                Image.open(
                    base / f"UI_CHR_PlayBonus_{SYNC_FILE[score.fs.name.lower()]}.png"
                ).resize((65, 65)),
                (1025, 261 + y),
            )
        rate_name = RATE_FILE.get(score.rate.name, "D") if score.rate else "D"
        rank_path = base / theme / f"UI_TTR_Rank_{rate_name}.png"
        if rank_path.exists():
            im.alpha_composite(Image.open(rank_path).resize((100, 45)), (737, 272 + y))
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
