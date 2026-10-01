# -*- coding: utf-8 -*-
"""OCR 名称拼图批量画布（审计 G3 切片 4.6b，自 GeneralRecognizer 出仓）。

同类 ROI 条带按检测器工作尺度分块拼接：画布超宽会被 PaddleOCR 检测器整体
降采样（长边压到 960），缩放过深时竖排名的行级检测退化为逐字分段
（2026-09 巅峰赛 14 卡 3372px 画布事故）；分块让每次检测都落在与单条回退
一致的原生尺度上。纯 numpy 静态函数，无引擎与实例状态。
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)

_BATCH_SLOT_GAP = 30
# PaddleOCR 检测器长边 960 上限：画布不超过它就不会被内部降采样；超限缩放过深时
# 行级检测退化（0.68×切行、0.285×逐字，见 2026-09 巅峰赛 14 卡画布回归）。
_BATCH_CANVAS_MAX_WIDTH = 960


def split_canvas_groups(prepared_slots: dict[int, np.ndarray]) -> list[list[int]]:
    """按宽度累积分组，保证每组画布（含槽间隙）不超过检测器工作尺度。"""
    groups: list[list[int]] = []
    current: list[int] = []
    width = 0
    for slot in sorted(prepared_slots):
        strip_width = prepared_slots[slot].shape[1]
        if current and width + _BATCH_SLOT_GAP + strip_width > _BATCH_CANVAS_MAX_WIDTH:
            groups.append(current)
            current, width = [], 0
        current.append(slot)
        width += strip_width + (_BATCH_SLOT_GAP if len(current) > 1 else 0)
    if current:
        groups.append(current)
    return groups


def build_batch_canvas(
    prepared_slots: dict[int, np.ndarray],
) -> tuple[np.ndarray, dict[int, tuple[int, int]]]:
    """将同组条带横向拼接为灰度画布，返回画布与各槽位的横向区间。"""
    height = max(image.shape[0] for image in prepared_slots.values())
    width = sum(image.shape[1] for image in prepared_slots.values())
    width += _BATCH_SLOT_GAP * (len(prepared_slots) - 1)
    canvas = np.zeros((height, width), dtype=np.uint8)
    ranges: dict[int, tuple[int, int]] = {}
    left = 0
    for slot, image in prepared_slots.items():
        image_height, image_width = image.shape[:2]
        canvas[:image_height, left:left + image_width] = image
        ranges[slot] = (left, left + image_width)
        left += image_width + _BATCH_SLOT_GAP
    return canvas, ranges


def join_name_fragments(
    slot: int,
    candidates: list[tuple[str, float, float]],
) -> list[tuple[str, float, float]]:
    """同槽多框按 y 序拼接为整名——竖排名条被拆成碎片时，框内文本按上下顺序重排即为原文。

    拼接置信度取碎片最小值：整名的可信度取决于最不可信的一截。
    """
    ordered = sorted(candidates, key=lambda item: item[2])
    joined = "".join(text for text, _confidence, _center_y in ordered)
    confidence = min(confidence for _text, confidence, _center_y in ordered)
    logger.debug("武将 %d 拼图碎片拼接: %s -> %r", slot, [text for text, _c, _y in ordered], joined)
    return [(joined, confidence, ordered[-1][2])]
