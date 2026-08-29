"""
名将杀 Agent - 实战配队座次解析

从 combos 的 note 自由文本中解析双方武将的座次要求。
规则说明：position 字段是配对级无序摘要（不含顺序），note 才是座次顺序的
权威来源。规则全量验证见方案文档：1170 条可 100% 分类
（1144 条解析出座次 + 26 条明确无座次要求，0 条失败）。
"""

from __future__ import annotations

import re

# 手录昵称/错别字别名：真实武将名 → note 中可能出现的写法（来源：2026-08 导出的发现）
ALIAS: dict[str, list[str]] = {
    "吕布": ["牢布"],
    "甄宓": ["甄姬"],
    "夏侯惇": ["夏侯停"],
}

# 解析状态
STATUS_PARSED = "parsed"      # 双方座次均已解析（空列表 = 无座次要求）
STATUS_PARTIAL = "partial"    # 仅一方解析成功
STATUS_NONE = "none"          # note 无任何数字 = 无座次要求
STATUS_UNPARSED = "unparsed"  # 有数字但无法归类，需人工复核


def _seats_of(digits: str) -> list[int] | None:
    """数字串 → 号位列表；'0' 表示无座次要求返回空列表；非法返回 None。"""
    if digits == "0":
        return []
    seats = sorted({int(ch) for ch in digits})
    return seats if seats and all(1 <= s <= 4 for s in seats) else None


def format_seats(seats: list[int]) -> str:
    """号位列表 → 展示文本（空 = 任意座）。"""
    return "/".join(str(s) for s in seats) if seats else "任意"


def parse_seats(note: str, hero1: str, hero2: str) -> tuple[str, list[int], list[int]]:
    """解析 note 中的座次，返回 (status, hero1_seats, hero2_seats)。

    规则按优先级：
    1. "武将名+数字"（含 "数字+武将名" 前置写法；两位数字 = 可选区间，如 "34"=3或4号）；
    2. 剥离武将名后取开头的纯数字 token，按顺序对应英雄1/英雄2（"0" = 无要求）。
    """
    candidates = {hero1, hero2}
    for hero in (hero1, hero2):
        candidates.update(ALIAS.get(hero, []))

    found: dict[str, list[int]] = {}
    for name in candidates:
        for pattern in (
            re.escape(name) + r"\s*([0-9]{1,2})",
            r"([0-9]{1,2})\s*" + re.escape(name),
        ):
            matched = re.search(pattern, note)
            if matched:
                seats = _seats_of(matched.group(1))
                if seats is not None:
                    real = hero1 if name == hero1 or name in ALIAS.get(hero1, []) else hero2
                    found[real] = seats
                    break
    if hero1 in found and hero2 in found:
        return STATUS_PARSED, found[hero1], found[hero2]

    stripped = note
    for name in candidates:
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
