"""配队座次展示格式化（自 data/combo_seats 迁入，#Phase3-B1：纯展示函数归 UI 层）。"""

from __future__ import annotations


def format_seats(seats: list[int]) -> str:
    """号位列表 → 展示文本（空 = 任意座）。"""
    return "/".join(str(s) for s in seats) if seats else "任意"
