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

# 修复路径尺度常数：基准均为"参考卡高"（一次检测中确信卡的中位高，1440p 基准 ≈326px）。
# 选将阶段上下行卡框重叠约 2px（2026-09-22 实测），顶排探底立绘（张飞火焰特效）
# 会把行间 4~6px 掩码腰填到 5px 闭运算核的桥接能力之内，上下两卡焊成 ~2 倍卡高
# 的连通域并被尺寸窗整块拒绝——8/9 卡时跌破计数下限冻结识别，10/11 卡时静默缺 2。
SINGLE_OVERGROWTH_MAX_RATIO = 1.35  # 近界单卡收纳上限：选中光效/抬升的掩码外扩幅度
STACKED_SPLIT_MAX_RATIO = 2.4       # 竖向融合块拆分上限：实测两卡焊接 651~652 ≈ 2.0 倍
REFIT_SPAN_RATIO = 0.6              # 拆分后半段卡体列跨度阈值：列掩码纵向跨度占段高比例
REPAIR_ARMED_MIN_CARDS = 6          # 修复武装门槛：合法牌面 ≥8 张、焊接后最少剩 6 张，防非牌面屏幕误播种

# 名条在卡内相对位置：以卡 bbox 左缘锚定，不受右侧立绘出画影响。
# 纵向实测（多张卡标定）：阵营徽章 0~13%、名字 17%~49%（三字名起点更高）、
# 等级数字 55~64%、费用角标 80~95%，故取 15%~53% 避开徽章与数字污染
NAME_ROI_X_RATIO = 0.06
NAME_ROI_Y_RATIO = 0.15
NAME_ROI_W_RATIO = 0.30
NAME_ROI_H_RATIO = 0.38

# 上次检测结果：轮询期间结果基本恒定（如持续"检出 0 张"），仅状态翻转时打
# 日志，避免每轮重复一条相同内容。仅在 OCR worker 单线程中调用，无并发问题。
_LAST_DETECTION_PASSED: bool | None = None


def detect_selection_cards(image: np.ndarray) -> list[Roi] | None:
    """检测 2v2 选将页牌面，返回行优先（上→下、行内左→右）的卡牌 bbox 列表。

    非 2v2 牌面（卡数不在合法区间或几何不符）返回 None，语义对齐轮询的
    healthy_no_match，由调用方决定回退行为。尺寸窗拒绝的可疑组件在确信卡
    足够时进入修复路径（近界单卡收纳 / 竖向融合块拆分），见模块尾常量注释。
    """
    global _LAST_DETECTION_PASSED
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
    suspicious: list[tuple[Roi, str]] = []
    for index in range(1, count):
        x, y, w, h, area = stats[index]
        if area < area_min:
            continue
        if not (x_min <= x and x + w <= x_max and y_min <= y and y + h <= y_max):
            continue
        box = (int(x), int(y), int(w), int(h))
        if w_low <= w <= w_high and h_low <= h <= h_high:
            aspect = w / h
            if aspect_low <= aspect <= aspect_high:
                cards.append(box)
                continue
            suspicious.append((box, "宽高比"))
        else:
            suspicious.append((box, "尺寸窗"))

    # 修复路径：确信卡足够时，对尺寸窗拒绝的可疑组件按参考卡高收纳或拆分
    if len(cards) >= REPAIR_ARMED_MIN_CARDS and suspicious:
        ref_h = int(np.median([h for _, _, _, h in cards]))
        ref_w = int(np.median([w for _, _, w, _ in cards]))
        remaining: list[tuple[Roi, str]] = []
        for box, reason in suspicious:
            _, _, w, h = box
            if h <= h_high:
                remaining.append((box, reason))  # 高度在窗内的其他越界（如龙饰横翼）无从修复
                continue
            if w_low <= w <= w_high and h <= ref_h * SINGLE_OVERGROWTH_MAX_RATIO:
                cards.append(box)
                logger.debug(
                    "卡位近界收纳: %s 高度 %d 越窗但 ≤%.2f×参考卡高 %d，按选中外扩收录",
                    box, h, SINGLE_OVERGROWTH_MAX_RATIO, ref_h,
                )
                continue
            parts = [
                part
                for part in _split_stacked_component(mask, box, ref_h, ref_w)
                if w_low <= part[2] <= w_high and h_low <= part[3] <= h_high
            ]
            if parts:
                cards.extend(parts)
                logger.debug(
                    "卡位融合拆分: %s 高度 %d ≈ %.2f×参考卡高 %d → %s",
                    box, h, h / ref_h, ref_h, parts,
                )
            else:
                remaining.append((box, reason))
                logger.debug(
                    "卡位组件维持拒绝: %s (%s，高度 %d = %.2f×参考卡高 %d)",
                    box, reason, h, h / ref_h, ref_h,
                )
        suspicious = remaining

    low, high = CARD_COUNT_RANGE
    if not low <= len(cards) <= high:
        if _LAST_DETECTION_PASSED is not False:
            detail = "；".join(f"{box}({reason})" for box, reason in suspicious[:6])
            logger.debug(
                "2v2 卡位检测未通过：检出 %d 张（合法区间 %d~%d）%s",
                len(cards), low, high, f"；越界组件: {detail}" if detail else "",
            )
        _LAST_DETECTION_PASSED = False
        return None
    if _LAST_DETECTION_PASSED is False:
        logger.debug("2v2 卡位检测恢复：检出 %d 张（合法区间 %d~%d）", len(cards), low, high)
    _LAST_DETECTION_PASSED = True
    return _sort_row_major(cards)


def _split_stacked_component(mask: np.ndarray, box: Roi, ref_h: int, ref_w: int) -> list[Roi]:
    """把竖向融合块沿最窄腰线拆成逐卡片段，并以参考卡宽重拟合各段卡体。

    返回片段未做卡牌窗口校验，由调用方逐段校验后收录；高度超过
    STACKED_SPLIT_MAX_RATIO × 参考卡高的组件不拆（维持上游拒绝防幻影）。
    """
    x, y, w, h = box
    if h > ref_h * STACKED_SPLIT_MAX_RATIO:
        return []
    strip = mask[y:y + h, x:x + w]
    row_widths = (strip > 0).sum(axis=1)
    margin = max(4, ref_h // 6)  # 避开上下边缘的龙饰展开，缝在真正的行间腰部
    interior = row_widths[margin:-margin]
    seam = (int(np.argmin(interior)) + margin) if interior.size else h // 2
    parts = []
    for part_y, part_h in ((y, seam), (y + seam, h - seam)):
        part = _refit_card_width(mask, x, part_y, w, part_h, ref_w)
        if part is not None:
            parts.append(part)
    return parts


def _refit_card_width(
    mask: np.ndarray, x: int, y: int, w: int, h: int, card_w: int,
) -> Roi | None:
    """按列纵向跨度定位卡体中心，并以参考卡宽重拟合卡体水平范围。

    卡体列的掩码纵向跨度贯穿整段高度（上下卡框边都在掩码内，淡色立绘卡
    也成立），而龙饰翼展只覆盖局部高度——以跨度 ≥ REFIT_SPAN_RATIO×段高
    判定卡体列。卡框边缘的斜切角会让跨度连续段略窄于真实卡宽，故宽度取
    参考卡宽、以连续段中心对齐。
    """
    strip = mask[y:y + h, x:x + w]
    min_span = h * REFIT_SPAN_RATIO
    full = np.zeros(w, dtype=bool)
    for col in range(w):
        rows = np.flatnonzero(strip[:, col])
        if rows.size and rows[-1] - rows[0] + 1 >= min_span:
            full[col] = True
    best_start = best_len = cur_len = 0
    for index, is_full in enumerate(full):
        if is_full:
            cur_len += 1
            if cur_len > best_len:
                best_start = index - cur_len + 1
                best_len = cur_len
        else:
            cur_len = 0
    if best_len == 0:
        return None
    if best_len >= card_w:
        trim = (best_len - card_w) // 2
        return (x + best_start + trim, y, card_w, h)
    center = best_start + best_len // 2
    left = max(0, min(center - card_w // 2, w - card_w))
    return (x + left, y, card_w, h)


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
