"""推分 / 等级进度 / 分数列表行卡体系（NB core/image/score.py 的移植）。

视觉基座：PRiSM PLUS 三色渐变底 + aurora/星光/彩虹/底纹装饰层，难度行卡用
``b50_score_*.png``（成绩）与 ``rise_score_*.png``（推分）素材，坐标对齐 NB 版。
底图尺寸由调用方按数据量计算后传入（NB handler 同款约定）。
"""

from collections.abc import Callable

from PIL import ImageDraw
from maimai_py import SongType, ScoreExtend
from maimai_py.models import SongDifficulty

from .fonts import FONT_HAN, FONT_NUM, FONT_RODIN, font
from .tools import (
    TEXT_BLUE,
    ID_TEXT_COLORS,
    DIFF_TEXT_COLORS,
    credit_text,
    image_to_bytes,
    truncate_hoshino,
    generate_prism_bg,
)
from .assets import assets
from .best50 import draw_score_row
from ...constants import DX_ID_OFFSET, LEVEL_INDEX_EN, chart_display_id
from .table_layout import (
    SCORE_ROW_GAP,
    SCORE_ROW_COLS,
    SCORE_ROW_START_X,
    SCORE_ROW_COL_STEP,
)

# 难度文字色 / 谱面 id 色（NB AssetsImage 同源，tools 单源）
_DEFAULT_TEXT_COLOR = TEXT_BLUE


SCORE_LIST_PER_PAGE = 80
"""分数列表每页行数（tables 条件列表与第三方扩展共用）。"""
SCORE_LIST_HEAD_HEIGHT = 280
"""分数列表画布头部固定高度（总高 = 头部 + :func:`score_list_height`）。"""


def score_list_page(total: int, page: int) -> "tuple[int, int]":
    """分数列表翻页钳制 → (end_page, real_page)（80/页口径单源，
    主插件条件列表与导分插件 pc 列表共用）。"""
    end_page = max(1, -(-total // SCORE_LIST_PER_PAGE))
    return end_page, min(max(page, 1), end_page)


def score_list_height(total: int, page: int, end_page: int) -> int:
    """分数列表行卡区高度（NB 版式算式，tables 分数列表与第三方扩展共用）。

    非末页整 80 条 4 段；末页按实际条数算行数与段数。调用方在结果上加
    固定头部高度得画布总高（DrawScore 构造参数）。
    """
    to_page = 80 if page < end_page else (total % 80 or 80)
    line = (to_page + 4) // 5
    if page < end_page:
        return line * 109 + 130 * 4
    multiplier = (to_page + 19) // 20
    actual_line = 4 if to_page <= 20 else line
    return actual_line * 109 + 130 * multiplier


class DrawScore:
    """行卡画布：构造时生成装饰底图，draw_* 系列输出成品 bytes。"""

    def __init__(self, height: int, *, service: str | None = None) -> None:
        # 行卡画布固定 prism_plus 版式（generate_prism_bg 渐变与装饰层均
        # prism_plus 专属，无 circle 变体）——非「默认主题」语义，勿改常量
        theme = "prism_plus"
        im = generate_prism_bg(height)
        self._im = im
        self._theme = theme
        self._title_bg = assets.pic("title.png", theme)
        self._title_lengthen_bg = assets.pic("title_lengthen.png", theme)
        # NB 版式：装饰层先贴，标题底图与文字随后（构造时只备料）
        self._service = service

    def _design_text(self) -> str:
        return credit_text(self._service)

    # -- 推分推荐卡（R3，NB draw_rise / while_rise_pic） ---------------------

    def _while_rise_pic(self, data: list[dict], base_x: int) -> None:
        """循环绘制上分推荐行卡。

        ``data`` 元素为 :func:`core.calc.rise_recommend` 的输出 dict（含 old_*）；
        ``base_x``：栏起点（SD 栏 200 / DX 栏 700，与 NB 一致）。
        """
        dr = ImageDraw.Draw(self._im)
        start_y, step = 120, 140
        for index, row in enumerate(data):
            diff: SongDifficulty = row["diff"]
            song = row["song"]
            li = diff.level_index.value
            x = base_x
            y = start_y + index * step

            self._im.alpha_composite(
                assets.pic(f"rise_score_{LEVEL_INDEX_EN[li]}.png"), (x + 30, y)
            )
            self._im.alpha_composite(
                assets.cover(song.id).resize((80, 80)), (x + 55, y + 41)
            )
            type_abbr = "DX" if diff.type == SongType.DX else "SD"
            if badge := assets.type_badge(type_abbr, (60, 22)):
                self._im.alpha_composite(badge, (x + 240, y + 114))
            # 旧成绩评级（Hoshino：无旧成绩不画旧章；未游玩推荐行不显示 D）
            old_ach = row.get("old_achievements") or 0
            if old_ach:
                old_rank = assets.rate_badge_of_achievement(
                    old_ach, self._theme, (63, 28)
                )
                if old_rank is not None:
                    self._im.alpha_composite(old_rank, (x + 145, y + 82))
            if rank := assets.rate_badge_of_achievement(
                row["achievements"], self._theme, (63, 28)
            ):
                self._im.alpha_composite(rank, (x + 305, y + 82))

            diff_color = DIFF_TEXT_COLORS[li]
            id_color = ID_TEXT_COLORS[li]

            # Hoshino 截断规则：宽 >26 才截，截后保留 ≤25 列再加省略号
            title = truncate_hoshino(song.title, 26)
            dr.text(
                (x + 142, y + 44),
                title,
                font=font(17, FONT_HAN),
                fill=diff_color,
                anchor="lm",
            )
            dr.text(
                (x + 145, y + 124),
                f"ID: {chart_display_id(song, diff)}",
                font=font(18, FONT_NUM),
                fill=id_color,
                anchor="lm",
            )
            dr.text(
                (x + 210, y + 71),
                f"{old_ach:.4f}%",
                font=font(25, FONT_NUM),
                fill=diff_color,
                anchor="mm",
            )
            dr.text(
                (x + 245, y + 96),
                f"Ra: {int(row.get('old_ra') or 0)}",
                font=font(17, FONT_NUM),
                fill=diff_color,
                anchor="mm",
            )
            dr.text(
                (x + 370, y + 71),
                f"{row['achievements']:.4f}%",
                font=font(25, FONT_NUM),
                fill=diff_color,
                anchor="mm",
            )
            dr.text(
                (x + 415, y + 96),
                f"Ra: {int(row['new_ra'])}",
                font=font(17, FONT_NUM),
                fill=diff_color,
                anchor="mm",
            )
            dr.text(
                (x + 315, y + 124),
                f"ds:{diff.level_value}",
                font=font(18, FONT_NUM),
                fill=id_color,
                anchor="lm",
            )
            dr.text(
                (x + 390, y + 124),
                f"Ra +{int(row['gain'])}",
                font=font(18, FONT_NUM),
                fill=id_color,
                anchor="lm",
            )

    def draw_rise(
        self,
        old: list[dict],
        new: list[dict],
        total_height: int,
    ) -> bytes:
        """绘制上分推荐表（左栏旧版本谱面 / 右栏新版本谱面，各 5 行）。

        双栏按版本划分：旧版本 = 当前版本以前全部谱面（b35 侧）、
        新版本 = 当前版本谱面（b15 侧），数据为 rise_recommend 输出 dict。
        """
        dr = ImageDraw.Draw(self._im)
        title_bg = self._title_bg.resize((273, 80))
        self._im.alpha_composite(title_bg, (314, 30))
        dr.text(
            (450, 68),
            "旧版本谱面推荐",
            font=font(18, FONT_HAN),
            fill=_DEFAULT_TEXT_COLOR,
            anchor="mm",
        )
        self._while_rise_pic(old, 200)
        self._im.alpha_composite(title_bg, (814, 30))
        dr.text(
            (950, 68),
            "新版本谱面推荐",
            font=font(18, FONT_HAN),
            fill=_DEFAULT_TEXT_COLOR,
            anchor="mm",
        )
        self._while_rise_pic(new, 700)

        height = self._im.size[1]
        dr.text(
            (700, height - 84),
            "「谱面推荐不使用任何算法，仅供参考」",
            font=font(25, FONT_HAN),
            fill=_DEFAULT_TEXT_COLOR,
            anchor="mm",
        )
        dr.text(
            (700, height - 36),
            self._design_text(),
            font=font(18, FONT_HAN),
            fill=_DEFAULT_TEXT_COLOR,
            anchor="mm",
            stroke_width=2,
            stroke_fill=(255, 255, 255, 255),
        )
        return image_to_bytes(self._im.crop((200, 0, 1200, total_height)))

    # -- 等级进度卡（R4，NB whiledraw / _while_pic / draw_plan / draw_category）

    def whiledraw(
        self,
        scores: list,
        list_y: int = 0,
        sub_of: Callable[[ScoreExtend], str | None] | None = None,
    ) -> None:
        """绘制成绩行卡（5 列 × N 行，b50_score_* 难度底，宴谱换 #EB77ED 染色底）。

        ``scores``：ScoreExtend 列表；DX 星直接取 ``score.dx_star``（库已算）。
        ``sub_of``：副行文字提取器，透传 :func:`draw_score_row`（None=默认
        「定数 -> 单曲Ra」）。
        """
        dr = ImageDraw.Draw(self._im)
        for num, score in enumerate(scores):
            row, col = divmod(num, SCORE_ROW_COLS)
            x = SCORE_ROW_START_X + col * SCORE_ROW_COL_STEP
            y = list_y + row * SCORE_ROW_GAP
            draw_score_row(self._im, dr, x, y, score, self._theme, sub_of=sub_of)

    def _while_pic(
        self, items: list[tuple[int, int, float]], start_y: int = 200
    ) -> None:
        """绘制未游玩谱面小卡（20 列 × N 行，border_progress_* 难度框）。

        ``items``：**(游戏内谱面 id, level_index, level_value)**——NB 显示
        per-type id（DX 曲 10231 形状）；曲绘按根 id（% 10000）回退链取。
        """
        dr = ImageDraw.Draw(self._im)
        step, start_x = 65, 55
        for num, (song_id, li, _lv) in enumerate(items):
            row, col = divmod(num, 20)
            x = start_x + col * step
            y = start_y + row * step
            # Hoshino 图层序：先曲绘后难度框（框缘压住曲绘边一像素）
            self._im.alpha_composite(
                assets.cover(song_id % DX_ID_OFFSET).resize((55, 55)), (x, y)
            )
            self._im.alpha_composite(
                assets.pic(f"border_progress_{LEVEL_INDEX_EN[li]}.png"), (x - 4, y - 4)
            )
            dr.text(
                (x + 36, y + 3),
                str(song_id),
                font=font(12, FONT_NUM),
                fill=DIFF_TEXT_COLORS[li],
                anchor="mm",
            )

    def _section_title(
        self, y: int, text: str, hint: str | None = None, *, size: int = 25
    ) -> None:
        """段落标题条（title_lengthen 底图 + 大字 + 可选右侧提示）。

        NB 字号：draw_plan 段落 25pt，draw_category 标题 28pt。
        """
        dr = ImageDraw.Draw(self._im)
        self._im.alpha_composite(self._title_lengthen_bg, (475, y - 47))
        dr.text(
            (700, y),
            text,
            font=font(size, FONT_HAN),
            fill=_DEFAULT_TEXT_COLOR,
            anchor="mm",
        )
        if hint:
            dr.multiline_text(
                (1300, y),
                hint,
                font=font(20, FONT_HAN),
                fill=_DEFAULT_TEXT_COLOR,
                anchor="rm",
                stroke_width=2,
                stroke_fill=(255, 255, 255, 255),
            )

    def _footer(
        self, text: str, *, design_bg_y: int, text_y: int, size: int = 22
    ) -> None:
        dr = ImageDraw.Draw(self._im)
        self._im.alpha_composite(
            assets.pic("design.png", self._theme), (200, design_bg_y)
        )
        dr.text(
            (700, text_y),
            text,
            font=font(size, FONT_HAN),
            fill=_DEFAULT_TEXT_COLOR,
            anchor="mm",
        )
        dr.text(
            (700, self._im.size[1] - 30),
            self._design_text(),
            font=font(25, FONT_HAN),
            fill=_DEFAULT_TEXT_COLOR,
            anchor="mm",
            stroke_width=2,
            stroke_fill=(255, 255, 255, 255),
        )

    def draw_plan(
        self,
        level: str,
        completed: list,
        completed_y: int,
        unfinished: list,
        unfinished_y: int,
        notstarted: list[tuple[int, int, float]],
        plan: str,
        completed_len: int,
        *,
        goal: "str | None" = None,
    ) -> bytes:
        """绘制三段进度总览（已完成 / 未完成 / 未游玩，NB draw_plan 同布局）。

        ``completed_y``/``unfinished_y``：调用方按 NB 公式预算的段落高度。
        ``plan``：旧指令形态的提示后缀（``13fc进度`` → level="13" plan="fc"
        拼出「13FC已完成进度」）；**条件化形态（combo_progress_card）传空串**
        ——level 已是完整条件串，再拼 plan 词会出「14+sss+SSSP」连串乱码。
        ``goal``：页脚达标线词（缺省 plan.upper()；条件化传规范化 label，
        如「SSS+」——plan 词大写 SSSP 是枚举名非玩家口径）。
        """
        suffix = plan.upper() if plan else ""
        goal_text = goal if goal is not None else plan.upper()
        self._section_title(
            77,
            f"已完成谱面「{len(completed)}」个",
            f"可使用「{level}{suffix}已完成进度」\n指令查询详细列表",
        )
        self._section_title(
            77 + completed_y,
            f"未完成谱面「{len(unfinished)}」个",
            f"可使用「{level}{suffix}未完成进度」\n指令查询详细列表",
        )
        self._section_title(
            77 + completed_y + unfinished_y, f"未游玩谱面「{len(notstarted)}」个"
        )

        self.whiledraw(completed[:completed_len], 140)
        self.whiledraw(unfinished[:30], 140 + completed_y)
        self._while_pic(notstarted[:100], 140 + completed_y + unfinished_y)

        height = self._im.size[1]
        max_count = len(completed) + len(unfinished) + len(notstarted)
        pagemsg = (
            f"「{level}」共计「{max_count}」个谱面，"
            f"剩余「{len(unfinished) + len(notstarted)}」个谱面未完成「{goal_text}」"
        )
        self._footer(pagemsg, design_bg_y=height - 133, text_y=height - 90)
        return image_to_bytes(self._im)

    def draw_category(
        self,
        category: str,
        data: list,
        page: int = 1,
        end_page: int = 1,
    ) -> bytes:
        """绘制指定分类进度（80/页成绩行卡或未游玩网格）。

        ``category``：``completed`` / ``unfinished`` / ``notplayed``。
        """
        if category in ("completed", "unfinished"):
            txt = "已完成" if category == "completed" else "未完成"
            newdata = data[(page - 1) * 80 : page * 80]
            # NB draw_category 标题与页脚均为 28/25pt（draw_plan 段落为 25/22pt）
            self._section_title(77, f"{txt}谱面", size=28)
            self.whiledraw(newdata, 140)
            height = self._im.size[1]
            pagemsg = (
                f"{txt}谱面共计「{len(data)}」个，"
                f"当前第「{(page - 1) * 80 + 1}-{(page - 1) * 80 + len(newdata)}」个，"
                f"第「{page} / {end_page}」页"
            )
            self._footer(pagemsg, design_bg_y=height - 133, text_y=height - 90, size=25)
        else:
            self._section_title(77, "未游玩谱面", size=28)
            self._while_pic(data)
            height = self._im.size[1]
            self._im.alpha_composite(
                assets.pic("design.png", self._theme), (200, height - 113)
            )
            ImageDraw.Draw(self._im).text(
                (700, height - 70),
                f"未游玩谱面共计「{len(data)}」个",
                font=font(25, FONT_HAN),
                fill=_DEFAULT_TEXT_COLOR,
                anchor="mm",
            )
        return image_to_bytes(self._im)

    def draw_score_list(
        self,
        rating: str | float,
        play_result: list,
        page: int,
        end_page: int,
        sub_of: Callable[[ScoreExtend], str | None] | None = None,
    ) -> bytes:
        """绘制分数列表（80/页，每 20 条一段，NB draw_score_list 同布局）。

        ``sub_of``：副行文字提取器，透传 :func:`draw_score_row`
        （None=默认「定数 -> 单曲Ra」；导分插件传「pc: N」提取器复用本版式）。
        """
        dr = ImageDraw.Draw(self._im)
        start_offset = (page - 1) * 80
        current_page_result = play_result[start_offset : page * 80]

        section_height = 140 + 4 * SCORE_ROW_GAP
        for num in range(0, len(current_page_result), 20):
            idx = num // 20
            result = current_page_result[num : num + 20]
            base_y = idx * section_height
            self._im.alpha_composite(self._title_lengthen_bg, (475, base_y + 20))

            no_start = start_offset + num + 1
            no_end = start_offset + num + len(result)
            dr.text(
                (700, base_y + 67),
                f"No.{no_start}- No.{no_end}",
                font=font(28, FONT_RODIN),
                fill=_DEFAULT_TEXT_COLOR,
                anchor="mm",
            )
            self.whiledraw(result, base_y + 140, sub_of=sub_of)

        height = self._im.size[1]
        self._im.alpha_composite(
            assets.pic("design.png", self._theme), (200, height - 153)
        )
        footer_text = (
            f"「{rating}」共计「{len(play_result)}」个成绩，"
            f"当前第「{start_offset + 1}-"
            f"{start_offset + len(current_page_result)}」个，"
            f"第「{page} / {end_page}」页"
        )
        dr.text(
            (700, height - 110),
            footer_text,
            font=font(25, FONT_HAN),
            fill=_DEFAULT_TEXT_COLOR,
            anchor="mm",
        )
        dr.text(
            (700, height - 35),
            self._design_text(),
            font=font(25, FONT_HAN),
            fill=_DEFAULT_TEXT_COLOR,
            anchor="mm",
            stroke_width=2,
            stroke_fill=(255, 255, 255, 255),
        )
        return image_to_bytes(self._im)
