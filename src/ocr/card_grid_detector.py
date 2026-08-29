"""2v2 选将页卡位检测：从整页截图定位剩余候选武将卡牌并派生名条 ROI。

2v2 模式牌面先发 14 张，双方同时禁选 3 名（可撞车）后剩余 8~11 张可选，
卡牌按行重排（如 7+7 / 5+5 / 4+5），因此不能沿用固定 ROI 布局，改为内容驱动：
卡面为深色/饱和色块、背景为低饱和宣纸（S≈8 / V≈230），先取非宣纸掩码再做
连通域尺寸过滤。参数均为相对比例（基准 2560×1440 实测），分辨率变化时自适应。
"""

from __future__ import annotations

import logging

import cv2
import numpy as np

from src.ocr.roi_config import Roi

logger = logging.getLogger(__name__)

# 卡片区范围（占图宽/高比例）：覆盖禁选期 7 列宽幅与候选期居中布局，
# 排除顶部序章图标、底部席位标签与进度条等 UI。
CARD_ZONE_FX = (0.12, 0.88)
CARD_ZONE_FY = (0.16, 0.67)
# 非宣纸背景掩码阈值：背景饱和度中位数≈8、明度≈230，卡面立绘远超此对比
MASK_SATURATION_MIN = 90
MASK_VALUE_MAX = 90
# 闭运算核：1440p 基准 5px。等待期上下两行 bbox 间隙仅 0~3px（掩码间隙中位
# 25~43px），核 ≥9 会把两行粘连成整块，必须停留在 5~7 档
CLOSE_KERNEL_BASE = 5
CLOSE_KERNEL_MIN = 3
# 合法卡数：禁选期 14，候选期 8~11；越界视为非 2v2 牌面
CARD_COUNT_RANGE = (8, 14)
# 卡牌尺寸窗（占图宽/高比例）：实测卡 238×326（w/h≈0.73），立绘出画使宽
# 最大 +12%；高是区分标准选将页（单行 8 张、卡高 365~368）的关键维度
CARD_WIDTH_RANGE = (0.086, 0.115)
CARD_HEIGHT_RANGE = (0.215, 0.245)
CARD_ASPECT_RANGE = (0.60, 0.95)
AREA_MIN_RATIO = 0.0055  # 连通域最小面积（占全图像素），滤除龙纹饰件等碎块

# 名条在卡内相对位置：以卡 bbox 左缘锚定，不受右侧立绘出画影响。
# 纵向实测（多张卡标定）：阵营徽章 0~13%、名字 17%~49%（三字名起点更高）、
# 等级数字 55~64%、费用角标 80~95%，故取 15%~53% 避开徽章与数字污染
NAME_ROI_X_RATIO = 0.06
NAME_ROI_Y_RATIO = 0.15
NAME_ROI_W_RATIO = 0.30
NAME_ROI_H_RATIO = 0.38


def detect_selection_cards(image: np.ndarray) -> list[Roi] | None:
    """检测 2v2 选将页牌面，返回行优先（上→下、行内左→右）的卡牌 bbox 列表。

    非 2v2 牌面（卡数不在合法区间或几何不符）返回 None，语义对齐轮询的
    healthy_no_match，由调用方决定回退行为。
    """
    height, width = image.shape[:2]
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask = (
        np.logical_or(
            hsv[:, :, 1] > MASK_SATURATION_MIN,
            hsv[:, :, 2] < MASK_VALUE_MAX,
        ).astype(np.uint8)
        * 255
    )
    kernel_size = max(CLOSE_KERNEL_MIN, round(height / 1440 * CLOSE_KERNEL_BASE))
    kernel = np.ones((kernel_size, kernel_size), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    x_min, x_max = width * CARD_ZONE_FX[0], width * CARD_ZONE_FX[1]
    y_min, y_max = height * CARD_ZONE_FY[0], height * CARD_ZONE_FY[1]
    w_low, w_high = width * CARD_WIDTH_RANGE[0], width * CARD_WIDTH_RANGE[1]
    h_low, h_high = height * CARD_HEIGHT_RANGE[0], height * CARD_HEIGHT_RANGE[1]
    aspect_low, aspect_high = CARD_ASPECT_RANGE
    area_min = AREA_MIN_RATIO * width * height

    cards: list[Roi] = []
    for index in range(1, count):
        x, y, w, h, area = stats[index]
        if area < area_min:
            continue
        if not (x_min <= x and x + w <= x_max and y_min <= y and y + h <= y_max):
            continue
        if not (w_low <= w <= w_high and h_low <= h <= h_high):
            continue
        aspect = w / h
        if not (aspect_low <= aspect <= aspect_high):
            continue
        cards.append((int(x), int(y), int(w), int(h)))

    low, high = CARD_COUNT_RANGE
    if not low <= len(cards) <= high:
        logger.debug("2v2 卡位检测未通过：检出 %d 张（合法区间 %d~%d）", len(cards), low, high)
        return None
    return _sort_row_major(cards)


def derive_name_rois(cards: list[Roi]) -> list[Roi]:
    """按卡内相对比例生成各卡竖排名条 ROI，顺序与输入卡牌列表一致。"""
    return [
        (
            round(x + w * NAME_ROI_X_RATIO),
            round(y + h * NAME_ROI_Y_RATIO),
            round(w * NAME_ROI_W_RATIO),
            round(h * NAME_ROI_H_RATIO),
        )
        for x, y, w, h in cards
    ]


def _sort_row_major(cards: list[Roi]) -> list[Roi]:
    """按行聚类后行内按 x 排序。行间距 ≥ 一个卡高，行内 y 抖动 ≤ 数像素，
    以半卡高为聚类阈值可避免绝对桶边界受抖动影响。"""
    ordered_by_y = sorted(cards, key=lambda card: card[1] + card[3] / 2)
    half_height = sorted(card[3] for card in cards)[len(cards) // 2] / 2
    rows: list[list[Roi]] = []
    for card in ordered_by_y:
        center = card[1] + card[3] / 2
        if rows:
            row_centers = [item[1] + item[3] / 2 for item in rows[-1]]
            if center - sum(row_centers) / len(row_centers) <= half_height:
                rows[-1].append(card)
                continue
        rows.append([card])
    return [card for row in rows for card in sorted(row, key=lambda item: item[0])]
