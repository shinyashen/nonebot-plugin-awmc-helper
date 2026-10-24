"""MaiConverter simai 片段 transformer fork（vendor，MIT License）。

语义与上游一致（时长 ``[X:Y]`` = Y/X 小节、``x`` = EX、``b`` = 破坏、
``?``/``!``/``$`` = tapless/无判定），差异见 ``__init__.py`` 文档：

- touch 保留 A-E 全区域（上游丢 A/D 是 bug）；C 区域保留 0-2 特例位；
- 连接滑 token 的星号与时长均可选（配合 core.simai 的预处理器，无星链
  先补插 ``*``）；tap/hold 修饰集放宽（``?``/``!`` 透传给调用方）。
"""

from lark import Transformer

# 形状首字符集（与语法 slide_beg 的形状正则一致；V 形带 reflect 位）
_SHAPE_HEAD = "-^<>szvwpqV"


class FragmentTransformer(Transformer):
    def bpm(self, n) -> dict:
        (n,) = n
        return {"type": "bpm", "value": float(n)}

    def divisor(self, n) -> dict:
        (n,) = n
        if float(n) == 0:
            raise ValueError("Divisor is 0.")
        return {"type": "divisor", "value": float(n)}

    def equivalent_bpm(self, n) -> dict:
        if len(n) == 0:
            return {"type": "equivalent_bpm", "value": None}
        (n,) = n
        return {"type": "equivalent_bpm", "value": float(n)}

    def duration(self, items) -> dict:
        """``[X:Y]`` → Y/X 小节（上游口径，经真实谱面 sanity 验证）。"""
        equivalent_bpm = None
        den = None
        num = None

        for item in items:
            if isinstance(item, str) and item.type == "INT" and den is None:
                den = int(item)
                if den <= 0:
                    return {
                        "type": "duration",
                        "equivalent_bpm": equivalent_bpm,
                        "duration": 0,
                    }
            elif isinstance(item, str) and item.type == "INT" and num is None:
                num = int(item)
            elif isinstance(item, str) and item[-1] == "#":
                equivalent_bpm = float(item[:-1])

        if den is None or num is None:
            raise ValueError("No denominator or numerator given")

        return {
            "type": "duration",
            "equivalent_bpm": equivalent_bpm,
            "duration": num / den,
        }

    def slide_beg(self, items) -> dict:
        text = items[0]
        start = int(text[0]) - 1
        text = text[1:]

        modifier = ""
        for i, char in enumerate(text):
            if char in "b@x?$!":
                modifier += char
            else:
                text = text[i:]
                break

        reflect = None

        if text[0] not in _SHAPE_HEAD:
            raise ValueError(f"Unknown slide pattern {text[0]}")

        pattern = text[0]
        if text[0] in "-^<>szvw":
            text = text[1:]
        elif text[0] in "pq" and len(text) > 1 and text[1] in "pq":
            pattern += text[1]
            text = text[2:]
        elif text[0] in "pq":
            text = text[1:]
        else:  # V slide
            reflect = int(text[1]) - 1
            text = text[2:]

        end = int(text[0]) - 1

        return {
            "type": "slide_beg",
            "start": start,
            "modifier": modifier,
            "pattern": pattern,
            "reflect": reflect,
            "end": end,
        }

    def chained_slide_note(self, items) -> dict:
        text = items[0]
        # 合并正则后 token 可能带前导星号（可缺省星号语法），剥掉
        if text.startswith("*"):
            text = text[1:]
        duration_dict = None
        postfix = ""
        for item in items[1:]:
            if isinstance(item, str):
                postfix += item
            elif item["type"] == "duration":
                duration_dict = item
        duration = duration_dict["duration"] if duration_dict else None
        equivalent_bpm = duration_dict["equivalent_bpm"] if duration_dict else None

        # 收集前导修饰（星号已剥），保留给链段（上游丢弃段级修饰，这里保留）
        modifier = ""
        for i, char in enumerate(text):
            if char in "b@x?$!":
                modifier += char
            else:
                text = text[i:]
                break

        pattern = text[0]
        reflect = None
        if text[0] in "-^<>szvw":
            text = text[1:]
        elif text[0] in "pq" and len(text) > 1 and text[1] in "pq":
            pattern += text[1]
            text = text[2:]
        elif text[0] in "pq":
            text = text[1:]
        else:  # V slide
            reflect = int(text[1]) - 1
            text = text[2:]

        end = int(text[0]) - 1

        return {
            "type": "chained_slide_note",
            "pattern": pattern,
            "reflect": reflect,
            "end": end,
            "modifier": modifier + postfix,
            "equivalent_bpm": equivalent_bpm,
            "duration": duration,
        }

    def slide_note(self, items):
        start = None
        end = None
        modifier = ""
        pattern = None
        reflect = None
        equivalent_bpm = None
        duration = None
        chained_slides = []
        postfix = ""

        for item in items:
            if isinstance(item, str):
                postfix += item  # slide_postfix（时长括号后的修饰，如 ]b）
                continue
            if item["type"] == "slide_beg":
                if item["start"] == -1 or item["reflect"] == -1 or item["end"] == -1:
                    return
                start = item["start"]
                modifier = item["modifier"]
                pattern = item["pattern"]
                end = item["end"]
                reflect = item["reflect"]
            elif item["type"] == "duration":
                equivalent_bpm = item["equivalent_bpm"]
                duration = item["duration"]
            elif item["type"] == "chained_slide_note":
                chained_slides.append(item)
            else:
                raise ValueError(f"Unknown value: {item}")

        if start is None or end is None or pattern is None:
            raise ValueError("Incomplete data")

        slides = []
        if start != -1 and end != -1 and reflect != -1:
            slides.append(
                {
                    "type": "slide",
                    "start_button": start,
                    "modifier": modifier,
                    "pattern": pattern,
                    "reflect_position": reflect,
                    "end_button": end,
                    "duration": duration,
                    "equivalent_bpm": equivalent_bpm,
                }
            )

        if chained_slides:
            slides += process_chained_slides(
                start if not slides else end, duration, equivalent_bpm, chained_slides
            )

        # 时长括号后的修饰（]b）挂到末段（段级语义，缺省即单段）
        if slides and postfix:
            slides[-1]["modifier"] += postfix

        return slides

    def tap_hold_note(self, items):
        text = items[0]
        duration_dict = items[1] if len(items) > 1 else None

        button = int(text[0]) - 1
        text = text[1:]
        if button == -1:
            # 忽略 0 位（simai 语法允许但游戏无此按钮）
            return

        is_tap = "h" not in text

        modifier = ""
        for char in text:
            if char == "h":
                continue
            if char in "b@x$?!":
                modifier += char

        if not is_tap:
            return {
                "type": "hold",
                "button": button,
                "modifier": modifier,
                "duration": duration_dict["duration"] if duration_dict else None,
            }

        return {
            "type": "tap",
            "button": button,
            "modifier": modifier,
        }

    def touch_tap_hold_note(self, items):
        text = items[0]
        duration_dict = items[1] if len(items) > 1 else None

        region = text[0]
        if len(text) > 1 and text[1].isdigit():
            position = int(text[1]) - 1
            text = text[2:]
        else:
            position = None
            text = text[1:]

        is_hold = "h" in text
        modifier = "".join(char for char in text if char == "f")

        if is_hold:
            return {
                "type": "touch_hold",
                "region": region,
                "location": position,
                "modifier": modifier,
                "duration": duration_dict["duration"] if duration_dict else None,
            }
        return {
            "type": "touch_tap",
            "region": region,
            "location": position,
            "modifier": modifier,
        }

    def slide_postfix(self, n) -> str:
        (n,) = n
        return n

    def pseudo_each(self, items):
        (item,) = items
        notes = item if isinstance(item, list) else [item]
        for note in notes:
            note["modifier"] += "`"
        return notes

    def chain(self, items) -> list:
        result = []
        for item in items:
            if isinstance(item, list):
                result.extend(sub for sub in item if isinstance(sub, dict))
            elif isinstance(item, dict):
                result.append(item)
        return result


def process_chained_slides(
    start_button: int,
    duration: float | None,
    equivalent_bpm: float | None,
    chained_slides: list[dict],
) -> list[dict]:
    """连接滑链展开：每段独立一条 slide（起点接上一段终点，时刻接续）。

    与上游差异：星号可选后，无星段的 duration 缺省由调用方（core.simai）
    按均摊补齐——这里只透传 None。
    """
    complete_slides = []
    cursor = start_button
    for slide in chained_slides:
        if slide["reflect"] == -1 or slide["end"] == -1:
            continue
        seg_duration = duration if slide["duration"] is None else slide["duration"]
        seg_bpm = (
            equivalent_bpm
            if slide["equivalent_bpm"] is None
            else slide["equivalent_bpm"]
        )
        complete_slides.append(
            {
                "type": "slide",
                "start_button": cursor,
                "modifier": f"*{slide.get('modifier', '')}",
                "pattern": slide["pattern"],
                "reflect_position": slide["reflect"],
                "end_button": slide["end"],
                "duration": seg_duration,
                "equivalent_bpm": seg_bpm,
            }
        )
        cursor = slide["end"]
    return complete_slides
