"""
名将杀 Agent - 实战配队座次解析

从 combos 的 note 自由文本中解析双方武将的座次要求。
规则说明：position 字段是配对级无序摘要（不含顺序），note 才是座次顺序的
权威来源。匹配范围限定在 hero1/hero2 两人内：除全名与 ALIAS 别名外，还接受
去掉名字首/尾字符的简称片段（如 相如=司马相如、临海=临海公主）；两将共享的
片段无法归属，剔除。座次词句式：坐N/坐N号/坐前(面)=先手12号、坐后(面)=后手
34号、不坐N/别坐N=取其余号位、先手/后手、N号位+名字；单字片段只允许与座次
词连用，避免误配普通数字。
"""

from __future__ import annotations

import re

# 手录昵称/错别字别名：真实武将名 → note 中可能出现的写法（来源：2026-08 导出的发现）
ALIAS: dict[str, list[str]] = {
    "吕布": ["牢布"],
    "甄宓": ["甄姬"],
    "夏侯惇": ["夏侯停"],
    "刘邦": ["刘绑"],
}

# 名字与座次词之间可出现的修饰词（来源：2026-09 导出高频写法）
_FILLER = r"(?:尽量|尽可能|永远|只能)?"

# 解析状态
STATUS_PARSED = "parsed"      # 双方座次均已解析（空列表 = 无座次要求）
STATUS_PARTIAL = "partial"    # 仅一方解析成功
STATUS_NONE = "none"          # note 无任何数字 = 无座次要求
STATUS_UNPARSED = "unparsed"  # 有数字但无法归类，需人工复核

_CN_NUM = str.maketrans("一二三四", "1234")


def _seats_of(digits: str) -> list[int] | None:
    """数字串 → 号位列表；'0' 表示无座次要求返回空列表；非法返回 None。"""
    digits = digits.translate(_CN_NUM)
    if digits == "0":
        return []
    seats = sorted({int(ch) for ch in digits})
    return seats if seats and all(1 <= s <= 4 for s in seats) else None


def _complement(seat: str) -> list[int]:
    """"不坐N" 的反面：其余三个号位。"""
    return [s for s in (1, 2, 3, 4) if s != int(seat.translate(_CN_NUM))]


def format_seats(seats: list[int]) -> str:
    """号位列表 → 展示文本（空 = 任意座）。"""
    return "/".join(str(s) for s in seats) if seats else "任意"


def _raw_fragments(hero: str) -> set[str]:
    """武将的候选写法：全名 + 别名 + 去首/去尾简称片段。"""
    frags = {hero, *ALIAS.get(hero, [])}
    for k in range(1, len(hero)):
        frags.add(hero[k:])
        frags.add(hero[:-k])
    return frags


def _seat_patterns(frag: str) -> list[tuple[re.Pattern[str], str]]:
    """单个片段的座次句式，按优先级排列：否定式 > 坐前/后 > 坐N > 裸数字
    （配对声明的座位声明）> N号位/先手/后手（语境提及）。

    kind：front=先手12号 / back=后手34号 / neg=取其余号位 / digits=数字组。
    """
    e = re.escape(frag)
    digit = r"([1-4一二三四])"
    pats = [
        (re.compile(e + r"\s*" + _FILLER + r"(?:不|别)\s*坐\s*" + digit + r"\s*号?"), "neg"),
        (re.compile(e + r"\s*" + _FILLER + r"[做在]?\s*坐前[面边]?"), "front"),
        (re.compile(e + r"\s*" + _FILLER + r"[做在]?\s*坐后[面边]?"), "back"),
        (re.compile(e + r"\s*" + _FILLER + r"坐\s*([1-4一二三四]{1,3})\s*号?"), "digits"),
    ]
    if len(frag) >= 2:
        # 单字片段不参与裸数字与"名+先手"句式，避免误配普通数字
        pats += [
            (re.compile(e + r"\s*" + _FILLER + r"\s*([0-9]{1,2})"), "digits"),
            (re.compile(r"([0-9]{1,2})\s*" + e), "digits"),
        ]
    pats += [
        (re.compile(r"([1-4一二三四]{1,2})\s*号[位]?\s*" + e), "digits"),
        (re.compile(r"先手" + e), "front"),
        (re.compile(r"后手" + e), "back"),
    ]
    if len(frag) >= 2:
        pats += [
            (re.compile(e + r"\s*(?:想)?先手"), "front"),
            (re.compile(e + r"\s*(?:想)?后手"), "back"),
        ]
    return pats


def _find_seats(note: str, frag: str) -> list[int] | None:
    for pattern, kind in _seat_patterns(frag):
        matched = pattern.search(note)
        if matched:
            if kind == "front":
                return [1, 2]
            if kind == "back":
                return [3, 4]
            if kind == "neg":
                return _complement(matched.group(1))
            return _seats_of(matched.group(1))
    return None


def parse_seats(note: str, hero1: str, hero2: str) -> tuple[str, list[int], list[int]]:
    """解析 note 中的座次，返回 (status, hero1_seats, hero2_seats)。

    规则按优先级：
    1. 座次词句式（坐N/坐前后/不坐N/先手后手/N号位，名字与座次词间可夹修饰词），
       匹配写法为全名、ALIAS 别名或去首尾简称片段（长片段优先，两将共享片段剔除）；
    2. 剥离武将名后取开头的纯数字 token，按顺序对应英雄1/英雄2（"0" = 无要求）。
    """
    frag1 = sorted(_raw_fragments(hero1) - _raw_fragments(hero2), key=lambda f: (-len(f), f))
    frag2 = sorted(_raw_fragments(hero2) - _raw_fragments(hero1), key=lambda f: (-len(f), f))
    found: dict[str, list[int]] = {}
    for hero, frags in ((hero1, frag1), (hero2, frag2)):
        for frag in frags:
            seats = _find_seats(note, frag)
            if seats is not None:
                found[hero] = seats
                break

    if hero1 in found and hero2 in found:
        return STATUS_PARSED, found[hero1], found[hero2]

    stripped = note
    for name in {hero1, hero2, *ALIAS.get(hero1, []), *ALIAS.get(hero2, [])}:
        stripped = stripped.replace(name, " ")
    tokens = []
    for token in stripped.split():
        if re.fullmatch(r"[0-9]{1,2}", token):
            tokens.append(token)
        else:
            break
    if len(tokens) == 1 and tokens[0] == "0":
        return STATUS_PARSED, [], []
    if len(tokens) == 2:
        seats1, seats2 = _seats_of(tokens[0]), _seats_of(tokens[1])
        if seats1 is not None and seats2 is not None:
            return STATUS_PARSED, seats1, seats2

    if found:
        return STATUS_PARTIAL, found.get(hero1, []), found.get(hero2, [])
    return (STATUS_NONE if not re.search(r"[0-9]", note) else STATUS_UNPARSED), [], []
